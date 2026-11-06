"""Disabled source delivery hides inactive failures without losing retry receipts."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from artifact_studio import app as service
from artifact_studio.autosync import AutoSync, SyncStatus
from artifact_studio.libraries import Libraries, Library, SyncResult
from artifact_studio.storage import Store


def test_disabled_conflict_status_preserves_receipt_and_retry_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Status reads cannot upload or erase a conflict; explicit re-enable queues retry."""
    store = Store(tmp_path / "collection", tmp_path / "state" / "review.sqlite3")
    worker = AutoSync(store)
    library = Library(
        id="fictional-backup",
        kind="github",
        location="fictional/backup",
        path=str(tmp_path / "cache"),
        sync_enabled=True,
        auto_sync_enabled=True,
    )
    with store.connect() as db:
        _ = db.execute(
            "INSERT INTO libraries VALUES (?, ?)", (library.id, library.model_dump_json())
        )
    receipt = SyncStatus(
        library_id=library.id,
        phase="error",
        observed="local-fingerprint",
        synced="previous-fingerprint",
        last_synced_at="2026-11-01T00:00:00+00:00",
        commit="a" * 40,
        error="Remote changes need review before syncing: projects/example/project.json",
    )
    worker.save(receipt)
    uploads: list[str] = []

    def sync(self: Libraries, identifier: str, *, automatic: bool = False) -> SyncResult:
        uploads.append(identifier)
        raise AssertionError("Status and permission changes must not upload")

    def verify_repository(self: Libraries, location: str, private: bool) -> None:
        """Keep this status journey entirely local."""

    monkeypatch.setattr(Libraries, "sync", sync)
    monkeypatch.setattr(Libraries, "verify_repository", verify_repository)
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    disabled = client.post(
        f"/api/libraries/{library.id}/settings", json={"auto_sync_enabled": False}
    )
    assert disabled.status_code == 200
    status = client.get("/api/library-sync").json()[0]
    assert status["phase"] == "disabled" and status["error"] == ""
    assert status["commit"] == receipt.commit
    assert status["last_synced_at"] == receipt.last_synced_at
    worker.tick(100)
    assert worker.state(library.id) == receipt
    assert not uploads
    enabled = client.post(f"/api/libraries/{library.id}/settings", json={"auto_sync_enabled": True})
    assert enabled.status_code == 200
    queued = worker.state(library.id)
    assert queued.phase == "pending" and queued.error == ""
    assert queued.observed == queued.synced == ""
    assert queued.commit == receipt.commit and queued.last_synced_at == receipt.last_synced_at
    assert not uploads
