"""Immutable element proposals and explicit, recoverable fragment decisions."""

import json
import re
import sqlite3
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Literal, cast

from pydantic import Field, field_validator

from .models import AgentContext, Deck, Slide, StrictModel
from .slide_designs import (
    MAX_SAVED_BYTES,
    SOURCE_FIELDS,
    DecideDesign,
    DesignDecision,
    UndoDesign,
    resource_key,
    source_key,
)
from .storage import ConflictError, SlideMarkup, Store, digest
from .version_history import capture_slide_version
from .visuals import CaptureOptions, Snapshot, VisualResult, snapshot

ElementKind = Literal["headline", "text", "graphic"]
VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
TARGET_ID = re.compile(r"^[^\s\"'<>]{1,200}$")


class ElementInput(StrictModel):
    """One static replacement subtree, retaining the target's existing HTML id."""

    name: str = Field(min_length=1, max_length=60)
    rationale: str = Field(default="", max_length=240)
    html: str = Field(min_length=1, max_length=150_000)

    @field_validator("name")
    @classmethod
    def readable_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Give each alternative a name.")
        return value.strip()


class ElementOption(StrictModel):
    """A retained fragment and its exact audience-only preview context."""

    id: str
    set_id: str
    name: str
    rationale: str
    html: str
    text: str
    source_hash: str
    context_hash: str
    preview_url: str
    is_original: bool = False


class ElementSet(StrictModel):
    """A target-scoped publication and its human decision state."""

    id: str
    target_id: str
    kind: ElementKind
    label: str
    created_at: str
    original_context_hash: str
    context_hash: str
    pending: bool
    stale: bool
    stale_reason: str
    current_option_id: str | None
    decision_id: str | None
    options: list[ElementOption]


class ElementPage(StrictModel):
    sets: list[ElementSet]
    decisions: list[DesignDecision]
    content_hash: str
    next_cursor: int | None = None
    next_decision_cursor: int | None = None


class ElementSource(StrictModel):
    """One immutable fragment with an explicit retained full-slide context."""

    option: ElementOption
    target_id: str
    kind: ElementKind
    label: str
    html: str
    original_context_hash: str
    context_hash: str


class ElementCapture(VisualResult):
    element_option_id: str
    set_id: str
    source_hash: str
    context_hash: str


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str | None]
    start: int
    end: int
    parent: int | None
    children: list[int] = field(default_factory=list)


class _Markup(HTMLParser):
    """Find exact source spans without reserializing any surrounding author source."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.html = html
        self.lines = [0]
        self.lines.extend(match.end() for match in re.finditer("\n", html))
        self.nodes: list[_Node] = []
        self.stack: list[int] = []
        self.text: list[str] = []
        self.feed(html)
        self.close()
        if self.stack:
            raise ValueError("Element alternatives require balanced static HTML.")

    def source_offset(self) -> int:
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if len({key for key, _ in attrs}) != len(attrs):
            raise ValueError("Element alternatives require unambiguous HTML attributes.")
        start = self.source_offset()
        opening = self.get_starttag_text()
        assert opening is not None
        end = start + len(opening)
        parent = self.stack[-1] if self.stack else None
        node = _Node(tag, dict(attrs), start, end, parent)
        index = len(self.nodes)
        self.nodes.append(node)
        if parent is not None:
            self.nodes[parent].children.append(index)
        if tag not in VOID:
            self.stack.append(index)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            _ = self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        if not self.stack or self.nodes[self.stack[-1]].tag != tag:
            raise ValueError("Element alternatives require balanced static HTML.")
        node = self.nodes[self.stack.pop()]
        closing = self.html.find(">", self.source_offset())
        if closing < 0:
            raise ValueError("Element alternatives require complete closing tags.")
        node.end = closing + 1

    def handle_data(self, data: str) -> None:
        if not self.stack or self.nodes[self.stack[-1]].tag not in {"style", "script"}:
            self.text.append(data)

    def target(self, target_id: str, *, allow_root: bool = False) -> _Node:
        matches = [node for node in self.nodes if node.attrs.get("id") == target_id]
        if len(matches) != 1:
            raise ValueError("Use an existing, unique HTML id for the element target.")
        node = matches[0]
        if node.parent is None and not allow_root:
            raise ValueError("Use whole-slide alternatives for the slide root.")
        return node


def _fragment(html: str, target_id: str) -> tuple[_Node, str]:
    parsed = _Markup(html)
    node = parsed.target(target_id)
    return node, html[node.start : node.end]


def _replace(html: str, target_id: str, candidate: str) -> str:
    """Validate one bounded subtree; preserve all bytes outside its exact source span."""
    original = _Markup(html)
    target = original.target(target_id)
    replacement = _Markup(candidate)
    roots = [node for node in replacement.nodes if node.parent is None]
    if len(roots) != 1:
        raise ValueError("Provide one replacement element, including its existing HTML id.")
    node = replacement.target(target_id, allow_root=True)
    if node is not roots[0] or candidate[: node.start].strip() or candidate[node.end :].strip():
        raise ValueError("The replacement root must retain the target's HTML id.")
    if any(item.tag == "style" for item in replacement.nodes):
        raise ValueError(
            "Use inline CSS for element alternatives; style blocks need a whole-slide design."
        )
    SlideMarkup().feed(candidate)
    outside_ids = {
        item.attrs["id"]
        for item in original.nodes
        if item.attrs.get("id") and not target.start <= item.start < target.end
    }
    ids = [item.attrs["id"] for item in replacement.nodes if item.attrs.get("id")]
    if len(ids) != len(set(ids)) or outside_ids.intersection(ids):
        raise ValueError("Replacement element ids must be unique within the slide.")
    return html[: target.start] + candidate + html[target.end :]


def _public(slide: Slide) -> Slide:
    return slide.model_copy(update={"notes": "", "notes_revision": 0, "agent": AgentContext()})


def install_element_alternatives(db: sqlite3.Connection) -> None:
    """Store immutable fragments, contextual snapshots and a durable decision journal."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS element_alternative_sets (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
            deck TEXT NOT NULL, slide TEXT NOT NULL, target_id TEXT NOT NULL,
            kind TEXT NOT NULL, label TEXT NOT NULL, created_at TEXT NOT NULL,
            original_context_hash TEXT NOT NULL, anchor_hash TEXT NOT NULL,
            anchor_json TEXT NOT NULL, visual_hash TEXT NOT NULL, resources_hash TEXT NOT NULL,
            parent_marker TEXT NOT NULL, pending INTEGER NOT NULL DEFAULT 1, decision_id TEXT);
        CREATE INDEX IF NOT EXISTS element_alternative_sets_slide
            ON element_alternative_sets(deck,slide,sequence DESC);
        CREATE TABLE IF NOT EXISTS element_alternative_options (
            id TEXT PRIMARY KEY, set_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
            name TEXT NOT NULL, rationale TEXT NOT NULL, html TEXT NOT NULL,
            text TEXT NOT NULL, source_hash TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS element_alternative_options_set
            ON element_alternative_options(set_id,ordinal);
        CREATE TABLE IF NOT EXISTS element_alternative_previews (
            option_id TEXT NOT NULL, context_hash TEXT NOT NULL, snapshot_json TEXT NOT NULL,
            PRIMARY KEY(option_id,context_hash));
        CREATE TABLE IF NOT EXISTS element_alternative_decisions (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
            deck TEXT NOT NULL, slide TEXT NOT NULL, set_id TEXT NOT NULL,
            request_id TEXT NOT NULL, payload_hash TEXT NOT NULL, action TEXT NOT NULL,
            option_id TEXT, name TEXT NOT NULL, created_at TEXT NOT NULL,
            before_json TEXT NOT NULL, after_json TEXT NOT NULL, approval_before TEXT,
            approval_after TEXT, pending_before INTEGER NOT NULL,
            decision_before TEXT, pending_after INTEGER NOT NULL,
            resources_hash TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
            result_json TEXT NOT NULL DEFAULT '', UNIQUE(deck,request_id));
        CREATE INDEX IF NOT EXISTS element_alternative_decisions_slide
            ON element_alternative_decisions(deck,slide,sequence DESC);
    """)


def _parent_marker(db: sqlite3.Connection, deck_id: str, slide_id: str) -> str:
    row = db.execute(
        "SELECT id FROM slide_design_decisions WHERE deck=? AND slide=? "
        "AND completed=1 AND action!='keep' ORDER BY sequence DESC LIMIT 1",
        (deck_id, slide_id),
    ).fetchone()
    return cast(str, row[0]) if row else ""


def _reason(store: Store, db: sqlite3.Connection, group: sqlite3.Row, current: Slide) -> str:
    if current.interactive_html:
        return "This slide now uses an interactive design. Ask for whole-slide alternatives."
    if _parent_marker(db, cast(str, group["deck"]), current.id) != group["parent_marker"]:
        return "These options belong to an earlier slide design. Ask to refresh them."
    if store.content_hash(cast(str, group["deck"]), current) != group["anchor_hash"]:
        return "The slide changed outside these options. Ask to refresh them."
    return ""


def element_badges(store: Store, deck: Deck) -> dict[str, list[dict[str, object]]]:
    """Read compact latest groups without loading their fragment or preview documents."""
    with store.connect() as db:
        rows = db.execute(
            "SELECT s.*,COUNT(o.id) AS count FROM element_alternative_sets s "
            "JOIN element_alternative_options o ON o.set_id=s.id WHERE s.deck=? "
            "AND s.sequence=(SELECT MAX(latest.sequence) FROM element_alternative_sets latest "
            "WHERE latest.deck=s.deck AND latest.slide=s.slide AND latest.target_id=s.target_id) "
            "GROUP BY s.id ORDER BY s.sequence DESC",
            (deck.id,),
        ).fetchall()
        slides = {slide.id: slide for slide in deck.slides}
        result: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            slide = slides.get(cast(str, row["slide"]))
            if slide is None:
                continue
            stale = bool(_reason(store, db, row, slide))
            result.setdefault(slide.id, []).append(
                {
                    "set_id": row["id"],
                    "target_id": row["target_id"],
                    "kind": row["kind"],
                    "label": row["label"],
                    "count": row["count"],
                    "pending": bool(row["pending"]) and not stale,
                    "stale": stale,
                    "decision_id": row["decision_id"],
                }
            )
        return result


def recover_element_decisions(store: Store, db: sqlite3.Connection, deck_id: str) -> None:
    """Complete prepared fragment choices once while preserving newer private state."""
    rows = db.execute(
        "SELECT * FROM element_alternative_decisions WHERE deck=? AND completed=0 ORDER BY "
        "sequence",
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
                raise ConflictError("Element resources changed during a prepared choice.")
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
            raise ConflictError("A pending element choice conflicts with newer slide content.")
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
        # Only a journalled element operation can advance sibling contexts. Arbitrary
        # edits never acquire this authority merely because their target still exists.
        for group in db.execute(
            "SELECT * FROM element_alternative_sets WHERE deck=? AND slide=?", (deck_id, current.id)
        ).fetchall():
            anchor = Slide.model_validate_json(cast(str, group["anchor_json"]))
            if source_key(anchor) != source_key(before):
                continue
            if group["id"] != row["set_id"]:
                try:
                    _, old_fragment = _fragment(before.html, cast(str, group["target_id"]))
                    _, new_fragment = _fragment(after.html, cast(str, group["target_id"]))
                except ValueError:
                    continue
                if old_fragment != new_fragment:
                    continue
            _ = db.execute(
                "UPDATE element_alternative_sets SET anchor_hash=?,anchor_json=? WHERE id=?",
                (result.content_hash, _public(current).model_dump_json(), group["id"]),
            )
        _ = db.execute(
            "UPDATE element_alternative_sets SET pending=?,decision_id=? WHERE id=?",
            (
                row["pending_after"],
                row["decision_before"] if result.action == "undo" else result.id,
                result.set_id,
            ),
        )
        _ = db.execute(
            "UPDATE element_alternative_decisions SET completed=1,result_json=? WHERE id=?",
            (result.model_dump_json(), result.id),
        )
        _ = db.execute(
            "INSERT INTO operations VALUES (?,?,?,?)",
            (deck_id, row["request_id"], row["payload_hash"], result.model_dump_json()),
        )


class ElementAlternatives:
    """Publish scoped proposals; only the human API can choose, keep or undo them."""

    def __init__(self, store: Store) -> None:
        self.store = store

    @staticmethod
    def _option(row: sqlite3.Row, deck_id: str, slide_id: str, context_hash: str) -> ElementOption:
        return ElementOption(
            id=cast(str, row["id"]),
            set_id=cast(str, row["set_id"]),
            name=cast(str, row["name"]),
            rationale=cast(str, row["rationale"]),
            html=cast(str, row["html"]),
            text=cast(str, row["text"]),
            source_hash=cast(str, row["source_hash"]),
            context_hash=context_hash,
            is_original=row["ordinal"] == 0,
            preview_url=f"/api/decks/{deck_id}/slides/{slide_id}/element-alternatives/{row['id']}/preview?context_hash={context_hash}",
        )

    def _snapshots(self, db: sqlite3.Connection, group: sqlite3.Row) -> None:
        """Freeze a new safe composed context without changing retained earlier snapshots."""
        deck_id, slide_id = cast(str, group["deck"]), cast(str, group["slide"])
        rows = db.execute(
            "SELECT * FROM element_alternative_options WHERE set_id=? ORDER BY ordinal",
            (group["id"],),
        ).fetchall()
        missing = [
            row
            for row in rows
            if db.execute(
                "SELECT 1 FROM element_alternative_previews WHERE option_id=? AND context_hash=?",
                (row["id"], group["anchor_hash"]),
            ).fetchone()
            is None
        ]
        if not missing:
            return
        if resource_key(self.store.folder(deck_id)) != group["resources_hash"]:
            raise ConflictError("Shared resources changed. Ask for refreshed element options.")
        deck = self.store.read(deck_id)
        anchor = Slide.model_validate_json(cast(str, group["anchor_json"]))
        total = 0
        for row in missing:
            html = _replace(anchor.html, cast(str, group["target_id"]), cast(str, row["html"]))
            candidate = Slide.model_validate(anchor.model_dump() | {"html": html})
            frozen = snapshot(
                self.store,
                deck_id,
                candidate,
                next(i + 1 for i, slide in enumerate(deck.slides) if slide.id == slide_id),
                CaptureOptions(reduced_motion=True),
            )
            encoded = frozen.model_dump_json()
            total += len(encoded.encode())
            if total > MAX_SAVED_BYTES:
                raise ValueError("Element previews exceed 64 MiB. Reduce bundled assets.")
            _ = db.execute(
                "INSERT INTO element_alternative_previews VALUES (?,?,?)",
                (row["id"], group["anchor_hash"], encoded),
            )
        if resource_key(self.store.folder(deck_id)) != group["resources_hash"]:
            raise ConflictError("Shared resources changed while composing element previews.")

    def _set(self, db: sqlite3.Connection, row: sqlite3.Row, current: Slide) -> ElementSet:
        reason = _reason(self.store, db, row, current)
        if not reason:
            try:
                self._snapshots(db, row)
            except ConflictError:
                reason = "Shared resources changed. Ask to refresh these options."
        options = db.execute(
            "SELECT * FROM element_alternative_options WHERE set_id=? ORDER BY ordinal",
            (row["id"],),
        ).fetchall()
        context_hash = cast(str, row["anchor_hash"])
        if (
            db.execute(
                "SELECT 1 FROM element_alternative_previews WHERE option_id=? AND context_hash=?",
                (options[0]["id"], context_hash),
            ).fetchone()
            is None
        ):
            context_hash = cast(str, row["original_context_hash"])
        try:
            _, current_fragment = _fragment(current.html, cast(str, row["target_id"]))
        except ValueError:
            current_fragment = ""
        return ElementSet(
            id=cast(str, row["id"]),
            target_id=cast(str, row["target_id"]),
            kind=cast(ElementKind, row["kind"]),
            label=cast(str, row["label"]),
            created_at=cast(str, row["created_at"]),
            original_context_hash=cast(str, row["original_context_hash"]),
            context_hash=context_hash,
            pending=bool(row["pending"]) and not reason,
            stale=bool(reason),
            stale_reason=reason,
            current_option_id=next(
                (
                    cast(str, option["id"])
                    for option in options
                    if option["html"] == current_fragment
                ),
                None,
            ),
            decision_id=cast(str | None, row["decision_id"]),
            options=[
                self._option(option, cast(str, row["deck"]), current.id, context_hash)
                for option in options
            ],
        )

    def list(
        self,
        deck_id: str,
        slide_id: str,
        *,
        cursor: int | None = None,
        decision_cursor: int | None = None,
        set_id: str | None = None,
    ) -> ElementPage:
        for value in (cursor, decision_cursor):
            if value is not None and not 0 <= value <= 2**63 - 1:
                raise ValueError("Invalid element history cursor.")
        with self.store.editing(deck_id) as db:
            current = self.store.locate(self.store.read(deck_id), slide_id)
            rows = db.execute(
                "SELECT * FROM element_alternative_sets WHERE deck=? AND slide=? AND (? IS NULL "
                "OR sequence<?) AND (? IS NULL OR id=?) ORDER BY sequence DESC LIMIT 21",
                (deck_id, slide_id, cursor, cursor, set_id, set_id),
            ).fetchall()
            decisions = db.execute(
                "SELECT result_json,sequence FROM element_alternative_decisions WHERE deck=? "
                "AND slide=? AND completed=1 AND (? IS NULL OR sequence<?) ORDER BY sequence "
                "DESC LIMIT 31",
                (deck_id, slide_id, decision_cursor, decision_cursor),
            ).fetchall()
            return ElementPage(
                sets=[self._set(db, row, current) for row in rows[:20]],
                decisions=[
                    DesignDecision.model_validate_json(cast(str, row[0])) for row in decisions[:30]
                ],
                content_hash=self.store.content_hash(deck_id, current),
                next_cursor=cast(int, rows[19]["sequence"]) if len(rows) > 20 else None,
                next_decision_cursor=cast(int, decisions[29]["sequence"])
                if len(decisions) > 30
                else None,
            )

    def _read(
        self, db: sqlite3.Connection, deck_id: str, slide_id: str, option_id: str
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        option = db.execute(
            "SELECT o.* FROM element_alternative_options o JOIN element_alternative_sets s ON "
            "o.set_id=s.id WHERE o.id=? AND s.deck=? AND s.slide=?",
            (option_id, deck_id, slide_id),
        ).fetchone()
        if option is None:
            raise ValueError("Element alternative does not exist in this slide.")
        group = db.execute(
            "SELECT * FROM element_alternative_sets WHERE id=?", (option["set_id"],)
        ).fetchone()
        assert group is not None
        return option, group

    def read(
        self, deck_id: str, slide_id: str, option_id: str, context_hash: str | None = None
    ) -> ElementSource:
        with self.store.editing(deck_id) as db:
            current = self.store.locate(self.store.read(deck_id), slide_id)
            option, group = self._read(db, deck_id, slide_id, option_id)
            if not _reason(self.store, db, group, current):
                with suppress(ConflictError):
                    self._snapshots(db, group)
            selected = context_hash or cast(str, group["anchor_hash"])
            if (
                context_hash is None
                and db.execute(
                    "SELECT 1 FROM element_alternative_previews "
                    "WHERE option_id=? AND context_hash=?",
                    (option_id, selected),
                ).fetchone()
                is None
            ):
                selected = cast(str, group["original_context_hash"])
            if (
                db.execute(
                    "SELECT 1 FROM element_alternative_previews WHERE option_id=? AND "
                    "context_hash=?",
                    (option_id, selected),
                ).fetchone()
                is None
            ):
                raise ValueError(
                    "That element preview context is unavailable. Read the options again."
                )
            return ElementSource(
                option=self._option(option, deck_id, slide_id, selected),
                target_id=cast(str, group["target_id"]),
                kind=cast(ElementKind, group["kind"]),
                label=cast(str, group["label"]),
                html=cast(str, option["html"]),
                original_context_hash=cast(str, group["original_context_hash"]),
                context_hash=selected,
            )

    def preview(
        self, deck_id: str, slide_id: str, option_id: str, context_hash: str | None = None
    ) -> Snapshot:
        source = self.read(deck_id, slide_id, option_id, context_hash)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT snapshot_json FROM element_alternative_previews WHERE option_id=? AND "
                "context_hash=?",
                (option_id, source.context_hash),
            ).fetchone()
        assert row is not None
        return Snapshot.model_validate_json(cast(str, row[0]))

    def publish(
        self,
        deck_id: str,
        slide_id: str,
        target_id: str,
        kind: ElementKind,
        label: str,
        expected_content_hash: str,
        request_id: str,
        options: list[ElementInput],
    ) -> ElementSet:
        if (
            not TARGET_ID.fullmatch(target_id)
            or kind not in {"headline", "text", "graphic"}
            or not 1 <= len(label.strip()) <= 80
        ):
            raise ValueError(
                "Supply a unique existing target_id, headline/text/graphic kind and concise label."
            )
        if not 1 <= len(options) <= 5 or not 8 <= len(request_id) <= 100:
            raise ValueError("Publish 1–5 alternatives with an 8–100 character request_id.")
        names = [item.name.casefold() for item in options]
        if len(names) != len(set(names)) or "original" in names:
            raise ValueError("Use distinct names; Original is retained automatically.")
        payload = digest(
            "publish-elements:"
            + json.dumps(
                [
                    slide_id,
                    target_id,
                    kind,
                    label,
                    expected_content_hash,
                    [item.model_dump() for item in options],
                ]
            )
        )
        with self.store.editing(deck_id) as db:
            prior = db.execute(
                "SELECT payload_hash,result FROM operations WHERE deck=? AND request_id=?",
                (deck_id, request_id),
            ).fetchone()
            if prior:
                if prior[0] != payload:
                    raise ConflictError("Request ID already belongs to another action.")
                return ElementSet.model_validate_json(cast(str, prior[1]))
            current = self.store.locate(self.store.read(deck_id), slide_id)
            if self.store.content_hash(deck_id, current) != expected_content_hash:
                raise ConflictError("Slide changed. Read it again before publishing alternatives.")
            if current.interactive_html:
                raise ValueError(
                    "Element alternatives support static slides. Use whole-slide designs for "
                    "interactive_html."
                )
            target, original = _fragment(current.html, target_id)
            for existing in db.execute(
                "SELECT * FROM element_alternative_sets WHERE deck=? AND slide=? AND pending=1",
                (deck_id, slide_id),
            ).fetchall():
                if existing["target_id"] == target_id or _reason(self.store, db, existing, current):
                    continue
                other, _ = _fragment(current.html, cast(str, existing["target_id"]))
                if max(target.start, other.start) < min(target.end, other.end):
                    raise ValueError(
                        "These targets overlap. Review the existing group first or publish "
                        "whole-slide designs."
                    )
            values = [("Original", "The element currently in the slide.", original)] + [
                (item.name, item.rationale, item.html) for item in options
            ]
            for _, _, candidate in values:
                _ = _replace(current.html, target_id, candidate)
            # A fresh publication supersedes the outstanding invitation for this
            # target. Its immutable candidates stay addressable in saved history.
            _ = db.execute(
                "UPDATE element_alternative_sets SET pending=0 "
                "WHERE deck=? AND slide=? AND target_id=? AND pending=1",
                (deck_id, slide_id, target_id),
            )
            group_id = uuid.uuid4().hex
            _ = db.execute(
                "INSERT INTO element_alternative_sets "
                "(id,deck,slide,target_id,kind,label,created_at,original_context_hash,"
                "anchor_hash,anchor_json,visual_hash,resources_hash,parent_marker) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    group_id,
                    deck_id,
                    slide_id,
                    target_id,
                    kind,
                    label.strip(),
                    datetime.now(UTC).isoformat(),
                    expected_content_hash,
                    expected_content_hash,
                    _public(current).model_dump_json(),
                    self.store.visual_hash(deck_id),
                    resource_key(self.store.folder(deck_id)),
                    _parent_marker(db, deck_id, slide_id),
                ),
            )
            _ = db.executemany(
                "INSERT INTO element_alternative_options VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        uuid.uuid4().hex,
                        group_id,
                        index,
                        name,
                        rationale,
                        html,
                        " ".join(_Markup(html).text).strip(),
                        digest(html),
                    )
                    for index, (name, rationale, html) in enumerate(values)
                ],
            )
            row = db.execute(
                "SELECT * FROM element_alternative_sets WHERE id=?", (group_id,)
            ).fetchone()
            assert row is not None
            self._snapshots(db, row)
            self.store.append_feedback(
                db,
                deck_id,
                slide_id,
                "agent",
                f"Prepared {len(options)} {label.strip().lower()} alternatives.",
                expected_content_hash,
                released=True,
                element_set_id=group_id,
            )
            result = self._set(db, row, current)
            _ = db.execute(
                "INSERT INTO operations VALUES (?,?,?,?)",
                (deck_id, request_id, payload, result.model_dump_json()),
            )
            return result

    def decide(self, deck_id: str, slide_id: str, body: DecideDesign) -> DesignDecision:
        return self._decision(deck_id, slide_id, body, None)

    def undo(self, deck_id: str, slide_id: str, body: UndoDesign) -> DesignDecision:
        return self._decision(deck_id, slide_id, None, body)

    def _decision(
        self, deck_id: str, slide_id: str, choice: DecideDesign | None, undo: UndoDesign | None
    ) -> DesignDecision:
        body = choice or undo
        assert body is not None
        payload = digest("element-decision:" + slide_id + body.model_dump_json())
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
                raise ConflictError("The slide changed. Review its latest content before deciding.")
            approval = db.execute(
                "SELECT content_hash FROM approvals WHERE deck=? AND slide=?", (deck_id, slide_id)
            ).fetchone()
            approval_before = cast(str, approval[0]) if approval else None
            approval_after = approval_before
            if choice:
                group = db.execute(
                    "SELECT * FROM element_alternative_sets WHERE id=? AND deck=? AND slide=?",
                    (choice.set_id, deck_id, slide_id),
                ).fetchone()
                if group is None:
                    raise ValueError("Element alternative set does not exist in this slide.")
                if group["decision_id"] != choice.expected_decision_id:
                    raise ConflictError(
                        "Another reviewer decided on these alternatives. Refresh first."
                    )
                after = current
                option_id = choice.option_id
                name = "Current element"
                if option_id is not None:
                    option = db.execute(
                        "SELECT * FROM element_alternative_options WHERE id=? AND set_id=?",
                        (option_id, choice.set_id),
                    ).fetchone()
                    if option is None:
                        raise ValueError("Element alternative does not belong to this set.")
                    reason = _reason(self.store, db, group, current)
                    if reason:
                        raise ConflictError(reason)
                    if resource_key(self.store.folder(deck_id)) != group["resources_hash"]:
                        raise ConflictError(
                            "Shared resources changed. Ask for refreshed element alternatives."
                        )
                    html = _replace(
                        current.html, cast(str, group["target_id"]), cast(str, option["html"])
                    )
                    after = Slide.model_validate(current.model_dump() | {"html": html})
                    name = cast(str, option["name"])
                changing = source_key(after) != source_key(current)
                action = "choose" if changing else "keep"
                if changing:
                    approval_after = None
                pending_after = False
                decision_before = cast(str | None, group["decision_id"])
            else:
                assert undo is not None
                original = db.execute(
                    "SELECT * FROM element_alternative_decisions WHERE id=? AND deck=? AND "
                    "slide=? AND completed=1",
                    (undo.decision_id, deck_id, slide_id),
                ).fetchone()
                latest = db.execute(
                    "SELECT id FROM element_alternative_decisions WHERE deck=? AND slide=? "
                    "ORDER BY sequence DESC LIMIT 1",
                    (deck_id, slide_id),
                ).fetchone()
                if (
                    original is None
                    or latest is None
                    or latest[0] != original["id"]
                    or original["action"] == "undo"
                ):
                    raise ConflictError("Only the latest element decision can be undone.")
                after = Slide.model_validate_json(cast(str, original["before_json"]))
                expected = Slide.model_validate_json(cast(str, original["after_json"]))
                if (
                    source_key(expected) != source_key(current)
                    or resource_key(self.store.folder(deck_id)) != original["resources_hash"]
                ):
                    raise ConflictError(
                        "Content changed since that decision. Review current content first."
                    )
                group = db.execute(
                    "SELECT * FROM element_alternative_sets WHERE id=?", (original["set_id"],)
                ).fetchone()
                assert group is not None
                option_id, name, action = None, "Previous element", "undo"
                approval_after = cast(str | None, original["approval_before"])
                pending_after = bool(original["pending_before"])
                decision_before = cast(str | None, original["decision_before"])
            decision_id = uuid.uuid4().hex
            _ = db.execute(
                "INSERT INTO element_alternative_decisions "
                "(id,deck,slide,set_id,request_id,payload_hash,action,option_id,name,created_at,"
                "before_json,after_json,approval_before,approval_after,pending_before,"
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
                "SELECT result_json FROM element_alternative_decisions WHERE id=?", (decision_id,)
            ).fetchone()
            assert row is not None
            return DesignDecision.model_validate_json(cast(str, row[0]))
