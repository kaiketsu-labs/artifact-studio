"""Report real SQLite lock contention without hiding database faults."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from artifact_studio import app as service
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.storage import Store


def test_busy_database_returns_retryable_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real lock error becomes a retryable HTTP response rather than an ASGI traceback."""
    with sqlite3.connect(tmp_path / "lock.sqlite3") as first:
        _ = first.execute("CREATE TABLE example (value INTEGER)")
        _ = first.execute("BEGIN IMMEDIATE")
        with (
            sqlite3.connect(tmp_path / "lock.sqlite3", timeout=0) as second,
            pytest.raises(sqlite3.OperationalError) as raised,
        ):
            _ = second.execute("INSERT INTO example VALUES (1)")
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", store)

    def busy(_registry: ArtifactStore, **_kwargs: object) -> None:
        raise raised.value

    monkeypatch.setattr(ArtifactStore, "list_projects", busy)
    client = TestClient(service.app, base_url="http://127.0.0.1")
    response = client.get("/api/hub")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert "Try again" in response.json()["detail"]


def test_unexpected_sqlite_error_is_not_relabelled_as_contention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Programming and storage faults keep their original failure semantics."""
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", store)

    def invalid(_registry: ArtifactStore, **_kwargs: object) -> None:
        with sqlite3.connect(":memory:") as database:
            _ = database.execute("SELECT * FROM nonexistent")

    monkeypatch.setattr(ArtifactStore, "list_projects", invalid)
    client = TestClient(service.app, base_url="http://127.0.0.1")
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        _ = client.get("/api/hub")
