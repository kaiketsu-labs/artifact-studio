"""Hosted capabilities grant only short-lived revision-scoped preview resources."""

import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from artifact_studio.artifact_preview import preview_document, preview_headers
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.hosted_preview import HostedPreviewService, hosted_preview_router
from artifact_studio.storage import Store


def test_capability_files_verify_only_returned_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ArtifactStore(Store(tmp_path / "workspace", tmp_path / "state/review.sqlite3"))
    source = tmp_path / "source"
    source.mkdir()
    (source / "tiny.txt").write_bytes(b"ok")
    (source / "large.bin").write_bytes(b"x" * (8 * 1024 * 1024))
    project = store.create_project("Fictional resource budget")
    artifact = store.register_artifact(project.id, "Budget", source, "tiny.txt")
    service = HostedPreviewService(store, "https://studio.example", "https://preview.example")
    capability = service.issue(artifact.id, artifact.revision)
    target = store.resolve_file(artifact.id, "tiny.txt", artifact.revision)
    (target.parent / "undeclared.txt").write_text("not in this revision")

    def full_inventory(_path: Path) -> None:
        raise AssertionError("A resource request must not hash sibling assets")

    monkeypatch.setattr("artifact_studio.artifacts.tree_inventory", full_inventory)
    app = FastAPI()
    app.include_router(hosted_preview_router(service))
    client = TestClient(app, base_url="https://preview.example")
    files = f"{capability}/api/artifacts/{artifact.id}/revisions/1/files"
    for _ in range(8):
        response = client.head(f"{files}/tiny.txt")
        assert response.status_code == 200
        assert response.headers["content-length"] == "2"
        assert response.content == b""
    assert client.get(f"{files}/tiny.txt").content == b"ok"
    partial = client.get(f"{files}/tiny.txt", headers={"range": "bytes=1-1"})
    assert partial.status_code == 206
    assert partial.content == b"k"
    assert partial.headers["content-range"] == "bytes 1-1/2"
    assert client.get(f"{files}/tiny.txt", headers={"range": "bytes=-1"}).content == b"k"
    assert client.get(f"{files}/tiny.txt", headers={"range": "bytes=9-"}).status_code == 416
    assert client.get(f"{files}/tiny.txt", headers={"range": "bytes=-0"}).status_code == 416
    assert client.get(f"{files}/tiny.txt", headers={"range": "bytes=0-0,1-1"}).content == b"ok"
    assert (
        client.get(
            f"{files}/tiny.txt", headers={"range": "bytes=1-1", "if-range": '"old"'}
        ).status_code
        == 200
    )
    assert client.get(f"{files}/undeclared.txt").status_code == 404
    target.write_bytes(b"NO")
    assert client.get(f"{files}/tiny.txt").status_code == 404
    target.unlink()
    target.symlink_to(source / "tiny.txt")
    assert client.get(f"{files}/tiny.txt").status_code == 404
    target.unlink()
    if hasattr(os, "mkfifo"):
        os.mkfifo(target)
        assert client.get(f"{files}/tiny.txt").status_code == 404


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
        store, "https://studio.example", "https://preview.example", ttl=60
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
    for destination in (None, "document", "object", "embed"):
        response = client.get(
            f"{files}/index.html",
            headers={"sec-fetch-dest": destination} if destination else {},
        )
        assert response.headers["content-disposition"].startswith("attachment;")
    embedded = client.get(f"{files}/index.html", headers={"sec-fetch-dest": "iframe"})
    assert embedded.status_code == 200
    assert "content-disposition" not in embedded.headers
    document = preview_document(store, artifact.id, service.public_url, 1, root)
    assert f"{root}/api/artifacts/{artifact.id}/revisions/1/files/" in document
    assert f"{root}/web/artifact-bridge.js" in document
    assert 'data-artifact-parent-origin="https://studio.example"' in document
    assert (
        "frame-ancestors https://studio.example"
        in preview_headers(artifact.id, 1, service.public_url, root)["Content-Security-Policy"]
    )
    import time
    from unittest.mock import patch

    with patch("artifact_studio.hosted_preview.time.time", return_value=time.time() + 63):
        assert client.get(f"{files}/app.js").status_code == 404
