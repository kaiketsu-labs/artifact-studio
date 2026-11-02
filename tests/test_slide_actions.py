"""Exercise durable structural slide changes using synthetic presentations."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import app as service
from studio.models import Slide
from studio.slide_actions import SlideActions
from studio.storage import ConflictError, Store, digest
from tests.design_factory import TEMPLATE_ID, make_design


def test_browser_slide_commands_and_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-trip browser commands and reject stale or cross-origin structural writes."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    endpoint = f"/api/decks/{TEMPLATE_ID}"
    slides = store.view(TEMPLATE_ID).slides
    payload = {
        "action": "delete",
        "slide_ids": [slides[0].id],
        "expected_slide_ids": [slide.id for slide in slides],
        "expected_hashes": {slides[0].id: slides[0].expected_hash},
        "request_id": "browser-delete",
    }
    assert (
        client.post(
            endpoint + "/slide-actions", json=payload, headers={"Origin": "https://other.example"}
        ).status_code
        == 403
    )
    stale = {**payload, "expected_hashes": {slides[0].id: "stale"}}
    assert client.post(endpoint + "/slide-actions", json=stale).status_code == 409
    result = client.post(endpoint + "/slide-actions", json=payload)
    assert result.status_code == 200
    assert client.post(endpoint + "/slide-actions", json=payload).json() == result.json()
    trash = client.get(endpoint + "/deleted-slides").json()
    assert trash == [
        {
            "operation_id": "browser-delete",
            "slides": [{"id": slides[0].id, "title": slides[0].title}],
        }
    ]
    restored = client.post(
        endpoint + "/restore-slides",
        json={"deletion_id": "browser-delete", "request_id": "browser-restore"},
    )
    assert restored.status_code == 200
    assert [slide.id for slide in store.read(TEMPLATE_ID).slides] == [slide.id for slide in slides]
    assert client.get(endpoint + "/deleted-slides").json() == []


def test_actions_preserve_state_and_retry(tmp_path: Path) -> None:
    """Create, copy, delete and restore without inheriting or losing review state."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    actions = SlideActions(store)
    deck = store.read(TEMPLATE_ID)
    ids = [s.id for s in deck.slides]
    view = store.view(TEMPLATE_ID).slides[0]
    store.approve(TEMPLATE_ID, view.id, view.content_hash, True)
    store.save_notes(TEMPLATE_ID, view.id, "Notes", view.notes)
    store.set_slide_hidden(TEMPLATE_ID, view.id, hidden=True, expected_hidden=False)
    _ = store.add_feedback(TEMPLATE_ID, view.id, "Feedback", view.content_hash, "feedback")
    added = actions.add(TEMPLATE_ID, ids[0], ids, "request-add")
    assert actions.add(TEMPLATE_ID, ids[0], ids, "request-add") == added
    with pytest.raises(ConflictError):
        actions.add(TEMPLATE_ID, ids[1], ids, "request-add")
    new_ids = [s.id for s in store.read(TEMPLATE_ID).slides]
    copy_hashes = {
        s.id: digest(s.model_dump_json()) for s in store.read(TEMPLATE_ID).slides if s.id in ids
    }
    copied = actions.duplicate(TEMPLATE_ID, [ids[1], ids[0]], new_ids, copy_hashes, "request-copy")
    slides = store.read(TEMPLATE_ID).slides
    assert [s.id for s in slides][3:5] == copied.added_ids
    copied_view = next(s for s in store.view(TEMPLATE_ID).slides if s.id == copied.added_ids[0])
    assert copied_view.notes == "Notes" and copied_view.hidden and not copied_view.approved
    assert not copied_view.feedback
    before = store.read(TEMPLATE_ID)
    order = [s.id for s in before.slides]
    hashes = {s.id: digest(s.model_dump_json()) for s in before.slides if s.id in ids}
    deleted = actions.delete(TEMPLATE_ID, ids, order, hashes, "request-delete")
    _ = store.add_slide(
        TEMPLATE_ID,
        Slide(id="concurrent", title="Concurrent", html='<article class="slide"></article>'),
    )
    restored = SlideActions(Store(store.root, store.database)).restore(
        TEMPLATE_ID, deleted.operation_id, "request-restore"
    )
    assert restored.added_ids == ids
    assert actions.restore(TEMPLATE_ID, deleted.operation_id, "request-restore") == restored
    final = store.view(TEMPLATE_ID)
    first = next(s for s in final.slides if s.id == ids[0])
    assert first.notes == "Notes" and first.hidden and first.approved and first.feedback
    original_notes_revision = next(s for s in before.slides if s.id == ids[0]).notes_revision
    assert first.notes_revision == original_notes_revision + 1
    with pytest.raises(ConflictError):
        store.save_notes(
            TEMPLATE_ID, first.id, "An old pending note", "Notes", original_notes_revision
        )
    assert any(s.id == "concurrent" for s in final.slides)


def test_invalid_and_stale_deletion(tmp_path: Path) -> None:
    """Reject stale hashes and attempts to remove every slide."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    actions = SlideActions(store)
    ids = [s.id for s in store.read(TEMPLATE_ID).slides]
    with pytest.raises(ValueError):
        actions.delete(TEMPLATE_ID, ids, ids, {}, "delete-all")
    with pytest.raises(ConflictError):
        actions.delete(TEMPLATE_ID, ids[:1], ids, {ids[0]: "stale"}, "delete-stale")


def test_recovers_interrupted_file_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry a prepared action after a failed canonical file write and restart."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    actions = SlideActions(store)
    ids = [s.id for s in store.read(TEMPLATE_ID).slides]
    original_write = store.write

    def fail_write(deck_id: str, deck: object) -> None:
        """Simulate failure before replacing the canonical document."""
        raise OSError("interrupted")

    monkeypatch.setattr(store, "write", fail_write)
    with pytest.raises(OSError):
        actions.add(TEMPLATE_ID, ids[0], ids, "interrupted-add")
    monkeypatch.setattr(store, "write", original_write)
    recovered = SlideActions(Store(store.root, store.database))
    recovered.recover(TEMPLATE_ID)
    result = recovered.add(TEMPLATE_ID, ids[0], ids, "interrupted-add")
    assert [slide.id for slide in store.read(TEMPLATE_ID).slides] == [
        ids[0],
        result.added_ids[0],
        ids[1],
    ]


def test_noncontiguous_restore_and_reversed_anchors(tmp_path: Path) -> None:
    """Restore separated slides and reject reversed surviving neighbor anchors."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    for name in ("third", "fourth", "fifth"):
        _ = store.add_slide(TEMPLATE_ID, Slide(id=name, title=name, html="<article></article>"))
    actions = SlideActions(store)
    slides = store.read(TEMPLATE_ID).slides
    ids = [slide.id for slide in slides]
    selected = [ids[1], ids[3]]
    hashes = {slide.id: digest(slide.model_dump_json()) for slide in slides if slide.id in selected}
    deleted = actions.delete(TEMPLATE_ID, selected, ids, hashes, "noncontiguous-delete")
    survivors = [ids[0], ids[2], ids[4]]
    _ = store.reorder(TEMPLATE_ID, list(reversed(survivors)))
    with pytest.raises(ConflictError):
        actions.restore(TEMPLATE_ID, deleted.operation_id, "restore-reversed")
    _ = store.reorder(TEMPLATE_ID, survivors)
    _ = actions.restore(TEMPLATE_ID, deleted.operation_id, "restore-correct")
    assert [slide.id for slide in store.read(TEMPLATE_ID).slides] == ids


def test_duplicate_rejects_changed_source(tmp_path: Path) -> None:
    """Reject a copy of source content changed since the user's snapshot."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    slides = store.read(TEMPLATE_ID).slides
    ids = [slide.id for slide in slides]
    old_hash = digest(slides[0].model_dump_json())
    store.save_notes(TEMPLATE_ID, ids[0], "Updated", slides[0].notes)
    with pytest.raises(ConflictError):
        _ = SlideActions(store).duplicate(
            TEMPLATE_ID, [ids[0]], ids, {ids[0]: old_hash}, "stale-copy"
        )


def test_content_write_recovers_pending_structure_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preserve a prepared insertion when a different writer saves human notes."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    first = store.read(TEMPLATE_ID).slides[0]
    original_write = store.write

    def fail_write(deck_id: str, deck: object) -> None:
        """Interrupt a structural write after its durable preparation."""
        raise OSError("interrupted")

    monkeypatch.setattr(store, "write", fail_write)
    with pytest.raises(OSError):
        _ = SlideActions(store).add(
            TEMPLATE_ID,
            first.id,
            [slide.id for slide in store.read(TEMPLATE_ID).slides],
            "prepared-insertion",
        )
    monkeypatch.setattr(store, "write", original_write)
    store.save_notes(TEMPLATE_ID, first.id, "Human note after interruption", first.notes)
    final = store.read(TEMPLATE_ID)
    assert len(final.slides) == 3
    assert final.slides[0].notes == "Human note after interruption"
    assert final.slides[1].title == "Untitled slide"


def test_recovers_after_file_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Finalize a prepared action if interruption follows the file replacement."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    actions = SlideActions(store)
    ids = [slide.id for slide in store.read(TEMPLATE_ID).slides]

    def fail_seed(*args: object) -> None:
        """Interrupt recovery after canonical file replacement."""
        raise OSError("interrupted after write")

    monkeypatch.setattr(store, "seed_slide_views", fail_seed)
    with pytest.raises(OSError):
        _ = actions.add(TEMPLATE_ID, ids[0], ids, "after-write-failure")
    after_write = [slide.id for slide in store.read(TEMPLATE_ID).slides]
    restarted = SlideActions(Store(store.root, store.database))
    restarted.recover(TEMPLATE_ID)
    result = restarted.add(TEMPLATE_ID, ids[0], ids, "after-write-failure")
    assert after_write == [ids[0], result.added_ids[0], ids[1]]
    assert [slide.id for slide in store.read(TEMPLATE_ID).slides] == after_write
