"""Check durable unread revisions and browser push notifications independently of approvals."""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import app as service
from studio.events import change_token, deck_events
from studio.models import Feedback, Slide, SlidePatch
from studio.storage import Store, digest
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """Create a fictional deck with its own database and visual assets."""
    make_design(tmp_path / TEMPLATE_ID)
    return Store(tmp_path, tmp_path / "review.sqlite3")


def revise(store: Store, text: str) -> None:
    """Change visible slide content without using a review snapshot first."""
    item = store.read(TEMPLATE_ID).slides[0]
    _ = store.revise(
        TEMPLATE_ID,
        item.id,
        digest(item.model_dump_json()),
        f"revision-{text}",
        SlidePatch(html=f'<article class="slide"><h1>{text}</h1></article>'),
    )


def test_opening_revision_is_independent_of_approval(store: Store) -> None:
    """Keep revisions unread through agent reads and clear only an exact human-opened version."""
    revise(store, "Updated")
    first = store.view(TEMPLATE_ID).slides[0]
    assert first.has_unseen_changes and not first.approved
    assert store.view(TEMPLATE_ID).slides[0].has_unseen_changes
    restarted = Store(store.root, store.database)
    assert restarted.view(TEMPLATE_ID).slides[0].has_unseen_changes
    assert restarted.mark_slide_seen(TEMPLATE_ID, first.id, first.content_hash)
    opened = store.view(TEMPLATE_ID).slides[0]
    assert not opened.has_unseen_changes and not opened.approved
    revise(store, "Again")
    assert not store.mark_slide_seen(TEMPLATE_ID, first.id, first.content_hash)
    newer = store.view(TEMPLATE_ID).slides[0]
    assert newer.has_unseen_changes
    store.approve(TEMPLATE_ID, newer.id, newer.content_hash, True)
    approved = store.view(TEMPLATE_ID).slides[0]
    assert approved.approved and not approved.has_unseen_changes


def test_private_context_does_not_look_like_a_visual_revision(store: Store) -> None:
    """Ignore notes and agent explanations, but notice direct theme and asset edits."""
    first = store.view(TEMPLATE_ID).slides[0]
    assert not first.has_unseen_changes
    _ = store.revise(
        TEMPLATE_ID,
        first.id,
        first.expected_hash,
        "private-context",
        SlidePatch(notes="Brief context not visible on the slide."),
    )
    assert not store.view(TEMPLATE_ID).slides[0].has_unseen_changes
    theme = store.folder(TEMPLATE_ID) / "theme.css"
    _ = theme.write_text(theme.read_text() + "\nh1{color:orange}")
    changed = store.view(TEMPLATE_ID).slides[0]
    assert changed.has_unseen_changes
    assert store.mark_slide_seen(TEMPLATE_ID, first.id, changed.content_hash)
    _ = (store.folder(TEMPLATE_ID) / "assets/new.svg").write_text("<svg/>")
    assert store.view(TEMPLATE_ID).slides[0].has_unseen_changes


def test_shared_design_is_read_once_per_snapshot(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reuse shared design and feedback reads while preserving exact slide approval hashes."""
    visual_hash, comments = store.visual_hash, store.comments
    reads: list[str] = []

    def design(deck_id: str) -> str:
        """Count full design scans across all slides in one response."""
        reads.append("design")
        return visual_hash(deck_id)

    def feedback(deck_id: str, after_event_id: int = 0) -> list[Feedback]:
        """Count decoding of the shared feedback history."""
        reads.append("feedback")
        return comments(deck_id, after_event_id)

    monkeypatch.setattr(store, "visual_hash", design)
    monkeypatch.setattr(store, "comments", feedback)
    snapshot = store.view(TEMPLATE_ID)
    assert reads == ["design", "feedback"]
    assert snapshot.design_hash == visual_hash(TEMPLATE_ID)
    assert all(
        item.content_hash == store.content_hash(TEMPLATE_ID, item) for item in snapshot.slides
    )


def test_design_version_stays_stable_between_slides_and_changes_with_assets(store: Store) -> None:
    """Keep reusable design caching independent from slide and private-context revisions."""
    before = store.view(TEMPLATE_ID)
    revise(store, "Only this slide changed")
    changed = store.view(TEMPLATE_ID)
    assert changed.design_hash == before.design_hash
    assert changed.slides[0].content_hash != before.slides[0].content_hash
    asset = store.folder(TEMPLATE_ID) / "assets/new.svg"
    _ = asset.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    assert store.view(TEMPLATE_ID).design_hash != changed.design_hash


def test_static_assets_revalidate_while_workspace_data_is_not_cached(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Allow private conditional asset requests without caching private workspace responses."""
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    first = client.get("/web/icons.js")
    assert first.headers["cache-control"] == "private, no-cache"
    same = client.get("/web/icons.js", headers={"If-None-Match": first.headers["etag"]})
    assert same.status_code == 304
    assert client.get("/").headers["cache-control"] == "no-store"
    assert client.get(f"/api/decks/{TEMPLATE_ID}").headers["cache-control"] == "no-store"


def test_initial_baselines_precede_first_agent_read(store: Store) -> None:
    """Track shared edits from startup, creation and addition before anyone opens a slide."""
    created = store.create("2026-10-29-created", "Created deck")
    added = store.add_slide(
        created.id, Slide(id="added", title="Added", html='<article class="slide">New</article>')
    )
    for deck_id in (TEMPLATE_ID, created.id):
        path = store.folder(deck_id) / "theme.css"
        _ = path.write_text(path.read_text() + "\n.slide{color:purple}")
    assert all(item.has_unseen_changes for item in store.view(TEMPLATE_ID).slides)
    assert all(item.has_unseen_changes for item in store.view(created.id).slides)
    assert store.view(created.id).slides[-1].id == added.id


def test_seen_http_contract(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate opened versions and keep cross-origin pages from acknowledging them."""
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    revise(store, "HTTP")
    item = store.view(TEMPLATE_ID).slides[0]
    url = f"/api/decks/{TEMPLATE_ID}/slides/{item.id}/seen"
    assert client.post(url, json={"content_hash": "bad"}).status_code == 422
    assert client.post(url, json={"content_hash": "0" * 64}).json() == {"seen": False}
    assert (
        client.post(
            url, json={"content_hash": item.content_hash}, headers={"Origin": "https://other.test"}
        ).status_code
        == 403
    )
    assert client.post(url, json={"content_hash": item.content_hash}).json() == {"seen": True}
    assert client.get("/api/decks/does-not-exist/events").status_code == 404


def test_stream_detects_file_and_review_changes_without_a_read_loop(store: Store) -> None:
    """Deliver canonical-file and database changes and stop once the browser disconnects."""

    async def exercise() -> None:
        disconnected = False

        async def gone() -> bool:
            """Expose a controlled browser disconnect."""
            return disconnected

        stream = deck_events(store, TEMPLATE_ID, gone, interval=0.01)
        assert "data: ready" in await anext(stream)
        stable = change_token(store, TEMPLATE_ID)
        _ = store.view(TEMPLATE_ID)
        assert change_token(store, TEMPLATE_ID) == stable
        revise(store, "Stream")
        assert "data: updated" in await asyncio.wait_for(anext(stream), 1)
        item = store.view(TEMPLATE_ID).slides[0]
        assert store.mark_slide_seen(TEMPLATE_ID, item.id, item.content_hash)
        assert "data: updated" in await asyncio.wait_for(anext(stream), 1)
        disconnected = True
        with pytest.raises(StopAsyncIteration):
            _ = await anext(stream)

    asyncio.run(exercise())
