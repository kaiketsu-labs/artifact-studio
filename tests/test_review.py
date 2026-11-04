"""Behavioral checks for lifecycle review across agent harnesses."""

import asyncio
import time
from pathlib import Path
from typing import Literal

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError

from artifact_studio import app as service
from artifact_studio.models import DesignRequestInput
from artifact_studio.review import ReviewHookInput, Reviews
from artifact_studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design

DECK = "2026-10-28-review-check"


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """Create an independent presentation and review database."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "review.sqlite3")
    _ = store.create(DECK, "Hook review")
    return store


def comment(store: Store, message: str, released: bool = True) -> None:
    """Save a human message through the existing transactional delivery writer."""
    with store.connect() as db:
        _ = store.append_feedback(db, DECK, "", "user", message, "", released=released)


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_automatic_attachment_respects_ownership_end_review_and_batch(
    store: Store, harness: Literal["codex", "claude-code"]
) -> None:
    """Join fresh authoring sessions without taking another reviewer or reopening ended work."""
    reviews = Reviews(store)
    body = ReviewHookInput(
        harness=harness,
        session_id="author",
        event="PostToolUse",
        deck_id=DECK,
        auto_start=True,
    )
    assert reviews.bind(body).listening
    assert store.workflow(DECK).review_session == "author"
    assert not reviews.bind(body.model_copy(update={"session_id": "other"})).listening
    comment(store, "Private batch request", released=False)
    assert not reviews.pending(body).feedback_available
    reviews.disconnect(DECK)
    assert not reviews.bind(body).listening
    assert reviews.bind(body.model_copy(update={"start_review": True})).listening
    second = "2026-11-04-second-deck"
    _ = store.create(second, "Second presentation")
    assert reviews.bind(body.model_copy(update={"deck_id": second})).listening
    assert store.workflow(second).review_session == "author"
    assert not store.workflow(DECK).review_connected
    assert not store.feedback_feed(DECK).events


@pytest.mark.parametrize("target", [DECK, ""])
def test_review_request_is_not_a_hook_connection(
    store: Store, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """A status read sees only this target's current hook lease and never renews it."""
    monkeypatch.setattr(service, "store", store)
    store.mark_agent_check(DECK, 30)
    assert store.workflow(DECK).agent_waiting
    assert service.get_live_review_status(target)["status"] == "unavailable"
    requested = service.start_live_review(target)
    assert requested["status"] == "awaiting_host_hook" and requested["review_connected"] is False
    assert requested["review_harness"] is None and requested["review_session"] is None
    assert "guide" not in requested and requested["guide_topic"] == "live_review"
    assert requested["guide_included"] is False and "PostToolUse" in str(requested["next_step"])
    expanded = service.start_live_review(target, include_guide=True)
    assert expanded["guide_included"] is True and "One-time setup" in str(expanded["guide"])
    assert service.get_live_review_status(target)["status"] == "unavailable"
    hooks = Reviews(store)
    other_target = "" if target else DECK
    body = ReviewHookInput(
        harness="codex",
        session_id="status-session",
        event="PostToolUse",
        start_review=True,
        deck_id=other_target,
    )
    assert hooks.bind(body).listening
    assert service.get_live_review_status(target)["review_connected"] is False
    assert hooks.bind(body.model_copy(update={"deck_id": target})).listening
    with store.connect() as db:
        expiry = db.execute("SELECT expires_at FROM review_sessions").fetchone()[0]
    connected = service.get_live_review_status(target)
    assert connected["status"] == "connected" and connected["review_connected"] is True
    assert connected["review_harness"] == "codex"
    assert connected["review_session"] == "status-session"
    assert service.start_live_review(target)["status"] == "awaiting_host_hook"
    with store.connect() as db:
        assert db.execute("SELECT expires_at FROM review_sessions").fetchone()[0] == expiry
        _ = db.execute("UPDATE review_sessions SET expires_at=?", (time.time() - 1,))
    assert service.get_live_review_status(target)["status"] == "unavailable"
    assert hooks.bind(body.model_copy(update={"deck_id": target})).listening
    hooks.disconnect(target)
    assert service.get_live_review_status(target)["status"] == "unavailable"
    with pytest.raises(ToolError):
        _ = service.start_live_review("unknown-deck")
    with pytest.raises(ToolError):
        _ = service.get_live_review_status("unknown-deck")


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_repeated_feedback_after_agent_response(
    store: Store, harness: Literal["codex", "claude-code"]
) -> None:
    """Wake the same host on later feedback without acknowledging it prematurely."""
    reviews = Reviews(store)
    post = ReviewHookInput(
        harness=harness, session_id="s", event="PostToolUse", deck_id=DECK, start_review=True
    )
    stop = post.model_copy(update={"event": "Stop", "start_review": False})
    assert reviews.bind(post).listening
    assert store.workflow(DECK).review_harness == harness
    assert store.workflow(DECK).review_session == "s"

    async def wait_and_deliver() -> None:
        waiting = asyncio.create_task(reviews.handle(stop, timeout=2))
        await asyncio.sleep(0.1)
        comment(store, "Make the title clearer.")
        result = await waiting
        assert result.feedback_available
        assert "read_feedback" in result.reason

    asyncio.run(wait_and_deliver())
    assert reviews.pending(stop).feedback_available
    cursor = store.feedback_feed(DECK).next_cursor
    _ = reviews.bind(post.model_copy(update={"start_review": False, "seen_cursor": cursor}))
    assert not reviews.pending(stop).feedback_available
    comment(store, "Use a diagram.")
    pending = reviews.pending(stop)
    assert pending.feedback_available
    assert pending.reason == (
        f"Artifact Studio feedback: call read_feedback(deck_id={DECK!r}, "
        f"after_delivery_id={cursor})."
    )
    assert store.workflow(DECK).review_connected


@pytest.mark.parametrize(
    ("deck_id", "feedback", "design", "expected_reason"),
    [
        (
            DECK,
            False,
            False,
            f"Artifact Studio idle renewal: call read_feedback(deck_id={DECK!r}, "
            "after_delivery_id=0); read_design_requests(after_event_id=0).",
        ),
        (
            "",
            False,
            False,
            "Artifact Studio idle renewal: call read_design_requests(after_event_id=0).",
        ),
        (
            DECK,
            True,
            False,
            f"Artifact Studio feedback: call read_feedback(deck_id={DECK!r}, after_delivery_id=0).",
        ),
        (
            DECK,
            False,
            True,
            "Artifact Studio feedback: call read_design_requests(after_event_id=0).",
        ),
        (
            DECK,
            True,
            True,
            f"Artifact Studio feedback: call read_feedback(deck_id={DECK!r}, "
            "after_delivery_id=0); read_design_requests(after_event_id=0).",
        ),
        ("", False, True, "Artifact Studio feedback: call read_design_requests(after_event_id=0)."),
    ],
)
def test_compact_continuation_reads(
    store: Store, deck_id: str, feedback: bool, design: bool, expected_reason: str
) -> None:
    """Wake only for pending queues and retain both cursor checks on idle renewal."""
    reviews = Reviews(store)
    body = ReviewHookInput(
        harness="codex", session_id="s", event="PostToolUse", deck_id=deck_id, start_review=True
    )
    _ = reviews.bind(body)
    comment(store, "Withheld private feedback", released=False)
    if feedback:
        comment(store, "Released feedback")
    if design:
        _ = store.post_design_request(
            DesignRequestInput(message="New design", request_id="design-request")
        )
    pending = reviews.pending(body)
    assert pending.listening and pending.feedback_available == (feedback or design)
    assert pending.reason == expected_reason
    assert reviews.pending(body) == pending


def test_idle_poll_renews_lease_only_near_expiry(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the listener live without rewriting its lease on every idle poll."""
    now = 1_000.0
    monkeypatch.setattr("artifact_studio.review.time.time", lambda: now)
    reviews = Reviews(store)
    body = ReviewHookInput(harness="codex", session_id="s", event="PostToolUse", start_review=True)
    assert reviews.bind(body).listening

    def expiry() -> float:
        """Read the persisted lease expiry."""
        with store.connect() as db:
            return float(db.execute("SELECT expires_at FROM review_sessions").fetchone()[0])

    assert expiry() == 1_120.0
    assert reviews.pending(body).listening
    assert expiry() == 1_040.0
    assert reviews.pending(body).listening
    assert expiry() == 1_040.0
    now = 1_021.0
    assert reviews.pending(body).listening
    assert expiry() == 1_061.0
    assert reviews.pending(body).listening
    assert expiry() == 1_061.0


def test_pending_checks_queue_presence_without_loading_content(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check delivery and current request status without constructing complete feeds."""
    other = "2026-10-28-other-review"
    _ = store.create(other, "Other review")
    reviews = Reviews(store)
    body = ReviewHookInput(
        harness="codex", session_id="s", event="PostToolUse", deck_id=DECK, start_review=True
    )
    assert reviews.bind(body).listening
    with store.connect() as db:
        _ = store.append_feedback(db, other, "", "user", "Other deck", "", released=True)
    comment(store, "Withheld", released=False)
    request = store.post_design_request(
        DesignRequestInput(message="Design", request_id="design-pending-check")
    )

    def forbidden(*_args: object, **_kwargs: object) -> None:
        """Reject full feed or guidance reads during the presence check."""
        raise AssertionError("pending read full queue content or workspace guidance")

    for name in (
        "feedback_feed",
        "comments",
        "design_inbox",
        "authoring_instructions",
        "learned_preferences",
    ):
        monkeypatch.setattr(store, name, forbidden)

    assert reviews.pending(body).feedback_available
    _ = store.reply_design(request.event_id, "Working", "reply-working", "working")
    assert not reviews.pending(body).feedback_available
    request2 = store.post_design_request(
        DesignRequestInput(message="Another design", request_id="design-pending-check-2")
    )
    assert reviews.pending(body).feedback_available
    _ = reviews.bind(
        body.model_copy(update={"start_review": False, "seen_design_cursor": request2.event_id})
    )
    assert not reviews.pending(body).feedback_available
    comment(store, "Released")
    assert reviews.pending(body).feedback_available
    with store.connect() as db:
        cursor = int(db.execute("SELECT MAX(id) FROM delivery").fetchone()[0])
    _ = reviews.bind(body.model_copy(update={"start_review": False, "seen_cursor": cursor}))
    assert not reviews.pending(body).feedback_available
    _ = store.release_batch(DECK)
    assert reviews.pending(body).feedback_available


def test_batch_release_and_design_inbox(store: Store) -> None:
    """Keep withheld feedback private while delivering a later batch and design request."""
    reviews = Reviews(store)
    body = ReviewHookInput(
        harness="claude-code",
        session_id="c",
        event="PostToolUse",
        deck_id=DECK,
        start_review=True,
    )
    _ = reviews.bind(body)
    _ = store.set_mode(DECK, "batch")
    comment(store, "Older batch", released=False)
    _ = store.set_mode(DECK, "live")
    comment(store, "New live")
    cursor = store.feedback_feed(DECK).next_cursor
    _ = reviews.bind(body.model_copy(update={"seen_cursor": cursor, "start_review": False}))
    assert not reviews.pending(body).feedback_available
    _ = store.release_batch(DECK)
    assert reviews.pending(body).feedback_available
    assert [item.message for item in store.feedback_feed(DECK, cursor).events] == ["Older batch"]
    _ = reviews.bind(body.model_copy(update={"deck_id": None}))
    assert store.workflow(DECK).review_connected is False
    assert store.design_inbox().agent_waiting
    request = store.post_design_request(
        DesignRequestInput(message="Add a design", request_id="design-request")
    )
    assert reviews.pending(body).feedback_available
    _ = reviews.bind(
        body.model_copy(update={"deck_id": None, "seen_design_cursor": request.event_id})
    )
    assert not reviews.pending(body).feedback_available


def test_explicit_start_end_and_transfer(store: Store) -> None:
    """Do not trap ordinary chats, rearm ended sessions or steal another active review."""
    reviews = Reviews(store)
    body = ReviewHookInput(harness="codex", session_id="s", event="PostToolUse", deck_id=DECK)
    assert not reviews.bind(body).listening
    assert not asyncio.run(reviews.handle(body.model_copy(update={"event": "Stop"}))).listening
    _ = reviews.bind(body.model_copy(update={"start_review": True}))
    other = body.model_copy(update={"session_id": "other", "start_review": True})
    assert not reviews.bind(other).listening
    reviews.disconnect(DECK)
    assert not reviews.pending(body).listening
    assert not reviews.bind(body).listening
    assert not reviews.bind(other.model_copy(update={"start_review": False})).listening
    assert reviews.bind(other).listening
    assert not reviews.pending(body).listening
    with store.connect() as db:
        _ = db.execute("UPDATE review_sessions SET expires_at=?", (time.time() - 1,))
    assert not store.workflow(DECK).review_connected
    assert reviews.bind(body.model_copy(update={"start_review": True})).listening
    assert not reviews.pending(other).listening
    for event in ("SessionEnd", "Interrupt", "StopFailure"):
        _ = reviews.bind(body.model_copy(update={"start_review": True}))
        ended = body.model_copy(update={"event": event})
        assert not asyncio.run(reviews.handle(ended)).listening
        assert not store.workflow(DECK).review_connected


def test_reference_reads_do_not_change_review_target(store: Store) -> None:
    """Keep a deck reviewer bound when it inspects a reference deck or workspace context."""
    reviews = Reviews(store)
    body = ReviewHookInput(
        harness="codex", session_id="s", event="PostToolUse", deck_id=DECK, start_review=True
    )
    _ = reviews.bind(body)
    _ = reviews.bind(body.model_copy(update={"deck_id": TEMPLATE_ID, "start_review": False}))
    _ = reviews.bind(body.model_copy(update={"deck_id": None, "start_review": False}))
    assert store.workflow(DECK).review_connected
    assert not store.workflow(TEMPLATE_ID).review_connected


def test_http_contract_and_disconnect(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate events and release the listener from the UI while preserving feedback."""
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    body = {
        "harness": "codex",
        "session_id": "s",
        "event": "PostToolUse",
        "deck_id": DECK,
        "start_review": True,
    }
    assert client.post("/api/review/hooks", json=body).json()["listening"]
    comment(store, "Keep this comment")
    assert client.post(f"/api/decks/{DECK}/review/disconnect", json={}).status_code == 200
    assert not client.post("/api/review/hooks", json={**body, "event": "Stop"}).json()["listening"]
    assert len(store.feedback_feed(DECK).events) == 1
    assert (
        client.post("/api/review/hooks", json={**body, "session_id": "../bad"}).status_code == 422
    )
    assert (
        client.post(
            "/api/review/hooks", json=body, headers={"Origin": "https://elsewhere.example"}
        ).status_code
        == 403
    )
