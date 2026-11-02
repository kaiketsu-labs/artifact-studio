"""Saved groups preserve slide identity, concurrent edits and portable review state."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from artifact_studio import app as service
from artifact_studio.models import DeckSection
from artifact_studio.projects import ClientState, audience_html, open_project, save_project
from artifact_studio.sections import Sections, sections_hash
from artifact_studio.slide_actions import SlideActions
from artifact_studio.storage import ConflictError, Store, digest
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    """Use the fictional design's canonical deck and an isolated review log."""
    make_design(tmp_path / TEMPLATE_ID)
    result = Store(tmp_path, tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", result)
    return result


def group(store: Store) -> list[DeckSection]:
    """Split after the opening slide without touching any slide fields."""
    ids = [slide.id for slide in store.read(TEMPLATE_ID).slides]
    return [
        DeckSection(id="opening", title="Opening", slide_ids=ids[:1]),
        DeckSection(id="detail", title="Details", slide_ids=ids[1:]),
    ]


def put(store: Store, groups: list[DeckSection]) -> None:
    """Apply a layout against the latest canonical structure."""
    current = store.read(TEMPLATE_ID)
    _ = Sections(store).replace(
        TEMPLATE_ID,
        [slide_id for item in groups for slide_id in item.slide_ids]
        if groups
        else [slide.id for slide in current.slides],
        groups,
        [slide.id for slide in current.slides],
        sections_hash(current.sections),
    )


def test_sections_reorder_rename_remove_preserve_all_slide_state(store: Store) -> None:
    """Grouping changes neither approval nor notes, and whole groups move together."""
    first = store.view(TEMPLATE_ID).slides[0]
    store.approve(TEMPLATE_ID, first.id, first.content_hash, True)
    store.save_notes(TEMPLATE_ID, first.id, "Human speaker notes", first.notes)
    before = {slide.id: slide.model_dump() for slide in store.view(TEMPLATE_ID).slides}
    groups = group(store)
    put(store, groups)
    groups[0].title = "Introduction"
    put(store, list(reversed(groups)))
    moved = store.view(TEMPLATE_ID)
    assert [item.id for item in moved.sections] == ["detail", "opening"]
    assert {slide.id: slide.model_dump() for slide in moved.slides} == before
    order = [slide.id for slide in moved.slides]
    put(store, [])
    assert [slide.id for slide in store.read(TEMPLATE_ID).slides] == order
    assert not store.read(TEMPLATE_ID).sections


def test_stale_layouts_and_invalid_partitions_cannot_overwrite_structure(store: Store) -> None:
    """Detect concurrent renames and preserve edits made since the UI read its snapshot."""
    original = store.read(TEMPLATE_ID)
    ids = [slide.id for slide in original.slides]
    groups = group(store)
    put(store, groups)
    renamed = [item.model_copy(deep=True) for item in groups]
    renamed[0].title = "An older proposed name"
    with pytest.raises(ConflictError):
        _ = Sections(store).replace(TEMPLATE_ID, ids, renamed, ids, sections_hash([]))
    assert store.read(TEMPLATE_ID).sections == groups
    bad = [groups[0], groups[0]]
    with pytest.raises(ValidationError):
        _ = Sections(store).replace(TEMPLATE_ID, ids, bad, ids, sections_hash(groups))
    current = store.read(TEMPLATE_ID)
    current.slides[0].notes = "A later human edit"
    store.write(TEMPLATE_ID, current)
    # The exact target is a safe no-op retry even after unrelated content edits.
    _ = Sections(store).replace(TEMPLATE_ID, ids, groups, ids, sections_hash([]))
    assert store.read(TEMPLATE_ID).slides[0].notes == "A later human edit"


def test_add_delete_restore_and_reordering_keep_section_membership(store: Store) -> None:
    """Retain empty groups and restore deleted slides to their original section."""
    put(store, group(store))
    actions = SlideActions(store)
    before = store.read(TEMPLATE_ID)
    added = actions.add(
        TEMPLATE_ID, before.slides[0].id, [item.id for item in before.slides], "section-add-001"
    )
    assert added.added_ids[0] in store.read(TEMPLATE_ID).sections[0].slide_ids
    current = store.read(TEMPLATE_ID)
    deleting = current.sections[1].slide_ids
    result = actions.delete(
        TEMPLATE_ID,
        deleting,
        [item.id for item in current.slides],
        {item.id: digest(item.model_dump_json()) for item in current.slides if item.id in deleting},
        "section-delete-001",
    )
    assert store.read(TEMPLATE_ID).sections[1].slide_ids == []
    _ = actions.restore(TEMPLATE_ID, result.operation_id, "section-restore-001")
    assert store.read(TEMPLATE_ID).sections[1].slide_ids == deleting
    order = [slide.id for slide in store.read(TEMPLATE_ID).slides]
    with pytest.raises(ConflictError):
        _ = store.reorder(TEMPLATE_ID, list(reversed(order)), expected_slide_ids=order)
    assert [slide.id for slide in store.read(TEMPLATE_ID).slides] == order


def test_http_agent_outline_and_portable_project_keep_sections_private(store: Store) -> None:
    """Share named groups with agents, retain folding in projects, omit it from audience export."""
    client = TestClient(service.app)
    snapshot = client.get(f"/api/decks/{TEMPLATE_ID}").json()
    groups = group(store)
    payload = {
        "slide_ids": [item["id"] for item in snapshot["slides"]],
        "expected_slide_ids": [item["id"] for item in snapshot["slides"]],
        "sections": [item.model_dump() for item in groups],
        "expected_sections_hash": snapshot["sections_hash"],
    }
    response = client.post(f"/api/decks/{TEMPLATE_ID}/sections", json=payload)
    assert response.status_code == 200
    assert client.post(f"/api/decks/{TEMPLATE_ID}/sections", json=payload).status_code == 200
    outline = service.get_presentation_outline(TEMPLATE_ID)
    assert outline["sections"] == payload["sections"]
    assert outline["sections_hash"] == response.json()["sections_hash"]
    data = save_project(store, TEMPLATE_ID, ClientState(collapsed_sections=["detail"]))
    opened, state = open_project(store, data)
    assert opened.sections == groups and state.collapsed_sections == ["detail"]
    exported = audience_html(store, opened.id)
    assert '"sections": []' in exported or '"sections":[]' in exported
    assert '"title": "Opening"' not in exported
