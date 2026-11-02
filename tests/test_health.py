"""Verify status monitoring is independent from private presentation data."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from artifact_studio import app as service
from artifact_studio.storage import Store


def test_health_does_not_read_decks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Report readiness without reading slides, chat, rules or sources."""
    store = Store(tmp_path, tmp_path / "review.sqlite3")
    monkeypatch.setattr(service, "store", store)

    def unexpected_read(*_args: object, **_kwargs: object) -> None:
        """Fail if a status check attempts to load workspace content."""
        pytest.fail("Health checks must not load presentation content.")

    monkeypatch.setattr(store, "list_decks", unexpected_read)
    monkeypatch.setattr(store, "authoring_instructions", unexpected_read)
    client = TestClient(service.app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"service": "artifact-studio", "status": "ready", "version": "0.1.0"}
    assert response.headers["cache-control"] == "no-store"
