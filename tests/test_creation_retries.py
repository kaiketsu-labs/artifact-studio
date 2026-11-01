"""Creation retries prove ownership without changing later presentation work."""

import base64
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import app as service
from studio.models import FileInput, SlidePatch
from studio.projects import ClientState, open_project, save_project
from studio.storage import ConflictError, Store, digest
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    make_design(tmp_path / "presentations" / TEMPLATE_ID)
    result = Store(tmp_path / "presentations", tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", result)
    return result


def test_replay_preserves_original_snapshot_and_existing_edits(store: Store) -> None:
    deck_id = "2026-11-01-create-retry"
    files = [FileInput(name="brief.txt", data_base64=base64.b64encode(b"Brief data").decode())]
    original = store.create(deck_id, "Original title", "", "Original brief", files, "creation-001")
    _ = store.revise(
        deck_id,
        "cover",
        digest(original.slides[0].model_dump_json()),
        "later-edit-001",
        SlidePatch(title="Edited later"),
    )
    shutil.rmtree(store.folder(TEMPLATE_ID))
    replay = store.create(deck_id, "Original title", "", "Original brief", files, "creation-001")
    assert replay == original
    assert store.read(deck_id).slides[0].title == "Edited later"
    assert store.brief_files(deck_id)[0].name == "brief.txt"
    assert len(store.comments(deck_id)) == 1
    with pytest.raises(ConflictError, match="different content"):
        store.create(deck_id, "Other title", "", "Original brief", files, "creation-001")
    changed_files = [FileInput(name="brief.txt", data_base64=base64.b64encode(b"Changed").decode())]
    with pytest.raises(ConflictError, match="different content"):
        store.create(deck_id, "Original title", "", "Original brief", changed_files, "creation-001")
    with pytest.raises(ConflictError, match="ownership"):
        store.create(deck_id, "Original title", "", "Original brief", files, "creation-002")
    shutil.rmtree(store.folder(deck_id))
    with pytest.raises(ConflictError, match="missing"):
        store.create(deck_id, "Original title", "", "Original brief", files, "creation-001")
    assert not store.folder(deck_id).exists()


def test_existing_unrecorded_and_imported_decks_are_not_claimed(store: Store) -> None:
    deck_id = "2026-11-01-existing"
    _ = store.create(deck_id, "Existing")
    with pytest.raises(ConflictError, match="ownership"):
        store.create(deck_id, "Existing", request_id="creation-003")
    owned = store.create("2026-11-01-owned", "Owned", request_id="creation-004")
    archive = save_project(store, owned.id, ClientState())
    imported, _ = open_project(store, archive)
    assert imported.id != owned.id
    with pytest.raises(ConflictError, match="ownership"):
        store.create(imported.id, "Owned", request_id="creation-004")
    assert store.read(imported.id).id == imported.id


def test_http_creation_remains_compatible(store: Store) -> None:
    response = TestClient(service.app).post(
        "/api/decks", json={"deck_id": "2026-11-01-browser", "title": "Browser"}
    )
    assert response.status_code == 200
    assert response.json()["id"] == "2026-11-01-browser"


def test_replacement_directory_cannot_claim_recorded_creation(store: Store) -> None:
    deck_id = "2026-11-01-replaced"
    _ = store.create(deck_id, "Original", request_id="creation-replace-001")
    shutil.rmtree(store.folder(deck_id))
    shutil.copytree(store.folder(TEMPLATE_ID), store.folder(deck_id))
    with pytest.raises(ConflictError, match="identity"):
        store.create(deck_id, "Original", request_id="creation-replace-001")


def test_unrecorded_folder_after_database_failure_requires_reconciliation(store: Store) -> None:
    import sqlite3

    with store.connect() as db:
        _ = db.execute(
            "CREATE TRIGGER fail_creation BEFORE INSERT ON creation_receipts "
            "BEGIN SELECT RAISE(ABORT, 'simulated database failure'); END"
        )
    deck_id = "2026-11-01-interrupted"
    with pytest.raises(sqlite3.IntegrityError, match="simulated"):
        store.create(deck_id, "Interrupted", request_id="creation-failure-001")
    assert store.folder(deck_id).exists()
    with pytest.raises(ConflictError, match="ownership"):
        store.create(deck_id, "Interrupted", request_id="creation-failure-001")
