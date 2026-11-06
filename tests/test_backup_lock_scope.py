"""Verify backup staging and compression use a coherent, nonblocking snapshot."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from zipfile import ZipFile, ZipInfo

import pytest

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.review import ReviewHookInput, Reviews
from artifact_studio.storage import ConflictError, Store
from artifact_studio.workspace_recovery import ARCHIVE_MANIFEST, WorkspaceArchive, WorkspaceRecovery
from tests.test_workspace_recovery import populate, workspace


def test_compression_releases_writer_and_restores_staged_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Application edits proceed while compression uses the earlier coherent snapshot."""
    store = workspace(tmp_path / "original")
    _, _, artifact_id = populate(store, tmp_path)
    file = store.root / "fictional-deck" / "theme.css"
    original = file.read_text()
    started, resume = Event(), Event()
    write = ZipFile.writestr

    def hold_compression(
        archive: ZipFile,
        name: str | ZipInfo,
        data: str | bytes,
        compress_type: int | None = None,
        compresslevel: int | None = None,
    ) -> None:
        write(archive, name, data, compress_type, compresslevel)
        if name == ARCHIVE_MANIFEST:
            started.set()
            if not resume.wait(20):
                raise TimeoutError("Compression was not released")

    monkeypatch.setattr(ZipFile, "writestr", hold_compression)
    recovery = WorkspaceRecovery(store)
    archive = tmp_path / "backup.zip"
    body = ReviewHookInput(harness="codex", session_id="fictional", event="Stop")

    def edit() -> None:
        with store.editing("fictional-deck"):
            _ = file.write_text("body { color: orange; }")

    with ThreadPoolExecutor(max_workers=5) as executor:
        backup = executor.submit(recovery.export_archive, archive)
        assert started.wait(10)
        try:
            operations = [
                executor.submit(ArtifactStore(store).get_artifact, artifact_id),
                executor.submit(Reviews(store).bind, body),
                executor.submit(Reviews(store).pending, body),
                executor.submit(edit),
            ]
            for operation in operations:
                _ = operation.result(timeout=5)
        finally:
            resume.set()
        summary = backup.result(timeout=20)
    assert file.read_text() != original
    target = workspace(tmp_path / "restored")
    restored = WorkspaceRecovery(target)
    preview = restored.preview(archive, mode="archive")
    _ = restored.restore(archive, preview.content_hash, mode="archive")
    assert (target.root / "fictional-deck" / "theme.css").read_text() == original
    assert ArtifactStore(target).get_artifact(artifact_id).id == artifact_id
    assert summary.fingerprint


def test_file_changed_after_inventory_aborts_without_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An external edit between inventory and staging cannot enter a mixed backup."""
    from artifact_studio import workspace_recovery

    store = workspace(tmp_path / "original")
    file = store.root / "fictional.txt"
    _ = file.write_text("before")
    original = workspace_recovery._copy_verified  # pyright: ignore[reportPrivateUsage]

    def change_before_copy(
        source: Path, destination: Path, expected: workspace_recovery.RecoveryFile
    ) -> None:
        if source == file:
            _ = file.write_text("after!")
        original(source, destination, expected)

    monkeypatch.setattr(workspace_recovery, "_copy_verified", change_before_copy)
    destination = tmp_path / "backup.zip"
    with pytest.raises(ConflictError):
        _ = WorkspaceRecovery(store).export_archive(destination)
    assert not destination.exists()


@pytest.mark.parametrize("change", ["content", "permissions"])
def test_file_changed_after_staging_aborts_without_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """A late external edit is detected before the staged snapshot is released."""
    from artifact_studio import workspace_recovery

    store = workspace(tmp_path / "original")
    file = store.root / "fictional.txt"
    _ = file.write_text("before")
    original = workspace_recovery._copy_verified  # pyright: ignore[reportPrivateUsage]

    def change_after_copy(
        source: Path, destination: Path, expected: workspace_recovery.RecoveryFile
    ) -> None:
        original(source, destination, expected)
        if source == file:
            if change == "permissions":
                file.chmod(0o600 if expected.mode != 0o600 else 0o644)
            else:
                _ = file.write_text("after!")

    monkeypatch.setattr(workspace_recovery, "_copy_verified", change_after_copy)
    destination = tmp_path / "backup.zip"
    with pytest.raises(ConflictError):
        _ = WorkspaceRecovery(store).export_archive(destination)
    assert not destination.exists()


def test_host_lock_files_are_excluded_when_state_is_collection_root(tmp_path: Path) -> None:
    """Local mutex files do not enter an archive or affect its content identity."""
    root = tmp_path / "collection"
    store = Store(root, root / "review.sqlite3")
    for name in (".auto-sync.lock", ".library-operations.lock"):
        _ = (root / name).write_text("local lock")
    recovery = WorkspaceRecovery(store)
    archive = tmp_path / "backup.zip"
    first = recovery.export_archive(archive)
    with ZipFile(archive) as contents:
        manifest = WorkspaceArchive.model_validate_json(contents.read(ARCHIVE_MANIFEST))
    assert not {".auto-sync.lock", ".library-operations.lock"} & {
        item.path for item in manifest.files
    }
    _ = (root / ".auto-sync.lock").write_text("changed lock")
    assert recovery.export_archive_if_changed(
        tmp_path / "unchanged.zip", first.fingerprint
    ).unchanged


def test_source_redirected_after_staging_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A late symlink is rejected even if its target has the same bytes."""
    from artifact_studio import workspace_recovery

    store = workspace(tmp_path / "original")
    source = store.root / "example.txt"
    outside = tmp_path / "outside.txt"
    _ = source.write_text("same bytes")
    _ = outside.write_text("same bytes")
    copy = workspace_recovery._copy_verified  # pyright: ignore[reportPrivateUsage]

    def redirect_after_copy(
        path: Path, destination: Path, expected: workspace_recovery.RecoveryFile
    ) -> None:
        copy(path, destination, expected)
        if path == source:
            path.unlink()
            path.symlink_to(outside)

    monkeypatch.setattr(workspace_recovery, "_copy_verified", redirect_after_copy)
    destination = tmp_path / "backup.zip"
    with pytest.raises(ConflictError):
        _ = WorkspaceRecovery(store).export_archive(destination)
    assert not destination.exists()
