"""Disaster recovery checks using fictional, isolated workspace collections."""

import base64
import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import cast
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from artifact_studio.artifact_context import artifact_context
from artifact_studio.artifact_models import ArtifactFeedbackInput, ArtifactFileInput
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.libraries import Libraries, LibraryInput
from artifact_studio.models import Deck, DesignRequestInput, FileInput, Slide
from artifact_studio.project_tree import ProjectTree, TreeChange
from artifact_studio.storage import ConflictError, Store
from artifact_studio.workspace_recovery import (
    ARCHIVE_MANIFEST,
    PORTABLE_METADATA,
    WorkspaceArchive,
    WorkspaceRecovery,
)


def workspace(path: Path) -> Store:
    """Create a fictional collection with private state outside its source tree."""
    return Store(path / "collection", path / "state" / "review.sqlite3")


def populate(store: Store, path: Path) -> tuple[str, str, str]:
    """Create moved, revised work, a legacy deck and ordered navigation folders."""
    registry = ArtifactStore(store)
    original = registry.create_project("Origin", "Initial project brief")
    destination = registry.create_project("Audience", "New shared context")
    source = path / "authored-source"
    source.mkdir()
    _ = (source / "index.html").write_text("<h1>First</h1>")
    _ = (source / "image.bin").write_bytes(b"fictional image")
    artifact = registry.register_artifact(original.id, "Research page", source, "index.html")
    artifact = registry.revise_artifact(
        artifact.id,
        [ArtifactFileInput(path="index.html", text="<h1>Current</h1>")],
        artifact.content_hash,
        "revision-recovery-001",
    )
    _ = registry.set_artifact_project(artifact.id, destination.id)
    _ = registry.approve(artifact.id, artifact.content_hash, True)
    deck_folder = store.root / "fictional-deck"
    deck_folder.mkdir()
    deck = Deck(
        id="fictional-deck",
        title="Fictional legacy deck",
        design_id="fictional",
        slides=[Slide(id="cover", title="Cover", html="<h1>Example</h1>")],
    )
    _ = (deck_folder / "index.html").write_text(
        '<script id="deck-data" type="application/json">' + deck.model_dump_json() + "</script>"
    )
    _ = (deck_folder / "theme.css").write_text("body { color: navy; }")
    _ = registry.set_artifact_project(deck.id, destination.id)
    tree = ProjectTree(store)
    initial = tree.view()
    grouped = tree.change(
        TreeChange(action="create", title="Folder", expected_hash=initial.content_hash)
    )
    folder = next(node for node in grouped.nodes if node.kind == "folder")
    _ = tree.change(
        TreeChange(
            action="move",
            id=destination.id,
            parent_id=folder.id,
            expected_hash=grouped.content_hash,
        )
    )
    with store.connect() as db:
        _ = db.execute("INSERT INTO archived_decks VALUES (?,?)", (deck.id, "2026-10-01"))
    _ = WorkspaceRecovery(store).write_portable_metadata()
    return original.id, destination.id, artifact.id


def snapshot(store: Store, destination: Path) -> Path:
    """Copy exactly the current-file policy used for GitHub collection snapshots."""
    destination.mkdir()
    for item in Libraries(store).snapshot_files():
        target = destination / item.path
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copyfile(item.local_path, target)
    return destination


def archive_with_database(source: Path, destination: Path, database: Path) -> Path:
    """Replace a fictional archive's database while retaining valid object checksums."""
    data = database.read_bytes()
    checksum = hashlib.sha256(data).hexdigest()
    with ZipFile(source) as archive:
        manifest = WorkspaceArchive.model_validate_json(archive.read(ARCHIVE_MANIFEST))
        objects = {item.sha256: archive.read("objects/" + item.sha256) for item in manifest.files}
    manifest.files = [
        item.model_copy(update={"sha256": checksum, "size": len(data)})
        if item.area == "database"
        else item
        for item in manifest.files
    ]
    objects[checksum] = data
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        archive.writestr(ARCHIVE_MANIFEST, manifest.model_dump_json())
        for checksum in {item.sha256 for item in manifest.files}:
            archive.writestr("objects/" + checksum, objects[checksum])
    return destination


def test_current_recovery_rebuilds_registry_moves_tree_and_current_revision(tmp_path: Path) -> None:
    """A fresh database rediscovers moved artifacts without inventing private history."""
    original = workspace(tmp_path / "original")
    origin_id, project_id, artifact_id = populate(original, tmp_path)
    source = snapshot(original, tmp_path / "snapshot")
    target = workspace(tmp_path / "recovered")
    recovery = WorkspaceRecovery(target)
    preview = recovery.preview(source)
    assert preview.can_restore and preview.projects == 2 and preview.artifacts == 1
    assert preview.presentations == 1 and preview.folders == 1
    result = recovery.restore(source, preview.content_hash)
    assert result.restored
    registry = ArtifactStore(target)
    restored = registry.get_artifact(artifact_id)
    assert restored.project_id == project_id and restored.storage_project_id == origin_id
    assert restored.revision == 2 and not restored.approved and restored.has_unseen_changes
    assert Path(restored.source_path).is_relative_to(target.root)
    assert restored.original_source_path == restored.source_path
    assert registry.snapshot_files(artifact_id, 2)["index.html"] == b"<h1>Current</h1>"
    with pytest.raises(FileNotFoundError):
        _ = registry.get_artifact_revision(artifact_id, 1)
    with target.connect() as db:
        assert (
            db.execute(
                "SELECT project_id FROM artifact_legacy_projects WHERE artifact_id='fictional-deck'"
            ).fetchone()[0]
            == project_id
        )
        assert db.execute("SELECT deck FROM archived_decks").fetchone()[0] == "fictional-deck"
        assert db.execute("SELECT COUNT(*) FROM artifact_feedback").fetchone()[0] == 0
    assert ProjectTree(target).view().nodes == ProjectTree(original).view().nodes
    project = next(item for item in registry.list_projects() if item.id == project_id)
    assert project.context == "New shared context" and project.source_path.startswith(
        str(target.root)
    )


def test_current_recovery_warns_about_older_missing_memberships(tmp_path: Path) -> None:
    """Older snapshots remain usable while clearly disclosing unrecoverable membership state."""
    original = workspace(tmp_path / "original")
    _ = populate(original, tmp_path)
    source = snapshot(original, tmp_path / "snapshot")
    (source / PORTABLE_METADATA).unlink()
    recovery = WorkspaceRecovery(workspace(tmp_path / "target"))
    preview = recovery.preview(source)
    assert any("older snapshot" in warning for warning in preview.warnings)
    _ = recovery.restore(source, preview.content_hash)
    assert (
        ArtifactStore(recovery.store).get_artifact("fictional-deck").project_id
        == "legacy-presentations"
    )


def test_changed_source_or_destination_invalidates_preview(tmp_path: Path) -> None:
    """A token cannot authorize bytes or destination state absent from its preview."""
    original = workspace(tmp_path / "original")
    _ = populate(original, tmp_path)
    source = snapshot(original, tmp_path / "snapshot")
    target = workspace(tmp_path / "target")
    recovery = WorkspaceRecovery(target)
    preview = recovery.preview(source)
    _ = (source / "README.md").write_text("Changed after preview")
    with pytest.raises(ConflictError, match="changed"):
        _ = recovery.restore(source, preview.content_hash)
    preview = recovery.preview(source)
    _ = (target.root / "user-document.txt").write_text("Do not replace")
    with pytest.raises(ConflictError, match="changed"):
        _ = recovery.restore(source, preview.content_hash)
    assert (target.root / "user-document.txt").read_text() == "Do not replace"


def test_existing_workspace_is_reported_as_conflict(tmp_path: Path) -> None:
    """Restoring never merges or overwrites live work or its review state."""
    original = workspace(tmp_path / "original")
    _ = populate(original, tmp_path)
    source = snapshot(original, tmp_path / "snapshot")
    recovery = WorkspaceRecovery(original)
    preview = recovery.preview(source)
    assert not preview.can_restore and len(preview.conflicts) >= 2
    with pytest.raises(ConflictError, match="empty workspace"):
        _ = recovery.restore(source, preview.content_hash)


def test_current_file_tampering_and_wrong_storage_membership_are_rejected(tmp_path: Path) -> None:
    """Snapshot manifests cannot bless edited source bytes or redirected storage paths."""
    original = workspace(tmp_path / "original")
    _, _, artifact_id = populate(original, tmp_path)
    source = snapshot(original, tmp_path / "snapshot")
    artifact = next(source.glob(f"projects/*/artifacts/{artifact_id}/artifact.json"))
    recovery = WorkspaceRecovery(workspace(tmp_path / "target"))
    _ = (artifact.parent / "files" / "image.bin").write_bytes(b"changed")
    with pytest.raises(ConflictError, match="differ"):
        _ = recovery.preview(source)
    _ = (artifact.parent / "files" / "image.bin").write_bytes(b"fictional image")
    document = cast(dict[str, object], json.loads(artifact.read_text()))
    document["storage_project_id"] = "other-project"
    _ = artifact.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="storage folder"):
        _ = recovery.preview(source)


def test_full_archive_restores_private_history_and_external_attachments(tmp_path: Path) -> None:
    """Full recovery retains private comments, approvals, historical sources and attachments."""
    original = workspace(tmp_path / "original")
    _, project_id, artifact_id = populate(original, tmp_path)
    registry = ArtifactStore(original)
    artifact = registry.get_artifact(artifact_id)
    _ = registry.set_mode(artifact_id, "batch")
    attachment = FileInput(
        name="fictional.txt", data_base64=base64.b64encode(b"private attachment").decode()
    )
    event = registry.add_feedback(
        artifact_id,
        ArtifactFeedbackInput(
            message="Private draft",
            request_id="private-feedback-001",
            content_hash=artifact.content_hash,
            files=[attachment],
        ),
    )
    message = original.post_design_request(
        DesignRequestInput(
            message="Private design request", request_id="private-design-001", files=[attachment]
        )
    )
    legacy_attachment = original.save_feedback_files(
        "fictional-deck", [("legacy.txt", b"legacy attachment")]
    )[0]
    reference = tmp_path / "host-reference"
    reference.mkdir()
    _ = Libraries(original).register(LibraryInput(location=str(reference)))
    with original.connect() as db:
        _ = db.execute(
            "INSERT INTO review_sessions(harness,session_id,deck,expires_at) "
            "VALUES ('codex','host-session','fictional-deck',9999999999)"
        )
    recovery = WorkspaceRecovery(original)
    archive_path = tmp_path / "backup.zip"
    summary = recovery.export_archive(archive_path)
    assert summary.files > summary.objects and summary.path == str(archive_path)
    with ZipFile(archive_path) as archive:
        manifest = WorkspaceArchive.model_validate_json(archive.read(ARCHIVE_MANIFEST))
        assert all(
            name == ARCHIVE_MANIFEST or name.startswith("objects/") for name in archive.namelist()
        )
        assert any(
            item.area == "state" and item.path.startswith("design-inbox/")
            for item in manifest.files
        )
    target = workspace(tmp_path / "restored")
    restore = WorkspaceRecovery(target)
    preview = restore.preview(archive_path, mode="archive")
    assert preview.can_restore and preview.projects == 2
    _ = restore.restore(archive_path, preview.content_hash, mode="archive")
    restored = ArtifactStore(target)
    assert restored.get_artifact(artifact_id).approved
    assert restored.get_artifact(artifact_id).project_id == project_id
    assert restored.snapshot_files(artifact_id, 1)["index.html"] == b"<h1>First</h1>"
    assert restored.snapshot_files(artifact_id, 2)["index.html"] == b"<h1>Current</h1>"
    events = restored.feedback(artifact_id, include_pending=True)
    assert len(events) == 1 and events[0].message == "Private draft" and not events[0].released
    restored_file, _ = restored.resolve_feedback_file(
        artifact_id, event.event_id, 0, include_pending=True
    )
    assert restored_file.read_bytes() == b"private attachment" and restored_file.is_relative_to(
        target.root
    )
    inbox = target.design_inbox()
    assert inbox.messages[0].message == "Private design request"
    assert Path(inbox.messages[0].files[0].path).read_bytes() == b"private attachment"
    assert str(original.database.parent) not in inbox.messages[0].files[0].path
    assert Path(message.files[0].path).read_bytes() == b"private attachment"
    assert (
        target.database.parent / "feedback-files" / "fictional-deck" / legacy_attachment.path
    ).read_bytes() == b"legacy attachment"
    with target.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM libraries").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM review_sessions").fetchone()[0] == 0
    assert ProjectTree(target).view().nodes == ProjectTree(original).view().nodes


def test_archive_rejects_corrupt_unlisted_and_traversal_entries(tmp_path: Path) -> None:
    """Malformed local archives are rejected before any destination content is written."""
    original = workspace(tmp_path / "original")
    _ = populate(original, tmp_path)
    archive_path = tmp_path / "backup.zip"
    _ = WorkspaceRecovery(original).export_archive(archive_path)
    recovery = WorkspaceRecovery(workspace(tmp_path / "target"))
    with ZipFile(archive_path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    object_name = next(name for name in files if name.startswith("objects/"))
    files[object_name] = b"corrupt"
    bad = tmp_path / "corrupt.zip"
    with ZipFile(bad, "w", ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    with pytest.raises(ValueError, match="ordinary|SHA-256"):
        _ = recovery.preview(bad, mode="archive")
    with ZipFile(archive_path, "a") as archive:
        archive.writestr("../escape.txt", b"no")
    with pytest.raises(ValueError, match="unlisted"):
        _ = recovery.preview(archive_path, mode="archive")
    assert not (tmp_path / "escape.txt").exists()
    assert not list(recovery.store.root.iterdir())


def test_archive_does_not_overwrite_existing_file_or_include_host_secrets(tmp_path: Path) -> None:
    """Full backups stay local, leave originals intact and omit host credential state."""
    store = workspace(tmp_path / "source")
    _ = populate(store, tmp_path)
    _ = (store.root / ".env").write_text("FICTIONAL_CREDENTIAL=value")
    _ = (store.root / "credentials.json").write_text("{}")
    (store.root / ".git").mkdir()
    _ = (store.root / ".git" / "config").write_text("fictional credential-bearing remote")
    archive_path = tmp_path / "backup.zip"
    recovery = WorkspaceRecovery(store)
    _ = recovery.export_archive(archive_path)
    with ZipFile(archive_path) as archive:
        manifest = WorkspaceArchive.model_validate_json(archive.read(ARCHIVE_MANIFEST))
        assert not any(
            item.path in {".env", "credentials.json", ".git/config"} for item in manifest.files
        )
    with pytest.raises(ConflictError, match="never replaced"):
        _ = recovery.export_archive(archive_path)
    with pytest.raises(ValueError, match="outside"):
        _ = recovery.export_archive(store.root / "backup.zip")


def test_full_archive_rebases_metadata_without_changing_artifact_json_bytes(tmp_path: Path) -> None:
    """Content identity remains valid when arbitrary source JSON contains old host paths."""
    original = workspace(tmp_path / "original")
    registry = ArtifactStore(original)
    project = registry.create_project("Data")
    source = tmp_path / "data.json"
    authored = json.dumps({"path": str(original.root / "authored-reference")})
    _ = source.write_text(authored)
    artifact = registry.register_artifact(project.id, "Data", source, "data.json")
    archive_path = tmp_path / "backup.zip"
    _ = WorkspaceRecovery(original).export_archive(archive_path)
    target = workspace(tmp_path / "target")
    recovery = WorkspaceRecovery(target)
    preview = recovery.preview(archive_path, mode="archive")
    _ = recovery.restore(archive_path, preview.content_hash, mode="archive")
    assert ArtifactStore(target).snapshot_files(artifact.id)["data.json"] == authored.encode()


def test_current_recovery_rejects_symlinked_manifest_parent(tmp_path: Path) -> None:
    """A crafted checkout cannot import files through a redirected project directory."""
    original = workspace(tmp_path / "original")
    origin_id, _, _ = populate(original, tmp_path)
    source = snapshot(original, tmp_path / "snapshot")
    project = source / "projects" / origin_id
    moved = tmp_path / "outside-project"
    project.rename(moved)
    project.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ValueError, match="linked|symbolic"):
        _ = WorkspaceRecovery(workspace(tmp_path / "target")).preview(source)


def test_archive_database_contains_no_live_upload_permissions(tmp_path: Path) -> None:
    """Portable full archives cannot carry previously enabled repository upload state."""
    store = workspace(tmp_path / "source")
    _ = populate(store, tmp_path)
    with store.connect() as db:
        _ = db.execute("CREATE TABLE library_auto_sync(id TEXT PRIMARY KEY, document TEXT)")
        _ = db.execute("INSERT INTO library_auto_sync VALUES ('host','{}')")
        for table in ("github_backup_settings", "github_backup_state", "library_sync_checkpoints"):
            _ = db.execute(f"CREATE TABLE {table}(id TEXT PRIMARY KEY, document TEXT)")
            _ = db.execute(f"INSERT INTO {table} VALUES ('host','{{}}')")
    archive_path = tmp_path / "backup.zip"
    _ = WorkspaceRecovery(store).export_archive(archive_path)
    with ZipFile(archive_path) as archive:
        manifest = WorkspaceArchive.model_validate_json(archive.read(ARCHIVE_MANIFEST))
        entry = next(item for item in manifest.files if item.area == "database")
        database = tmp_path / "inspect.sqlite3"
        _ = database.write_bytes(archive.read("objects/" + entry.sha256))
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM library_auto_sync").fetchone() == (0,)
        for table in ("github_backup_settings", "github_backup_state", "library_sync_checkpoints"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM library_auto_sync").fetchone()[0] == 1
        for table in ("github_backup_settings", "github_backup_state", "library_sync_checkpoints"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1


def test_full_archive_preserves_legacy_asset_mtimes_and_approvals(tmp_path: Path) -> None:
    """Legacy approval identity survives relocation because asset timestamps are retained."""
    original = workspace(tmp_path / "original")
    _ = populate(original, tmp_path)
    assets = original.root / "fictional-deck" / "assets"
    assets.mkdir()
    _ = (assets / "image.svg").write_text("<svg>Fictional</svg>")
    slide = original.view("fictional-deck").slides[0]
    original.approve("fictional-deck", slide.id, slide.content_hash, True)
    archive = tmp_path / "backup.zip"
    _ = WorkspaceRecovery(original).export_archive(archive)
    target = workspace(tmp_path / "target")
    recovery = WorkspaceRecovery(target)
    preview = recovery.preview(archive, mode="archive")
    _ = recovery.restore(archive, preview.content_hash, mode="archive")
    assert target.view("fictional-deck").slides[0].approved
    assert (target.root / "fictional-deck/assets/image.svg").stat().st_mtime_ns == (
        assets / "image.svg"
    ).stat().st_mtime_ns


def test_full_archive_supports_private_database_inside_collection(tmp_path: Path) -> None:
    """Nested private state preserves attachments without duplicating the live database."""
    root = tmp_path / "source-workspace"
    original = Store(root, root / ".state" / "review.sqlite3")
    _, _, artifact_id = populate(original, tmp_path)
    registry = ArtifactStore(original)
    artifact = registry.get_artifact(artifact_id)
    event = registry.add_feedback(
        artifact_id,
        ArtifactFeedbackInput(
            message="Private",
            request_id="private-nested-001",
            content_hash=artifact.content_hash,
            files=[
                FileInput(
                    name="attachment", data_base64=base64.b64encode(b"nested attachment").decode()
                )
            ],
        ),
    )
    archive = tmp_path / "backup.zip"
    _ = WorkspaceRecovery(original).export_archive(archive)
    root = tmp_path / "target-workspace"
    target = Store(root, root / ".state" / "review.sqlite3")
    recovery = WorkspaceRecovery(target)
    preview = recovery.preview(archive, mode="archive")
    _ = recovery.restore(archive, preview.content_hash, mode="archive")
    path, _ = ArtifactStore(target).resolve_feedback_file(artifact_id, event.event_id, 0)
    assert path.read_bytes() == b"nested attachment"
    with ZipFile(archive) as container:
        manifest = WorkspaceArchive.model_validate_json(container.read(ARCHIVE_MANIFEST))
        assert not any(item.path == ".state/review.sqlite3" for item in manifest.files)


def test_archive_rejects_unknown_trigger_before_executing_imported_sql(tmp_path: Path) -> None:
    """Imported SQL cannot disguise side effects as a first-party history trigger."""
    original = workspace(tmp_path / "original")
    _ = populate(original, tmp_path)
    with original.connect() as db:
        _ = db.execute(
            "CREATE TRIGGER malicious AFTER UPDATE ON artifact_items "
            "BEGIN DELETE FROM artifact_projects; END"
        )
    archive = tmp_path / "backup.zip"
    with pytest.raises(ValueError, match="unknown trigger"):
        _ = WorkspaceRecovery(original).export_archive(archive)
    assert not archive.exists()
    assert len(ArtifactStore(original).list_projects()) == 2


def test_portable_metadata_captures_out_of_band_current_edits(tmp_path: Path) -> None:
    """A saved file edit is synchronized before future portable metadata is collected."""
    original = workspace(tmp_path / "original")
    _, _, artifact_id = populate(original, tmp_path)
    artifact = ArtifactStore(original).get_artifact(artifact_id)
    _ = (Path(artifact.source_path) / "index.html").write_text("<h1>Saved externally</h1>")
    _ = WorkspaceRecovery(original).write_portable_metadata()
    source = snapshot(original, tmp_path / "snapshot")
    recovery = WorkspaceRecovery(workspace(tmp_path / "target"))
    preview = recovery.preview(source)
    _ = recovery.restore(source, preview.content_hash)
    recovered = ArtifactStore(recovery.store).get_artifact(artifact_id)
    assert recovered.revision == 3
    assert (
        ArtifactStore(recovery.store).snapshot_files(artifact_id)["index.html"]
        == b"<h1>Saved externally</h1>"
    )


def test_local_archive_excludes_generated_dependencies_but_keeps_authoring_source(
    tmp_path: Path,
) -> None:
    """A local authoring cache's executable links do not block complete source recovery."""
    store = workspace(tmp_path / "original")
    _ = populate(store, tmp_path)
    authoring = store.root / ".studio-authoring" / "demo-video"
    binaries = authoring / "node_modules" / ".bin"
    binaries.mkdir(parents=True)
    _ = (authoring / "video.tsx").write_text("export const Video = () => null;")
    (binaries / "compiler").symlink_to("../compiler/bin.js")
    archive = tmp_path / "workspace.artifact-studio.zip"
    _ = WorkspaceRecovery(store).export_archive(archive)
    with ZipFile(archive) as content:
        manifest = WorkspaceArchive.model_validate_json(content.read(ARCHIVE_MANIFEST))
    assert any(item.path.endswith("video.tsx") for item in manifest.files)
    assert not any("node_modules" in item.path for item in manifest.files)
    assert not any("node_modules" in folder for folder in manifest.directories)
    recovery = WorkspaceRecovery(workspace(tmp_path / "recovered"))
    preview = recovery.preview(archive, mode="archive")
    _ = recovery.restore(archive, preview.content_hash, mode="archive")
    assert (recovery.store.root / ".studio-authoring/demo-video/video.tsx").is_file()
    assert not (recovery.store.root / ".studio-authoring/demo-video/node_modules").exists()
    (authoring / "external.html").symlink_to(tmp_path / "outside.html")
    with pytest.raises(ValueError, match="symbolic links"):
        _ = WorkspaceRecovery(store).export_archive(tmp_path / "unsafe.zip")


def test_archive_fingerprint_skips_timestamp_and_host_only_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated checks ignore leases, context receipts, file times and SQLite page layout."""
    store = workspace(tmp_path / "original")
    _, _, artifact_id = populate(store, tmp_path)
    note = store.root / "notes.txt"
    _ = note.write_text("Saved notes")
    recovery = WorkspaceRecovery(store)
    first = recovery.export_archive(tmp_path / "first.zip")
    repeated = recovery.export_archive(tmp_path / "second.zip")
    assert first.fingerprint == repeated.fingerprint and len(first.fingerprint) == 64
    assert not first.unchanged and not repeated.unchanged
    assert first.sha256 != repeated.sha256
    os.utime(note, ns=(note.stat().st_atime_ns, note.stat().st_mtime_ns + 1_000_000_000))
    store.mark_agent_check("fictional-deck", 30)
    _ = artifact_context(store, artifact_id=artifact_id, record_delivery=True)
    _ = artifact_context(store, artifact_id=artifact_id, record_delivery=True)
    with store.connect() as db:
        _ = db.execute(
            "INSERT INTO review_sessions(harness,session_id,deck,expires_at) "
            "VALUES ('codex','fictional-host','fictional-deck',9999999999)"
        )
        _ = db.execute("CREATE TABLE github_backup_state(id TEXT PRIMARY KEY, document TEXT)")
        _ = db.execute("INSERT INTO github_backup_state VALUES ('host',?)", ("x" * 200_000,))
        _ = db.execute("CREATE TABLE library_sync_checkpoints(id TEXT PRIMARY KEY, document TEXT)")
        _ = db.execute("INSERT INTO library_sync_checkpoints VALUES ('host','prepared backup')")
    with store.connect() as db:
        _ = db.execute("DELETE FROM github_backup_state")
    with store.connect() as db:
        _ = db.execute("VACUUM")

    def unexpected_archive(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("Unchanged workspace must not construct a ZIP archive")

    monkeypatch.setattr("artifact_studio.workspace_recovery.ZipFile", unexpected_archive)
    destination = tmp_path / "unchanged.zip"
    result = recovery.export_archive_if_changed(destination, first.fingerprint)
    assert result.unchanged and result.fingerprint == first.fingerprint
    assert result.sha256 == "" and result.path == str(destination)
    assert not destination.exists()


@pytest.mark.parametrize(
    "change",
    [
        "file",
        "directory",
        "mode",
        "workspace_context",
        "project_context",
        "chat",
        "approval",
        "attachment",
        "retained_revision",
        "workflow_mode",
        "sequence",
    ],
)
def test_archive_fingerprint_tracks_saved_full_workspace_state(tmp_path: Path, change: str) -> None:
    """Each saved archive area participates, including private state and old source revisions."""
    store = workspace(tmp_path / "original")
    _, project_id, artifact_id = populate(store, tmp_path)
    note = store.root / "notes.txt"
    _ = note.write_text("Saved notes")
    registry = ArtifactStore(store)
    artifact = registry.get_artifact(artifact_id)
    recovery = WorkspaceRecovery(store)
    first = recovery.export_archive(tmp_path / "first.zip")
    if change == "file":
        _ = note.write_text("Edited notes")
    elif change == "directory":
        (store.root / "empty-folder").mkdir()
    elif change == "mode":
        note.chmod(0o755)
    elif change == "workspace_context":
        with store.connect() as db:
            _ = db.execute(
                "INSERT INTO workspace_settings VALUES ('authoring_instructions','Saved context')"
            )
    elif change == "project_context":
        project = next(project for project in registry.list_projects() if project.id == project_id)
        _ = (Path(project.source_path) / "README.md").write_text("Edited project context")
    elif change == "chat":
        _ = store.post_design_request(
            DesignRequestInput(message="Saved discussion", request_id="fingerprint-chat-001")
        )
    elif change == "approval":
        _ = registry.approve(artifact_id, artifact.content_hash, False)
    elif change == "attachment":
        _ = store.save_feedback_files("fictional-deck", [("saved.txt", b"Saved attachment")])
    elif change == "retained_revision":
        retained = Path(artifact.source_path).parent / "revisions/1/files/index.html"
        _ = retained.write_text("Edited retained source")
    elif change == "workflow_mode":
        _ = store.set_mode("fictional-deck", "batch")
    elif change == "sequence":
        with store.connect() as db:
            _ = db.execute("INSERT INTO design_chat VALUES (50,'removed','hash','{}')")
            _ = db.execute("DELETE FROM design_chat WHERE id=50")
    else:
        raise AssertionError(change)
    destination = tmp_path / "changed.zip"
    result = recovery.export_archive_if_changed(destination, first.fingerprint)
    assert not result.unchanged and result.fingerprint != first.fingerprint
    assert destination.is_file()
    unchanged = tmp_path / "unchanged.zip"
    assert recovery.export_archive_if_changed(unchanged, result.fingerprint).unchanged
    assert not unchanged.exists()


def test_conditional_archive_still_rejects_forged_database_schema(tmp_path: Path) -> None:
    """A matching checkpoint never bypasses the archive's untrusted-schema validation."""
    store = workspace(tmp_path / "original")
    _ = populate(store, tmp_path)
    recovery = WorkspaceRecovery(store)
    first = recovery.export_archive(tmp_path / "first.zip")
    with store.connect() as db:
        _ = db.execute("CREATE VIEW forged AS SELECT * FROM artifact_projects")
    destination = tmp_path / "forged.zip"
    with pytest.raises(ValueError, match="cannot contain views"):
        _ = recovery.export_archive_if_changed(destination, first.fingerprint)
    assert not destination.exists()


def test_archive_fingerprint_canonicalizes_typed_database_rows(tmp_path: Path) -> None:
    """Rowids and insertion order are physical; SQLite value types and schema are saved state."""
    store = workspace(tmp_path / "original")
    with store.connect() as db:
        _ = db.execute("CREATE TABLE saved_values(value)")
        _ = db.executemany(
            "INSERT INTO saved_values VALUES (?)", [(1,), (1.0,), ("1",), (b"1",), (None,)]
        )
    recovery = WorkspaceRecovery(store)
    first = recovery.export_archive(tmp_path / "first.zip")
    with store.connect() as db:
        _ = db.execute("DELETE FROM saved_values")
        _ = db.executemany(
            "INSERT INTO saved_values VALUES (?)", [(None,), (b"1",), ("1",), (1.0,), (1,)]
        )
    assert recovery.export_archive_if_changed(
        tmp_path / "reordered.zip", first.fingerprint
    ).unchanged
    with store.connect() as db:
        _ = db.execute("DELETE FROM saved_values WHERE typeof(value)='integer'")
        _ = db.execute("INSERT INTO saved_values VALUES (1.0)")
    changed = recovery.export_archive_if_changed(tmp_path / "typed.zip", first.fingerprint)
    assert not changed.unchanged and changed.fingerprint != first.fingerprint
    with store.connect() as db:
        _ = db.execute("CREATE INDEX saved_value_index ON saved_values(value)")
    schema = recovery.export_archive_if_changed(tmp_path / "schema.zip", changed.fingerprint)
    assert not schema.unchanged and schema.fingerprint != changed.fingerprint


def test_fingerprint_archive_matches_export_and_extracts_only_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing verified archives establish the same checkpoint without restoring authored files."""
    store = workspace(tmp_path / "original")
    _ = populate(store, tmp_path)
    recovery = WorkspaceRecovery(store)
    summary = recovery.export_archive(tmp_path / "previous.zip")
    extract = WorkspaceRecovery._extract  # pyright: ignore[reportPrivateUsage]

    def database_only(
        source: Path, manifest: WorkspaceArchive, stage: Path, *, metadata_only: bool = False
    ) -> None:
        assert [item.area for item in manifest.files] == ["database"]
        assert not manifest.directories
        extract(source, manifest, stage, metadata_only=metadata_only)

    monkeypatch.setattr(WorkspaceRecovery, "_extract", staticmethod(database_only))
    assert recovery.fingerprint_archive(Path(summary.path)) == summary.fingerprint
    _ = (store.root / "new-notes.txt").write_text("Changed since previous archive")
    assert recovery.fingerprint_archive(Path(summary.path)) == summary.fingerprint


def test_fingerprint_older_archive_normalizes_transient_saved_database_state(
    tmp_path: Path,
) -> None:
    """Pre-checkpoint archives' listening timestamps and delivery receipts are not saved edits."""
    store = workspace(tmp_path / "original")
    _, _, artifact_id = populate(store, tmp_path)
    recovery = WorkspaceRecovery(store)
    summary = recovery.export_archive(tmp_path / "previous.zip")
    store.mark_agent_check("fictional-deck", 30)
    _ = artifact_context(store, artifact_id=artifact_id, record_delivery=True)
    database = tmp_path / "older.sqlite3"
    with store.connect() as source, sqlite3.connect(database) as destination:
        source.backup(destination)
    older = archive_with_database(Path(summary.path), tmp_path / "older.zip", database)
    assert recovery.fingerprint_archive(older) == summary.fingerprint
    assert recovery.export_archive_if_changed(
        tmp_path / "unchanged.zip", summary.fingerprint
    ).unchanged


@pytest.mark.parametrize(
    "forgery", ["view", "trigger", "invalid_database", "corrupt_object", "path"]
)
def test_fingerprint_archive_rejects_forged_metadata_schema_and_objects(
    tmp_path: Path, forgery: str
) -> None:
    """A migration checkpoint requires every archive object and the database schema to validate."""
    store = workspace(tmp_path / "original")
    _ = populate(store, tmp_path)
    recovery = WorkspaceRecovery(store)
    summary = recovery.export_archive(tmp_path / "previous.zip")
    archive_path = Path(summary.path)
    if forgery in {"view", "trigger", "invalid_database"}:
        database = tmp_path / "forged.sqlite3"
        if forgery == "invalid_database":
            _ = database.write_bytes(b"invalid database bytes")
        else:
            with store.connect() as source, sqlite3.connect(database) as destination:
                source.backup(destination)
                if forgery == "view":
                    _ = destination.execute("CREATE VIEW forged AS SELECT * FROM artifact_projects")
                else:
                    _ = destination.execute(
                        "CREATE TRIGGER forged AFTER UPDATE ON artifact_items "
                        "BEGIN DELETE FROM artifact_projects; END"
                    )
        archive_path = archive_with_database(archive_path, tmp_path / "forged.zip", database)
    else:
        with ZipFile(archive_path) as archive:
            content = {name: archive.read(name) for name in archive.namelist()}
        manifest = WorkspaceArchive.model_validate_json(content[ARCHIVE_MANIFEST])
        if forgery == "path":
            manifest.files[0].path = "../escape"
            content[ARCHIVE_MANIFEST] = manifest.model_dump_json().encode()
        else:
            item = next(item for item in manifest.files if item.area == "collection")
            content["objects/" + item.sha256] = b"x" * item.size
        archive_path = tmp_path / "forged.zip"
        with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
            for name, data in content.items():
                archive.writestr(name, data)
    with pytest.raises(ValueError):
        _ = recovery.fingerprint_archive(archive_path)
