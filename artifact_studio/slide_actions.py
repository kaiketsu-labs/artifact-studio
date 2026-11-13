"""Durable, conflict-aware structural operations on presentation slides."""

import json
import sqlite3
import uuid
from typing import Literal, cast

from pydantic import BaseModel, Field

from .models import Deck, Slide
from .storage import ConflictError, Store, digest


class SlideActionResult(BaseModel):
    """Describe a completed structural change using stable slide IDs."""

    operation_id: str
    action: Literal["add", "duplicate", "delete", "restore"]
    selected_ids: list[str] = Field(default_factory=list)
    added_ids: list[str] = Field(default_factory=list)
    deleted_ids: list[str] = Field(default_factory=list)
    insertion_anchor: str | None = None
    deletion_id: str | None = None


class SlideActionRecord(BaseModel):
    """Serializable journal row, excluding the containing presentation ID."""

    request_id: str
    payload_hash: str
    before_json: str
    after_json: str
    result_json: str
    completed: int


def recover_pending(store: Store, db: sqlite3.Connection, deck_id: str) -> None:
    """Finish prepared writes only if the canonical document has not diverged."""
    rows = db.execute(
        "SELECT * FROM slide_actions WHERE deck=? AND completed=0 ORDER BY rowid", (deck_id,)
    ).fetchall()
    for row in rows:
        before = Deck.model_validate_json(cast(str, row["before_json"]))
        after = Deck.model_validate_json(cast(str, row["after_json"]))
        current = store.read(deck_id)
        if current == before:
            store.write(deck_id, after)
        elif current != after:
            raise ConflictError(
                "A pending slide operation conflicts with newer presentation changes."
            )
        result = SlideActionResult.model_validate_json(cast(str, row["result_json"]))
        if result.action == "delete":
            _ = db.executemany(
                "INSERT OR IGNORE INTO revisions VALUES (?,?,?,?)",
                [
                    (deck_id, slide.id, digest(slide.model_dump_json()), slide.model_dump_json())
                    for slide in before.slides
                    if slide.id in result.deleted_ids
                ],
            )
        _ = db.execute(
            "UPDATE feedback SET context_slide_id=? WHERE deck=? "
            "AND COALESCE(slide,'')='' AND context_slide_id IS NULL AND reply_to IS NULL",
            (before.slides[0].id, deck_id),
        )
        store.seed_slide_views(db, after)
        _ = db.execute(
            "UPDATE slide_actions SET completed=1 WHERE deck=? AND request_id=?",
            (deck_id, row["request_id"]),
        )


def pending_deletions(db: sqlite3.Connection, deck_id: str) -> list[SlideActionResult]:
    """Read completed deletions still eligible for explicit recovery."""
    rows = db.execute(
        "SELECT result_json FROM slide_actions WHERE deck=? AND completed=1 ORDER BY rowid DESC",
        (deck_id,),
    ).fetchall()
    results = [SlideActionResult.model_validate_json(cast(str, row[0])) for row in rows]
    restored = {result.deletion_id for result in results if result.action == "restore"}
    return [
        result
        for result in results
        if result.action == "delete" and result.operation_id not in restored
    ]


class SlideActions:
    """Apply slide actions using a durable journal around the canonical file write."""

    def __init__(self, store: Store) -> None:
        """Attach the actions to an existing store."""
        self.store = store

    def recover(self, deck_id: str) -> None:
        """Complete any interrupted operation, or reject a divergent canonical document."""
        with self.store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            recover_pending(self.store, db, deck_id)

    def deleted(self, deck_id: str) -> list[SlideActionResult]:
        """List deletion operations that have not already been restored."""
        self.recover(deck_id)
        with self.store.connect() as db:
            return pending_deletions(db, deck_id)

    def deletion_summaries(self, deck_id: str) -> list[dict[str, object]]:
        """List recoverable deletions with their original slide IDs and titles."""
        deleted = {item.operation_id: item for item in self.deleted(deck_id)}
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT request_id,before_json FROM slide_actions WHERE deck=? ORDER BY rowid DESC",
                (deck_id,),
            ).fetchall()
        result: list[dict[str, object]] = []
        for row in rows:
            item = deleted.get(str(row["request_id"]))
            if item is not None:
                before = Deck.model_validate_json(str(row["before_json"]))
                result.append(
                    {
                        "operation_id": item.operation_id,
                        "slides": [
                            {"id": slide.id, "title": slide.title}
                            for slide in before.slides
                            if slide.id in item.deleted_ids
                        ],
                    }
                )
        return result

    def _run(self, deck_id: str, request_id: str, payload: dict[str, object]) -> SlideActionResult:
        """Prepare an idempotent change and finalize it after its file write."""
        if not 8 <= len(request_id) <= 100:
            raise ValueError("Use a unique request_id of 8–100 characters.")
        payload_hash = digest(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        with self.store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            recover_pending(self.store, db, deck_id)
            prior = db.execute(
                "SELECT payload_hash,result_json FROM slide_actions WHERE deck=? AND request_id=?",
                (deck_id, request_id),
            ).fetchone()
            if prior:
                if prior["payload_hash"] != payload_hash:
                    raise ConflictError("This request_id was already used with another payload.")
                return SlideActionResult.model_validate_json(cast(str, prior["result_json"]))
            before = self.store.read(deck_id)
            after, result = self._prepare(db, before, request_id, payload)
            from .sections import reconcile

            after.sections = reconcile(after.sections, [slide.id for slide in after.slides])
            _ = db.execute(
                "INSERT INTO slide_actions VALUES (?,?,?,?,?,?,0)",
                (
                    deck_id,
                    request_id,
                    payload_hash,
                    before.model_dump_json(),
                    after.model_dump_json(),
                    result.model_dump_json(),
                ),
            )
        with self.store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            recover_pending(self.store, db, deck_id)
        return result

    def _prepare(
        self, db: sqlite3.Connection, before: Deck, request_id: str, payload: dict[str, object]
    ) -> tuple[Deck, SlideActionResult]:
        """Validate an action and construct the intended document without mutating state."""
        action = cast(Literal["add", "duplicate", "delete", "restore"], payload["action"])
        after = before.model_copy(deep=True)
        ids = [slide.id for slide in before.slides]
        result = SlideActionResult(operation_id=request_id, action=action)
        if action == "restore":
            deletion = db.execute(
                "SELECT before_json,result_json FROM slide_actions "
                "WHERE deck=? AND request_id=? AND completed=1",
                (before.id, payload["deletion_id"]),
            ).fetchone()
            if not deletion:
                raise ConflictError("Deletion was not found or has not completed.")
            deleted = SlideActionResult.model_validate_json(cast(str, deletion["result_json"]))
            if deleted.action != "delete":
                raise ValueError("The referenced operation is not a deletion.")
            restored_rows = db.execute(
                "SELECT result_json FROM slide_actions WHERE deck=? AND completed=1",
                (before.id,),
            ).fetchall()
            if any(
                SlideActionResult.model_validate_json(cast(str, row["result_json"])).deletion_id
                == payload["deletion_id"]
                for row in restored_rows
            ):
                raise ConflictError("This deletion has already been restored.")
            result.deletion_id = cast(str, payload["deletion_id"])
            original = Deck.model_validate_json(cast(str, deletion["before_json"]))
            original_ids = [slide.id for slide in original.slides]
            if set(deleted.deleted_ids) & set(ids):
                raise ConflictError("A deleted slide ID is already in use.")
            if len(ids) + len(deleted.deleted_ids) > 100:
                raise ValueError("A presentation supports at most 100 slides.")
            for slide in original.slides:
                if slide.id not in deleted.deleted_ids:
                    continue
                index = original_ids.index(slide.id)
                left = next(
                    (
                        item
                        for item in reversed(original_ids[:index])
                        if item in [s.id for s in after.slides]
                    ),
                    None,
                )
                right = next(
                    (
                        item
                        for item in original_ids[index + 1 :]
                        if item in [s.id for s in after.slides]
                    ),
                    None,
                )
                current_ids = [s.id for s in after.slides]
                if left and right and current_ids.index(left) >= current_ids.index(right):
                    raise ConflictError(
                        "Original slide anchors changed order; "
                        "restore cannot choose a position safely."
                    )
                position = (
                    current_ids.index(left) + 1
                    if left
                    else current_ids.index(right)
                    if right
                    else len(current_ids)
                )
                restored = slide.model_copy(deep=True)
                restored.notes_revision += 1
                after.slides.insert(position, restored)
            result.added_ids = deleted.deleted_ids
            result.insertion_anchor = deleted.insertion_anchor
            for section in after.sections:
                section.slide_ids.extend(
                    slide_id
                    for group in original.sections
                    if group.id == section.id
                    for slide_id in group.slide_ids
                    if slide_id in deleted.deleted_ids
                )
            return after, result
        expected = payload.get("expected_slide_ids")
        if expected is not None and ids != expected:
            raise ConflictError("Slide order changed. Try again.")
        selected = cast(list[str], payload["selected_ids"])
        if not selected or len(set(selected)) != len(selected) or not set(selected) <= set(ids):
            raise ValueError("Select existing slides exactly once.")
        selected = [item for item in ids if item in selected]
        result.selected_ids = selected
        if action in {"delete", "duplicate"}:
            hashes = cast(dict[str, str], payload["expected_hashes"])
            if set(hashes) != set(selected) or any(
                digest(slide.model_dump_json()) != hashes[slide.id]
                for slide in before.slides
                if slide.id in selected
            ):
                raise ConflictError("A selected slide changed. Read it again.")
        if action == "delete":
            if len(selected) == len(ids):
                raise ValueError("Keep at least one slide in the presentation.")
            result.deleted_ids = selected
            first = ids.index(selected[0])
            result.insertion_anchor = next(
                (item for item in reversed(ids[:first]) if item not in selected), None
            )
            after.slides = [slide for slide in after.slides if slide.id not in selected]
        else:
            if action == "add" and len(selected) != 1:
                raise ValueError("Choose one insertion anchor.")
            copies = (
                [
                    Slide(
                        id="slide-" + uuid.uuid4().hex,
                        title="Untitled slide",
                        html='<article class="slide"></article>',
                    )
                ]
                if action == "add"
                else [
                    slide.model_copy(
                        deep=True, update={"id": "slide-" + uuid.uuid4().hex, "revision": 1}
                    )
                    for slide in before.slides
                    if slide.id in selected
                ]
            )
            if len(ids) + len(copies) > 100:
                raise ValueError("A presentation supports at most 100 slides.")
            position = ids.index(selected[-1]) + 1
            after.slides[position:position] = copies
            result.added_ids = [slide.id for slide in copies]
            result.insertion_anchor = selected[-1]
        return after, result

    def add(
        self, deck_id: str, anchor_id: str, expected_slide_ids: list[str], request_id: str
    ) -> SlideActionResult:
        """Insert a blank slide immediately after the selected slide."""
        return self._run(
            deck_id,
            request_id,
            {
                "action": "add",
                "selected_ids": [anchor_id],
                "expected_slide_ids": expected_slide_ids,
            },
        )

    def duplicate(
        self,
        deck_id: str,
        slide_ids: list[str],
        expected_slide_ids: list[str],
        expected_hashes: dict[str, str],
        request_id: str,
    ) -> SlideActionResult:
        """Copy selected slides after the last selected slide in original order."""
        return self._run(
            deck_id,
            request_id,
            {
                "action": "duplicate",
                "expected_hashes": expected_hashes,
                "selected_ids": slide_ids,
                "expected_slide_ids": expected_slide_ids,
            },
        )

    def delete(
        self,
        deck_id: str,
        slide_ids: list[str],
        expected_slide_ids: list[str],
        expected_hashes: dict[str, str],
        request_id: str,
    ) -> SlideActionResult:
        """Delete selected slides while retaining their identities and review records."""
        return self._run(
            deck_id,
            request_id,
            {
                "action": "delete",
                "selected_ids": slide_ids,
                "expected_slide_ids": expected_slide_ids,
                "expected_hashes": expected_hashes,
            },
        )

    def remove(
        self, deck_id: str, slide_id: str, expected_hash: str, request_id: str
    ) -> SlideActionResult:
        """Remove one exact slide without imposing an unrelated slide-order precondition."""
        return self._run(
            deck_id,
            request_id,
            {
                "action": "delete",
                "selected_ids": [slide_id],
                "expected_hashes": {slide_id: expected_hash},
            },
        )

    def restore(self, deck_id: str, deletion_id: str, request_id: str) -> SlideActionResult:
        """Restore a deletion using surviving original neighbor anchors."""
        return self._run(deck_id, request_id, {"action": "restore", "deletion_id": deletion_id})
