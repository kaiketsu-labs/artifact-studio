"""Protect a shared backup from independently stale Studio workspaces."""

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from artifact_studio import libraries as module
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.libraries import Libraries, LibraryInput, command
from artifact_studio.storage import Store
from tests.test_libraries import library_setup as library_setup  # noqa: F401

DECK = "2026-10-28-example"


def replica(tmp_path: Path, source: Libraries) -> Libraries:
    """Create a separate Store with matching project files and its own checkout."""
    root = tmp_path / "replica"
    root.mkdir()
    deck = root / DECK
    deck.mkdir()
    for name in ("index.html", "notes.md"):
        _ = (deck / name).write_text((source.store.root / DECK / name).read_text())
    return Libraries(Store(root, tmp_path / "replica-state" / "review.sqlite3"))


def register(libraries: Libraries) -> str:
    return libraries.register(LibraryInput(location="test/slides", auto_sync_enabled=True)).id


def remote_file(remote: Path, path: str) -> str:
    return command("git", "--git-dir", str(remote), "show", f"main:{path}")


def test_new_replica_cannot_replace_existing_managed_file(
    library_setup: tuple[Libraries, Path], tmp_path: Path
) -> None:
    first, remote = library_setup
    first_id = register(first)
    _ = first.sync(first_id, DECK)
    second = replica(tmp_path, first)
    _ = (second.store.root / DECK / "index.html").write_text("Different local draft")
    second_id = register(second)
    with pytest.raises(ValueError, match="Remote changes"):
        _ = second.sync(second_id, DECK)
    assert remote_file(remote, f"{DECK}/index.html") == "<h1>Local draft</h1>"


def test_stale_replica_cannot_replace_acknowledged_deck_after_refresh(
    library_setup: tuple[Libraries, Path], tmp_path: Path
) -> None:
    first, remote = library_setup
    first_id = register(first)
    _ = first.sync(first_id, DECK)
    second = replica(tmp_path, first)
    second_id = register(second)
    _ = second.sync(second_id, DECK)
    _ = (first.store.root / DECK / "index.html").write_text("First replica update")
    _ = first.sync(first_id, DECK)
    _ = second.refresh(second_id)
    _ = (second.store.root / DECK / "index.html").write_text("Second replica update")
    with pytest.raises(ValueError, match="Remote changes"):
        _ = second.sync(second_id, DECK)
    assert remote_file(remote, f"{DECK}/index.html") == "First replica update"


def test_automatic_workspace_sync_preserves_other_replica_changes_and_projects(
    library_setup: tuple[Libraries, Path], tmp_path: Path
) -> None:
    first, remote = library_setup
    first_id = register(first)
    _ = first.sync(first_id, DECK)
    second = replica(tmp_path, first)
    second_id = register(second)
    _ = second.sync(second_id, DECK)
    _ = (first.store.root / DECK / "index.html").write_text("First replica update")
    extra = first.store.root / "2026-10-30-first-only"
    extra.mkdir()
    _ = (extra / "index.html").write_text("First replica project")
    _ = first.sync(first_id)
    other = second.store.root / "2026-10-31-second"
    other.mkdir()
    _ = (other / "index.html").write_text("Second replica project")
    with pytest.raises(ValueError, match="Remote changes"):
        _ = second.sync(second_id, automatic=True)
    assert remote_file(remote, f"{DECK}/index.html") == "First replica update"
    assert remote_file(remote, "2026-10-30-first-only/index.html") == "First replica project"


def test_remote_deletion_is_not_resurrected_by_stale_replica(
    library_setup: tuple[Libraries, Path], tmp_path: Path
) -> None:
    first, remote = library_setup
    first_id = register(first)
    _ = first.sync(first_id, DECK)
    second = replica(tmp_path, first)
    second_id = register(second)
    _ = second.sync(second_id, DECK)
    (first.store.root / DECK / "index.html").unlink()
    _ = first.sync(first_id, automatic=True)
    with pytest.raises(ValueError, match="Remote changes"):
        _ = second.sync(second_id, DECK)
    tracked = command("git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main")
    assert f"{DECK}/index.html" not in tracked.splitlines()


def test_automatic_backup_preserves_unacknowledged_remote_project(
    library_setup: tuple[Libraries, Path], tmp_path: Path
) -> None:
    """A project created on another computer is not a local deletion."""
    first, remote = library_setup
    first_id = register(first)
    _ = first.sync(first_id, DECK)
    second = replica(tmp_path, first)
    second_id = register(second)
    _ = second.sync(second_id, DECK)
    project = ArtifactStore(first.store).create_project("Remote project", "Remote brief")
    _ = first.sync(first_id)

    try:
        _ = second.sync(second_id, automatic=True)
    except ValueError as error:
        assert "Remote changes" in str(error)
    assert remote_file(remote, f"projects/{project.id}/README.md") == "Remote brief"
    assert '"title": "Remote project"' in remote_file(remote, f"projects/{project.id}/project.json")


def test_simultaneous_uploads_do_not_overwrite_winner(
    library_setup: tuple[Libraries, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both uploads race at push, and the losing replica remains stale after retry."""
    first, remote = library_setup
    first_id = register(first)
    _ = first.sync(first_id, DECK)
    second = replica(tmp_path, first)
    second_id = register(second)
    _ = second.sync(second_id, DECK)
    _ = (first.store.root / DECK / "index.html").write_text("First concurrent edit")
    _ = (second.store.root / DECK / "index.html").write_text("Second concurrent edit")
    barrier = threading.Barrier(2, timeout=20)
    original_git = module.git

    def synchronized_push(folder: Path, *args: str) -> str:
        if args and args[0] == "push":
            _ = barrier.wait()
        return original_git(folder, *args)

    monkeypatch.setattr(module, "git", synchronized_push)

    def upload(libraries: Libraries, identifier: str) -> str | ValueError:
        try:
            return libraries.sync(identifier, DECK).commit
        except ValueError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_result = executor.submit(upload, first, first_id)
        second_result = executor.submit(upload, second, second_id)
        results = [first_result.result(timeout=30), second_result.result(timeout=30)]
    assert sum(isinstance(result, str) for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    winner = remote_file(remote, f"{DECK}/index.html")
    assert winner in {"First concurrent edit", "Second concurrent edit"}
    assert (first.store.root / DECK / "index.html").read_text() == "First concurrent edit"
    assert (second.store.root / DECK / "index.html").read_text() == "Second concurrent edit"
    monkeypatch.setattr(module, "git", original_git)
    loser, loser_id = (second, second_id) if isinstance(results[0], str) else (first, first_id)
    with pytest.raises(ValueError):
        _ = loser.sync(loser_id, DECK)
    assert remote_file(remote, f"{DECK}/index.html") == winner
