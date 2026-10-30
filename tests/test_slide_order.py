"""Verify slide movement preserves slide identity and detects concurrent structure edits."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import app as service
from studio.models import Slide, SlidePatch
from studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design


def test_reordering_preserves_content_notes_feedback_and_review_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Move an approved hidden slide and retain all per-slide state after restart."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    first = store.view(TEMPLATE_ID).slides[0]
    store.approve(TEMPLATE_ID, first.id, first.content_hash, True)
    store.save_notes(TEMPLATE_ID, first.id, "Human notes", first.notes)
    store.set_slide_hidden(TEMPLATE_ID, first.id, hidden=True, expected_hidden=False)
    _ = store.add_feedback(
        TEMPLATE_ID, first.id, "Keep this example", first.content_hash, "feedback"
    )
    before = store.view(TEMPLATE_ID)
    original = [slide.id for slide in before.slides]
    changed = list(reversed(original))
    client = TestClient(service.app)
    for _ in range(2):
        result = client.post(
            f"/api/decks/{TEMPLATE_ID}/slide-order",
            json={"slide_ids": changed, "expected_slide_ids": original},
        )
        assert result.status_code == 200
        assert result.json() == {"slide_ids": changed}
    after = Store(store.root, store.database).view(TEMPLATE_ID)
    assert [slide.id for slide in after.slides] == changed
    assert {slide.id: slide.model_dump() for slide in before.slides} == {
        slide.id: slide.model_dump() for slide in after.slides
    }


def test_stale_reordering_preserves_new_order_and_concurrent_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject an old order while safely merging a move with newer slide content."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    _ = store.add_slide(
        TEMPLATE_ID, Slide(id="ending", title="Ending", html='<article class="slide">End</article>')
    )
    original = [slide.id for slide in store.read(TEMPLATE_ID).slides]
    first = store.view(TEMPLATE_ID).slides[0]
    _ = store.revise(
        TEMPLATE_ID,
        first.id,
        first.expected_hash,
        "reorder-concurrent-revision",
        SlidePatch(html='<article class="slide">Revised</article>'),
    )
    changed = ["message", "cover", "ending"]
    _ = service.reorder_slides(TEMPLATE_ID, changed, original)
    client = TestClient(service.app)
    result = client.post(
        f"/api/decks/{TEMPLATE_ID}/slide-order",
        json={"slide_ids": list(reversed(original)), "expected_slide_ids": original},
    )
    assert result.status_code == 409
    final = store.view(TEMPLATE_ID)
    assert [slide.id for slide in final.slides] == changed
    revised = next(slide for slide in final.slides if slide.id == first.id)
    assert "Revised" in revised.html and revised.has_unseen_changes


def test_order_requires_every_slide_once_and_same_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject missing, duplicate, unknown, invalid and cross-origin slide orders."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    endpoint = f"/api/decks/{TEMPLATE_ID}/slide-order"
    original = [slide.id for slide in store.read(TEMPLATE_ID).slides]
    for ids in [["cover"], ["cover", "cover"], ["cover", "unknown"]]:
        assert (
            client.post(
                endpoint, json={"slide_ids": ids, "expected_slide_ids": original}
            ).status_code
            == 409
        )
    assert (
        client.post(endpoint, json={"slide_ids": [], "expected_slide_ids": original}).status_code
        == 422
    )
    assert (
        client.post(
            endpoint,
            json={"slide_ids": list(reversed(original)), "expected_slide_ids": original},
            headers={"Origin": "https://other.example"},
        ).status_code
        == 403
    )
    assert [slide.id for slide in store.read(TEMPLATE_ID).slides] == original


def test_reordering_anchors_legacy_presentation_chat_to_its_original_slide(tmp_path: Path) -> None:
    """Keep old shared chat on the same slide when a different slide becomes first."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    _ = store.add_feedback(TEMPLATE_ID, None, "Keep it visual", "", "legacy-message")
    _ = store.reorder(TEMPLATE_ID, ["message", "cover"])
    assert store.view(TEMPLATE_ID).feedback[0].context_slide_id == "cover"
