"""Experimental identity-bound receipts for conflict-aware offline slide edits."""

import base64
import hashlib
import json
import mimetypes
import secrets
from datetime import UTC, datetime
from typing import Literal, cast

import jwt
from jwt.utils import base64url_decode, base64url_encode
from pydantic import Field, ValidationError

from .artifact_models import ArtifactTarget
from .artifacts import ArtifactStore
from .models import Slide, SlidePatch, StrictModel
from .multiplayer_access import AccessDenied, Actor, Scope, Sharing
from .multiplayer_chat import Conversations
from .offline_receipts import ReceiptSource, initialize, retain
from .storage import ConflictError, Store, digest
from .version_authors import revision_author
from .version_history import capture_slide_version

_SIGNED_RECEIPT_PREFIX = "v2."


def _preview_assets(store: Store, deck_id: str) -> tuple[str, dict[str, str]]:
    """Capture bounded passive bundle bytes, refusing links and oversized collections."""
    folder = store.folder(deck_id)
    theme_file = folder / "theme.css"
    if theme_file.is_symlink() or not theme_file.is_file() or theme_file.stat().st_size > 1_000_000:
        raise ValueError("The presentation theme cannot be saved offline.")
    theme = theme_file.read_text(encoding="utf-8")
    total = len(theme.encode())
    assets: dict[str, str] = {}
    root = folder / "assets"
    if root.is_symlink():
        raise ValueError("Offline assets cannot include symbolic links.")
    if root.exists():
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError("Offline assets cannot include symbolic links.")
            if not path.is_file():
                continue
            if len(assets) >= 500 or path.stat().st_size > 5_000_000:
                raise ValueError("The presentation has too many or oversized offline assets.")
            data = path.read_bytes()
            total += len(data)
            if total > 10_000_000:
                raise ValueError("The presentation's offline assets exceed 10 MB.")
            name = path.relative_to(folder).as_posix()
            mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
            assets[name] = "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")
    return theme, assets


class OfflineReceiptInput(StrictModel):
    slide_id: str = Field(min_length=1, max_length=80)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    channel: Literal["shared", "private"] = "shared"


class OfflineSnapshotInput(StrictModel):
    slides: list[OfflineReceiptInput] = Field(min_length=1, max_length=500)
    receipt_version: Literal[1, 2] = 1


class SignedOfflineReceipt(OfflineReceiptInput):
    """Authenticate the original source and slide anchor without storing a slide copy."""

    version: Literal[2]
    binding: str = Field(pattern=r"^[a-f0-9]{64}$")
    html_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    aggregate_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class OfflineCommentInput(StrictModel):
    receipt: str = Field(min_length=32, max_length=1024)
    request_id: str = Field(min_length=8, max_length=100)
    message: str = Field(min_length=1, max_length=10_000)
    channel: Literal["shared", "private"] = "shared"


class OfflineEditInput(StrictModel):
    receipt: str = Field(min_length=32, max_length=1024)
    request_id: str = Field(min_length=8, max_length=100)
    html: str = Field(min_length=1, max_length=150_000)
    overwrite_expected: str = Field(default="", pattern=r"^$|^[a-f0-9]{64}$")
    base_html: str | None = Field(default=None, min_length=1, max_length=150_000)


class OfflineDelivery:
    """Verify offline anchors and record replayable edit results under the sharing lock."""

    def __init__(self, sharing: Sharing) -> None:
        self.sharing = sharing
        with sharing.locked(), sharing.connect() as db:
            initialize(db)
            db.execute(
                "CREATE TABLE IF NOT EXISTS offline_edits ("
                "operation TEXT PRIMARY KEY, receipt TEXT NOT NULL, payload TEXT NOT NULL,"
                "expected_hash TEXT NOT NULL, before_source TEXT NOT NULL,"
                "result TEXT NOT NULL DEFAULT '')"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS offline_edit_owners ("
                "operation TEXT PRIMARY KEY, owner_key TEXT NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS offline_edit_owner_key "
                "ON offline_edit_owners(owner_key)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS offline_signing_key ("
                "id INTEGER PRIMARY KEY CHECK(id=1), key BLOB NOT NULL)"
            )
            _ = db.execute(
                "UPDATE offline_edits SET before_source='' WHERE result!='' AND before_source!=''"
            )
            _ = db.execute(
                "INSERT OR IGNORE INTO offline_signing_key VALUES (1,?)", (secrets.token_bytes(32),)
            )
            row = db.execute("SELECT key FROM offline_signing_key WHERE id=1").fetchone()
            key: object = row["key"] if row else None
            if not isinstance(key, bytes) or len(key) != 32:
                raise RuntimeError("Invalid offline receipt signing key.")
            self._signing_key = key

    def _binding(self, actor: Actor, scope: Scope, namespace: str) -> str:
        """Bind signatures to the verified account and the requested artifact."""
        return digest(
            json.dumps([namespace, actor.person, scope.workspace, scope.kind, scope.resource])
        )

    def _encode_anchor(self, anchor: SignedOfflineReceipt) -> str:
        """Issue a canonical token so retries retain the same receipt hash."""
        return _SIGNED_RECEIPT_PREFIX + jwt.encode(
            anchor.model_dump(), self._signing_key, algorithm="HS256"
        )

    def _signed_anchor(
        self, actor: Actor, scope: Scope, namespace: str, receipt: str
    ) -> SignedOfflineReceipt:
        """Resolve a signed receipt only in the identity and scope that requested it."""
        if not receipt.isascii():
            raise AccessDenied
        try:
            payload = jwt.decode(
                receipt.removeprefix(_SIGNED_RECEIPT_PREFIX),
                self._signing_key,
                algorithms=["HS256"],
            )
            anchor = SignedOfflineReceipt.model_validate(payload)
            signature = receipt.rsplit(".", 1)[1].encode("ascii")
            if signature != base64url_encode(base64url_decode(signature)):
                raise AccessDenied
        except (jwt.InvalidTokenError, ValidationError) as error:
            raise AccessDenied from error
        if anchor.binding != self._binding(actor, scope, namespace):
            raise AccessDenied
        return anchor

    def issue(
        self,
        actor: Actor,
        scope: Scope,
        namespace: str,
        slide_id: str,
        content_hash: str,
        expected_hash: str,
        channel: str,
    ) -> str:
        """Capture the reviewed source only while this identity can still view the deck."""
        if actor.agent or not namespace or channel not in {"shared", "private"}:
            raise AccessDenied
        with self.sharing.guard(actor, scope, "view") as store:
            artifact = ArtifactStore(store).get_artifact(scope.resource)
            if artifact.legacy_deck_id != scope.resource:
                raise AccessDenied
            slide = store.locate(store.read(scope.resource), slide_id)
            view = store.view_slide(scope.resource, slide, feedback=[])
            if view.content_hash != content_hash or view.expected_hash != expected_hash:
                raise ConflictError("The slide changed. Review it before saving offline.")
            source = json.dumps(
                slide.model_dump(
                    include={"id", "title", "html", "interactive_html", "sources", "revision"}
                ),
                ensure_ascii=False,
            )
            with self.sharing.connect() as db:
                return retain(
                    db,
                    [
                        ReceiptSource(
                            namespace,
                            actor.person,
                            scope.workspace,
                            scope.resource,
                            slide_id,
                            content_hash,
                            expected_hash,
                            source,
                            channel,
                            artifact.content_hash,
                        )
                    ],
                )[0]

    def issue_snapshot(
        self,
        actor: Actor,
        scope: Scope,
        namespace: str,
        slides: list[OfflineReceiptInput],
        *,
        receipt_version: Literal[1, 2] = 1,
    ) -> dict[str, object]:
        """Verify every requested revision before issuing any receipts for the snapshot."""
        if actor.agent or not namespace or len({item.slide_id for item in slides}) != len(slides):
            raise AccessDenied
        with self.sharing.guard(actor, scope, "view") as store:
            artifact = ArtifactStore(store).get_artifact(scope.resource)
            if artifact.legacy_deck_id != scope.resource:
                raise AccessDenied
            deck = store.read(scope.resource)
            theme, assets = _preview_assets(store, scope.resource)
            entries: list[ReceiptSource] = []
            receipts: dict[str, str] = {}
            binding = self._binding(actor, scope, namespace)
            for item in slides:
                slide = store.locate(deck, item.slide_id)
                view = store.view_slide(scope.resource, slide, feedback=[])
                if (
                    view.content_hash != item.content_hash
                    or view.expected_hash != item.expected_hash
                ):
                    raise ConflictError("The slide changed. Review it before saving offline.")
                if receipt_version == 2:
                    claims = SignedOfflineReceipt.model_validate(
                        {
                            **item.model_dump(),
                            "version": 2,
                            "binding": binding,
                            "html_hash": digest(view.html),
                            "aggregate_hash": artifact.content_hash,
                        }
                    )
                    receipts[item.slide_id] = self._encode_anchor(claims)
                    continue
                source = json.dumps(
                    slide.model_dump(
                        include={"id", "title", "html", "interactive_html", "sources", "revision"}
                    ),
                    ensure_ascii=False,
                )
                entries.append(
                    ReceiptSource(
                        namespace,
                        actor.person,
                        scope.workspace,
                        scope.resource,
                        item.slide_id,
                        item.content_hash,
                        item.expected_hash,
                        source,
                        item.channel,
                        artifact.content_hash,
                    )
                )
            if receipt_version == 2:
                return {"receipts": receipts, "theme": theme, "assets": assets}
            with self.sharing.connect() as db:
                tokens = retain(db, entries)
            return {
                "receipts": {
                    item.slide_id: token for item, token in zip(slides, tokens, strict=True)
                },
                "theme": theme,
                "assets": assets,
            }

    def edit(
        self,
        actor: Actor,
        scope: Scope,
        namespace: str,
        receipt: str,
        request_id: str,
        html: str,
        overwrite_expected: str = "",
        base_html: str | None = None,
    ) -> dict[str, object]:
        """Apply an independent edit or return both sources without replacing a conflict."""
        if actor.agent or not namespace or not receipt or not html or len(html) > 150_000:
            raise ValueError("Invalid offline edit.")
        token_hash = hashlib.sha256(receipt.encode()).hexdigest()
        operation = self.sharing.request_id(actor, request_id)
        owner_key = digest(json.dumps([namespace, actor.person]))
        payload = hashlib.sha256(
            json.dumps(
                [token_hash, namespace, scope.workspace, scope.resource, html, overwrite_expected],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        with self.sharing.guard(actor, scope, "edit") as store:
            if ArtifactStore(store).get_artifact(scope.resource).legacy_deck_id != scope.resource:
                raise AccessDenied
            with self.sharing.connect() as db:
                if receipt.startswith(_SIGNED_RECEIPT_PREFIX):
                    anchor = self._signed_anchor(actor, scope, namespace, receipt)
                    if base_html is None or digest(base_html) != anchor.html_hash:
                        raise AccessDenied
                    slide_id, original_html = anchor.slide_id, base_html
                else:
                    row = db.execute(
                        "SELECT source,slide FROM offline_receipts WHERE token_hash=? "
                        "AND namespace=? AND person=? AND workspace=? AND deck=?",
                        (token_hash, namespace, actor.person, scope.workspace, scope.resource),
                    ).fetchone()
                    if row is None:
                        raise AccessDenied
                    slide_id = cast(str, row["slide"])
                    original_html = cast(str, json.loads(row["source"])["html"])
                person = db.execute(
                    "SELECT email FROM people WHERE id=?", (actor.person,)
                ).fetchone()
                if person is None:
                    raise AccessDenied
                author = cast(str, person["email"])
                prior = db.execute(
                    "SELECT * FROM offline_edits WHERE operation=?", (operation,)
                ).fetchone()
                if prior and (prior["receipt"] != token_hash or prior["payload"] != payload):
                    raise ConflictError("This request_id already belongs to another edit.")
                if prior and prior["result"]:
                    return cast(dict[str, object], json.loads(prior["result"]))
                slide = store.locate(store.read(scope.resource), slide_id)
                current = store.view_slide(scope.resource, slide, feedback=[])
                if (
                    not prior
                    and current.html != original_html
                    and current.html != html
                    and overwrite_expected != current.expected_hash
                ):
                    return {
                        "status": "conflict",
                        "current_expected_hash": current.expected_hash,
                        "current_content_hash": current.content_hash,
                        "base": original_html,
                        "current": current.html,
                        "draft": html,
                    }
                expected = cast(str, prior["expected_hash"]) if prior else current.expected_hash
                if not prior:
                    before_source = slide.model_dump_json()
                    used = db.execute(
                        "SELECT COALESCE(SUM(length(CAST(e.before_source AS BLOB))),0) "
                        "FROM offline_edits e WHERE e.result='' AND (e.operation IN ("
                        "SELECT operation FROM offline_edit_owners WHERE owner_key=?) OR "
                        "e.receipt IN (SELECT token_hash FROM offline_receipts "
                        "WHERE namespace=? AND person=?))",
                        (owner_key, namespace, actor.person),
                    ).fetchone()[0]
                    if used + len(before_source.encode()) > 20_000_000:
                        raise ValueError("Offline edit recovery storage is full.")
                    _ = db.execute(
                        "INSERT INTO offline_edits VALUES (?,?,?,?,?, '')",
                        (operation, token_hash, payload, expected, before_source),
                    )
                    _ = db.execute(
                        "INSERT INTO offline_edit_owners VALUES (?,?)", (operation, owner_key)
                    )
                else:
                    before_source = cast(str, prior["before_source"])
            with revision_author(author):
                before = Slide.model_validate_json(before_source)
                predicted = before.model_copy(update={"html": html})
                if predicted.html != before.html:
                    predicted.revision = before.revision + 1
                store_request = "offline-" + operation
                if prior and slide.model_dump_json() == predicted.model_dump_json():
                    with store.editing(scope.resource) as db:
                        existing = db.execute(
                            "SELECT 1 FROM operations WHERE deck=? AND request_id=?",
                            (scope.resource, store_request),
                        ).fetchone()
                        latest = store.locate(store.read(scope.resource), slide_id)
                        if not existing and latest.model_dump_json() == predicted.model_dump_json():
                            capture_slide_version(db, scope.resource, before)
                            capture_slide_version(
                                db,
                                scope.resource,
                                latest,
                                saved_at=datetime.now(UTC).isoformat(),
                                authored=True,
                            )
                            approved = db.execute(
                                "SELECT content_hash FROM approvals WHERE deck=? AND slide=?",
                                (scope.resource, slide_id),
                            ).fetchone()
                            seen = db.execute(
                                "SELECT content_hash FROM slide_views WHERE deck=? AND slide=?",
                                (scope.resource, slide_id),
                            ).fetchone()
                            recovered = store.view_slide(
                                scope.resource,
                                latest,
                                feedback=[],
                                review_hashes=(
                                    {slide_id: cast(str, approved[0])} if approved else {},
                                    {slide_id: cast(str, seen[0])} if seen else {},
                                ),
                            )
                            operation_payload = digest(
                                slide_id + expected + SlidePatch(html=html).model_dump_json()
                            )
                            _ = db.execute(
                                "INSERT INTO operations VALUES (?,?,?,?)",
                                (
                                    scope.resource,
                                    store_request,
                                    operation_payload,
                                    recovered.model_dump_json(),
                                ),
                            )
                saved = store.revise_view(
                    scope.resource, slide_id, expected, store_request, SlidePatch(html=html)
                )
            result: dict[str, object] = {
                "status": "applied",
                "slide_id": slide_id,
                "expected_hash": saved.expected_hash,
                "content_hash": saved.content_hash,
            }
            with self.sharing.connect() as db:
                _ = db.execute(
                    "UPDATE offline_edits SET result=?,before_source='' WHERE operation=?",
                    (json.dumps(result), operation),
                )
                _ = db.execute("DELETE FROM offline_edit_owners WHERE operation=?", (operation,))
            return result

    def comment(
        self,
        actor: Actor,
        scope: Scope,
        namespace: str,
        receipt: str,
        request_id: str,
        message: str,
        channel: str,
    ) -> dict[str, object]:
        """Deliver an original slide anchor only while the same person can still comment."""
        if actor.agent or not namespace or not receipt or channel not in {"shared", "private"}:
            raise AccessDenied
        token_hash = hashlib.sha256(receipt.encode()).hexdigest()
        with self.sharing.guard(actor, scope, "comment"):
            if receipt.startswith(_SIGNED_RECEIPT_PREFIX):
                anchor = self._signed_anchor(actor, scope, namespace, receipt)
                if anchor.channel != channel:
                    raise AccessDenied
                slide_id, original_deck, original_slide = (
                    anchor.slide_id,
                    anchor.aggregate_hash,
                    anchor.content_hash,
                )
            else:
                with self.sharing.connect() as db:
                    row = db.execute(
                        "SELECT slide,aggregate_hash,content_hash FROM offline_receipts "
                        "WHERE token_hash=? AND namespace=? AND person=? "
                        "AND workspace=? AND deck=? AND channel=?",
                        (
                            token_hash,
                            namespace,
                            actor.person,
                            scope.workspace,
                            scope.resource,
                            channel,
                        ),
                    ).fetchone()
                if row is None:
                    raise AccessDenied
                slide_id = cast(str, row["slide"])
                original_deck = cast(str, row["aggregate_hash"])
                original_slide = cast(str, row["content_hash"])
            return Conversations(self.sharing).post(
                actor,
                scope,
                cast("Literal['shared', 'private']", channel),
                message,
                request_id,
                target=ArtifactTarget(kind="slide", slide_id=slide_id),
                content_hash=original_deck,
                slide_content_hash=original_slide,
                offline_anchor=(token_hash, original_deck, original_slide),
            )
