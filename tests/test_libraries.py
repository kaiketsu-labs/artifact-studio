"""Exercise library discovery and safe repeat sync against real disposable Git repositories."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import app as service
from studio import libraries as module
from studio.libraries import Libraries, LibraryInput, command, inventory, safe_file
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
