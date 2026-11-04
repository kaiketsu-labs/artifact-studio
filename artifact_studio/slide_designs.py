"""Immutable slide proposals and explicit, recoverable human design decisions."""

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from pydantic import Field, field_validator

from .interactive import prepare_document
from .models import AgentContext, Deck, Slide, Source, StrictModel
from .storage import ConflictError, SlideMarkup, Store, digest
from .version_history import capture_slide_version
from .visuals import CaptureOptions, Snapshot, snapshot

MAX_SAVED_BYTES = 64 * 1024 * 1024
SOURCE_FIELDS = {"title", "html", "interactive_html", "sources"}


class DesignInput(StrictModel):
    """A whole-slide direction; human notes and approval cannot be supplied."""

    name: str = Field(min_length=1, max_length=60)
    rationale: str = Field(default="", max_length=240)
    html: str = Field(min_length=1, max_length=150_000)
    interactive_html: str = Field(default="", max_length=2_000_000)
    title: str | None = Field(default=None, min_length=1, max_length=160)
    sources: list[Source] | None = None

    @field_validator("name")
    @classmethod
    def readable_name(cls, value: str) -> str:
        """Keep direction names concise and meaningful."""
        if not value.strip():
            raise ValueError("Give each design a name.")
        return value.strip()


class DesignOption(StrictModel):
    """Small proposal metadata, separate from its source and frozen document."""

    id: str
    set_id: str
    name: str
    rationale: str
    source_hash: str
    preview_url: str
    is_original: bool = False


class DesignSet(StrictModel):
    """One immutable publication and its current review decision."""

    id: str
    created_at: str
    pending: bool
    stale: bool
    options: list[DesignOption]
    current_option_id: str | None
    decision_id: str | None


class DesignDecision(StrictModel):
    """A source-free receipt, also used in the human decision history."""

    id: str
    set_id: str
    action: Literal["choose", "keep", "undo"]
    option_id: str | None
    name: str
    created_at: str
    content_hash: str
    revision: int
    pending: bool


class DesignPage(StrictModel):
    """Lazy proposal metadata; no rendered documents, human notes or source bodies."""

    sets: list[DesignSet]
    decisions: list[DesignDecision]
    content_hash: str
    next_cursor: int | None = None
    next_decision_cursor: int | None = None


class DesignSource(StrictModel):
    """One immutable proposal's editable audience source, excluding private notes."""

    option: DesignOption
    title: str
    html: str
    interactive_html: str
    sources: list[Source]


class DecideDesign(StrictModel):
    """A human action anchored to the slide and proposal set actually reviewed."""

    set_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    option_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    expected_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_decision_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    reviewed_changed_slide: bool = False
    request_id: str = Field(min_length=8, max_length=100)


class UndoDesign(StrictModel):
    """Undo only the latest decision while its resulting content is still current."""

    decision_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    expected_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: str = Field(min_length=8, max_length=100)


def install_designs(db: sqlite3.Connection) -> None:
    """Keep proposal source and the two-phase decision journal in private workspace state."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS slide_design_sets (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
            deck TEXT NOT NULL, slide TEXT NOT NULL, created_at TEXT NOT NULL,
            anchor_hash TEXT NOT NULL, visual_hash TEXT NOT NULL, resources_hash TEXT NOT NULL,
            pending INTEGER NOT NULL DEFAULT 1, decision_id TEXT);
        CREATE INDEX IF NOT EXISTS slide_design_sets_slide
            ON slide_design_sets(deck,slide,sequence DESC);
        CREATE TABLE IF NOT EXISTS slide_design_options (
            id TEXT PRIMARY KEY, set_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
            name TEXT NOT NULL, rationale TEXT NOT NULL, source_hash TEXT NOT NULL,
            source_json TEXT NOT NULL, snapshot_json TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS slide_design_options_set
            ON slide_design_options(set_id,ordinal);
        CREATE TABLE IF NOT EXISTS slide_design_decisions (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
            deck TEXT NOT NULL, slide TEXT NOT NULL, set_id TEXT NOT NULL,
            request_id TEXT NOT NULL, payload_hash TEXT NOT NULL, action TEXT NOT NULL,
            option_id TEXT, name TEXT NOT NULL, created_at TEXT NOT NULL,
            before_json TEXT NOT NULL, after_json TEXT NOT NULL, approval_before TEXT,
            approval_after TEXT, pending_before INTEGER NOT NULL,
            decision_before TEXT, pending_after INTEGER NOT NULL,
            resources_hash TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
            result_json TEXT NOT NULL DEFAULT '', UNIQUE(deck,request_id));
        CREATE INDEX IF NOT EXISTS slide_design_decisions_slide
            ON slide_design_decisions(deck,slide,sequence DESC);
    """)


def source_key(slide: Slide) -> str:
    """Identify authored audience source independently of notes and review metadata."""
    return digest(slide.model_dump_json(include=SOURCE_FIELDS))


def resource_key(folder: Path) -> str:
    """Check bytes, including same-size/m time external edits, only on explicit writes."""
    paths = [folder / "theme.css"]
    paths.extend(sorted(path for path in (folder / "assets").rglob("*") if path.is_file()))
    result = hashlib.sha256()
    total = 0
    for path in paths:
        if not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError("Design resources must stay inside the presentation.")
        total += path.stat().st_size
        if total > MAX_SAVED_BYTES:
            raise ValueError("Design resources exceed 64 MiB. Reduce the presentation assets.")
        result.update(path.relative_to(folder).as_posix().encode())
        result.update(b"\0")
        with path.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                result.update(chunk)
        result.update(b"\0")
    return result.hexdigest()


def design_badges(store: Store, deck: Deck) -> dict[str, dict[str, object]]:
    """Read one compact latest-set badge per slide without loading proposal source."""
    with store.connect() as db:
        rows = db.execute(
            "SELECT s.id,s.slide,s.pending,s.decision_id,COUNT(o.id) AS count "
            "FROM slide_design_sets s JOIN slide_design_options o ON o.set_id=s.id "
            "WHERE s.deck=? AND s.sequence=(SELECT MAX(latest.sequence) "
            "FROM slide_design_sets latest WHERE latest.deck=s.deck AND latest.slide=s.slide) "
            "GROUP BY s.id",
            (deck.id,),
        ).fetchall()
    return {
        cast(str, row["slide"]): {
            "set_id": row["id"],
            "count": row["count"],
            "pending": bool(row["pending"]),
            "decision_id": row["decision_id"],
        }
        for row in rows
    }


def recover_design_decisions(store: Store, db: sqlite3.Connection, deck_id: str) -> None:
    """Complete a prepared choice after a crash, without replacing current private state."""
    rows = db.execute(
        "SELECT * FROM slide_design_decisions WHERE deck=? AND completed=0 ORDER BY sequence",
        (deck_id,),
    ).fetchall()
    for row in rows:
        deck = store.read(deck_id)
        current = store.locate(deck, cast(str, row["slide"]))
        before = Slide.model_validate_json(cast(str, row["before_json"]))
        after = Slide.model_validate_json(cast(str, row["after_json"]))
        changing = source_key(before) != source_key(after)
        if changing and source_key(current) == source_key(before):
            if resource_key(store.folder(deck_id)) != row["resources_hash"]:
                raise ConflictError("Design resources changed during a prepared choice.")
            replacement = Slide.model_validate(
                current.model_dump()
                | after.model_dump(include=SOURCE_FIELDS)
                | {"revision": current.revision + 1}
            )
            capture_slide_version(db, deck_id, current)
            deck.slides[deck.slides.index(current)] = replacement
            store.write(deck_id, deck)
            current = replacement
        elif source_key(current) != source_key(after):
            raise ConflictError("A pending design choice conflicts with newer slide content.")
        if changing:
            capture_slide_version(db, deck_id, current, saved_at=cast(str, row["created_at"]))
            if row["approval_after"] is None:
                _ = db.execute(
                    "DELETE FROM approvals WHERE deck=? AND slide=?", (deck_id, current.id)
                )
            else:
                _ = db.execute(
                    "INSERT OR REPLACE INTO approvals VALUES (?,?,?)",
                    (deck_id, current.id, row["approval_after"]),
                )
        result = DesignDecision(
            id=cast(str, row["id"]),
            set_id=cast(str, row["set_id"]),
            action=cast(Literal["choose", "keep", "undo"], row["action"]),
            option_id=cast(str | None, row["option_id"]),
            name=cast(str, row["name"]),
            created_at=cast(str, row["created_at"]),
            content_hash=store.content_hash(deck_id, current),
            revision=current.revision,
            pending=bool(row["pending_after"]),
        )
        _ = db.execute(
            "UPDATE slide_design_sets SET pending=?,decision_id=?,anchor_hash=? WHERE id=?",
            (
                row["pending_after"],
                row["decision_before"] if result.action == "undo" else result.id,
                result.content_hash,
                result.set_id,
            ),
        )
        _ = db.execute(
            "UPDATE slide_design_decisions SET completed=1,result_json=? WHERE id=?",
            (result.model_dump_json(), result.id),
        )
        _ = db.execute(
            "INSERT INTO operations VALUES (?,?,?,?)",
            (deck_id, row["request_id"], row["payload_hash"], result.model_dump_json()),
        )


class SlideDesigns:
    """Publish parallel directions; only human HTTP actions can choose or keep one."""

    def __init__(self, store: Store) -> None:
        self.store = store

    @staticmethod
    def _option(row: sqlite3.Row, deck_id: str, slide_id: str) -> DesignOption:
        return DesignOption(
            id=cast(str, row["id"]),
            set_id=cast(str, row["set_id"]),
            name=cast(str, row["name"]),
            rationale=cast(str, row["rationale"]),
            source_hash=cast(str, row["source_hash"]),
            is_original=row["ordinal"] == 0,
            preview_url=f"/api/decks/{deck_id}/slides/{slide_id}/designs/{row['id']}/preview",
        )

    def _set(self, db: sqlite3.Connection, row: sqlite3.Row, slide: Slide) -> DesignSet:
        options = db.execute(
            "SELECT id,set_id,name,rationale,source_hash,ordinal FROM slide_design_options "
            "WHERE set_id=? ORDER BY ordinal",
            (row["id"],),
        ).fetchall()
        return DesignSet(
            id=cast(str, row["id"]),
            created_at=cast(str, row["created_at"]),
            pending=bool(row["pending"]),
            stale=self.store.content_hash(cast(str, row["deck"]), slide) != row["anchor_hash"],
            options=[self._option(item, cast(str, row["deck"]), slide.id) for item in options],
            current_option_id=next(
                (
                    cast(str, item["id"])
                    for item in options
                    if item["source_hash"] == source_key(slide)
                    and self.store.visual_hash(cast(str, row["deck"])) == row["visual_hash"]
                ),
                None,
            ),
            decision_id=cast(str | None, row["decision_id"]),
        )

    def list(
        self,
        deck_id: str,
        slide_id: str,
        *,
        cursor: int | None = None,
        decision_cursor: int | None = None,
        set_id: str | None = None,
    ) -> DesignPage:
        """Return recent publication groups and decisions without source or private notes."""
        for value in (cursor, decision_cursor):
            if value is not None and not 0 <= value <= 2**63 - 1:
                raise ValueError("Invalid design history cursor.")
        with self.store.editing(deck_id) as db:
            slide = self.store.locate(self.store.read(deck_id), slide_id)
            rows = db.execute(
                "SELECT * FROM slide_design_sets WHERE deck=? AND slide=? "
                "AND (? IS NULL OR sequence<?) AND (? IS NULL OR id=?) "
                "ORDER BY sequence DESC LIMIT 21",
                (deck_id, slide_id, cursor, cursor, set_id, set_id),
            ).fetchall()
            decisions = db.execute(
                "SELECT result_json,sequence FROM slide_design_decisions WHERE deck=? AND slide=? "
                "AND completed=1 AND (? IS NULL OR sequence<?) "
                "ORDER BY sequence DESC LIMIT 31",
                (deck_id, slide_id, decision_cursor, decision_cursor),
            ).fetchall()
            return DesignPage(
                sets=[self._set(db, row, slide) for row in rows[:20]],
                decisions=[
                    DesignDecision.model_validate_json(cast(str, row[0])) for row in decisions[:30]
                ],
                content_hash=self.store.content_hash(deck_id, slide),
                next_cursor=cast(int, rows[19]["sequence"]) if len(rows) > 20 else None,
                next_decision_cursor=cast(int, decisions[29]["sequence"])
                if len(decisions) > 30
                else None,
            )

    def _read_option(self, deck_id: str, slide_id: str, option_id: str) -> sqlite3.Row:
        _ = self.store.locate(self.store.read(deck_id), slide_id)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT o.* FROM slide_design_options o JOIN slide_design_sets s ON s.id=o.set_id "
                "WHERE o.id=? AND s.deck=? AND s.slide=?",
                (option_id, deck_id, slide_id),
            ).fetchone()
        if row is None:
            raise ValueError("Slide design does not exist in this slide.")
        return row

    def read(self, deck_id: str, slide_id: str, option_id: str) -> DesignSource:
        row = self._read_option(deck_id, slide_id, option_id)
        source = Slide.model_validate_json(cast(str, row["source_json"]))
        return DesignSource(
            option=self._option(row, deck_id, slide_id), **source.model_dump(include=SOURCE_FIELDS)
        )

    def preview(self, deck_id: str, slide_id: str, option_id: str) -> Snapshot:
        row = self._read_option(deck_id, slide_id, option_id)
        return Snapshot.model_validate_json(cast(str, row["snapshot_json"]))

    def publish(
        self,
        deck_id: str,
        slide_id: str,
        expected_content_hash: str,
        request_id: str,
        options: list[DesignInput],
    ) -> DesignSet:
        """Freeze proposals and their local dependencies without replacing the chosen slide."""
        if not 1 <= len(options) <= 5 or not 8 <= len(request_id) <= 100:
            raise ValueError("Publish 1–5 designs with an 8–100 character request_id.")
        names = [item.name.casefold() for item in options]
        if len(set(names)) != len(names) or "original" in names:
            raise ValueError(
                "Use distinct design names; Original is reserved for the current slide."
            )
        payload = digest(
            "publish-designs:"
            + slide_id
            + expected_content_hash
            + json.dumps([item.model_dump(mode="json") for item in options])
        )
        with self.store.editing(deck_id) as db:
            prior = db.execute(
                "SELECT payload_hash,result FROM operations WHERE deck=? AND request_id=?",
                (deck_id, request_id),
            ).fetchone()
            if prior:
                if prior[0] != payload:
                    raise ConflictError("Request ID already belongs to another action.")
                return DesignSet.model_validate_json(cast(str, prior[1]))
            deck = self.store.read(deck_id)
            current = self.store.locate(deck, slide_id)
            if self.store.content_hash(deck_id, current) != expected_content_hash:
                raise ConflictError("Slide changed. Read it again before publishing alternatives.")
            resources = resource_key(self.store.folder(deck_id))
            group_id = uuid.uuid4().hex
            created = datetime.now(UTC).isoformat()
            base = current.model_copy(
                update={"notes": "", "notes_revision": 0, "agent": AgentContext()}
            )
            candidates = [
                ("Original", "The design in the slide when these options were created.", base)
            ]
            for item in options:
                candidate = Slide.model_validate(
                    base.model_dump() | item.model_dump(include=SOURCE_FIELDS, exclude_none=True)
                )
                if candidate.interactive_html:
                    candidate = Slide.model_validate(
                        candidate.model_dump()
                        | {
                            "interactive_html": prepare_document(
                                self.store, deck_id, candidate.interactive_html
                            )
                        }
                    )
                SlideMarkup().feed(candidate.html)
                candidates.append((item.name, item.rationale, candidate))
            prepared: list[tuple[str, str, str, Slide, Snapshot]] = []
            total = 0
            for name, rationale, candidate in candidates:
                displayed = candidate
                if candidate.interactive_html:
                    displayed = candidate.model_copy(
                        update={
                            "interactive_html": prepare_document(
                                self.store, deck_id, candidate.interactive_html
                            )
                        }
                    )
                frozen = snapshot(
                    self.store,
                    deck_id,
                    displayed,
                    deck.slides.index(current) + 1,
                    CaptureOptions(reduced_motion=True),
                )
                total += len(frozen.model_dump_json().encode())
                if total > MAX_SAVED_BYTES:
                    raise ValueError("Frozen design previews exceed 64 MiB. Reduce bundled assets.")
                prepared.append((uuid.uuid4().hex, name, rationale, candidate, frozen))
            if resource_key(self.store.folder(deck_id)) != resources:
                raise ConflictError(
                    "Design resources changed during publication. Retry with a fresh read."
                )
            _ = db.execute(
                "INSERT INTO slide_design_sets "
                "(id,deck,slide,created_at,anchor_hash,visual_hash,resources_hash) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    group_id,
                    deck_id,
                    slide_id,
                    created,
                    expected_content_hash,
                    self.store.visual_hash(deck_id),
                    resources,
                ),
            )
            _ = db.executemany(
                "INSERT INTO slide_design_options VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        option_id,
                        group_id,
                        ordinal,
                        name,
                        rationale,
                        source_key(candidate),
                        candidate.model_dump_json(),
                        frozen.model_dump_json(),
                    )
                    for ordinal, (option_id, name, rationale, candidate, frozen) in enumerate(
                        prepared
                    )
                ],
            )
            self.store.append_feedback(
                db,
                deck_id,
                slide_id,
                "agent",
                f"Prepared {len(options)} design alternatives.",
                expected_content_hash,
                released=True,
                design_set_id=group_id,
            )
            row = db.execute("SELECT * FROM slide_design_sets WHERE id=?", (group_id,)).fetchone()
            assert row is not None
            result = self._set(db, row, current)
            _ = db.execute(
                "INSERT INTO operations VALUES (?,?,?,?)",
                (deck_id, request_id, payload, result.model_dump_json()),
            )
            return result

    def decide(self, deck_id: str, slide_id: str, body: DecideDesign) -> DesignDecision:
        """Prepare a human choice or keep-current decision before touching canonical source."""
        return self._decision(deck_id, slide_id, body, None)

    def undo(self, deck_id: str, slide_id: str, body: UndoDesign) -> DesignDecision:
        return self._decision(deck_id, slide_id, None, body)

    def _decision(
        self, deck_id: str, slide_id: str, choice: DecideDesign | None, undo: UndoDesign | None
    ) -> DesignDecision:
        body = choice or undo
        assert body is not None
        payload = digest("design-decision:" + slide_id + body.model_dump_json())
        with self.store.editing(deck_id) as db:
            prior = db.execute(
                "SELECT payload_hash,result FROM operations WHERE deck=? AND request_id=?",
                (deck_id, body.request_id),
            ).fetchone()
            if prior:
                if prior[0] != payload:
                    raise ConflictError("Request ID already belongs to another action.")
                return DesignDecision.model_validate_json(cast(str, prior[1]))
            current = self.store.locate(self.store.read(deck_id), slide_id)
            current_hash = self.store.content_hash(deck_id, current)
            if current_hash != body.expected_content_hash:
                raise ConflictError(
                    "The chosen slide changed. Review the latest slide before deciding."
                )
            approval = db.execute(
                "SELECT content_hash FROM approvals WHERE deck=? AND slide=?", (deck_id, slide_id)
            ).fetchone()
            approval_before = cast(str, approval[0]) if approval else None
            approval_after = approval_before
            if choice:
                group = db.execute(
                    "SELECT * FROM slide_design_sets WHERE id=? AND deck=? AND slide=?",
                    (choice.set_id, deck_id, slide_id),
                ).fetchone()
                if group is None:
                    raise ValueError("Design set does not exist in this slide.")
                if group["decision_id"] != choice.expected_decision_id:
                    raise ConflictError(
                        "Another reviewer decided on these designs. Refresh before deciding."
                    )
                after = current
                option_id = choice.option_id
                if option_id is not None:
                    option = db.execute(
                        "SELECT * FROM slide_design_options WHERE id=? AND set_id=?",
                        (option_id, choice.set_id),
                    ).fetchone()
                    if option is None:
                        raise ValueError("Design does not belong to this set.")
                    if resource_key(self.store.folder(deck_id)) != group["resources_hash"]:
                        raise ConflictError(
                            "Shared design resources changed. "
                            "Ask the agent to publish refreshed designs."
                        )
                    if group["anchor_hash"] != current_hash and not choice.reviewed_changed_slide:
                        raise ConflictError(
                            "The slide changed since these proposals. "
                            "Compare with the latest slide first."
                        )
                    after = Slide.model_validate_json(cast(str, option["source_json"]))
                    name = cast(str, option["name"])
                else:
                    name = "Current design"
                changing = source_key(after) != source_key(current)
                action = "choose" if changing else "keep"
                if changing:
                    approval_after = None
                pending_after = False
                decision_before = cast(str | None, group["decision_id"])
            else:
                assert undo is not None
                original = db.execute(
                    "SELECT * FROM slide_design_decisions "
                    "WHERE id=? AND deck=? AND slide=? AND completed=1",
                    (undo.decision_id, deck_id, slide_id),
                ).fetchone()
                latest = db.execute(
                    "SELECT id FROM slide_design_decisions WHERE deck=? AND slide=? "
                    "ORDER BY sequence DESC LIMIT 1",
                    (deck_id, slide_id),
                ).fetchone()
                if (
                    original is None
                    or latest is None
                    or latest[0] != original["id"]
                    or original["action"] == "undo"
                ):
                    raise ConflictError("Only the latest design decision can be undone.")
                group = db.execute(
                    "SELECT * FROM slide_design_sets WHERE id=?", (original["set_id"],)
                ).fetchone()
                assert group is not None
                if resource_key(self.store.folder(deck_id)) != original["resources_hash"]:
                    raise ConflictError(
                        "Design resources changed since the decision. Review current content first."
                    )
                after = Slide.model_validate_json(cast(str, original["before_json"]))
                option_id = None
                name = "Previous design"
                action = "undo"
                approval_after = cast(str | None, original["approval_before"])
                pending_after = bool(original["pending_before"])
                decision_before = cast(str | None, original["decision_before"])
            decision_id = uuid.uuid4().hex
            _ = db.execute(
                "INSERT INTO slide_design_decisions "
                "(id,deck,slide,set_id,request_id,payload_hash,action,option_id,"
                "name,created_at,before_json,after_json,approval_before,approval_after,pending_before,"
                "decision_before,pending_after,resources_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    decision_id,
                    deck_id,
                    slide_id,
                    group["id"],
                    body.request_id,
                    payload,
                    action,
                    option_id,
                    name,
                    datetime.now(UTC).isoformat(),
                    current.model_dump_json(),
                    after.model_dump_json(),
                    approval_before,
                    approval_after,
                    group["pending"],
                    decision_before,
                    int(pending_after),
                    resource_key(self.store.folder(deck_id)),
                ),
            )
        with self.store.editing(deck_id) as db:
            row = db.execute(
                "SELECT result_json FROM slide_design_decisions WHERE id=?", (decision_id,)
            ).fetchone()
            assert row is not None
            return DesignDecision.model_validate_json(cast(str, row[0]))
