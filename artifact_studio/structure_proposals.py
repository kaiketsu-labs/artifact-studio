"""Durable, presentation-scoped structural decisions delivered to the originating agent."""

import json
import re
import time
import uuid
from typing import Literal, cast

from pydantic import Field

from .artifacts import ArtifactStore
from .models import StrictModel
from .multiplayer_access import LEVEL, AccessDenied, Actor, Scope, Sharing, checksum
from .sections import sections_hash
from .storage import ConflictError, Store


class StructureDecision(StrictModel):
    """Decision anchored to the structure actually shown to a reviewer."""

    decision: Literal["keep", "request_change", "accept"]
    message: str = Field(default="", max_length=10000)
    expected_slide_ids: list[str]
    expected_sections_hash: str
    request_id: str = Field(min_length=8, max_length=100)


class StructureProposals:
    """Keep proposal decisions and their assigned messages in one database transaction."""

    def __init__(self, sharing: Sharing) -> None:
        """Create the proposal table in the shared conversation database."""
        self.sharing = sharing
        with sharing.locked(), sharing.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS structure_proposals (
                id TEXT PRIMARY KEY, workspace TEXT NOT NULL, artifact TEXT NOT NULL,
                agent TEXT NOT NULL, person TEXT NOT NULL, summary TEXT NOT NULL,
                base_ids TEXT NOT NULL, base_sections_hash TEXT NOT NULL,
                proposed_ids TEXT NOT NULL, titles TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL, decision TEXT NOT NULL DEFAULT '',
                reply TEXT NOT NULL DEFAULT '',
                decision_request_id TEXT NOT NULL DEFAULT '',
                decision_payload TEXT NOT NULL DEFAULT '')""")
            db.execute(
                "CREATE INDEX IF NOT EXISTS structure_proposal_scope "
                "ON structure_proposals(workspace,artifact)"
            )

    def publish(
        self,
        actor: Actor,
        scope: Scope,
        summary: str,
        base_slide_ids: list[str],
        base_sections_hash: str,
        proposed_slide_ids: list[str],
        request_id: str,
        proposed_titles: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """Publish an immutable comparison from the structure the agent actually read."""
        if (
            not actor.agent
            or not summary.strip()
            or len(summary) > 2000
            or not 8 <= len(request_id) <= 100
        ):
            raise ValueError("Provide an agent, summary and stable request ID.")
        if len(set(base_slide_ids)) != len(base_slide_ids) or len(set(proposed_slide_ids)) != len(
            proposed_slide_ids
        ):
            raise ValueError("Slide IDs must be unique.")
        titles = proposed_titles or {}
        if (
            len(base_slide_ids) > 200
            or len(proposed_slide_ids) > 200
            or len(titles) > 200
            or any(
                not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", item)
                for item in [*base_slide_ids, *proposed_slide_ids, *titles]
            )
            or any(len(title) > 160 for title in titles.values())
            or not set(titles).issubset(proposed_slide_ids)
        ):
            raise ValueError("Invalid slide IDs or proposed titles.")
        operation = self.sharing.request_id(actor, request_id)
        payload = checksum(
            json.dumps(
                [
                    scope.workspace,
                    scope.resource,
                    summary,
                    base_slide_ids,
                    base_sections_hash,
                    proposed_slide_ids,
                    proposed_titles,
                ],
                sort_keys=True,
            )
        )
        with self.sharing.guard(actor, scope, "edit") as store:
            if ArtifactStore(store).get_artifact(scope.resource).legacy_deck_id != scope.resource:
                raise AccessDenied
            return self._publish_locked(
                store,
                actor,
                scope,
                summary,
                base_slide_ids,
                base_sections_hash,
                proposed_slide_ids,
                proposed_titles or {},
                operation,
                payload,
            )

    def _publish_locked(
        self,
        store: Store,
        actor: Actor,
        scope: Scope,
        summary: str,
        base_slide_ids: list[str],
        base_sections_hash: str,
        proposed_slide_ids: list[str],
        proposed_titles: dict[str, str],
        operation: str,
        payload: str,
    ) -> dict[str, object]:
        """Publish while the sharing grant remains locked."""
        with store.editing(scope.resource):
            deck = store.read(scope.resource)
            with self.sharing.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                prior = db.execute(
                    "SELECT * FROM structure_proposals WHERE request_id=?", (operation,)
                ).fetchone()
                if prior:
                    if prior["payload"] != payload:
                        raise ConflictError("This request ID belongs to another proposal.")
                    return self._proposal(prior, [s.id for s in deck.slides])
                if [s.id for s in deck.slides] != base_slide_ids or sections_hash(
                    deck.sections
                ) != base_sections_hash:
                    raise ConflictError("Presentation structure changed. Read the outline again.")
                pending = db.execute(
                    "SELECT COUNT(*) FROM structure_proposals WHERE workspace=? AND artifact=? "
                    "AND decision=''",
                    (scope.workspace, scope.resource),
                ).fetchone()
                if pending is not None and pending[0] >= 100:
                    raise ValueError("Resolve existing proposals before publishing more.")
                titles = proposed_titles | {slide.id: slide.title for slide in deck.slides}
                proposal_id = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO structure_proposals (id,workspace,artifact,agent,person,summary,"
                    "base_ids,base_sections_hash,proposed_ids,titles,request_id,payload) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        proposal_id,
                        scope.workspace,
                        scope.resource,
                        actor.agent,
                        actor.person,
                        summary,
                        json.dumps(base_slide_ids),
                        base_sections_hash,
                        json.dumps(proposed_slide_ids),
                        json.dumps(titles),
                        operation,
                        payload,
                    ),
                )
                row = db.execute(
                    "SELECT * FROM structure_proposals WHERE id=?", (proposal_id,)
                ).fetchone()
                assert row is not None
                return self._proposal(row, base_slide_ids)

    @staticmethod
    def _proposal(
        row: object, current_ids: list[str], current_titles: dict[str, str] | None = None
    ) -> dict[str, object]:
        """Build a comparison without treating an absent slide as an authorized deletion."""
        import sqlite3

        record = cast(sqlite3.Row, row)
        base = cast(list[str], json.loads(record["base_ids"]))
        proposed = cast(list[str], json.loads(record["proposed_ids"]))
        titles = cast(dict[str, str], json.loads(record["titles"])) | (current_titles or {})
        return {
            "id": record["id"],
            "summary": record["summary"],
            "base_slide_ids": base,
            "base_sections_hash": record["base_sections_hash"],
            "proposed_slide_ids": proposed,
            "missing": [
                {"id": item, "title": titles.get(item, item)}
                for item in current_ids
                if item not in proposed
            ],
            "extra": [
                {"id": item, "title": titles.get(item, item)}
                for item in proposed
                if item not in current_ids
            ],
            "status": "decided" if record["decision"] else "pending",
            "decision": record["decision"] or None,
            "reply": record["reply"],
        }

    def list(self, actor: Actor, scope: Scope) -> dict[str, object]:
        """Read one atomic presentation snapshot without taking its structural write lock."""
        result, _ = self.snapshot(actor, scope)
        assert result is not None
        return result

    def snapshot(
        self, actor: Actor, scope: Scope, validator: str = ""
    ) -> tuple[dict[str, object] | None, str]:
        """Reauthorize the reader before comparing the canonical source and proposal version."""
        with self.sharing.guard_presentation_snapshot(actor, scope.workspace, scope.resource) as (
            store,
            _,
            level,
        ):
            if actor.agent or level < LEVEL["edit"]:
                raise AccessDenied
            source = store.folder(scope.resource) / "index.html"
            details = source.stat()
            with self.sharing.connect() as db:
                version = db.execute(
                    "SELECT COUNT(*),COALESCE(MAX(rowid),0),COUNT(NULLIF(decision,'')) "
                    "FROM structure_proposals WHERE workspace=? AND artifact=?",
                    (scope.workspace, scope.resource),
                ).fetchone()
                assert version is not None
                tag = (
                    '"'
                    + checksum(
                        json.dumps(
                            [
                                scope.workspace,
                                scope.resource,
                                details.st_ino,
                                details.st_size,
                                details.st_mtime_ns,
                                details.st_ctime_ns,
                                *version,
                            ]
                        )
                    )
                    + '"'
                )
                if tag == validator:
                    return None, tag
                rows = db.execute(
                    "SELECT * FROM structure_proposals WHERE workspace=? AND artifact=? "
                    "ORDER BY (decision='') DESC, rowid DESC LIMIT 200",
                    (scope.workspace, scope.resource),
                ).fetchall()
            deck = store.read(scope.resource)
            ids = [slide.id for slide in deck.slides]
            titles = {slide.id: slide.title for slide in deck.slides}
            return {
                "slide_ids": ids,
                "sections_hash": sections_hash(deck.sections),
                "slides": [{"id": slide.id, "title": slide.title} for slide in deck.slides],
                "proposals": [self._proposal(row, ids, titles) for row in rows],
            }, tag if source.stat() == details else ""

    def decide(
        self, actor: Actor, scope: Scope, proposal_id: str, body: StructureDecision
    ) -> dict[str, object]:
        """Record one human decision and deliver it to the original agent atomically."""
        if actor.agent:
            raise AccessDenied
        operation = self.sharing.request_id(actor, body.request_id)
        payload = checksum(body.model_dump_json())
        with self.sharing.guard(actor, scope, "edit") as store:
            with self.sharing.connect() as db:
                origin = db.execute(
                    "SELECT person,agent FROM structure_proposals "
                    "WHERE id=? AND workspace=? AND artifact=?",
                    (proposal_id, scope.workspace, scope.resource),
                ).fetchone()
            if origin is None:
                raise AccessDenied
            recipient = Actor(cast(str, origin["person"]), cast(str, origin["agent"]))
            self.sharing.agent_record(recipient)
            self.sharing.require(recipient, scope, "edit")
            return self._decide_locked(store, actor, scope, proposal_id, body, operation, payload)

    def _decide_locked(
        self,
        store: Store,
        actor: Actor,
        scope: Scope,
        proposal_id: str,
        body: StructureDecision,
        operation: str,
        payload: str,
    ) -> dict[str, object]:
        """Apply a decision under the presentation and sharing locks."""
        with store.editing(scope.resource):
            deck = store.read(scope.resource)
            ids = [s.id for s in deck.slides]
            with self.sharing.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM structure_proposals WHERE id=? AND workspace=? AND artifact=?",
                    (proposal_id, scope.workspace, scope.resource),
                ).fetchone()
                if row is None:
                    raise AccessDenied
                if row["decision_request_id"] == operation:
                    if row["decision_payload"] != payload:
                        raise ConflictError("This request ID belongs to another decision.")
                    return self._proposal(row, ids)
                if row["decision"]:
                    raise ConflictError("Another reviewer already decided this proposal.")
                if db.execute(
                    "SELECT 1 FROM conversations WHERE request_id=?", (operation,)
                ).fetchone():
                    raise ConflictError("This request ID belongs to another message.")
                if (
                    ids != body.expected_slide_ids
                    or sections_hash(deck.sections) != body.expected_sections_hash
                ):
                    raise ConflictError("Presentation structure changed. Review it again.")
                if body.decision == "accept" and (
                    ids != json.loads(row["base_ids"])
                    or sections_hash(deck.sections) != row["base_sections_hash"]
                ):
                    raise ConflictError("This proposal is stale. Ask the agent to update it.")
                text = (
                    f"Structural proposal {proposal_id}: {body.decision}. {body.message}\n"
                    f"Reviewed slide IDs: {json.dumps(ids)}; "
                    f"sections hash: {body.expected_sections_hash}; "
                    f"proposed slide IDs: {row['proposed_ids']}"
                )
                db.execute(
                    "INSERT INTO conversations (workspace,artifact,channel,person,author,message,"
                    "target,content_hash,assigned,released,request_id,payload,created) "
                    "VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?)",
                    (
                        scope.workspace,
                        scope.resource,
                        "shared",
                        actor.person,
                        actor.person,
                        text,
                        "null",
                        "",
                        row["agent"],
                        operation,
                        payload,
                        time.time(),
                    ),
                )
                db.execute(
                    "UPDATE structure_proposals SET decision=?,reply=?,decision_request_id=?,"
                    "decision_payload=? WHERE id=?",
                    (body.decision, body.message, operation, payload, proposal_id),
                )
                result = db.execute(
                    "SELECT * FROM structure_proposals WHERE id=?", (proposal_id,)
                ).fetchone()
                assert result is not None
                return self._proposal(result, ids)
