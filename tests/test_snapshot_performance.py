"""Protect snapshot scaling and review-state semantics for fictional presentations."""

import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from artifact_studio import app as service
from artifact_studio.models import Slide, SlidePatch
from artifact_studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """Prepare a small independent presentation and review database."""
    make_design(tmp_path / TEMPLATE_ID)
    return Store(tmp_path, tmp_path / "review.sqlite3")


def test_review_queries_do_not_grow_with_slide_count(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read approval and seen state with a fixed number of queries per snapshot."""
    for index in range(20):
        _ = store.add_slide(
            TEMPLATE_ID,
            Slide(
                id=f"slide-{index}",
                title=f"Slide {index}",
                html='<article class="slide">Test</article>',
            ),
        )
    statements: list[str] = []
    original = store.connect

    @contextmanager
    def traced_connect() -> Generator[sqlite3.Connection]:
        """Capture SQL executed by each snapshot connection."""
        with original() as db:
            db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(store, "connect", traced_connect)
    snapshot = store.view(TEMPLATE_ID)
    assert len(snapshot.slides) == 22
    review_queries = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT")
        and ("FROM approvals" in statement or "FROM slide_views" in statement)
    ]
    assert len(review_queries) == 2


def test_snapshot_and_standalone_slide_keep_review_and_feedback_state(store: Store) -> None:
    """Preserve exact-version approval, unread status, and withheld feedback filtering."""
    initial = store.view(TEMPLATE_ID)
    first, second = initial.slides
    store.approve(TEMPLATE_ID, first.id, first.content_hash, True)
    with store.connect() as db:
        _ = store.append_feedback(db, TEMPLATE_ID, first.id, "user", "Released", "", released=True)
        _ = store.append_feedback(db, TEMPLATE_ID, first.id, "user", "Withheld", "", released=False)
        _ = store.append_feedback(
            db, TEMPLATE_ID, second.id, "user", "Other slide", "", released=True
        )
        _ = store.append_feedback(db, TEMPLATE_ID, "", "user", "Deck feedback", "", released=False)

    private = store.view(TEMPLATE_ID)
    public = store.view(TEMPLATE_ID, include_pending=False)
    assert private.slides[0].approved and not private.slides[0].has_unseen_changes
    assert [item.message for item in private.slides[0].feedback] == ["Released", "Withheld"]
    assert [item.message for item in public.slides[0].feedback] == ["Released"]
    assert [item.message for item in public.slides[1].feedback] == ["Other slide"]
    assert [item.message for item in private.feedback] == ["Deck feedback"]
    assert not public.feedback
    standalone = store.view_slide(
        TEMPLATE_ID, store.read(TEMPLATE_ID).slides[0], include_pending=False
    )
    assert standalone == public.slides[0]

    _ = store.revise(
        TEMPLATE_ID,
        first.id,
        first.expected_hash,
        "fictional-revision",
        SlidePatch(html='<article class="slide"><h1>Changed</h1></article>'),
    )
    changed = store.view(TEMPLATE_ID, include_pending=False).slides[0]
    assert not changed.approved and changed.has_unseen_changes
    assert (
        store.view_slide(TEMPLATE_ID, store.read(TEMPLATE_ID).slides[0], include_pending=False)
        == changed
    )
    assert store.mark_slide_seen(TEMPLATE_ID, first.id, changed.content_hash)
    store.approve(TEMPLATE_ID, first.id, changed.content_hash, True)
    latest = store.view(TEMPLATE_ID, include_pending=False).slides[0]
    assert latest.approved and not latest.has_unseen_changes
    assert (
        store.view_slide(TEMPLATE_ID, store.read(TEMPLATE_ID).slides[0], include_pending=False)
        == latest
    )


def test_availability_poll_does_not_load_deck_or_chat(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep agent lease expiry current without building a full presentation snapshot."""
    with store.connect() as db:
        _ = db.execute(
            "INSERT INTO review_sessions VALUES (?,?,?,?,?,?,?)",
            ("codex", "performance", TEMPLATE_ID, 0, 0, time.time() + 60, 1),
        )
    monkeypatch.setattr(service, "store", store)

    def unexpected(*_args: object, **_kwargs: object) -> None:
        """Fail if an availability poll parses slide content or discussion history."""
        pytest.fail("Availability must not read slides or chat.")

    monkeypatch.setattr(store, "read", unexpected)
    monkeypatch.setattr(store, "comments", unexpected)
    monkeypatch.setattr(store, "view", unexpected)
    client = TestClient(service.app)
    result = client.get(f"/api/decks/{TEMPLATE_ID}/workflow")
    assert result.status_code == 200
    assert result.json()["agent_waiting"]
    assert "slides" not in result.json() and "feedback" not in result.json()
    with store.connect() as db:
        _ = db.execute("UPDATE review_sessions SET expires_at=0")
    assert not client.get(f"/api/decks/{TEMPLATE_ID}/workflow").json()["agent_waiting"]
    assert client.get("/api/decks/missing/workflow").status_code == 404
