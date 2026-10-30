"""Verify hidden-slide metadata preserves content, review state and concurrent work."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import app as service
from studio.models import SlidePatch
from studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design


def test_hidden_setting_survives_restart_without_touching_review_or_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hide, retry and unhide an approved slide while retaining notes and HTML."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    first = store.view(TEMPLATE_ID).slides[0]
    assert not first.hidden
    store.approve(TEMPLATE_ID, first.id, first.content_hash, True)
    store.save_notes(TEMPLATE_ID, first.id, "Human context", first.notes)
    endpoint = f"/api/decks/{TEMPLATE_ID}/slides/{first.id}/visibility"
    for _ in range(2):
        result = client.post(endpoint, json={"hidden": True, "expected_hidden": False})
        assert result.status_code == 200
        assert result.json() == {"hidden": True}
    hidden = Store(store.root, store.database).view(TEMPLATE_ID).slides[0]
    assert hidden.hidden and hidden.approved and not hidden.has_unseen_changes
    assert hidden.content_hash == first.content_hash
    assert hidden.html == first.html and hidden.notes == "Human context"
    assert hidden.revision == first.revision
    assert hidden.expected_hash != first.expected_hash
    assert service.get_presentation(TEMPLATE_ID).slides[0].hidden
    assert client.post(endpoint, json={"hidden": False, "expected_hidden": True}).status_code == 200
    assert not store.view(TEMPLATE_ID).slides[0].hidden


def test_visibility_and_agent_revisions_merge_without_overwriting_each_other(
    tmp_path: Path,
) -> None:
    """Preserve visibility when changing content and preserve content when hiding later."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    first = store.view(TEMPLATE_ID).slides[0]
    _ = store.revise(
        TEMPLATE_ID,
        first.id,
        first.expected_hash,
        "agent-sets-hidden",
        SlidePatch(hidden=True),
    )
    hidden = store.view(TEMPLATE_ID).slides[0]
    assert hidden.hidden and not hidden.has_unseen_changes
    revised = store.revise(
        TEMPLATE_ID,
        first.id,
        hidden.expected_hash,
        "agent-content-edit",
        SlidePatch(html='<article class="slide"><h1>New content</h1></article>'),
    )
    assert revised.hidden
    store.set_slide_hidden(TEMPLATE_ID, first.id, hidden=False, expected_hidden=True)
    final = store.view(TEMPLATE_ID).slides[0]
    assert not final.hidden
    assert final.html == revised.html
    assert final.has_unseen_changes


def test_visibility_write_requires_valid_state_and_same_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject invalid payloads, stale state and cross-origin visibility mutations."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    endpoint = f"/api/decks/{TEMPLATE_ID}/slides/cover/visibility"
    assert (
        client.post(endpoint, json={"hidden": "true", "expected_hidden": False}).status_code == 422
    )
    assert client.post(endpoint, json={"hidden": True, "expected_hidden": True}).status_code == 409
    assert (
        client.post(
            endpoint,
            json={"hidden": True, "expected_hidden": False},
            headers={"Origin": "https://other.example"},
        ).status_code
        == 403
    )
    assert not store.read(TEMPLATE_ID).slides[0].hidden


def test_new_deck_starts_visible_and_has_standalone_slideshow_styles(tmp_path: Path) -> None:
    """Keep hidden example slides from producing an invisible cover in a new deck."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    store.set_slide_hidden(TEMPLATE_ID, "cover", hidden=True, expected_hidden=False)
    created = store.create("2026-10-29-new-visible-deck", "New deck")
    assert not created.slides[0].hidden
    assert (store.folder(created.id) / "slideshow.css").is_file()


def test_selection_visibility_is_atomic_and_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject an entire selection on stale state and retain strict boolean validation."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    ids = [slide.id for slide in store.read(TEMPLATE_ID).slides]
    endpoint = f"/api/decks/{TEMPLATE_ID}/slides-visibility"
    payload = {"slide_ids": ids, "hidden": True, "expected_hidden": dict.fromkeys(ids, False)}
    stale = {**payload, "expected_hidden": {ids[0]: False, ids[1]: True}}
    assert client.post(endpoint, json=stale).status_code == 409
    assert not any(slide.hidden for slide in store.read(TEMPLATE_ID).slides)
    invalid = {**payload, "expected_hidden": dict.fromkeys(ids, "false")}
    assert client.post(endpoint, json=invalid).status_code == 422
    assert client.post(endpoint, json=payload).status_code == 200
    assert all(slide.hidden for slide in store.read(TEMPLATE_ID).slides)
