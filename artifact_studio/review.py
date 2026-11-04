"""Harness-independent review leases and stop-hook continuation over a durable queue."""

import asyncio
import time
from typing import Literal, cast

from pydantic import Field

from .models import StrictModel
from .storage import Store


class ReviewHookInput(StrictModel):
    """Normalized lifecycle data shared by Codex and Claude Code adapters."""

    harness: Literal["codex", "claude-code"]
    session_id: str = Field(pattern=r"^[A-Za-z0-9:_-]{1,180}$")
    event: Literal["PostToolUse", "Stop", "SessionEnd", "Interrupt", "StopFailure"]
    deck_id: str | None = None
    seen_cursor: int | None = Field(default=None, ge=0)
    seen_design_cursor: int | None = Field(default=None, ge=0)
    start_review: bool = False
    auto_start: bool = False


class ReviewHookReply(StrictModel):
    """A host-neutral decision that a lifecycle adapter maps to hook output."""

    listening: bool = False
    feedback_available: bool = False
    reason: str = ""


class Reviews:
    """Bind a review chat to a deck without relying on an MCP transport session."""

    def __init__(self, store: Store) -> None:
        """Use the workspace's transactional state and canonical source files."""
        self.store = store

    def connection(self, deck_id: str) -> dict[str, str | bool | None]:
        """Report only a live hook lease for this exact target, excluding ordinary MCP waits."""
        with self.store.connect() as db:
            row = db.execute(
                "SELECT harness,session_id FROM review_sessions "
                "WHERE deck=? AND enabled=1 AND expires_at>? ORDER BY expires_at DESC LIMIT 1",
                (deck_id, time.time()),
            ).fetchone()
        return {
            "review_connected": bool(row),
            "review_harness": cast(str, row["harness"]) if row else None,
            "review_session": cast(str, row["session_id"]) if row else None,
        }

    def disconnect(self, deck_id: str) -> None:
        """Release review waits and prevent automatic rebinding until explicitly resumed."""
        with self.store.connect() as db:
            _ = db.execute(
                "UPDATE review_sessions SET enabled=0,expires_at=0 WHERE deck=?", (deck_id,)
            )

    def bind(self, body: ReviewHookInput) -> ReviewHookReply:
        """Associate a successful workspace tool call with the host's review chat."""
        deck_id = body.deck_id or ""
        if deck_id:
            _ = self.store.read(deck_id)
        with self.store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            own = db.execute(
                "SELECT * FROM review_sessions WHERE harness=? AND session_id=?",
                (body.harness, body.session_id),
            ).fetchone()
            automatic = (
                body.auto_start
                and bool(deck_id)
                and (not own or own["enabled"] == 1 or own["deck"] != deck_id)
            )
            starting = body.start_review or automatic
            if not starting and (not own or not own["enabled"]):
                return ReviewHookReply()
            if body.deck_id is None and own and not starting:
                deck_id = cast(str, own["deck"])
            if own and own["deck"] != deck_id and not starting:
                return ReviewHookReply()
            other = db.execute(
                "SELECT 1 FROM review_sessions WHERE deck=? AND enabled=1 AND expires_at>? "
                "AND NOT(harness=? AND session_id=?)",
                (deck_id, time.time(), body.harness, body.session_id),
            ).fetchone()
            if other:
                return ReviewHookReply(
                    reason="This presentation already has a live reviewer."
                    if body.start_review
                    else ""
                )
            same_deck = bool(own and own["deck"] == deck_id)
            feedback_cursor = max(
                cast(int, own["feedback_cursor"]) if same_deck else 0, body.seen_cursor or 0
            )
            design_cursor = max(
                cast(int, own["design_cursor"]) if own else 0, body.seen_design_cursor or 0
            )
            _ = db.execute(
                "INSERT INTO review_sessions VALUES (?,?,?,?,?,?,1) "
                "ON CONFLICT(harness,session_id) DO UPDATE SET deck=excluded.deck,"
                "feedback_cursor=excluded.feedback_cursor,design_cursor=excluded.design_cursor,"
                "expires_at=excluded.expires_at,enabled=1",
                (
                    body.harness,
                    body.session_id,
                    deck_id,
                    feedback_cursor,
                    design_cursor,
                    time.time() + 120,
                ),
            )
        return ReviewHookReply(listening=True)

    def release(self, body: ReviewHookInput) -> ReviewHookReply:
        """End listening immediately after a host interruption or session failure."""
        with self.store.connect() as db:
            _ = db.execute(
                "UPDATE review_sessions SET expires_at=0,enabled=0 "
                "WHERE harness=? AND session_id=?",
                (body.harness, body.session_id),
            )
        return ReviewHookReply()

    def pending(self, body: ReviewHookInput) -> ReviewHookReply:
        """Renew a real hook wait and report only feedback the agent has not received."""
        with self.store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM review_sessions WHERE harness=? AND session_id=? AND enabled=1",
                (body.harness, body.session_id),
            ).fetchone()
            if not row:
                return ReviewHookReply()
            deck_id = cast(str, row["deck"])
            other = db.execute(
                "SELECT 1 FROM review_sessions WHERE deck=? AND enabled=1 AND expires_at>? "
                "AND NOT(harness=? AND session_id=?)",
                (deck_id, time.time(), body.harness, body.session_id),
            ).fetchone()
            if other:
                return ReviewHookReply()
            now = time.time()
            expiry = cast(float, row["expires_at"])
            if expiry < now + 20 or expiry > now + 40:
                _ = db.execute(
                    "UPDATE review_sessions SET expires_at=? WHERE harness=? AND session_id=?",
                    (now + 40, body.harness, body.session_id),
                )
            cursor = cast(int, row["feedback_cursor"])
            design_cursor = cast(int, row["design_cursor"])
            designs = bool(
                db.execute(
                    "SELECT 1 FROM design_chat WHERE id>? "
                    "AND json_extract(document,'$.author')='user' "
                    "AND json_extract(document,'$.status')='queued' LIMIT 1",
                    (design_cursor,),
                ).fetchone()
            )
            events = (
                bool(
                    db.execute(
                        "SELECT 1 FROM delivery d JOIN feedback f ON f.id=d.event_id "
                        "WHERE d.id>? AND f.deck=? LIMIT 1",
                        (cursor, deck_id),
                    ).fetchone()
                )
                if deck_id
                else False
            )
        reads: list[str] = []
        if deck_id and (events or not designs):
            reads.append(f"read_feedback(deck_id={deck_id!r}, after_delivery_id={cursor})")
        if designs or not events:
            reads.append(f"read_design_requests(after_event_id={design_cursor})")
        status = "feedback" if events or designs else "idle renewal"
        reason = f"Artifact Studio {status}: call {'; '.join(reads)}."
        return ReviewHookReply(listening=True, feedback_available=events or designs, reason=reason)

    async def handle(self, body: ReviewHookInput, timeout: float = 25) -> ReviewHookReply:
        """Long-poll a stop hook without spending model requests on each polling interval."""
        if body.event == "PostToolUse":
            return await asyncio.to_thread(self.bind, body)
        if body.event != "Stop":
            return await asyncio.to_thread(self.release, body)
        deadline = time.monotonic() + min(25, max(0, timeout))
        while True:
            result = await asyncio.to_thread(self.pending, body)
            if not result.listening or result.feedback_available or time.monotonic() >= deadline:
                return result
            await asyncio.sleep(0.2)
