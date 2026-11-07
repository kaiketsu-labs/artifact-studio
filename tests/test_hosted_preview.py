"""Hosted capabilities grant only short-lived revision-scoped preview resources."""

from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from artifact_studio.artifact_preview import preview_document, preview_headers
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.hosted_preview import HostedPreviewService, hosted_preview_router
from artifact_studio.storage import Store


def test_hosted_capability_scope_and_expiry(tmp_path: Path) -> None:
    store = ArtifactStore(Store(tmp_path / "workspace", tmp_path / "state" / "review.sqlite3"))
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.html").write_text('<script type="module" src="app.js"></script>')
    (source / "app.js").write_text('import "./other.js"')
    (source / "other.js").write_text("export default 1")
    project = store.create_project("Preview")
    artifact = store.register_artifact(project.id, "Bundle", source, "index.html")
    service = HostedPreviewService(
        store, "https://studio.example", "https://preview.example", ttl=1
    )
    root = service.issue(artifact.id, artifact.revision)
    path = urlsplit(root).path
    files = f"{path}/api/artifacts/{artifact.id}/revisions/1/files"
    app = FastAPI()
    app.include_router(hosted_preview_router(service))
    client = TestClient(app, base_url="https://preview.example")
    response = client.get(f"{files}/app.js", headers={"Origin": "null"})
    assert response.status_code == 200
    assert response.text == 'import "./other.js"'
    assert response.headers["access-control-allow-origin"] == "null"
    assert client.head(f"{files}/other.js").status_code == 200
    assert client.get(f"{path}/web/artifact-bridge.js").status_code == 200
    assert client.get(f"{path}/web/index.html").status_code == 404
    assert client.get(f"{path}/api/artifacts/other/revisions/1/files/app.js").status_code == 404
    assert (
        client.get(f"{path}/api/artifacts/{artifact.id}/revisions/2/files/app.js").status_code
        == 404
    )
    assert client.get(f"{files}/%2e%2e/index.html").status_code == 404
    assert client.post(f"{files}/app.js").status_code == 405
    assert client.get(f"{path}x/web/artifact-bridge.js").status_code == 404
    document = preview_document(store, artifact.id, service.public_url, 1, root)
    assert f"{root}/api/artifacts/{artifact.id}/revisions/1/files/" in document
    assert f"{root}/web/artifact-bridge.js" in document
    assert (
        "frame-ancestors https://studio.example"
        in preview_headers(artifact.id, 1, service.public_url, root)["Content-Security-Policy"]
    )
    import time
    from unittest.mock import patch

    with patch("artifact_studio.hosted_preview.time.time", return_value=time.time() + 3):
        assert client.get(f"{files}/app.js").status_code == 404
