"""Behavioral checks for lifecycle review across agent harnesses."""

import asyncio
import time
from pathlib import Path
from typing import Literal

import pytest
from fastapi.testclient import TestClient

from studio import app as service
from studio.models import DesignRequestInput
from studio.review import ReviewHookInput, Reviews
from studio.storage import Store
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
    assert reviews.pending(stop).feedback_available
    assert store.workflow(DECK).review_connected


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
