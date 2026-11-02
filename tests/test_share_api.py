"""Exercise local share routes against isolated fictional workspaces."""

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from artifact_studio import app as service
from artifact_studio import share_api
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.storage import Store


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Store, str, str]:
    """Install one isolated project and artifact into the application."""
    store = Store(tmp_path / "workspace", tmp_path / "state" / "review.sqlite3")
    artifacts = ArtifactStore(store)
    project = artifacts.create_project("Example")
    source = tmp_path / "source.html"
    source.write_text("<h1>Original</h1>")
    artifact = artifacts.register_artifact(project.id, "Example", source, "index.html")
    monkeypatch.setattr(service, "store", store)
    return store, project.id, artifact.id


def test_immutable_download_and_stale_hash(workspace: tuple[Store, str, str]) -> None:
    """Prepared bytes remain stable and stale review tokens cannot prepare new exports."""
    store, _, artifact_id = workspace
    artifact = ArtifactStore(store).get_artifact(artifact_id)
    client = TestClient(service.app)
    response = client.post(
        "/api/shares",
        json={"scope": "artifact", "id": artifact_id, "expected_hash": artifact.content_hash},
    )
    assert response.status_code == 200
    download = str(response.json()["download_url"])
    (Path(artifact.source_path) / "index.html").write_text("<h1>Changed</h1>")
    assert client.get(download).content == b"<h1>Original</h1>"
    assert (
        client.post(
            "/api/shares",
            json={"scope": "artifact", "id": artifact_id, "expected_hash": artifact.content_hash},
        ).status_code
        == 409
    )


def test_origin_and_outlook_idempotence(
    workspace: tuple[Store, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a same-origin request opens one unsent draft with the prepared bytes."""
    _, _, artifact_id = workspace
    calls: list[bytes] = []

    def fake_draft(path: Path) -> None:
        """Capture the attachment passed to the draft handoff."""
        calls.append(path.read_bytes())

    monkeypatch.setattr(share_api, "create_outlook_draft", fake_draft)
    client = TestClient(service.app)
    token = str(
        client.post("/api/shares", json={"scope": "artifact", "id": artifact_id}).json()["token"]
    )
    url = f"/api/shares/{token}/outlook"
    assert client.post(url, headers={"Origin": "https://foreign.example"}).status_code == 403
    assert calls == []
    assert client.post(url).json() == {"opened": True}
    assert client.post(url).json() == {"opened": True}
    assert calls == [b"<h1>Original</h1>"]


def test_expiry_and_workspace_scope(
    workspace: tuple[Store, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tokens cannot cross workspaces and become unavailable after expiry."""
    store, _, artifact_id = workspace
    client = TestClient(service.app)
    monkeypatch.setattr(share_api, "SHARE_TTL", -1)
    response = client.post("/api/shares", json={"scope": "artifact", "id": artifact_id})
    assert client.get(str(response.json()["download_url"])).status_code == 404
    monkeypatch.setattr(share_api, "SHARE_TTL", 3600)
    response = client.post("/api/shares", json={"scope": "artifact", "id": artifact_id})
    url = str(response.json()["download_url"])
    monkeypatch.setattr(
        service, "store", Store(tmp_path / "other", tmp_path / "other-state" / "review.sqlite3")
    )
    assert client.get(url).status_code == 404
    monkeypatch.setattr(service, "store", store)
    assert client.get(url).status_code == 200


def test_original_zip_is_not_unpacked_in_project(
    workspace: tuple[Store, str, str], tmp_path: Path
) -> None:
    """Treat an uploaded ZIP as audience content, never as a generated bundle."""
    store, project_id, _ = workspace
    source = tmp_path / "uploaded.zip"
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("../outside.txt", "payload")
    source.write_bytes(output.getvalue())
    artifacts = ArtifactStore(store)
    _ = artifacts.register_artifact(project_id, "Uploaded", source, "uploaded.zip")
    response = TestClient(service.app).post(
        "/api/shares", json={"scope": "project", "id": project_id}
    )
    assert response.status_code == 200
    content = TestClient(service.app).get(str(response.json()["download_url"])).content
    with ZipFile(BytesIO(content)) as archive:
        name = next(name for name in archive.namelist() if name.endswith("Uploaded.zip"))
        assert archive.read(name) == output.getvalue()
        assert "../outside.txt" not in archive.namelist()
