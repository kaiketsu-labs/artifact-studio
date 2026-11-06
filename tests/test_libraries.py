"""Exercise library discovery and safe repeat sync against real disposable Git repositories."""

import asyncio
import json
import os
import threading
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from artifact_studio import app as service
from artifact_studio import libraries as module
from artifact_studio.autosync import QUIET_SECONDS, AutoSync, SyncStatus
from artifact_studio.libraries import (
    Libraries,
    LibraryInput,
    LibrarySyncSettings,
    SyncResult,
    command,
    inventory,
    safe_file,
)
from artifact_studio.storage import Store
from artifact_studio.workspace_recovery import PORTABLE_METADATA, PortableWorkspace


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
    assert first.files_changed == 3
    assert command("git", "--git-dir", str(remote), "rev-parse", "main") == first.commit
    tracked = command("git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main")
    assert ".env" not in tracked and "review.sqlite3" not in tracked
    assert "notes.md" in tracked
    assert PORTABLE_METADATA in tracked
    recovered_metadata = PortableWorkspace.model_validate_json(
        command("git", "--git-dir", str(remote), "show", f"main:{PORTABLE_METADATA}")
    )
    assert recovered_metadata.source_root == str(libraries.store.root)
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
    assert not (checkout / ".artifact-studio-manifest.json").exists()
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
    assert not (Path(library.path) / ".artifact-studio-manifest.json").exists()


def test_automatic_sync_is_explicit_and_durable(library_setup: tuple[Libraries, Path]) -> None:
    """Leave reference and legacy manual destinations idle until automatic sync is enabled."""
    libraries, remote = library_setup
    reference = libraries.register(LibraryInput(location="test/slides"))
    worker = AutoSync(libraries.store)
    worker.tick(100)
    assert not (Path(reference.path) / ".artifact-studio-manifest.json").exists()
    _ = libraries.register(LibraryInput(location="test/slides", sync_enabled=True))
    worker.tick(200)
    assert not (Path(reference.path) / ".artifact-studio-manifest.json").exists()
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


def test_frontend_rules_join_opted_in_auto_sync(
    library_setup: tuple[Libraries, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deliver saved rules through the existing worker to a disposable local Git remote."""
    libraries, remote = library_setup
    library = libraries.register(LibraryInput(location="test/slides"))
    monkeypatch.setattr(service, "store", libraries.store)
    client = TestClient(service.app)
    initial = client.get("/api/instructions").json()
    rules, preferences = "- Visually check charts.\n", "- Use concise labels.\n"
    saved = client.post(
        "/api/instructions",
        json={
            "markdown": rules,
            "expected_hash": initial["expected_hash"],
            "learned_preferences": {
                "markdown": preferences,
                "expected_hash": initial["learned_preferences_hash"],
            },
        },
    )
    assert saved.status_code == 200
    worker = AutoSync(libraries.store)
    worker.tick(100)
    assert not (Path(library.path) / ".artifact-studio-manifest.json").exists()
    _ = libraries.configure_sync(library.id, LibrarySyncSettings(auto_sync_enabled=True))
    worker.tick(200)
    worker.tick(200 + QUIET_SECONDS)
    assert worker.state(library.id).phase == "synced"
    for name, expected in (("INSTRUCTIONS.md", rules), ("LEARNED-PREFERENCES.md", preferences)):
        assert command("git", "--git-dir", str(remote), "show", f"main:{name}") == expected.strip()
    baseline = saved.json()
    assert (
        client.post(
            "/api/instructions",
            json={
                "markdown": rules,
                "expected_hash": baseline["expected_hash"],
                "learned_preferences": {
                    "markdown": "- Prefer clear arrows.\n",
                    "expected_hash": baseline["learned_preferences_hash"],
                },
            },
        ).status_code
        == 200
    )
    worker.tick(300)
    assert worker.state(library.id).phase == "pending"
    worker.tick(300 + QUIET_SECONDS)
    assert command("git", "--git-dir", str(remote), "show", "main:LEARNED-PREFERENCES.md") == (
        "- Prefer clear arrows."
    )


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


def test_paused_auto_sync_does_not_scan_files_until_explicit_retry(
    library_setup: tuple[Libraries, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An error awaiting human attention must not rehash a workspace on every idle tick."""
    libraries, _ = library_setup
    library = libraries.register(LibraryInput(location="test/slides", auto_sync_enabled=True))
    worker = AutoSync(libraries.store)
    worker.save(SyncStatus(library_id=library.id, phase="error", error="Resolve remote changes."))
    observed: list[bool] = []
    original = worker.fingerprint

    def fingerprint() -> str:
        observed.append(True)
        return original()

    monkeypatch.setattr(worker, "fingerprint", fingerprint)
    for now in (100, 103, 106):
        worker.tick(now)
    assert not observed
    assert worker.state(library.id).error == "Resolve remote changes."
    _ = worker.queue(library.id)
    worker.tick(200)
    assert observed == [True]
    assert worker.state(library.id).phase == "pending"


def test_active_sync_continues_beside_a_paused_destination(
    library_setup: tuple[Libraries, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipping paused work must not suppress a second opted-in destination or crash recovery."""
    libraries, _ = library_setup
    paused = libraries.register(LibraryInput(location="test/paused", auto_sync_enabled=True))
    active = libraries.register(LibraryInput(location="test/active", auto_sync_enabled=True))
    worker = AutoSync(libraries.store)
    worker.save(SyncStatus(library_id=paused.id, phase="error", error="Resolve remote changes."))
    worker.save(SyncStatus(library_id=active.id, phase="syncing", observed="old", synced="old"))
    delivered: list[str] = []

    def sync(identifier: str, *, automatic: bool = False) -> SyncResult:
        assert automatic
        delivered.append(identifier)
        return SyncResult(
            repository="test/active",
            commit="fictional",
            url="https://example.invalid",
            files_changed=1,
        )

    monkeypatch.setattr(worker.libraries, "sync", sync)
    worker.tick(100)
    worker.tick(100 + QUIET_SECONDS)
    assert delivered == [active.id]
    assert worker.state(active.id).phase == "synced"
    assert worker.state(paused.id).phase == "error"
    assert worker.state(paused.id).error == "Resolve remote changes."


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


def test_reverted_local_edit_clears_pending_without_an_upload(
    library_setup: tuple[Libraries, Path],
) -> None:
    """A quiet-period edit reverted to the delivered content is already backed up."""
    libraries, _ = library_setup
    library = libraries.register(LibraryInput(location="test/slides", auto_sync_enabled=True))
    worker = AutoSync(libraries.store)
    worker.tick(100)
    worker.tick(100 + QUIET_SECONDS)
    delivered = worker.state(library.id)
    path = libraries.store.root / "2026-10-28-example/notes.md"
    original = path.read_bytes()
    _ = path.write_text("A temporary change")
    worker.tick(200)
    assert worker.state(library.id).phase == "pending"
    _ = path.write_bytes(original)
    worker.tick(203)
    restored = worker.state(library.id)
    assert restored.phase == "synced"
    assert restored.commit == delivered.commit
    assert restored.last_synced_at == delivered.last_synced_at


def test_disappearing_file_during_observation_recovers_without_manual_retry(
    library_setup: tuple[Libraries, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An atomic replacement may invalidate an inventory entry before it is hashed."""
    libraries, _ = library_setup
    library = libraries.register(LibraryInput(location="test/slides", auto_sync_enabled=True))
    worker = AutoSync(libraries.store)
    original = worker.fingerprint

    def vanished() -> str:
        raise FileNotFoundError("File replaced during save")

    monkeypatch.setattr(worker, "fingerprint", vanished)
    worker.tick(100)
    assert worker.state(library.id).phase == "pending"
    assert not worker.state(library.id).error
    monkeypatch.setattr(worker, "fingerprint", original)
    worker.tick(103)
    worker.tick(103 + QUIET_SECONDS)
    assert worker.state(library.id).phase == "synced"


def test_source_removed_during_delivery_is_reobserved(
    library_setup: tuple[Libraries, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A normal removal after the quiet period must not permanently pause backup."""
    libraries, _ = library_setup
    library = libraries.register(LibraryInput(location="test/slides", auto_sync_enabled=True))
    worker = AutoSync(libraries.store)
    sync = worker.libraries.sync
    calls = 0

    def changing_source(
        identifier: str, deck_id: str | None = None, *, automatic: bool = False
    ) -> SyncResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            (libraries.store.root / "2026-10-28-example/notes.md").unlink()
            raise FileNotFoundError("Reference removed while preparing backup")
        return sync(identifier, deck_id, automatic=automatic)

    monkeypatch.setattr(worker.libraries, "sync", changing_source)
    worker.tick(100)
    worker.tick(100 + QUIET_SECONDS)
    assert worker.state(library.id).phase == "pending"
    worker.tick(111)
    worker.tick(111 + QUIET_SECONDS)
    assert calls == 2
    assert worker.state(library.id).phase == "synced"
    assert not (Path(library.path) / "2026-10-28-example/notes.md").exists()


def test_unacknowledged_push_retries_without_a_duplicate_commit(
    library_setup: tuple[Libraries, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise actual Git delivery, then lose only its acknowledgement."""
    libraries, remote = library_setup
    library = libraries.register(LibraryInput(location="test/slides", auto_sync_enabled=True))
    run = module.command
    lose_ack = True

    def unreliable(*args: str, cwd: Path | None = None) -> str:
        nonlocal lose_ack
        result = run(*args, cwd=cwd)
        if "push" in args and lose_ack:
            lose_ack = False
            raise ValueError("Connection lost after push")
        return result

    monkeypatch.setattr(module, "command", unreliable)
    worker = AutoSync(libraries.store)
    worker.tick(100)
    worker.tick(100 + QUIET_SECONDS)
    assert worker.state(library.id).phase == "error"
    delivered = command("git", "--git-dir", str(remote), "rev-parse", "main")
    _ = worker.queue(library.id)
    worker.tick(200)
    worker.tick(200 + QUIET_SECONDS)
    assert worker.state(library.id).phase == "synced"
    assert worker.state(library.id).commit == delivered
    assert command("git", "--git-dir", str(remote), "rev-list", "--count", "main") == "1"


def test_restart_reconciles_an_interrupted_delivery(
    library_setup: tuple[Libraries, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A persisted syncing phase is an unknown outcome and must be checked again."""
    libraries, _ = library_setup
    library = libraries.register(LibraryInput(location="test/slides", auto_sync_enabled=True))
    worker = AutoSync(libraries.store)
    worker.tick(100)
    worker.tick(100 + QUIET_SECONDS)
    checkpoint = worker.state(library.id)
    checkpoint.phase = "syncing"
    worker.save(checkpoint)
    restarted = AutoSync(libraries.store)
    attempts: list[str] = []
    sync = restarted.libraries.sync

    def deliver(
        identifier: str, deck_id: str | None = None, *, automatic: bool = False
    ) -> SyncResult:
        attempts.append(identifier)
        return sync(identifier, deck_id, automatic=automatic)

    monkeypatch.setattr(restarted.libraries, "sync", deliver)
    restarted.tick(200)
    assert restarted.state(library.id).phase == "pending"
    restarted.tick(200 + QUIET_SECONDS)
    assert attempts == [library.id]
    assert restarted.state(library.id).phase == "synced"


def test_auto_sync_includes_owned_current_artifact_sources_without_history(
    library_setup: tuple[Libraries, Path], tmp_path: Path
) -> None:
    """Publish a fictional bundle and its metadata without private or revision state."""
    from artifact_studio.artifacts import ArtifactStore

    libraries, remote = library_setup
    artifacts = ArtifactStore(libraries.store)
    project = artifacts.create_project("Fictional project", "A fictional project brief")
    bundle = tmp_path / "fictional-bundle"
    (bundle / "research").mkdir(parents=True)
    _ = (bundle / "index.html").write_text('<script type="module" src="app.mjs"></script>')
    _ = (bundle / "app.mjs").write_text("export const answer = 42;")
    _ = (bundle / "research" / "model.bin").write_bytes(b"fictional-data")
    _ = (bundle / ".env").write_text("private")
    _ = (bundle / "secrets.json").write_text("private")
    artifact = artifacts.register_artifact(project.id, "Fictional page", bundle, "index.html")
    library = libraries.register(LibraryInput(location="test/slides", auto_sync_enabled=True))
    worker = AutoSync(libraries.store)
    first_fingerprint = worker.fingerprint()
    root = libraries.store.root / "projects" / project.id
    source = root / "artifacts" / artifact.id / "files"
    _ = (source / "research" / "model.bin").write_bytes(b"updated-fictional-data")
    assert worker.fingerprint() != first_fingerprint
    worker.tick(10)
    worker.tick(10 + QUIET_SECONDS)
    assert worker.state(library.id).phase == "synced"
    tracked = set(
        command(
            "git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main"
        ).splitlines()
    )
    prefix = f"projects/{project.id}"
    artifact_prefix = f"{prefix}/artifacts/{artifact.id}"
    assert {
        f"{prefix}/README.md",
        f"{prefix}/project.json",
        f"{artifact_prefix}/artifact.json",
    } <= tracked
    assert {
        f"{artifact_prefix}/files/index.html",
        f"{artifact_prefix}/files/app.mjs",
        f"{artifact_prefix}/files/research/model.bin",
    } <= tracked
    assert not any(
        "/revisions/" in path or path.endswith(("/.env", "/secrets.json")) for path in tracked
    )
    assert (
        command(
            "git",
            "--git-dir",
            str(remote),
            "show",
            f"main:{artifact_prefix}/files/research/model.bin",
        )
        == "updated-fictional-data"
    )


def test_auto_sync_preserves_previously_managed_revision_history(
    library_setup: tuple[Libraries, Path],
) -> None:
    """Stop updating old revision snapshots without deleting already delivered history."""
    import json

    libraries, remote = library_setup
    library = libraries.register(LibraryInput(location="test/slides", auto_sync_enabled=True))
    _ = libraries.sync(library.id)
    checkout = Path(library.path)
    relative = "projects/project-a/artifacts/artifact-a/revisions/1/files/old.bin"
    old = checkout / relative
    old.parent.mkdir(parents=True)
    _ = old.write_bytes(b"old revision")
    manifest_path = checkout / ".artifact-studio-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest[relative] = module.file_hash(old)
    _ = manifest_path.write_text(json.dumps(manifest))
    _ = module.git(checkout, "add", "--", relative, manifest_path.name)
    _ = module.git(checkout, "commit", "-m", "test(sync): retain older revision")
    _ = module.git(checkout, "push")
    _ = libraries.sync(library.id, automatic=True)
    assert command("git", "--git-dir", str(remote), "show", f"main:{relative}") == "old revision"
    assert relative in json.loads(manifest_path.read_text())


def test_local_save_observes_saved_workspace_files_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use file modification times, ignoring private state, history and cached references."""
    root = tmp_path / "collection"
    root.mkdir()
    store = Store(root, root / "state" / "review.sqlite3")
    libraries = Libraries(store)
    clock = 10.0
    monkeypatch.setattr(module.time, "monotonic", lambda: clock)

    def saved(relative: str, stamp: int) -> Path:
        """Create a fictional file with a deterministic modification time."""
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text("fictional")
        os.utime(path, (stamp, stamp))
        return path

    saved("deck/index.html", 100)
    saved("deck/notes.md", 200)
    saved("INSTRUCTIONS.md", 300)
    source = saved("projects/p/artifacts/a/files/research/source.bin", 400)
    saved("projects/p/artifacts/a/revisions/1/files/old.html", 900)
    saved("workspace-recovery.json", 900)
    saved("review.sqlite3", 900)
    saved("state/libraries/cached/index.html", 900)
    saved(".hidden/file.html", 900)

    def refuse_read(_: Path) -> bytes:
        """Ensure observation never opens file contents."""
        pytest.fail("contents were read")

    monkeypatch.setattr(Path, "read_bytes", refuse_read)
    first = libraries.list()[0].last_saved_at
    assert first is not None and datetime.fromisoformat(first).timestamp() == 400
    os.utime(source, (500, 500))
    assert Libraries(store).list()[0].last_saved_at == first
    clock += 16
    second = Libraries(store).list()[0].last_saved_at
    assert second is not None and datetime.fromisoformat(second).timestamp() == 500
    clock += 16
    assert Libraries(store).list()[0].last_saved_at == second


def test_local_save_empty_or_unavailable_is_unknown(tmp_path: Path) -> None:
    """An empty or missing collection has no invented save timestamp."""
    root = tmp_path / "empty"
    root.mkdir()
    cache = tmp_path / "cache"
    assert module.last_local_save(root, cache) is None
    root.rmdir()
    assert module.last_local_save(root, tmp_path / "other-cache") is None


def test_local_save_excludes_registered_nested_reference_and_serializes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exclude registered references without hiding a collection registered as itself."""
    root = tmp_path / "collection"
    root.mkdir()
    source = root / "deck" / "index.html"
    source.parent.mkdir()
    _ = source.write_text("<aside>Notes in presentation</aside>")
    os.utime(source, (0, 0))
    reference = root / "reference-library"
    reference.mkdir()
    _ = (reference / "reference.html").write_text("read-only reference")
    store = Store(root, tmp_path / "state" / "review.sqlite3")
    libraries = Libraries(store)
    _ = libraries.register(LibraryInput(location=str(reference)))
    _ = libraries.register(LibraryInput(location=str(root)))
    _ = libraries.register(LibraryInput(location=str(tmp_path)))
    monkeypatch.setattr(service, "store", store)
    item = libraries.list()[0]
    assert item.last_saved_at is not None
    assert datetime.fromisoformat(item.last_saved_at).timestamp() == 0
    assert json.loads(json.dumps(service.get_working_context(include_discovery=True)))
    response = TestClient(service.app).get("/api/libraries")
    assert response.status_code == 200
    assert response.json()[0]["last_saved_at"] == item.last_saved_at
    assert (
        service.list_libraries()[0].model_dump(mode="json")["last_saved_at"] == item.last_saved_at
    )
