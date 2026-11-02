"""Local reference libraries and explicit, conflict-aware private GitHub snapshots."""

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from pydantic import Field, TypeAdapter

from .models import StrictModel
from .storage import Store

EXCLUDED = {"node_modules", "__pycache__", "research", "qa", "feedback-files"}
EXTENSIONS = {
    ".html",
    ".css",
    ".js",
    ".json",
    ".md",
    ".txt",
    ".emf",
    ".wmf",
    ".wdp",
    ".pptx",
    ".potx",
    ".ppt",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".svg",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".csv",
    ".tsv",
    ".xlsx",
    ".mp4",
    ".webm",
}
REPOSITORY = re.compile(r"(?:https://github\.com/)?([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/?")


class LibraryInput(StrictModel):
    """A user-selected folder or GitHub repository, optionally enabled for uploads."""

    location: str = Field(min_length=1, max_length=2048)
    sync_enabled: bool = False
    auto_sync_enabled: bool = False


class Library(StrictModel):
    """A durable reference with a local working copy and explicit sync permission."""

    id: str
    kind: Literal["local", "github"]
    location: str
    path: str
    sync_enabled: bool = False
    auto_sync_enabled: bool = False


class LibrarySyncSettings(StrictModel):
    """Explicitly opt a private repository into automatic workspace snapshots."""

    auto_sync_enabled: bool = False


class LibraryFile(StrictModel):
    """Discoverable source metadata without loading large binary files into agent context."""

    path: str
    local_path: str
    size: int


class SyncInput(StrictModel):
    """Select the complete collection or one canonical presentation for a snapshot."""

    deck_id: str | None = None


class SyncResult(StrictModel):
    """The verified outcome of an explicit repository upload."""

    repository: str
    commit: str
    url: str
    files_changed: int


def command(*args: str, cwd: Path | None = None) -> str:
    """Run a bounded command without a shell or interactive credential prompts."""
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"},
        )
    except FileNotFoundError as error:
        raise ValueError(f"Install {args[0]} and sign in with gh auth login.") from error
    except subprocess.TimeoutExpired as error:
        raise ValueError("GitHub operation timed out. Retry to check its current state.") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout).strip()[-1200:]
        raise ValueError(f"{args[0]} operation failed: {detail}") from error
    return result.stdout.strip()


def git(folder: Path, *args: str) -> str:
    """Use Git without repository hooks or recursive submodule execution."""
    return command(
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "protocol.file.allow=never",
        "-c",
        "submodule.recurse=false",
        *args,
        cwd=folder,
    )


def snapshot_target(destination: Path, relative: str) -> Path:
    """Validate managed paths before writing or removing a repository snapshot file."""
    path = Path(relative)
    target = destination / path
    if (
        path.is_absolute()
        or ".." in path.parts
        or not snapshot_allowed(path)
        or any(part.is_symlink() for part in (target, *target.parents) if part != destination)
        or not target.resolve().is_relative_to(destination.resolve())
    ):
        raise ValueError(f"Remote path escapes the repository: {relative}")
    return target


def managed_reference(relative: Path) -> bool:
    """Recognize only the reserved, content-addressed upload tree."""
    return (
        len(relative.parts) >= 4
        and relative.parts[-4:-2] == ("reference", "uploads")
        and re.fullmatch(r"[a-f0-9]{64}", relative.parts[-2]) is not None
    )


def allowed(
    relative: Path,
    *,
    all_extensions: bool = False,
    excluded: frozenset[str] = frozenset(EXCLUDED),
) -> bool:
    """Keep caches, credentials, hidden state and unsupported files out of snapshots."""
    return (
        (all_extensions or relative.suffix.lower() in EXTENSIONS or managed_reference(relative))
        and not any(part.startswith(".") or part in excluded for part in relative.parts)
        and relative.name.lower() not in {"credentials.json", "secrets.json", "secrets.txt"}
    )


def artifact_source(relative: Path) -> bool:
    """Recognize working artifact files without treating other project paths as bundles."""
    parts = relative.parts
    return (
        len(parts) >= 6
        and parts[0] == "projects"
        and parts[2] == "artifacts"
        and parts[4] == "files"
    )


def snapshot_allowed(relative: Path) -> bool:
    """Include arbitrary owned source types while excluding hidden state and credentials."""
    if relative.parts and relative.parts[0] == "projects":
        if re.search(r"\.(?:db|sqlite|sqlite3)(?:-wal|-shm|-journal)?$", relative.name, re.I):
            return False
        excluded: frozenset[str] = frozenset() if artifact_source(relative) else frozenset(EXCLUDED)
        return allowed(relative, all_extensions=True, excluded=excluded)
    return allowed(relative)


def revision_directory(relative: Path) -> bool:
    """Leave immutable history out of portable current-source snapshots."""
    parts = relative.parts
    return (
        len(parts) >= 5
        and parts[0] == "projects"
        and parts[2] == "artifacts"
        and parts[4] == "revisions"
    )


def inventory(
    root: Path,
    *,
    all_extensions: bool = False,
    excluded: frozenset[str] = frozenset(EXCLUDED),
    snapshot: bool = False,
) -> list[LibraryFile]:
    """List reusable documents and assets without following symbolic links."""
    result: list[LibraryFile] = []
    for folder, directories, names in root.walk(follow_symlinks=False):
        directories[:] = [
            name
            for name in directories
            if not name.startswith(".")
            and (
                name not in excluded
                or snapshot
                and artifact_source((folder / name).relative_to(root) / "placeholder")
            )
            and not (snapshot and revision_directory((folder / name).relative_to(root)))
            and not (folder / name).is_symlink()
        ]
        for name in sorted(names):
            path = folder / name
            relative = path.relative_to(root)
            if (
                (
                    snapshot_allowed(relative)
                    if snapshot
                    else allowed(relative, all_extensions=all_extensions, excluded=excluded)
                )
                and path.is_file()
                and not path.is_symlink()
            ):
                result.append(
                    LibraryFile(
                        path=relative.as_posix(),
                        local_path=str(path),
                        size=path.stat().st_size,
                    )
                )
    return sorted(result, key=lambda item: item.path)


def safe_file(root: Path, name: str) -> Path:
    """Resolve a registered document without exposing hidden state or escaped symlinks."""
    relative = Path(name)
    target = (root / relative).resolve()
    if relative.is_absolute() or ".." in relative.parts or not allowed(relative):
        raise ValueError("Choose a document from the library inventory.")
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise ValueError("The requested library file is unavailable.")
    return target


def file_hash(path: Path) -> str:
    """Hash a file incrementally so large presentation media does not fill memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class Libraries:
    """Register references and publish deliberate snapshots without changing local decks."""

    def __init__(self, store: Store) -> None:
        """Reuse the workspace database and isolate repository checkouts in local state."""
        self.store = store
        self.cache = store.database.parent / "libraries"
        self.cache.mkdir(parents=True, exist_ok=True)
        with store.connect() as db:
            _ = db.execute(
                "CREATE TABLE IF NOT EXISTS libraries (id TEXT PRIMARY KEY, document TEXT)"
            )

    @contextmanager
    def locked(self) -> Generator[None]:
        """Prevent concurrent clone, refresh and sync operations across server processes."""
        with (self.cache / ".lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError(
                    "A library operation is running. Try again when it finishes."
                ) from error
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def list(self) -> list[Library]:
        """Include the canonical collection alongside explicitly registered sources."""
        local = Library(
            id="collection", kind="local", location=str(self.store.root), path=str(self.store.root)
        )
        with self.store.connect() as db:
            return [
                local,
                *(
                    Library.model_validate_json(str(row[0]))
                    for row in db.execute("SELECT document FROM libraries ORDER BY id")
                ),
            ]

    def get(self, identifier: str) -> Library:
        """Resolve a registered library ID without treating it as a filesystem path."""
        library = next((item for item in self.list() if item.id == identifier), None)
        if library is None:
            raise ValueError("Unknown library. Call list_libraries first.")
        return library

    def verify_repository(self, repository: str, sync: bool) -> None:
        """Require a private destination every time an upload is requested."""
        private = command(
            "gh", "repo", "view", repository, "--json", "isPrivate", "--jq", ".isPrivate"
        )
        if sync and private != "true":
            raise ValueError("Sync is available only for private GitHub repositories.")

    def register(self, body: LibraryInput) -> Library:
        """Persist a folder or clone an accessible repository without uploading anything."""
        location = body.location.strip().removesuffix("/").removesuffix(".git")
        match = REPOSITORY.fullmatch(location)
        kind: Literal["local", "github"] = "github" if match else "local"
        sync_enabled = body.sync_enabled or body.auto_sync_enabled
        if match:
            location = match[1]
            if any(part.startswith(".") for part in location.split("/")):
                raise ValueError("Use a GitHub owner/repository or an absolute folder path.")
            self.verify_repository(location, sync_enabled)
        elif not location.startswith(("/", "~/")) or sync_enabled:
            raise ValueError("Use a GitHub URL or absolute local folder. Sync requires GitHub.")
        else:
            location = str(Path(location).expanduser().resolve())
            if not Path(location).is_dir():
                raise ValueError("The local reference folder does not exist.")
        identifier = hashlib.sha256(location.encode()).hexdigest()[:16]
        path = self.cache / identifier if match else Path(location)
        with self.locked():
            if match and not path.exists():
                command("gh", "repo", "clone", location, str(path), "--", "--no-recurse-submodules")
            library = Library(
                id=identifier,
                kind=kind,
                location=location,
                path=str(path),
                sync_enabled=sync_enabled,
                auto_sync_enabled=body.auto_sync_enabled,
            )
            with self.store.connect() as db:
                _ = db.execute(
                    "INSERT OR REPLACE INTO libraries VALUES (?, ?)",
                    (identifier, library.model_dump_json()),
                )
        return library

    def configure_sync(self, identifier: str, body: LibrarySyncSettings) -> Library:
        """Change opt-in permission without implicitly enabling any existing repository."""
        with self.locked():
            library = self.get(identifier)
            if library.kind != "github":
                raise ValueError("Automatic sync requires a private GitHub repository.")
            if body.auto_sync_enabled:
                self.verify_repository(library.location, True)
            library = library.model_copy(
                update={
                    "sync_enabled": body.auto_sync_enabled,
                    "auto_sync_enabled": body.auto_sync_enabled,
                }
            )
            with self.store.connect() as db:
                _ = db.execute(
                    "UPDATE libraries SET document=? WHERE id=?",
                    (library.model_dump_json(), identifier),
                )
            return library

    def refresh_checkout(self, library: Library) -> None:
        """Fast-forward a clean cache while retaining any local or remote divergence."""
        folder = Path(library.path)
        if git(folder, "status", "--porcelain"):
            raise ValueError("The library cache has uncommitted changes. Resolve them before sync.")
        git(folder, "fetch", "origin", "--prune")
        remote_head = git(folder, "ls-remote", "--symref", "origin", "HEAD")
        if not remote_head:
            return
        match = re.search(r"ref: refs/heads/(\S+)\s+HEAD", remote_head)
        if not match:
            raise ValueError("The repository default branch is unavailable.")
        if git(folder, "branch", "--show-current") != match[1]:
            raise ValueError("The library cache is not on the repository default branch.")
        git(folder, "pull", "--ff-only", "origin", match[1])

    def refresh(self, identifier: str) -> Library:
        """Fetch new reference material without overwriting the canonical local collection."""
        library = self.get(identifier)
        if library.kind == "github":
            with self.locked():
                self.refresh_checkout(library)
        return library

    def files(self, identifier: str) -> list[LibraryFile]:
        """Expose original masters, custom artwork and earlier decks to agents."""
        return inventory(Path(self.get(identifier).path))

    def snapshot_files(self, deck_id: str | None = None) -> list[LibraryFile]:
        """Select canonical files without recursively including a configured reference cache."""
        source = self.store.folder(deck_id) if deck_id else self.store.root
        if not source.is_dir():
            raise ValueError("The presentation folder does not exist.")
        return [
            item
            for item in inventory(source, snapshot=deck_id is None)
            if not Path(item.local_path).is_relative_to(self.cache)
        ]

    def sync(
        self, identifier: str, deck_id: str | None = None, *, automatic: bool = False
    ) -> SyncResult:
        """Publish saved files while protecting independent remote changes."""
        library = self.get(identifier)
        if library.kind != "github" or not library.sync_enabled:
            raise ValueError("Enable sync on a private GitHub library first.")
        root = self.store.root
        files = self.snapshot_files(deck_id)
        if any(item.size >= 100_000_000 for item in files):
            raise ValueError(
                "A file exceeds the 100 MB sync limit; use smaller media before syncing."
            )
        with self.locked():
            library = self.get(identifier)
            if not library.sync_enabled or automatic and not library.auto_sync_enabled:
                raise ValueError("Repository sync is disabled.")
            self.verify_repository(library.location, True)
            self.refresh_checkout(library)
            destination = Path(library.path)
            manifest = destination / ".artifact-studio-manifest.json"
            previous: dict[str, str] = {}
            if manifest.is_symlink():
                raise ValueError("The sync manifest cannot be a symbolic link.")
            if manifest.exists():
                previous = TypeAdapter(dict[str, str]).validate_json(
                    manifest.read_text(), strict=True
                )
            if not files and not previous:
                raise ValueError("No presentation files were found to sync.")
            current_paths = {Path(item.local_path).relative_to(root).as_posix() for item in files}
            removals: list[tuple[Path, str]] = []
            if automatic:
                for relative in previous.keys() - current_paths:
                    if revision_directory(Path(relative)):
                        continue
                    target = snapshot_target(destination, relative)
                    if target.exists():
                        if not target.is_file() or file_hash(target) != previous[relative]:
                            raise ValueError(
                                f"Remote changes need review before syncing: {relative}"
                            )
                        removals.append((target, relative))
                previous = {
                    path: value
                    for path, value in previous.items()
                    if path in current_paths or revision_directory(Path(path))
                }
            updates: list[tuple[Path, Path, str, str]] = []
            for item in files:
                original = Path(item.local_path)
                relative = original.relative_to(root).as_posix()
                target = snapshot_target(destination, relative)
                current_hash = file_hash(original)
                remote_hash = file_hash(target) if target.is_file() else None
                if remote_hash != current_hash:
                    if remote_hash != previous.get(relative):
                        raise ValueError(f"Remote changes need review before syncing: {relative}")
                    updates.append((original, target, relative, current_hash))
                previous[relative] = current_hash
            with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=self.cache) as temporary:
                staged: list[tuple[Path, Path]] = []
                for original, target, relative, _ in updates:
                    snapshot = Path(temporary) / relative
                    snapshot.parent.mkdir(parents=True, exist_ok=True)
                    for _ in range(3):
                        expected = file_hash(original)
                        _ = shutil.copy2(original, snapshot)
                        if snapshot.stat().st_size >= 100_000_000:
                            raise ValueError(f"File exceeds the 100 MB sync limit: {relative}")
                        if file_hash(snapshot) == expected:
                            previous[relative] = expected
                            break
                    else:
                        raise ValueError(
                            f"File is changing during sync; retry when saved: {relative}"
                        )
                    staged.append((snapshot, target))
                for snapshot, target in staged:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _ = shutil.copy2(snapshot, target)
                for target, _ in removals:
                    target.unlink()
            manifest.write_text(json.dumps(previous, indent=2, sort_keys=True) + "\n")
            paths = [relative for _, _, relative, _ in updates] + [path for _, path in removals]
            git(destination, "add", "--", *paths, manifest.name)
            if git(destination, "diff", "--cached", "--name-only"):
                git(
                    destination,
                    "commit",
                    "-m",
                    "chore(presentations): sync local presentation snapshot",
                )
            branch = git(destination, "branch", "--show-current")
            git(destination, "push", "-u", "origin", branch)
            commit = git(destination, "rev-parse", "HEAD")
            return SyncResult(
                repository=library.location,
                commit=commit,
                url=f"https://github.com/{library.location}/commit/{commit}",
                files_changed=len(updates) + len(removals),
            )
