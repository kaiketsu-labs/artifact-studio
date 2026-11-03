"""Exercise recovery HTTP boundaries with fictional workspaces and streamed archives."""

from pathlib import Path
from typing import Protocol, cast

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.libraries import Libraries, LibraryInput
from artifact_studio.recovery_api import recovery_routes
from artifact_studio.storage import ConflictError, Store
from artifact_studio.workspace_recovery import WorkspaceRecovery
from tests.test_workspace_recovery import populate, snapshot


@pytest.fixture
def context(tmp_path: Path) -> tuple[Store, TestClient]:
    """Keep route tests entirely outside the real workspace and running instance."""
    store = Store(tmp_path / "Current", tmp_path / "state" / "review.sqlite3")
    app = FastAPI()

    @app.exception_handler(ValueError)
    def invalid(_request: Request, error: ValueError) -> JSONResponse:
        """Match the application's explicit invalid-input and concurrency boundary."""
        return JSONResponse(
            {"error": str(error)}, status_code=409 if isinstance(error, ConflictError) else 400
        )

    app.include_router(recovery_routes(lambda: store))
    return store, TestClient(app)


class JsonResponse(Protocol):
    """The common JSON boundary across the installed test-client transport versions."""

    def json(self) -> object:
        """Decode one response body."""
        ...


def object_json(response: JsonResponse) -> dict[str, object]:
    """Decode a validated HTTP result without implicit Any leaking through tests."""
    return cast(dict[str, object], response.json())


def test_archive_download_stream_restore_and_original_untouched(
    context: tuple[Store, TestClient], tmp_path: Path
) -> None:
    """Round-trip private state locally and restore files without switching the server."""
    store, client = context
    _, _, artifact_id = populate(store, tmp_path)
    original = ArtifactStore(store).get_artifact(artifact_id)
    download = client.get("/api/workspace/archive")
    assert download.status_code == 200 and download.content.startswith(b"PK")
    assert download.headers["content-type"] == "application/zip"
    assert "attachment" in download.headers["content-disposition"]
    prepared = client.post(
        "/api/workspace/recovery/upload?name=Recovered",
        content=(
            download.content[index : index + 8192]
            for index in range(0, len(download.content), 8192)
        ),
        headers={"Content-Type": "application/zip"},
    )
    assert prepared.status_code == 200, prepared.text
    data = object_json(prepared)
    preview = cast(dict[str, object], data["preview"])
    destination = Path(str(data["destination"]))
    assert destination == tmp_path / "Recovered" and preview["can_restore"] is True
    assert not (destination / "projects").exists()
    restored = client.post(
        f"/api/workspace/recovery/{data['token']}/restore",
        json={"content_hash": preview["content_hash"]},
    )
    assert restored.status_code == 200, restored.text
    recovered = Store(destination, Path(str(data["database"])))
    assert ArtifactStore(recovered).get_artifact(artifact_id).approved
    assert ArtifactStore(store).get_artifact(artifact_id) == original
    assert recovered.root != store.root


def test_registered_current_source_restore_rebuilds_registry_without_private_history(
    context: tuple[Store, TestClient], tmp_path: Path
) -> None:
    """Only registered sources are addressable and their current files rediscover as artifacts."""
    store, client = context
    _, _, artifact_id = populate(store, tmp_path)
    source = snapshot(store, tmp_path / "backup")
    library = Libraries(store).register(LibraryInput(location=str(source)))
    response = client.post(
        "/api/workspace/recovery/library",
        json={"library_id": library.id, "name": "Current content recovery"},
    )
    assert response.status_code == 200, response.text
    data = object_json(response)
    preview = cast(dict[str, object], data["preview"])
    assert preview["projects"] == 2 and preview["folders"] == 1
    assert any("private" in str(warning) for warning in cast(list[object], preview["warnings"]))
    restored = client.post(
        f"/api/workspace/recovery/{data['token']}/restore",
        json={"content_hash": preview["content_hash"]},
    )
    assert restored.status_code == 200, restored.text
    recovered = Store(Path(str(data["destination"])), Path(str(data["database"])))
    assert not ArtifactStore(recovered).get_artifact(artifact_id).approved
    assert (recovered.root / "project-tree.json").is_file()


def test_cancel_and_existing_destination_are_safe(
    context: tuple[Store, TestClient], tmp_path: Path
) -> None:
    """Reject path traversal and existing directories, then clean only untouched preparations."""
    store, client = context
    _ = populate(store, tmp_path)
    archive = tmp_path / "archive.zip"
    _ = WorkspaceRecovery(store).export_archive(archive)
    for name in ("../outside", "/tmp/outside", ".hidden", "Current"):
        invalid = client.post(
            "/api/workspace/recovery/upload", params={"name": name}, content=archive.read_bytes()
        )
        assert invalid.status_code in {400, 409}
    prepared = client.post(
        "/api/workspace/recovery/upload?name=Cancelled", content=archive.read_bytes()
    )
    assert prepared.status_code == 200, prepared.text
    data = object_json(prepared)
    assert client.delete(f"/api/workspace/recovery/{data['token']}").status_code == 200
    assert not (tmp_path / "Cancelled").exists()
    assert (
        client.post(
            f"/api/workspace/recovery/{data['token']}/restore",
            json={"content_hash": cast(dict[str, object], data["preview"])["content_hash"]},
        ).status_code
        == 409
    )


def test_changed_destination_rejected_and_cancel_preserves_added_files(
    context: tuple[Store, TestClient], tmp_path: Path
) -> None:
    """Late human writes prevent publication and are never deleted by cancellation."""
    store, client = context
    _ = populate(store, tmp_path)
    archive = client.get("/api/workspace/archive").content
    response = client.post("/api/workspace/recovery/upload?name=Changed", content=archive)
    assert response.status_code == 200, response.text
    data = object_json(response)
    human = Path(str(data["destination"])) / "human.txt"
    _ = human.write_text("Keep this")
    restored = client.post(
        f"/api/workspace/recovery/{data['token']}/restore",
        json={"content_hash": cast(dict[str, object], data["preview"])["content_hash"]},
    )
    assert restored.status_code == 409
    _ = client.delete(f"/api/workspace/recovery/{data['token']}")
    assert human.read_text() == "Keep this"


def test_download_cleanup_on_invalid_range_and_cross_site_guard(
    context: tuple[Store, TestClient], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clean private exports on early responses and reject cross-site expensive downloads."""
    from artifact_studio.workspace_recovery import ArchiveSummary

    store, client = context
    _ = populate(store, tmp_path)
    directories: list[Path] = []
    original = WorkspaceRecovery.export_archive

    def record(service: WorkspaceRecovery, destination: Path) -> ArchiveSummary:
        """Observe only generated paths, retaining the real archive implementation."""
        directories.append(destination.parent)
        return original(service, destination)

    monkeypatch.setattr(WorkspaceRecovery, "export_archive", record)
    denied = client.get("/api/workspace/archive", headers={"Sec-Fetch-Site": "cross-site"})
    assert denied.status_code == 400 and not directories
    failed = client.get("/api/workspace/archive", headers={"Range": "bytes=not-a-range"})
    assert failed.status_code == 400 and directories
    assert all(not directory.exists() for directory in directories)
    assert client.get("/api/workspace/archive").status_code == 200
    assert all(not directory.exists() for directory in directories)


def test_restore_confirmation_retry_and_normal_shutdown_cleanup(
    context: tuple[Store, TestClient], tmp_path: Path
) -> None:
    """Lost acknowledgements do not repeat publication and abandoned previews are cleaned."""
    store, client = context
    _ = populate(store, tmp_path)
    with client:
        archive = client.get("/api/workspace/archive").content
        preview_response = client.post(
            "/api/workspace/recovery/upload?name=Retried", content=archive
        )
        assert preview_response.status_code == 200, preview_response.text
        prepared = object_json(preview_response)
        path = f"/api/workspace/recovery/{prepared['token']}/restore"
        body = {"content_hash": cast(dict[str, object], prepared["preview"])["content_hash"]}
        confirmed = client.post(path, json=body)
        assert confirmed.status_code == 200
        retried = client.post(path, json=body)
        assert retried.status_code == 200 and retried.text == confirmed.text
        abandoned = client.post("/api/workspace/recovery/upload?name=Abandoned", content=archive)
        assert abandoned.status_code == 200, abandoned.text
        assert (tmp_path / "Abandoned").exists()
    assert not (tmp_path / "Abandoned").exists()
    assert (tmp_path / "Retried" / "projects").exists()
