"""Exercise library discovery and safe repeat sync against real disposable Git repositories."""

import asyncio
import os
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import app as service
from studio import libraries as module
from studio.autosync import QUIET_SECONDS, AutoSync
from studio.libraries import (
    Libraries,
    LibraryInput,
    LibrarySyncSettings,
    command,
    inventory,
    safe_file,
)
from studio.storage import Store


@pytest.fixture
def library_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Libraries, Path]:
    """Route only the external GitHub transport to a disposable local bare repository."""
    root = tmp_path / "collection"
    root.mkdir()
    deck = root / "2026-10-28-example"
    deck.mkdir()
    _ = (deck / "index.html").write_text("<h1>Local draft</h1>")
    _ = (deck / "notes.md").write_text("Speaker notes")
    _ = (root / ".env").write_text("secret=not-for-sync")
    _ = (root / "review.sqlite3").write_text("private review state")
    cache = tmp_path / "state"
    remote = tmp_path / "remote.git"
    _ = command("git", "init", "--bare", "--initial-branch=main", str(remote))
    store = Store(root, cache / "review.sqlite3")
    libraries = Libraries(store)

    def run(*args: str, cwd: Path | None = None) -> str:
        """Use real Git and local transport while simulating GitHub metadata and cloning."""
        if args[:3] == ("gh", "repo", "view"):
            return "true"
        if args[:3] == ("gh", "repo", "clone"):
            result = command("git", "clone", str(remote), args[4])
            _ = command("git", "config", "user.email", "tests@example.invalid", cwd=Path(args[4]))
            _ = command("git", "config", "user.name", "Library tests", cwd=Path(args[4]))
            _ = command("git", "config", "commit.gpgsign", "false", cwd=Path(args[4]))
            return result
        return command(
            *(
                arg.replace("protocol.file.allow=never", "protocol.file.allow=always")
                for arg in args
            ),
            cwd=cwd,
        )

    monkeypatch.setattr(module, "command", run)
    return libraries, remote


def test_repeat_sync_and_remote_conflict(library_setup: tuple[Libraries, Path]) -> None:
    """Upload real files once, preserve repeat commits and reject independently edited content."""
    libraries, remote = library_setup
    library = libraries.register(
        LibraryInput(location="https://github.com/test/slides", sync_enabled=True)
    )
    first = libraries.sync(library.id)
    assert first.files_changed == 2
    assert command("git", "--git-dir", str(remote), "rev-parse", "main") == first.commit
    tracked = command("git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main")
    assert ".env" not in tracked and "review.sqlite3" not in tracked
    assert "notes.md" in tracked
    second = libraries.sync(library.id)
    assert second.files_changed == 0 and second.commit == first.commit
    local = libraries.store.root / "2026-10-28-example/index.html"
    _ = local.write_text("<h1>Updated locally</h1>")
    third = libraries.sync(library.id, "2026-10-28-example")
    assert third.files_changed == 1 and third.commit != first.commit
    checkout = Path(library.path)
    _ = (checkout / "2026-10-28-example/index.html").write_text("Remote collaborator edit")
    _ = module.git(checkout, "add", ".")
    _ = module.git(checkout, "commit", "-m", "test(references): simulate remote edit")
    _ = module.git(checkout, "push")
    with pytest.raises(ValueError, match="Remote changes"):
        _ = libraries.sync(library.id)
    assert local.read_text() == "<h1>Updated locally</h1>"
    assert (checkout / "2026-10-28-example/index.html").read_text() == "Remote collaborator edit"


def test_reference_only_and_private_checks(
    library_setup: tuple[Libraries, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registration alone cannot upload and changing a destination to public stops later sync."""
    libraries, _ = library_setup
    library = libraries.register(LibraryInput(location="test/slides"))
    with pytest.raises(ValueError, match="Enable sync"):
        _ = libraries.sync(library.id)
    library = libraries.register(LibraryInput(location="test/slides", sync_enabled=True))
    original = module.command

    def public(*args: str, cwd: Path | None = None) -> str:
        """Report a visibility change without changing other Git behavior."""
        return "false" if args[:3] == ("gh", "repo", "view") else original(*args, cwd=cwd)

    monkeypatch.setattr(module, "command", public)
    with pytest.raises(ValueError, match="only for private"):
        _ = libraries.sync(library.id)
    with pytest.raises(ValueError, match="only for private"):
        _ = libraries.register(LibraryInput(location="test/public", sync_enabled=True))


def test_local_assets_and_file_boundaries(
    library_setup: tuple[Libraries, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Make masters discoverable without exposing hidden files or executing imported HTML."""
    libraries, _ = library_setup
    assets = tmp_path / "brand"
    assets.mkdir()
    _ = (assets / "Master.pptx").write_bytes(b"PK\x00\xffsource")
    _ = (assets / "icon.svg").write_text('<svg viewBox="0 0 24 24"/>')
    _ = (assets / "example.html").write_text("<h1>Reference</h1>")
    (assets / "outside.md").symlink_to(libraries.store.root / ".env")
    library = libraries.register(LibraryInput(location=str(assets)))
    assert {item.path for item in inventory(assets)} == {"Master.pptx", "icon.svg", "example.html"}
    for path in ("../collection/.env", "/etc/passwd", "outside.md", ".git/config"):
        with pytest.raises(ValueError):
            _ = safe_file(assets, path)
    monkeypatch.setattr(service, "store", libraries.store)
    client = TestClient(service.app)
    response = client.get(f"/api/libraries/{library.id}/files/example.html")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["content-type"] == "application/octet-stream"
    assert client.get("/api/libraries").json()[0]["id"] == "collection"
    result = service.read_library_file(library.id, "Master.pptx")
    assert "text" not in result and result["local_path"].endswith("Master.pptx")


def test_unmanaged_collision_and_lock(library_setup: tuple[Libraries, Path]) -> None:
    """Protect existing repository content and prevent simultaneous checkout mutations."""
    libraries, _ = library_setup
    library = libraries.register(LibraryInput(location="test/slides", sync_enabled=True))
    checkout = Path(library.path)
    target = checkout / "2026-10-28-example"
    target.mkdir()
    _ = (target / "index.html").write_text("Existing repository deck")
    _ = module.git(checkout, "add", ".")
    _ = module.git(checkout, "commit", "-m", "test(references): create existing deck")
    _ = module.git(checkout, "push", "-u", "origin", "main")
    with pytest.raises(ValueError, match="Remote changes"):
        _ = libraries.sync(library.id)
    assert not (checkout / ".slide-studio-manifest.json").exists()
    with libraries.locked(), pytest.raises(ValueError, match="operation is running"):
        _ = libraries.refresh(library.id)


def test_changing_file_does_not_leave_partial_checkout(
    library_setup: tuple[Libraries, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Abort a continuously changing source before writing any destination files."""
    libraries, _ = library_setup
    library = libraries.register(LibraryInput(location="test/slides", sync_enabled=True))
    original_copy = module.shutil.copy2
    counter = 0

    def changing(source: Path, target: Path) -> Path:
        """Simulate another editor saving while the snapshot is being copied."""
        nonlocal counter
        counter += 1
        _ = source.write_text(f"Concurrent save {counter}")
        return Path(original_copy(source, target))

    monkeypatch.setattr(module.shutil, "copy2", changing)
    with pytest.raises(ValueError, match="changing during sync"):
        _ = libraries.sync(library.id)
    assert module.git(Path(library.path), "status", "--porcelain") == ""
    assert not (Path(library.path) / ".slide-studio-manifest.json").exists()


def test_automatic_sync_is_explicit_and_durable(library_setup: tuple[Libraries, Path]) -> None:
    """Leave reference and legacy manual destinations idle until automatic sync is enabled."""
    libraries, remote = library_setup
    reference = libraries.register(LibraryInput(location="test/slides"))
    worker = AutoSync(libraries.store)
    worker.tick(100)
    assert not (Path(reference.path) / ".slide-studio-manifest.json").exists()
    _ = libraries.register(LibraryInput(location="test/slides", sync_enabled=True))
    worker.tick(200)
    assert not (Path(reference.path) / ".slide-studio-manifest.json").exists()
    _ = libraries.configure_sync(reference.id, LibrarySyncSettings(auto_sync_enabled=True))
    worker.tick(300)
    assert worker.state(reference.id).phase == "pending"
    worker.tick(300 + QUIET_SECONDS)
    state = worker.state(reference.id)
    assert state.phase == "synced" and state.last_synced_at
    assert command("git", "--git-dir", str(remote), "rev-parse", "main") == state.commit
    restarted = AutoSync(libraries.store)
    restarted.tick(500)
    assert restarted.state(reference.id) == state
    assert libraries.get(reference.id).auto_sync_enabled


def test_auto_sync_debounces_all_projects_and_respects_disable(
    library_setup: tuple[Libraries, Path],
) -> None:
    """Include stable saved changes across the workspace while excluding review state."""
    libraries, remote = library_setup
    library = libraries.register(LibraryInput(location="test/slides", auto_sync_enabled=True))
    worker = AutoSync(libraries.store)
    worker.tick(100)
    extra = libraries.store.root / "2026-10-30-second"
    extra.mkdir()
    _ = (extra / "index.html").write_text("<h1>Second fictional project</h1>")
    worker.tick(105)
    worker.tick(109)
    assert worker.state(library.id).phase == "pending"
    worker.tick(105 + QUIET_SECONDS)
    tracked = command("git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main")
    assert "2026-10-30-second/index.html" in tracked
    assert "review.sqlite3" not in tracked and ".env" not in tracked
    original = worker.state(library.id).commit
    _ = (extra / "index.html").write_text("<h1>A later save</h1>")
    worker.tick(200)
    worker.tick(200 + QUIET_SECONDS)
    assert worker.state(library.id).commit != original
    _ = libraries.configure_sync(library.id, LibrarySyncSettings(auto_sync_enabled=False))
    assert not libraries.get(library.id).sync_enabled
    previous = worker.state(library.id)
    _ = (extra / "index.html").write_text("<h1>Keep this local now</h1>")
    worker.tick(300)
    worker.tick(400)
    assert worker.state(library.id) == previous
    assert worker.statuses()[0].phase == "disabled"
    with pytest.raises(ValueError, match="Enable sync"):
        _ = libraries.sync(library.id)


def test_auto_sync_reports_failures_and_retries_only_when_requested(
    library_setup: tuple[Libraries, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep a failed upload pending for attention without repeatedly contacting the destination."""
    libraries, _ = library_setup
    library = libraries.register(LibraryInput(location="test/slides", auto_sync_enabled=True))
    worker = AutoSync(libraries.store)
    attempts: list[str] = []

    def fail(identifier: str, deck_id: str | None = None, *, automatic: bool = False) -> None:
        """Simulate a conflicting remote without changing local or remote files."""
        attempts.append(identifier)
        raise ValueError("Remote changes need review before syncing.")

    monkeypatch.setattr(worker.libraries, "sync", fail)
    worker.tick(100)
    worker.tick(100 + QUIET_SECONDS)
    worker.tick(200)
    assert attempts == [library.id]
    assert worker.state(library.id).phase == "error"
    assert "Remote changes" in worker.state(library.id).error
    _ = (libraries.store.root / "2026-10-28-example/notes.md").write_text("Unrelated save")
    worker.tick(210)
    worker.tick(220)
    assert attempts == [library.id]
    _ = worker.queue(library.id)
    worker.tick(300)
    worker.tick(300 + QUIET_SECONDS)
    assert attempts == [library.id, library.id]


def test_auto_sync_settings_http_and_cache_exclusion(
    library_setup: tuple[Libraries, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expose opt-in settings and keep nested repository caches out of workspace snapshots."""
    libraries, _ = library_setup
    monkeypatch.setattr(service, "store", libraries.store)
    client = TestClient(service.app)
    created = client.post("/api/libraries", json={"location": "test/slides"}).json()
    identifier = created["id"]
    assert not created["auto_sync_enabled"]
    response = client.post(
        f"/api/libraries/{identifier}/settings", json={"auto_sync_enabled": True}
    )
    assert response.status_code == 200 and response.json()["sync_enabled"]
    assert client.get("/api/library-sync").json()[0]["phase"] == "pending"
    assert client.post(f"/api/libraries/{identifier}/retry", json={}).status_code == 200
    _ = client.post(f"/api/libraries/{identifier}/settings", json={"auto_sync_enabled": False})
    assert client.post(f"/api/libraries/{identifier}/retry", json={}).status_code == 400
    nested = Libraries(Store(libraries.store.root, libraries.store.root / "nested.sqlite3"))
    leaked = nested.cache / "unrelated-reference.json"
    leaked.write_text('{"reference":"not part of the workspace"}')
    assert not any(item.local_path == str(leaked) for item in nested.snapshot_files())


def test_auto_sync_detects_preserved_metadata(library_setup: tuple[Libraries, Path]) -> None:
    """Detect content replacements even when a copying tool preserves file size and mtime."""
    libraries, _ = library_setup
    worker = AutoSync(libraries.store)
    source = libraries.store.root / "2026-10-28-example/notes.md"
    before = source.stat()
    fingerprint = worker.fingerprint()
    _ = source.write_text("Changed notes")
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert source.stat().st_size == before.st_size
    assert worker.fingerprint() != fingerprint


def test_auto_sync_observation_failure_is_visible(
    library_setup: tuple[Libraries, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replace an old successful status when saved inputs can no longer be read."""
    libraries, _ = library_setup
    library = libraries.register(LibraryInput(location="test/slides", auto_sync_enabled=True))
    worker = AutoSync(libraries.store)
    worker.tick(100)
    worker.tick(100 + QUIET_SECONDS)
    assert worker.state(library.id).phase == "synced"

    def unreadable() -> str:
        """Simulate an unreadable saved file without relying on operating-system ownership."""
        raise PermissionError("notes.md is unreadable")

    monkeypatch.setattr(worker, "fingerprint", unreadable)
    worker.tick(200)
    assert worker.state(library.id).phase == "error"
    assert "notes.md is unreadable" in worker.state(library.id).error


def test_auto_sync_shutdown_waits_for_delivery(
    library_setup: tuple[Libraries, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Let an in-flight operation finish and avoid starting another after shutdown."""
    libraries, _ = library_setup
    worker = AutoSync(libraries.store)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    attempts: list[bool] = []

    def delivery() -> None:
        """Hold a fictional delivery until its orderly shutdown has been observed."""
        attempts.append(True)
        started.set()
        assert release.wait(5)
        finished.set()

    async def shutdown() -> None:
        """Stop scheduling new work while awaiting the current thread."""
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run(stop))
        assert await asyncio.to_thread(started.wait, 5)
        stop.set()
        await asyncio.sleep(0)
        assert not task.done() and not finished.is_set()
        release.set()
        await asyncio.wait_for(task, 5)
        assert finished.is_set() and attempts == [True]

    monkeypatch.setattr(worker, "tick", delivery)
    asyncio.run(shutdown())


def test_auto_sync_removes_only_unchanged_managed_files(
    library_setup: tuple[Libraries, Path],
) -> None:
    """Mirror removals without deleting unrelated files or a collaborator's changed copy."""
    libraries, _ = library_setup
    library = libraries.register(LibraryInput(location="test/slides", auto_sync_enabled=True))
    _ = libraries.sync(library.id, automatic=True)
    checkout = Path(library.path)
    _ = (checkout / "unrelated.md").write_text("Repository context")
    _ = module.git(checkout, "add", ".")
    _ = module.git(checkout, "commit", "-m", "test(sync): add unrelated context")
    _ = module.git(checkout, "push")
    (libraries.store.root / "2026-10-28-example/notes.md").unlink()
    result = libraries.sync(library.id, automatic=True)
    assert result.files_changed == 1
    assert not (checkout / "2026-10-28-example/notes.md").exists()
    assert (checkout / "unrelated.md").read_text() == "Repository context"
    _ = (checkout / "2026-10-28-example/index.html").write_text("Independent edit")
    _ = module.git(checkout, "add", ".")
    _ = module.git(checkout, "commit", "-m", "test(sync): edit remote slide")
    _ = module.git(checkout, "push")
    (libraries.store.root / "2026-10-28-example/index.html").unlink()
    with pytest.raises(ValueError, match="Remote changes"):
        _ = libraries.sync(library.id, automatic=True)
    assert (checkout / "2026-10-28-example/index.html").read_text() == "Independent edit"
