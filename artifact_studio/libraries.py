"""Local reference libraries and explicit, conflict-aware private GitHub snapshots."""

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import defaultdict
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, TypeAdapter

from .artifact_models import ArtifactManifest
from .artifacts import bundle_hash, relative_path
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
    last_saved_at: str | None = None


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


class SyncBaseline(StrictModel):
    """Hashes acknowledged by this local workspace, never by a fetched manifest."""

    commit: str = Field(default="", pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})?$")
    files: dict[str, str] = Field(default_factory=dict)


class PendingSync(SyncBaseline):
    """An exact prepared commit whose push outcome may still be unknown."""

    commit: str = Field(pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
    base_commit: str = Field(pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})?$")


class SyncCheckpoint(StrictModel):
    """Keep publication ownership local and bound to its collection and branch."""

    collection: str
    branch: str
    confirmed: SyncBaseline = Field(default_factory=SyncBaseline)
    pending: PendingSync | None = None


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
    if relative.name.casefold().endswith(".artifact-studio.zip"):
        return False
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


# Libraries are reconstructed for each request, so cache observations across instances.
_SAVE_CACHE: dict[tuple[Path, tuple[Path, ...]], tuple[float, str | None]] = {}
_SAVE_CACHE_LOCK = threading.Lock()
_SAVE_CACHE_SECONDS = 15.0


def last_local_save(root: Path, cache: Path, references: tuple[Path, ...] = ()) -> str | None:
    """Observe the newest eligible saved file without reading its contents."""
    resolved = root.resolve()
    exclusions = {cache.resolve(), *(path.resolve() for path in references)}
    key = (
        resolved,
        tuple(
            sorted(
                path for path in exclusions if path != resolved and path.is_relative_to(resolved)
            )
        ),
    )
    with _SAVE_CACHE_LOCK:
        now = time.monotonic()
        previous = _SAVE_CACHE.get(key)
        if previous is not None and now - previous[0] < _SAVE_CACHE_SECONDS:
            return previous[1]
        try:
            newest: int | None = None

            def fail(error: OSError) -> None:
                """Avoid presenting a partial inventory as a complete observation."""
                raise error

            if not root.is_dir():
                raise FileNotFoundError(root)
            for folder, directories, names in root.walk(follow_symlinks=False, on_error=fail):
                directories[:] = [
                    name
                    for name in directories
                    if not name.startswith(".")
                    and (
                        name not in EXCLUDED
                        or artifact_source((folder / name).relative_to(root) / "placeholder")
                    )
                    and not revision_directory((folder / name).relative_to(root))
                    and not (folder / name).is_symlink()
                    and not any((folder / name).resolve().is_relative_to(path) for path in key[1])
                ]
                for name in names:
                    path = folder / name
                    relative = path.relative_to(root)
                    if (
                        relative == Path("workspace-recovery.json")
                        or re.search(r"\.(?:db|sqlite|sqlite3)(?:-wal|-shm|-journal)?$", name, re.I)
                        or not snapshot_allowed(relative)
                        or path.is_symlink()
                        or not path.is_file()
                    ):
                        continue
                    modified = path.stat().st_mtime_ns
                    newest = modified if newest is None else max(newest, modified)
            result = (
                datetime.fromtimestamp(newest / 1_000_000_000, UTC).isoformat()
                if newest is not None
                else None
            )
        except OSError, OverflowError, ValueError:
            result = None
        if len(_SAVE_CACHE) >= 128 and key not in _SAVE_CACHE:
            _ = _SAVE_CACHE.pop(next(iter(_SAVE_CACHE)))
        _SAVE_CACHE[key] = (now, result)
        return result


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


def _verify_snapshot_artifacts(files: list[LibraryFile], hashes: dict[str, str]) -> None:
    """Abort mixed owned bundles when direct file saves bypass the application's database lock."""
    sources: defaultdict[tuple[str, ...], dict[str, tuple[str, int]]] = defaultdict(dict)
    manifests: list[LibraryFile] = []
    for item in files:
        parts = Path(item.path).parts
        if artifact_source(Path(item.path)):
            sources[parts[:4]]["/".join(parts[5:])] = (hashes[item.path], item.size)
        elif (
            len(parts) == 5
            and parts[0] == "projects"
            and parts[2] == "artifacts"
            and parts[4] == "artifact.json"
        ):
            manifests.append(item)
    for item in manifests:
        manifest = ArtifactManifest.model_validate_json(Path(item.local_path).read_bytes())
        expected = {
            relative_path(entry.path): (entry.sha256, entry.size)
            for entry in manifest.files
            if snapshot_allowed(Path(item.path).parent / "files" / relative_path(entry.path))
        }
        if (
            sources[Path(item.path).parts[:4]] != expected
            or bundle_hash(manifest.files, manifest.entrypoint, manifest.kind)
            != manifest.content_hash
        ):
            raise ValueError("Artifact files are changing during sync; retry when saved.")


class Libraries:
    """Register references and publish deliberate snapshots without changing local decks."""

    def __init__(self, store: Store) -> None:
        """Reuse the workspace database and isolate repository checkouts in local state."""
        self.store = store
        self.cache = store.database.parent / "libraries"
        self.cache.mkdir(exist_ok=True)
        with store.connect() as db:
            _ = db.execute(
                "CREATE TABLE IF NOT EXISTS libraries (id TEXT PRIMARY KEY, document TEXT)"
            )
            _ = db.execute(
                "CREATE TABLE IF NOT EXISTS library_sync_checkpoints "
                "(id TEXT PRIMARY KEY, document TEXT NOT NULL)"
            )

    @contextmanager
    def locked(self) -> Generator[None]:
        """Prevent concurrent clone, refresh and sync operations across server processes."""
        with (self.store.database.parent / ".library-operations.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError(
                    "A library operation is running. Try again when it finishes."
                ) from error
            try:
                self.cache.mkdir(exist_ok=True)
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def list(self) -> list[Library]:
        """Include the canonical collection alongside explicitly registered sources."""
        with self.store.connect() as db:
            references = [
                Library.model_validate_json(str(row[0]))
                for row in db.execute("SELECT document FROM libraries ORDER BY id")
            ]
        local = Library(
            id="collection",
            kind="local",
            location=str(self.store.root),
            path=str(self.store.root),
            last_saved_at=last_local_save(
                self.store.root, self.cache, tuple(Path(item.path) for item in references)
            ),
        )
        return [local, *references]

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
        if deck_id is None:
            from .workspace_recovery import WorkspaceRecovery

            _ = WorkspaceRecovery(self.store).write_portable_metadata(synchronize=False)
        return self._snapshot_inventory(deck_id)

    def _snapshot_inventory(self, deck_id: str | None) -> list[LibraryFile]:
        """Select local sources without synchronizing artifact state inside a write transaction."""
        source = self.store.folder(deck_id) if deck_id else self.store.root
        if not source.is_dir():
            raise ValueError("The presentation folder does not exist.")
        return [
            item
            for item in inventory(source, snapshot=deck_id is None)
            if not Path(item.local_path).is_relative_to(self.cache)
        ]

    @contextmanager
    def _frozen_snapshot(self, deck_id: str | None) -> Generator[list[LibraryFile]]:
        """Freeze complete local source under the database lock before any network operation."""
        from .workspace_recovery import WorkspaceRecovery

        recovery = WorkspaceRecovery(self.store)
        if deck_id is None:
            _ = recovery.write_portable_metadata()
        with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=self.cache) as temporary:
            frozen: list[LibraryFile] = []
            hashes: dict[str, str] = {}
            with self.store.connect() as db:
                _ = db.execute("BEGIN IMMEDIATE")
                if deck_id is None:
                    _ = recovery.write_portable_metadata(synchronize=False)
                files = self._snapshot_inventory(deck_id)
                if any(item.size >= 100_000_000 for item in files):
                    raise ValueError(
                        "A file exceeds the 100 MB sync limit; use smaller media before syncing."
                    )
                for item in files:
                    original = Path(item.local_path)
                    relative = original.relative_to(self.store.root).as_posix()
                    snapshot = Path(temporary) / relative
                    snapshot.parent.mkdir(parents=True, exist_ok=True)
                    for _ in range(3):
                        expected = file_hash(original)
                        _ = shutil.copy2(original, snapshot)
                        if snapshot.stat().st_size >= 100_000_000:
                            raise ValueError(f"File exceeds the 100 MB sync limit: {relative}")
                        if file_hash(snapshot) == expected:
                            break
                    else:
                        raise ValueError(
                            f"File is changing during sync; retry when saved: {relative}"
                        )
                    frozen.append(
                        LibraryFile(
                            path=relative, local_path=str(snapshot), size=snapshot.stat().st_size
                        )
                    )
                    hashes[relative] = expected
                _verify_snapshot_artifacts(frozen, hashes)
            yield frozen

    @staticmethod
    def _ref_commit(destination: Path, ref: str) -> str:
        """Resolve an exact ref, including an unborn branch without a commit."""
        for line in git(
            destination, "for-each-ref", "--format=%(refname) %(objectname)", ref
        ).splitlines():
            name, commit = line.split(" ", 1)
            if name == ref:
                return commit
        return ""

    @staticmethod
    def _contains_commit(destination: Path, tip: str, commit: str) -> bool:
        """Require known publication history to remain reachable on the remote branch."""
        if not tip:
            return False
        try:
            _ = git(destination, "merge-base", "--is-ancestor", commit, tip)
        except ValueError:
            return False
        return True

    @staticmethod
    def _manifest_files(destination: Path, content: str) -> dict[str, str]:
        """Validate managed inventory without treating it as local upload permission."""
        files = TypeAdapter(dict[str, str]).validate_json(content, strict=True)
        for relative, checksum in files.items():
            _ = snapshot_target(destination, relative)
            if not re.fullmatch(r"[a-f0-9]{64}", checksum):
                raise ValueError("The sync manifest contains an invalid content hash.")
        return files

    def _save_checkpoint(self, identifier: str, checkpoint: SyncCheckpoint) -> None:
        """Persist a prepared push before sending it and acknowledge only verified delivery."""
        with self.store.connect() as db:
            _ = db.execute(
                "INSERT OR REPLACE INTO library_sync_checkpoints VALUES (?, ?)",
                (identifier, checkpoint.model_dump_json()),
            )

    def _checkpoint(
        self, library: Library, branch: str, remote: str, current_paths: set[str]
    ) -> SyncCheckpoint:
        """Migrate only locally confirmed automatic uploads, never a cached checkout HEAD."""
        with self.store.connect() as db:
            row = db.execute(
                "SELECT document FROM library_sync_checkpoints WHERE id=?", (library.id,)
            ).fetchone()
            if row:
                return SyncCheckpoint.model_validate_json(str(row[0]))
            legacy = None
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='library_auto_sync'"
            ).fetchone():
                legacy = db.execute(
                    "SELECT document FROM library_auto_sync WHERE id=?", (library.id,)
                ).fetchone()
        checkpoint = SyncCheckpoint(collection=str(self.store.root.resolve()), branch=branch)
        if legacy:
            from .autosync import SyncStatus

            commit = SyncStatus.model_validate_json(str(legacy[0])).commit
            if commit:
                destination = Path(library.path)
                if not re.fullmatch(
                    r"[a-f0-9]{40}|[a-f0-9]{64}", commit
                ) or not self._contains_commit(destination, remote, commit):
                    raise ValueError(
                        "Remote changes need review: the last backup is no longer reachable."
                    )
                files = self._manifest_files(
                    destination,
                    git(destination, "show", f"{commit}:.artifact-studio-manifest.json"),
                )
                # Legacy manifests are global inventories. They cannot prove that
                # absent files were ever owned locally, so do not migrate deletions.
                checkpoint.confirmed = SyncBaseline(
                    commit=commit,
                    files={
                        path: checksum for path, checksum in files.items() if path in current_paths
                    },
                )
        return checkpoint

    def _prepare_sync(
        self, library: Library, current_paths: set[str]
    ) -> tuple[SyncCheckpoint, str]:
        """Reconcile exact pending commits before comparing the current remote with our baseline."""
        destination = Path(library.path)
        branch = git(destination, "branch", "--show-current")
        if not branch:
            raise ValueError("The library cache must be on the repository default branch.")
        remote = self._ref_commit(destination, f"refs/remotes/origin/{branch}")
        checkpoint = self._checkpoint(library, branch, remote, current_paths)
        if checkpoint.collection != str(self.store.root.resolve()) or checkpoint.branch != branch:
            raise ValueError(
                "Remote changes need review: this backup belongs to another collection or branch."
            )
        pending = checkpoint.pending
        if pending:
            if self._contains_commit(destination, remote, pending.commit):
                # A successful push may have lost its acknowledgement. The exact
                # prepared commit being on the remote is independent confirmation.
                pass
            elif (
                self._ref_commit(destination, f"refs/heads/{branch}") == pending.commit
                and remote == pending.base_commit
            ):
                # Deliver only the exact previously validated snapshot. Later local
                # edits are compared with its confirmed baseline below.
                git(destination, "push", "-u", "origin", branch)
                remote = pending.commit
            else:
                raise ValueError("Remote changes need review before retrying the prepared backup.")
            checkpoint.confirmed = SyncBaseline(commit=pending.commit, files=pending.files)
            checkpoint.pending = None
            self._save_checkpoint(library.id, checkpoint)
        if self._ref_commit(destination, f"refs/heads/{branch}") != remote:
            raise ValueError(
                "The library cache has an unconfirmed commit. Review it before syncing."
            )
        if checkpoint.confirmed.commit and not self._contains_commit(
            destination, remote, checkpoint.confirmed.commit
        ):
            raise ValueError("Remote changes need review: the last backup is no longer reachable.")
        return checkpoint, remote

    def sync(
        self, identifier: str, deck_id: str | None = None, *, automatic: bool = False
    ) -> SyncResult:
        """Publish saved files while protecting independent remote changes."""
        library = self.get(identifier)
        if library.kind != "github" or not library.sync_enabled:
            raise ValueError("Enable sync on a private GitHub library first.")
        if automatic and deck_id is not None:
            raise ValueError("Automatic sync requires a complete workspace snapshot.")
        with self.locked(), self._frozen_snapshot(deck_id) as files:
            library = self.get(identifier)
            if not library.sync_enabled or automatic and not library.auto_sync_enabled:
                raise ValueError("Repository sync is disabled.")
            self.verify_repository(library.location, True)
            self.refresh_checkout(library)
            destination = Path(library.path)
            current_paths = {item.path for item in files}
            checkpoint, remote_commit = self._prepare_sync(library, current_paths)
            owned = checkpoint.confirmed.files.copy()
            manifest = destination / ".artifact-studio-manifest.json"
            previous: dict[str, str] = {}
            if manifest.is_symlink():
                raise ValueError("The sync manifest cannot be a symbolic link.")
            if manifest.exists():
                previous = self._manifest_files(destination, manifest.read_text())
            if not files and not previous:
                raise ValueError("No presentation files were found to sync.")
            removals: list[tuple[Path, str]] = []
            if automatic:
                for relative in owned.keys() - current_paths:
                    if revision_directory(Path(relative)):
                        continue
                    target = snapshot_target(destination, relative)
                    if target.exists():
                        if not target.is_file() or file_hash(target) != owned[relative]:
                            raise ValueError(
                                f"Remote changes need review before syncing: {relative}"
                            )
                        removals.append((target, relative))
                    _ = previous.pop(relative, None)
                    _ = owned.pop(relative)
            updates: list[tuple[Path, Path, str, str]] = []
            for item in files:
                original = Path(item.local_path)
                relative = item.path
                target = snapshot_target(destination, relative)
                current_hash = file_hash(original)
                if target.exists() and not target.is_file():
                    raise ValueError(f"Remote changes need review before syncing: {relative}")
                remote_hash = file_hash(target) if target.is_file() else None
                if remote_hash != current_hash:
                    if remote_hash != owned.get(relative):
                        raise ValueError(
                            f"Remote changes need review before syncing: {relative}. "
                            "Save a workspace archive and reconcile the local and GitHub copies "
                            "before retrying."
                        )
                    updates.append((original, target, relative, current_hash))
                previous[relative] = current_hash
                owned[relative] = current_hash
            for original, target, _, _ in updates:
                target.parent.mkdir(parents=True, exist_ok=True)
                _ = shutil.copy2(original, target)
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
            commit = git(destination, "rev-parse", "HEAD")
            checkpoint.pending = PendingSync(commit=commit, base_commit=remote_commit, files=owned)
            self._save_checkpoint(library.id, checkpoint)
            git(destination, "push", "-u", "origin", branch)
            checkpoint.confirmed = SyncBaseline(commit=commit, files=owned)
            checkpoint.pending = None
            self._save_checkpoint(library.id, checkpoint)
            return SyncResult(
                repository=library.location,
                commit=commit,
                url=f"https://github.com/{library.location}/commit/{commit}",
                files_changed=len(updates) + len(removals),
            )
