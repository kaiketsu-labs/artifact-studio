"""Verify coherent source snapshots without holding database locks during remote operations."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from artifact_studio import libraries as library_module
from artifact_studio.artifact_models import ArtifactFileInput, ArtifactManifest
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.autosync import AutoSync
from artifact_studio.libraries import Libraries, Library
from artifact_studio.storage import Store
from artifact_studio.workspace_recovery import (
    PORTABLE_METADATA,
    PortableWorkspace,
    WorkspaceRecovery,
)
from tests.test_workspace_recovery import populate


@pytest.fixture
def sync_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Libraries, Library, tuple[str, str, str]]:
    """Replace only remote transport while retaining real local workspace and copy behavior."""
    store = Store(tmp_path / "workspace", tmp_path / "state/review.sqlite3")
    ids = populate(store, tmp_path)
    libraries = Libraries(store)
    checkout = libraries.cache / "fictional-checkout"
    checkout.mkdir()
    library = Library(
        id="fictional-backup",
        kind="github",
        location="fictional/backup",
        path=str(checkout),
        sync_enabled=True,
    )
    with store.connect() as db:
        _ = db.execute(
            "INSERT INTO libraries VALUES (?,?)", (library.id, library.model_dump_json())
        )

    def verify(_repository: str, _sync: bool) -> None:
        """Keep repository visibility checks local to the test."""

    def refresh(_library: Library) -> None:
        """Keep repository refresh local to the test."""

    def git(_folder: Path, *args: str) -> str:
        """Return deterministic transport responses without invoking Git or a network."""
        if args[:2] == ("branch", "--show-current"):
            return "main"
        if args[:2] == ("rev-parse", "HEAD"):
            return "fictional-commit"
        return "snapshot.json" if args[:2] == ("diff", "--cached") else ""

    monkeypatch.setattr(libraries, "verify_repository", verify)
    monkeypatch.setattr(libraries, "refresh_checkout", refresh)
    monkeypatch.setattr(library_module, "git", git)
    return libraries, library, ids


def test_sync_freezes_manifests_and_files_before_concurrent_app_edit(
    sync_workspace: tuple[Libraries, Library, tuple[str, str, str]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An app edit waits for the complete freeze, then proceeds before remote operations start."""
    libraries, library, (_, _, artifact_id) = sync_workspace
    store = libraries.store
    registry = ArtifactStore(store)
    artifact = registry.get_artifact(artifact_id)
    copying = threading.Event()
    attempting = threading.Event()
    edited = threading.Event()
    copy = library_module.shutil.copy2

    def observe(source: Path, target: Path) -> Path:
        """Allow a concurrent writer to attempt its transaction during the first source copy."""
        if source.is_relative_to(store.root) and not copying.is_set():
            copying.set()
            assert attempting.wait(5)
            assert not edited.is_set()
        return Path(copy(source, target))

    def edit() -> None:
        """Perform a real artifact revision while source freezing owns the database lock."""
        assert copying.wait(5)
        attempting.set()
        _ = registry.revise_artifact(
            artifact_id,
            [ArtifactFileInput(path="index.html", text="<h1>Later edit</h1>")],
            artifact.content_hash,
            "concurrent-freeze-001",
        )
        edited.set()

    def network(_repository: str, _sync: bool) -> None:
        """Require the blocked writer to finish before simulating any network operation."""
        assert edited.wait(5)
        with sqlite3.connect(store.database, timeout=0) as db:
            _ = db.execute("BEGIN IMMEDIATE")

    monkeypatch.setattr(library_module.shutil, "copy2", observe)
    monkeypatch.setattr(libraries, "verify_repository", network)
    with ThreadPoolExecutor(max_workers=1) as executor:
        writer = executor.submit(edit)
        _ = libraries.sync(library.id)
        writer.result(timeout=5)
    checkout = Path(library.path)
    manifest_path = next(checkout.glob(f"projects/*/artifacts/{artifact_id}/artifact.json"))
    manifest = ArtifactManifest.model_validate_json(manifest_path.read_text())
    assert manifest.revision == 2
    assert (manifest_path.parent / "files/index.html").read_text() == "<h1>Current</h1>"
    assert registry.get_artifact(artifact_id).revision == 3
    recovery = WorkspaceRecovery(
        Store(tmp_path / "recovered", tmp_path / "recovered-state/review.sqlite3")
    )
    assert recovery.preview(checkout).can_restore


def test_sync_refreshes_legacy_membership_after_preparation_race(
    sync_workspace: tuple[Libraries, Library, tuple[str, str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB-only membership changes between preparation and locking enter the frozen metadata."""
    libraries, library, (origin_id, _, _) = sync_workspace
    write = WorkspaceRecovery.write_portable_metadata
    moved = False

    def prepare(self: WorkspaceRecovery, *, synchronize: bool = True) -> Path:
        """Change membership immediately after the initial out-of-transaction preparation."""
        nonlocal moved
        result = write(self, synchronize=synchronize)
        if synchronize and not moved:
            moved = True
            _ = ArtifactStore(self.store).set_artifact_project("fictional-deck", origin_id)
        return result

    monkeypatch.setattr(WorkspaceRecovery, "write_portable_metadata", prepare)
    _ = libraries.sync(library.id)
    metadata = PortableWorkspace.model_validate_json(
        (Path(library.path) / PORTABLE_METADATA).read_text()
    )
    assert moved and metadata.legacy_projects["fictional-deck"] == origin_id


def test_fingerprint_does_not_load_artifact_bundles_or_rewrite_metadata(
    sync_workspace: tuple[Libraries, Library, tuple[str, str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated observation remains streaming and leaves unchanged portable metadata untouched."""
    libraries, _, _ = sync_workspace
    path = libraries.store.root / PORTABLE_METADATA
    before = path.stat()

    def unexpected(_self: ArtifactStore, _artifact_id: str) -> None:
        """Fail if an observation attempts full artifact synchronization or bundle reads."""
        raise AssertionError("Fingerprinting must not load complete artifact bundles.")

    monkeypatch.setattr(ArtifactStore, "get_artifact", unexpected)
    worker = AutoSync(libraries.store)
    first = worker.fingerprint()
    assert worker.fingerprint() == first
    assert path.stat().st_ino == before.st_ino and path.stat().st_mtime_ns == before.st_mtime_ns


def test_actual_sync_synchronizes_saved_external_artifact_edits(
    sync_workspace: tuple[Libraries, Library, tuple[str, str, str]],
    tmp_path: Path,
) -> None:
    """An actual upload refreshes owned manifests once before freezing a saved external edit."""
    libraries, library, (_, _, artifact_id) = sync_workspace
    artifact = ArtifactStore(libraries.store).get_artifact(artifact_id)
    _ = (Path(artifact.source_path) / "index.html").write_text("<h1>External save</h1>")
    _ = libraries.sync(library.id)
    checkout = Path(library.path)
    manifest_path = next(checkout.glob(f"projects/*/artifacts/{artifact_id}/artifact.json"))
    manifest = ArtifactManifest.model_validate_json(manifest_path.read_text())
    assert manifest.revision == 3
    assert (manifest_path.parent / "files/index.html").read_text() == "<h1>External save</h1>"
    recovery = WorkspaceRecovery(
        Store(tmp_path / "recovered", tmp_path / "recovered-state/review.sqlite3")
    )
    assert recovery.preview(checkout).can_restore


def test_direct_cross_file_edit_aborts_before_remote_upload(
    sync_workspace: tuple[Libraries, Library, tuple[str, str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A direct save between manifest and source copying cannot publish a mixed artifact bundle."""
    libraries, library, (_, _, artifact_id) = sync_workspace
    artifact = ArtifactStore(libraries.store).get_artifact(artifact_id)
    source = Path(artifact.source_path) / "index.html"
    copy = library_module.shutil.copy2
    changed = False

    def mutate(original: Path, target: Path) -> Path:
        """Save new source after its older manifest has already entered staging."""
        nonlocal changed
        result = Path(copy(original, target))
        if original.name == "artifact.json" and original.is_relative_to(libraries.store.root):
            _ = source.write_text("<h1>Saved between files</h1>")
            changed = True
        return result

    def unexpected(_repository: str, _sync: bool) -> None:
        """Fail if an inconsistent snapshot reaches remote transport."""
        raise AssertionError("An inconsistent snapshot must abort before transport.")

    monkeypatch.setattr(library_module.shutil, "copy2", mutate)
    monkeypatch.setattr(libraries, "verify_repository", unexpected)
    with pytest.raises(ValueError, match="changing during sync"):
        _ = libraries.sync(library.id)
    assert changed and not list(Path(library.path).iterdir())


def test_reserved_full_archive_suffix_is_excluded_from_owned_snapshot_files(
    sync_workspace: tuple[Libraries, Library, tuple[str, str, str]],
) -> None:
    """A browser-saved private recovery ZIP never joins GitHub snapshots inside an owned bundle."""
    libraries, library, (_, _, artifact_id) = sync_workspace
    artifact = ArtifactStore(libraries.store).get_artifact(artifact_id)
    exported = Path(artifact.source_path) / "workspace.artifact-studio.zip"
    _ = exported.write_bytes(b"fictional private review database and history")
    assert library_module.snapshot_allowed(Path("projects/project/artifacts/item/files/data.zip"))
    assert not library_module.snapshot_allowed(
        Path("projects/project/artifacts/item/files/workspace.artifact-studio.zip")
    )
    assert not any(item.local_path == str(exported) for item in libraries.snapshot_files())
    _ = libraries.sync(library.id)
    assert not list(Path(library.path).rglob("*.artifact-studio.zip"))
