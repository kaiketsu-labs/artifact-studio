"""Keep local backup baselines safe through upgrades, retries and partial snapshots."""

import json
import shutil
from pathlib import Path

import pytest

from artifact_studio import libraries as module
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.autosync import QUIET_SECONDS, AutoSync, SyncStatus
from artifact_studio.libraries import Libraries, SyncCheckpoint, command
from tests.test_libraries import library_setup as library_setup  # noqa: F401
from tests.test_library_sync_conflicts import DECK, register, remote_file, replica


def checkpoint(libraries: Libraries, identifier: str) -> SyncCheckpoint:
    """Read the durable outcome after simulating a process or connection failure."""
    with libraries.store.connect() as db:
        row = db.execute(
            "SELECT document FROM library_sync_checkpoints WHERE id=?", (identifier,)
        ).fetchone()
    assert row is not None
    return SyncCheckpoint.model_validate_json(str(row[0]))


def forget_checkpoint(libraries: Libraries, identifier: str) -> None:
    """Model an upgrade from the version that saved only AutoSync delivery receipts."""
    with libraries.store.connect() as db:
        _ = db.execute("DELETE FROM library_sync_checkpoints WHERE id=?", (identifier,))


def test_prepared_unpublished_backup_retries_before_later_local_changes(
    library_setup: tuple[Libraries, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    libraries, remote = library_setup
    identifier = register(libraries)
    run = module.command

    def offline(*args: str, cwd: Path | None = None) -> str:
        if "push" in args:
            raise ValueError("Offline before push")
        return run(*args, cwd=cwd)

    monkeypatch.setattr(module, "command", offline)
    with pytest.raises(ValueError, match="Offline"):
        _ = libraries.sync(identifier, DECK)
    pending = checkpoint(libraries, identifier)
    assert pending.pending is not None and pending.confirmed.commit == ""
    _ = (libraries.store.root / DECK / "index.html").write_text("Later local edit")
    monkeypatch.setattr(module, "command", run)
    result = Libraries(libraries.store).sync(identifier, DECK)
    assert remote_file(remote, f"{DECK}/index.html") == "Later local edit"
    assert command("git", "--git-dir", str(remote), "rev-list", "--count", "main") == "2"
    saved = checkpoint(libraries, identifier)
    assert saved.pending is None and saved.confirmed.commit == result.commit


def test_lost_push_acknowledgement_does_not_adopt_later_remote_changes(
    library_setup: tuple[Libraries, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, remote = library_setup
    identifier = register(first)
    run = module.command

    def lost_ack(*args: str, cwd: Path | None = None) -> str:
        result = run(*args, cwd=cwd)
        if "push" in args:
            raise ValueError("Push succeeded but acknowledgement was lost")
        return result

    monkeypatch.setattr(module, "command", lost_ack)
    with pytest.raises(ValueError, match="acknowledgement"):
        _ = first.sync(identifier, DECK)
    saved = checkpoint(first, identifier)
    assert saved.pending is not None
    first_commit = saved.pending.commit
    monkeypatch.setattr(module, "command", run)
    second = replica(tmp_path, first)
    second_id = register(second)
    _ = second.sync(second_id, DECK)
    _ = (second.store.root / DECK / "index.html").write_text("Newer remote edit")
    _ = second.sync(second_id, DECK)
    _ = (first.store.root / DECK / "notes.md").write_text("First computer's later notes")
    with pytest.raises(ValueError, match="Remote changes"):
        _ = Libraries(first.store).sync(identifier, DECK)
    assert remote_file(remote, f"{DECK}/index.html") == "Newer remote edit"
    saved = checkpoint(first, identifier)
    assert saved.pending is None and saved.confirmed.commit == first_commit


@pytest.mark.parametrize("remote_changed", [False, True], ids=["own-edits", "remote-conflict"])
def test_legacy_automatic_checkpoint_uses_its_exact_confirmed_commit(
    library_setup: tuple[Libraries, Path], tmp_path: Path, remote_changed: bool
) -> None:
    first, remote = library_setup
    identifier = register(first)
    worker = AutoSync(first.store)
    worker.tick(100)
    worker.tick(100 + QUIET_SECONDS)
    assert worker.state(identifier).phase == "synced"
    old_commit = worker.state(identifier).commit
    forget_checkpoint(first, identifier)
    if remote_changed:
        second = replica(tmp_path, first)
        second_id = register(second)
        _ = second.sync(second_id, DECK)
        _ = (second.store.root / DECK / "index.html").write_text("Other computer's newer edit")
        _ = second.sync(second_id, DECK)
    _ = (first.store.root / DECK / "index.html").write_text("Local edit after upgrade")
    if remote_changed:
        with pytest.raises(ValueError, match="Remote changes"):
            _ = first.sync(identifier)
        assert remote_file(remote, f"{DECK}/index.html") == "Other computer's newer edit"
        assert worker.state(identifier).commit == old_commit
    else:
        _ = first.sync(identifier)
        assert remote_file(remote, f"{DECK}/index.html") == "Local edit after upgrade"


def test_partial_upload_does_not_acknowledge_other_remote_decks(
    library_setup: tuple[Libraries, Path], tmp_path: Path
) -> None:
    first, remote = library_setup
    other_deck = first.store.root / "2026-10-30-other"
    other_deck.mkdir()
    _ = (other_deck / "index.html").write_text("Other deck baseline")
    first_id = register(first)
    _ = first.sync(first_id)
    second = replica(tmp_path, first)
    shutil.copytree(other_deck, second.store.root / other_deck.name)
    second_id = register(second)
    _ = second.sync(second_id, DECK)
    _ = (other_deck / "index.html").write_text("Other deck changed remotely")
    _ = first.sync(first_id, other_deck.name)
    _ = second.sync(second_id, DECK)
    with pytest.raises(ValueError, match="Remote changes"):
        _ = second.sync(second_id, other_deck.name)
    assert remote_file(remote, f"{other_deck.name}/index.html") == "Other deck changed remotely"


def test_unknown_unpublished_cache_commit_cannot_be_delivered(
    library_setup: tuple[Libraries, Path],
) -> None:
    libraries, remote = library_setup
    identifier = register(libraries)
    _ = libraries.sync(identifier, DECK)
    folder = Path(libraries.get(identifier).path)
    _ = (folder / DECK / "index.html").write_text("Unconfirmed cached content")
    _ = module.git(folder, "add", ".")
    _ = module.git(folder, "commit", "-m", "test: unexplained unpublished cache change")
    with pytest.raises(ValueError, match="unconfirmed commit"):
        _ = libraries.sync(identifier, DECK)
    assert remote_file(remote, f"{DECK}/index.html") == "<h1>Local draft</h1>"


@pytest.mark.parametrize("legacy_upgrade", [False, True], ids=["new-baseline", "legacy-upgrade"])
def test_successful_automatic_backup_preserves_unowned_remote_inventory(
    library_setup: tuple[Libraries, Path], tmp_path: Path, legacy_upgrade: bool
) -> None:
    libraries, remote = library_setup
    identifier = register(libraries)
    _ = libraries.sync(identifier, automatic=True)
    # Model another writer publishing a project and the global manifest, while
    # leaving the existing computer's workspace navigation metadata unchanged.
    second = replica(tmp_path, libraries)
    project = ArtifactStore(second.store).create_project(
        "Other computer project", "Keep this brief"
    )
    checkout = Path(libraries.get(identifier).path)
    manifest_path = checkout / ".artifact-studio-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    project_root = Path(project.source_path)
    for source in project_root.iterdir():
        target = checkout / source.relative_to(second.store.root)
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copyfile(source, target)
        manifest[source.relative_to(second.store.root).as_posix()] = module.file_hash(source)
    _ = manifest_path.write_text(json.dumps(manifest))
    _ = module.git(checkout, "add", ".")
    _ = module.git(checkout, "commit", "-m", "test: another workspace publishes a managed project")
    _ = module.git(checkout, "push")
    if legacy_upgrade:
        worker = AutoSync(libraries.store)
        worker.save(
            SyncStatus(
                library_id=identifier,
                phase="synced",
                commit=module.git(checkout, "rev-parse", "HEAD"),
            )
        )
        forget_checkpoint(libraries, identifier)
    _ = (libraries.store.root / DECK / "notes.md").write_text("An unrelated local notes edit")
    _ = libraries.sync(identifier, automatic=True)
    assert remote_file(remote, f"projects/{project.id}/README.md") == "Keep this brief"
    assert remote_file(remote, f"{DECK}/notes.md") == "An unrelated local notes edit"
    assert (
        f"projects/{project.id}/README.md" not in checkpoint(libraries, identifier).confirmed.files
    )


def test_automatic_partial_snapshot_is_rejected_before_any_deletion(
    library_setup: tuple[Libraries, Path],
) -> None:
    libraries, remote = library_setup
    identifier = register(libraries)
    _ = libraries.sync(identifier)
    before = command("git", "--git-dir", str(remote), "rev-parse", "main")
    with pytest.raises(ValueError, match="complete workspace"):
        _ = libraries.sync(identifier, DECK, automatic=True)
    assert command("git", "--git-dir", str(remote), "rev-parse", "main") == before
