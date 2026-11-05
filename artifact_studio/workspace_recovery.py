"""Local workspace archives and explicit recovery of portable current-source snapshots."""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Literal, cast
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from pydantic import Field

from .artifact_models import ArtifactManifest, ProjectView
from .artifacts import (
    LEGACY_PROJECT,
    ArtifactStore,
    bundle_hash,
    file_inventory,
    read_tree,
    relative_path,
)
from .libraries import artifact_source, file_hash, inventory
from .models import Deck, StrictModel
from .project_tree import TreeDocument
from .storage import DATA, SAFE_ID, ConflictError, Store, digest, replace_text

PORTABLE_METADATA = "workspace-recovery.json"
ARCHIVE_MANIFEST = "workspace.json"
MAX_FILES = 100_000
MAX_FILE_BYTES = 2_000_000_000
MAX_TOTAL_BYTES = 20_000_000_000
CHUNK_BYTES = 1024 * 1024
MAX_METADATA_BYTES = 16_000_000
STATE_DIRECTORIES = ("feedback-files", "design-inbox")
HOST_TABLES = frozenset(
    {
        "libraries",
        "library_auto_sync",
        "github_backup_settings",
        "github_backup_state",
        "review_sessions",
        "review_pauses",
        "design_presence",
        "artifact_context_receipts",
    }
)
SECRET_NAMES = frozenset({"credentials.json", "secrets.json", "secrets.txt", ".env"})
PATH_KEYS = frozenset({"path", "local_path", "source_path", "original_source_path", "source_root"})


class PortableWorkspace(StrictModel):
    """Public navigation metadata needed to recover database-only legacy memberships."""

    format: Literal["artifact-studio-current-workspace"] = "artifact-studio-current-workspace"
    version: Literal[1] = 1
    source_root: str = ""
    legacy_projects: dict[str, str] = Field(default_factory=dict, max_length=10_000)
    archived_decks: dict[str, str] = Field(default_factory=dict, max_length=10_000)
    archived_designs: list[str] = Field(default_factory=list, max_length=10_000)
    default_design: str = ""


class RecoveryFile(StrictModel):
    """One portable destination referencing a deduplicated, verified archive object."""

    area: Literal["collection", "state", "database"]
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=0, le=MAX_TOTAL_BYTES)
    mtime_ns: int = Field(default=0, ge=0)
    mode: int = Field(default=0o644, ge=0, le=0o777)


class WorkspaceArchive(StrictModel):
    """Versioned local recovery manifest with no live credentials or upload permission."""

    format: Literal["artifact-studio-workspace"] = "artifact-studio-workspace"
    version: Literal[1] = 1
    created_at: str
    source_root: str
    source_state: str
    files: list[RecoveryFile] = Field(max_length=MAX_FILES)
    directories: list[str] = Field(default_factory=list, max_length=MAX_FILES)


class RecoveryPreview(StrictModel):
    """A source-and-destination-bound review of a non-overwriting workspace restore."""

    mode: Literal["current", "archive"]
    source: str
    content_hash: str
    projects: int
    artifacts: int
    presentations: int
    folders: int
    files: int
    bytes: int
    conflicts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    can_restore: bool


class RecoveryResult(RecoveryPreview):
    """The reviewed inventory that was successfully installed at the selected destination."""

    restored: bool = True


class ArchiveSummary(StrictModel):
    """A completed local archive, suitable for an explicit file download or local copy."""

    path: str
    sha256: str
    files: int
    objects: int
    bytes: int
    fingerprint: str = ""
    unchanged: bool = False


def _identifier(value: str) -> str:
    """Reject identifiers that cannot safely address workspace-owned directories."""
    if not SAFE_ID.fullmatch(value):
        raise ValueError("Invalid recovery project or artifact ID.")
    return value


def _regular_files(
    root: Path, excluded: frozenset[Path] = frozenset(), directories_out: list[str] | None = None
) -> Iterator[Path]:
    """Walk an owned tree without following links, sockets, credentials or Git state."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Recovery source must be an ordinary directory.")
    for folder, directories, names in root.walk(follow_symlinks=False):
        directories[:] = sorted(
            name
            for name in directories
            if name.casefold() not in {".git", ".ssh", ".aws", "__pycache__"}
            and folder / name not in excluded
            and (
                name not in {"node_modules", ".venv"}
                or artifact_source((folder / name).relative_to(root))
            )
        )
        if directories_out is not None and folder != root:
            relative = folder.relative_to(root)
            if not any(part.startswith(".") for part in relative.parts):
                directories_out.append(relative.as_posix())
        for name in [*directories, *sorted(names)]:
            path = folder / name
            if path in excluded:
                continue
            if path.is_symlink():
                raise ValueError("Recovery sources cannot contain symbolic links.")
            if path.is_dir():
                continue
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError("Recovery sources contain only ordinary files.")
            if artifact_source(path.relative_to(root)) or (
                name.casefold() not in SECRET_NAMES and not name.startswith(".env.")
            ):
                yield path


def _read_json(path: Path) -> str:
    """Bound metadata before parsing it into a strict recovery model."""
    if (
        any(parent.is_symlink() for parent in (path, *path.parents))
        or path.stat().st_size > MAX_METADATA_BYTES
    ):
        raise ValueError("Recovery metadata is linked or too large.")
    return path.read_text(encoding="utf-8")


def _copy_verified(source: Path, destination: Path, expected: RecoveryFile) -> None:
    """Stream one immutable inventory entry and reject changes during copying."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    size = 0
    created = False
    try:
        with source.open("rb") as incoming, destination.open("xb") as outgoing:
            created = True
            while chunk := incoming.read(CHUNK_BYTES):
                size += len(chunk)
                if size > expected.size:
                    raise ConflictError("Recovery source changed after preview. Preview it again.")
                hasher.update(chunk)
                _ = outgoing.write(chunk)
        if size != expected.size or hasher.hexdigest() != expected.sha256:
            raise ConflictError("Recovery source changed after preview. Preview it again.")
        destination.chmod(expected.mode)
        os.utime(destination, ns=(expected.mtime_ns, expected.mtime_ns))
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def _entry(area: Literal["collection", "state", "database"], name: str, path: Path) -> RecoveryFile:
    """Hash bounded source bytes without retaining large media in memory."""
    relative_path(name)
    details = path.stat()
    size = details.st_size
    if size > MAX_FILE_BYTES:
        raise ValueError("Keep recovery files below 2 GB.")
    return RecoveryFile(
        area=area,
        path=name,
        sha256=file_hash(path),
        size=size,
        mtime_ns=details.st_mtime_ns,
        mode=stat.S_IMODE(details.st_mode) & 0o777,
    )


def _bound(files: list[RecoveryFile]) -> None:
    """Reject oversized workspaces and ambiguous, case-colliding destinations."""
    names = [(item.area, item.path.casefold()) for item in files]
    if (
        len(files) > MAX_FILES
        or sum(item.size for item in files) > MAX_TOTAL_BYTES
        or any(item.size > MAX_FILE_BYTES for item in files)
    ):
        raise ValueError("Keep recovery archives below 20 GB expanded and 100,000 files.")
    if len(set(names)) != len(names):
        raise ValueError("Recovery paths collide, including case-insensitive filenames.")
    paths = set(names)
    for area, name in names:
        if any(
            (area, parent.as_posix()) in paths
            for parent in Path(name).parents
            if str(parent) != "."
        ):
            raise ValueError("A recovery file collides with another file's parent directory.")


def _replace_paths(value: str, replacements: list[tuple[str, str]]) -> str:
    """Rebase absolute collection and state paths while preserving unrelated text."""
    for old, new in replacements:
        if old:
            value = re.sub(
                re.escape(old) + r"(?=/|$|[\s\"'<>?#)])", new.replace("\\", "\\\\"), value
            )
    return value


def _rebase_json(value: object, replacements: list[tuple[str, str]]) -> object:
    """Rebase registry and attachment paths without changing authored content or source hashes."""
    if isinstance(value, dict):
        document = cast(dict[str, object], value)
        return {
            key: _replace_paths(item, replacements)
            if key in PATH_KEYS and isinstance(item, str)
            else _rebase_json(item, replacements)
            for key, item in document.items()
        }
    if isinstance(value, list):
        return [_rebase_json(item, replacements) for item in cast(list[object], value)]
    return value


def _database_tables(db: sqlite3.Connection) -> list[str]:
    """Read ordinary application tables without executing imported schema objects."""
    unsafe = db.execute("SELECT name FROM sqlite_master WHERE type='view'").fetchall()
    if unsafe:
        raise ValueError("Recovery databases cannot contain views.")
    for name, sql in db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"):
        if _history_triggers().get(str(name)) != " ".join(str(sql).split()):
            raise ValueError("The recovery database contains an unknown trigger.")
    return [
        cast(str, row[0])
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        if not str(row[0]).startswith("sqlite_")
    ]


@cache
def _history_triggers() -> dict[str, str]:
    """Recognize exact first-party history triggers without trusting imported SQL names."""
    from .version_history import install_artifact_history, install_slide_history

    with sqlite3.connect(":memory:") as db:
        db.executescript(
            "CREATE TABLE revisions(deck,slide,hash,document);"
            "CREATE TABLE artifact_revisions(artifact_id,revision,document);"
        )
        install_slide_history(db)
        install_artifact_history(db)
        return {
            cast(str, row[0]): " ".join(cast(str, row[1]).split())
            for row in db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")
        }


def _quoted(name: str) -> str:
    """Quote SQLite schema identifiers without accepting executable SQL fragments."""
    return '"' + name.replace('"', '""') + '"'


def _sanitize_database(database: Path, replacements: list[tuple[str, str]]) -> None:
    """Keep durable local history while removing host sessions and upload opt-ins."""
    with sqlite3.connect(database) as db:
        if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("The recovery database failed its integrity check.")
        tables = _database_tables(db)
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            _ = db.execute(f"DROP TRIGGER {_quoted(cast(str, row[0]))}")
        for table in tables:
            if table in HOST_TABLES:
                _ = db.execute(f"DELETE FROM {_quoted(table)}")
                continue
            columns = [
                cast(str, row[1]) for row in db.execute(f"PRAGMA table_info({_quoted(table)})")
            ]
            for row in db.execute(f"SELECT rowid,* FROM {_quoted(table)} ORDER BY rowid"):
                for index, column in enumerate(columns, start=1):
                    value = row[index]
                    if isinstance(value, str):
                        try:
                            parsed = cast(object, json.loads(value))
                        except json.JSONDecodeError:
                            continue
                        rebased = _rebase_json(parsed, replacements)
                        if rebased == parsed:
                            continue
                        updated = json.dumps(rebased, ensure_ascii=False)
                        if updated != value:
                            _ = db.execute(
                                f"UPDATE {_quoted(table)} SET {_quoted(column)}=? WHERE rowid=?",
                                (updated, row[0]),
                            )
            if table == "workflow":
                _ = db.execute("UPDATE workflow SET waiting_until=0,last_check=NULL")
                _ = db.execute("DELETE FROM workflow WHERE mode='live'")
        from .version_history import install_artifact_history, install_slide_history

        if "revisions" in tables:
            install_slide_history(db)
        if "artifact_revisions" in tables:
            install_artifact_history(db)


def _logical_database_hash(database: Path) -> str:
    """Identify durable SQLite schema and typed rows independently of physical pages."""
    hasher = hashlib.sha256()

    def append(value: object) -> None:
        hasher.update(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
        hasher.update(b"\n")

    with sqlite3.connect(database) as db:
        tables = [table for table in _database_tables(db) if table not in HOST_TABLES]
        append(["application_id", db.execute("PRAGMA application_id").fetchone()[0]])
        append(["user_version", db.execute("PRAGMA user_version").fetchone()[0]])
        for kind, name, table, sql in db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT GLOB 'sqlite_*' ORDER BY type,name"
        ):
            if table not in HOST_TABLES:
                append(["schema", kind, name, table, sql])
        for table in tables:
            columns = [
                cast(str, row[1]) for row in db.execute(f"PRAGMA table_info({_quoted(table)})")
            ]
            # Explicit types break numeric comparison ties; BINARY avoids application
            # collations, and no implicit rowid participates in durable content identity.
            order = ",".join(
                [f"{_quoted(column)} COLLATE BINARY" for column in columns]
                + [f"typeof({_quoted(column)}) COLLATE BINARY" for column in columns]
            )
            append(["table", table, columns])
            for row in db.execute(f"SELECT * FROM {_quoted(table)} ORDER BY {order}"):
                append(
                    [
                        ["blob", value.hex()]
                        if isinstance(value, bytes)
                        else [type(value).__name__, value]
                        for value in row
                    ]
                )
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
        ).fetchone():
            for name, sequence in db.execute("SELECT name,seq FROM sqlite_sequence ORDER BY name"):
                if name in tables:
                    append(["sequence", name, sequence])
    return hasher.hexdigest()


def _archive_fingerprint(files: list[RecoveryFile], directories: list[str], database: Path) -> str:
    """Hash the prepared archive inventory while ignoring timestamps and SQLite layout."""
    document = {
        "format": "artifact-studio-workspace-content-v1",
        "directories": sorted(directories),
        "files": [
            [
                item.area,
                item.path,
                _logical_database_hash(database) if item.area == "database" else item.sha256,
                None if item.area == "database" else item.size,
                item.mode,
            ]
            for item in sorted(files, key=lambda item: (item.area, item.path))
        ],
    }
    return hashlib.sha256(
        json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


class WorkspaceRecovery:
    """Preview and restore into an empty workspace, with no network operations."""

    def __init__(self, store: Store) -> None:
        """Share the existing collection and review database without reading personal sources."""
        self.store = store
        if store.database.is_symlink():
            raise ValueError("The review database cannot be a symbolic link.")
        self.registry = ArtifactStore(store)

    def portable_metadata(self, *, synchronize: bool = True) -> str:
        """Return upload-safe current metadata for future complete collection snapshots."""
        if synchronize:
            with self.store.connect() as db:
                ids = [cast(str, row[0]) for row in db.execute("SELECT id FROM artifact_items")]
            for artifact_id in ids:
                _ = self.registry.get_artifact(artifact_id)
        with self.store.connect() as db:
            memberships = {
                cast(str, row[0]): cast(str, row[1])
                for row in db.execute("SELECT artifact_id,project_id FROM artifact_legacy_projects")
            }
            archived = {
                cast(str, row[0]): cast(str, row[1])
                for row in db.execute("SELECT deck,archived_at FROM archived_decks")
            }
            designs = [
                cast(str, row[0]) for row in db.execute("SELECT folder FROM design_archives")
            ]
            default = db.execute(
                "SELECT value FROM workspace_settings WHERE key='default_design'"
            ).fetchone()
        return (
            PortableWorkspace(
                source_root=str(self.store.root),
                legacy_projects=memberships,
                archived_decks=archived,
                archived_designs=designs,
                default_design=cast(str, default[0]) if default else "",
            ).model_dump_json(indent=2)
            + "\n"
        )

    def write_portable_metadata(self, *, synchronize: bool = True) -> Path:
        """Persist public navigation metadata before an authorized current-source snapshot."""
        path = self.store.root / PORTABLE_METADATA
        if path.is_symlink():
            raise ValueError("Recovery metadata cannot be a symbolic link.")
        content = self.portable_metadata(synchronize=synchronize)
        if (
            not path.exists()
            or path.stat().st_size != len(content.encode())
            or path.read_text() != content
        ):
            replace_text(path, content)
        return path

    def _current_files(self, source: Path) -> list[RecoveryFile]:
        """Reuse the current-source snapshot policy without creating state in the source."""
        if source.is_symlink() or not source.is_dir():
            raise ValueError("Choose an ordinary current-source snapshot directory.")
        files = [
            _entry("collection", item.path, Path(item.local_path))
            for item in inventory(source, snapshot=True)
        ]
        _bound(files)
        for item in files:
            if any(parent.is_symlink() for parent in (source / item.path).parents):
                raise ValueError("Recovery sources cannot pass through symbolic links.")
        return files

    def _current_metadata(
        self, source: Path
    ) -> tuple[
        list[ProjectView], list[ArtifactManifest], PortableWorkspace, list[str], TreeDocument
    ]:
        """Validate portable manifests and current sources before rebuilding registry state."""
        projects: list[ProjectView] = []
        artifacts: list[ArtifactManifest] = []
        for path in sorted(source.glob("projects/*/project.json")):
            project = ProjectView.model_validate_json(_read_json(path))
            if _identifier(project.id) != path.parent.name or project.legacy:
                raise ValueError("Project metadata does not match its storage folder.")
            brief = path.parent / "README.md"
            if not brief.is_file():
                raise ValueError("A recovered project is missing its README.md brief.")
            projects.append(project)
        project_ids = {project.id for project in projects}
        for path in sorted(source.glob("projects/*/artifacts/*/artifact.json")):
            artifact = ArtifactManifest.model_validate_json(_read_json(path))
            storage_id = artifact.storage_project_id or artifact.project_id
            if (
                _identifier(artifact.id) != path.parent.name
                or _identifier(storage_id) != path.parents[2].name
                or artifact.project_id not in project_ids
            ):
                raise ValueError("Artifact metadata does not match its project or storage folder.")
            files = read_tree(path.parent / "files")
            if (
                relative_path(artifact.entrypoint) not in files
                or [(item.path, item.size, item.sha256) for item in file_inventory(files)]
                != [(item.path, item.size, item.sha256) for item in artifact.files]
                or bundle_hash(artifact.files, artifact.entrypoint, artifact.kind)
                != artifact.content_hash
            ):
                raise ConflictError("Current artifact files differ from their recovery manifest.")
            artifacts.append(artifact)
        if len({artifact.id for artifact in artifacts}) != len(artifacts):
            raise ValueError("The snapshot contains duplicate artifact IDs.")
        presentations: list[str] = []
        for path in sorted(source.glob("*/index.html")):
            match = DATA.search(path.read_text(encoding="utf-8"))
            if match is not None:
                deck = Deck.model_validate_json(match[2])
                if _identifier(deck.id) != path.parent.name:
                    raise ValueError("Presentation metadata does not match its storage folder.")
                presentations.append(deck.id)
        portable_path = source / PORTABLE_METADATA
        portable = (
            PortableWorkspace.model_validate_json(_read_json(portable_path))
            if portable_path.exists()
            else PortableWorkspace()
        )
        for deck_id, project_id in portable.legacy_projects.items():
            if _identifier(deck_id) not in presentations or project_id not in project_ids | {
                LEGACY_PROJECT
            }:
                raise ValueError("A legacy project membership references missing content.")
        tree_path = source / "project-tree.json"
        tree = (
            TreeDocument.model_validate_json(_read_json(tree_path))
            if tree_path.exists()
            else TreeDocument()
        )
        if any(
            node.kind == "project" and node.id not in project_ids | {LEGACY_PROJECT}
            for node in tree.nodes
        ):
            raise ValueError("The project tree references a missing project.")
        if any(
            node.kind == "folder" and node.id in project_ids | {LEGACY_PROJECT}
            for node in tree.nodes
        ):
            raise ValueError("A folder cannot use a recovered project's ID.")
        if {artifact.id for artifact in artifacts}.intersection(presentations):
            raise ValueError("An artifact and presentation cannot share the same ID.")
        if not projects and not presentations:
            raise ValueError("The source contains no recoverable projects or presentations.")
        for deck_id in [*portable.archived_decks, *portable.archived_designs]:
            if _identifier(deck_id) not in presentations:
                raise ValueError("Archived visibility references a missing presentation.")
        return projects, artifacts, portable, presentations, tree

    def _destination(self) -> tuple[list[str], str]:
        """Fingerprint the live target and explain why an existing workspace cannot be replaced."""
        conflicts: list[str] = []
        hasher = hashlib.sha256()
        root = self.store.root
        state = self.store.database.parent.resolve()
        for path in sorted(root.rglob("*")):
            if state != root and state.is_relative_to(root) and path.is_relative_to(state):
                continue
            if path == self.store.database or path.name in {
                self.store.database.name + suffix for suffix in ("-wal", "-shm", "-journal")
            }:
                continue
            if path.is_symlink() or path.is_file():
                conflicts.append(
                    "Destination collection contains files; choose an empty workspace."
                )
                break
        with self.store.connect() as db:
            for table in _database_tables(db):
                if db.execute(f"SELECT 1 FROM {_quoted(table)} LIMIT 1").fetchone() is not None:
                    conflicts.append(
                        "Destination review database contains user state; choose a fresh database."
                    )
                    break
            for line in db.iterdump():
                hasher.update(line.encode())
        for directory in STATE_DIRECTORIES:
            path = state / directory
            if path.exists() and any(path.rglob("*")):
                conflicts.append("Destination already contains private attachments.")
        artifact_feedback = root / ".state" / "artifact-feedback"
        if artifact_feedback.exists() and any(artifact_feedback.rglob("*")):
            conflicts.append("Destination already contains private artifact attachments.")
        hasher.update(str(root).encode())
        hasher.update(str(self.store.database.resolve()).encode())
        hasher.update(json.dumps(conflicts).encode())
        return conflicts, hasher.hexdigest()

    def _archive(self, source: Path) -> WorkspaceArchive:
        """Verify names, sizes, hashes and exact ZIP membership before any extraction."""
        if source.is_symlink() or not source.is_file():
            raise ValueError("Choose an ordinary local workspace archive.")
        try:
            with ZipFile(source) as archive:
                members = archive.infolist()
                names = [item.filename for item in members]
                if len(members) > MAX_FILES + 1 or len(set(names)) != len(names):
                    raise ValueError("The archive has too many or duplicate objects.")
                if (
                    ARCHIVE_MANIFEST not in names
                    or archive.getinfo(ARCHIVE_MANIFEST).file_size > MAX_METADATA_BYTES
                ):
                    raise ValueError("The workspace archive manifest is missing or too large.")
                manifest = WorkspaceArchive.model_validate_json(archive.read(ARCHIVE_MANIFEST))
                _bound(manifest.files)
                database = [item for item in manifest.files if item.area == "database"]
                if len(database) != 1 or database[0].path != "review.sqlite3":
                    raise ValueError("A workspace archive needs exactly one review database.")
                expected = {
                    ARCHIVE_MANIFEST,
                    *("objects/" + item.sha256 for item in manifest.files),
                }
                if set(names) != expected:
                    raise ValueError("The archive contains missing or unlisted objects.")
                sizes: dict[str, int] = {}
                for item in manifest.files:
                    relative_path(item.path)
                    if item.area == "state" and Path(item.path).parts[0] not in STATE_DIRECTORIES:
                        raise ValueError("The archive contains unknown private state paths.")
                    if item.area == "collection" and any(
                        part == ".git" for part in Path(item.path).parts
                    ):
                        raise ValueError("Archives cannot restore Git configuration.")
                    previous = sizes.setdefault(item.sha256, item.size)
                    if previous != item.size:
                        raise ValueError("A recovery object has inconsistent sizes.")
                collection_paths = {
                    item.path.casefold() for item in manifest.files if item.area == "collection"
                }
                for directory in manifest.directories:
                    relative_path(directory)
                    path = Path(directory.casefold())
                    if ".git" in path.parts or any(
                        parent.as_posix() in collection_paths for parent in (path, *path.parents)
                    ):
                        raise ValueError("A recovery directory collides with a file or Git state.")
                for sha256, size in sizes.items():
                    name = "objects/" + sha256
                    info = archive.getinfo(name)
                    if (
                        info.file_size != size
                        or info.flag_bits & 1
                        or stat.S_ISLNK(info.external_attr >> 16)
                    ):
                        raise ValueError("Archive objects must be ordinary, unencrypted files.")
                    hasher = hashlib.sha256()
                    count = 0
                    with archive.open(name) as stream:
                        while chunk := stream.read(CHUNK_BYTES):
                            count += len(chunk)
                            if count > size:
                                raise ValueError("An archive object exceeds its declared size.")
                            hasher.update(chunk)
                    if count != size or hasher.hexdigest() != sha256:
                        raise ValueError("An archive object failed SHA-256 verification.")
                return manifest
        except BadZipFile as error:
            raise ValueError("The file is not a valid workspace archive.") from error

    def fingerprint_archive(self, source: Path) -> str:
        """Validate a prior archive and identify its saved content without restoring files."""
        source = source.expanduser().absolute()
        manifest = self._archive(source)
        database_entry = next(item for item in manifest.files if item.area == "database")
        with tempfile.TemporaryDirectory(prefix="workspace-fingerprint-") as temporary:
            stage = Path(temporary).resolve()
            database_only = manifest.model_copy(
                update={"files": [database_entry], "directories": []}
            )
            self._extract(source, database_only, stage)
            database = stage / "database" / "review.sqlite3"
            database.chmod(0o600)
            try:
                # Older archives retain listening timestamps and context delivery
                # receipts, which follow the current archive's host-state policy.
                _sanitize_database(database, [])
                return _archive_fingerprint(manifest.files, manifest.directories, database)
            except sqlite3.DatabaseError as error:
                raise ValueError("The recovery database is not a valid SQLite database.") from error

    def preview(
        self, source: Path, *, mode: Literal["current", "archive"] = "current"
    ) -> RecoveryPreview:
        """Validate the complete source and bind the review token to the empty destination."""
        source = source.expanduser().absolute()
        if source.is_symlink():
            raise ValueError("Recovery sources cannot be symbolic links.")
        source = source.resolve()
        conflicts, target_hash = self._destination()
        warnings: list[str] = []
        if mode == "current":
            files = self._current_files(source)
            projects, artifacts, _, presentations, tree = self._current_metadata(source)
            if not (source / PORTABLE_METADATA).exists():
                warnings.append(
                    "This older snapshot has no legacy memberships; decks use Presentations."
                )
            warnings.append(
                "Current-source recovery excludes private comments, approvals and history."
            )
            counts = (
                len(projects),
                len(artifacts),
                len(presentations),
                sum(node.kind == "folder" for node in tree.nodes),
            )
            source_hash = digest(json.dumps([item.model_dump() for item in files], sort_keys=True))
        else:
            manifest = self._archive(source)
            files = manifest.files
            source_hash = file_hash(source)
            with tempfile.TemporaryDirectory(prefix="workspace-preview-") as temporary:
                stage = Path(temporary).resolve()
                self._extract(source, manifest, stage, metadata_only=True)
                counts = self._archive_counts(stage)
            warnings.append(
                "Repository registrations, upload permissions and agent sessions are reset."
            )
        token = digest(json.dumps([mode, str(source), source_hash, target_hash]))
        return RecoveryPreview(
            mode=mode,
            source=str(source),
            content_hash=token,
            projects=counts[0],
            artifacts=counts[1],
            presentations=counts[2],
            folders=counts[3],
            files=len(files),
            bytes=sum(item.size for item in files),
            conflicts=conflicts,
            warnings=warnings,
            can_restore=not conflicts,
        )

    @staticmethod
    def _archive_counts(stage: Path) -> tuple[int, int, int, int]:
        """Count restored identities using the archived registry and navigation document."""
        with sqlite3.connect(stage / "database" / "review.sqlite3") as db:
            tables = _database_tables(db)
            projects = (
                cast(int, db.execute("SELECT COUNT(*) FROM artifact_projects").fetchone()[0])
                if "artifact_projects" in tables
                else 0
            )
            artifacts = (
                cast(int, db.execute("SELECT COUNT(*) FROM artifact_items").fetchone()[0])
                if "artifact_items" in tables
                else 0
            )
        collection = stage / "collection"
        presentations = sum(
            DATA.search(path.read_text()) is not None for path in collection.glob("*/index.html")
        )
        tree_path = collection / "project-tree.json"
        tree = (
            TreeDocument.model_validate_json(_read_json(tree_path))
            if tree_path.exists()
            else TreeDocument()
        )
        return projects, artifacts, presentations, sum(node.kind == "folder" for node in tree.nodes)

    @staticmethod
    def _extract(
        source: Path, manifest: WorkspaceArchive, stage: Path, *, metadata_only: bool = False
    ) -> None:
        """Stream verified objects into a disposable staging tree without ZIP extraction APIs."""
        if not metadata_only:
            for directory in manifest.directories:
                (stage / "collection" / relative_path(directory)).mkdir(parents=True, exist_ok=True)
        with ZipFile(source) as archive:
            for item in manifest.files:
                if metadata_only and not (
                    item.area == "database"
                    or item.area == "collection"
                    and (
                        item.path == "project-tree.json"
                        or len(Path(item.path).parts) == 2
                        and Path(item.path).name == "index.html"
                    )
                ):
                    continue
                target = stage / item.area / relative_path(item.path)
                target.parent.mkdir(parents=True, exist_ok=True)
                hasher = hashlib.sha256()
                size = 0
                with (
                    archive.open("objects/" + item.sha256) as incoming,
                    target.open("xb") as outgoing,
                ):
                    while chunk := incoming.read(CHUNK_BYTES):
                        size += len(chunk)
                        if size > item.size:
                            raise ConflictError("The archive changed after preview.")
                        hasher.update(chunk)
                        _ = outgoing.write(chunk)
                if size != item.size or hasher.hexdigest() != item.sha256:
                    raise ConflictError("The archive changed after preview.")
                target.chmod(item.mode)
                os.utime(target, ns=(item.mtime_ns, item.mtime_ns))

    def _rebuild_current(self, source: Path, stage: Path) -> None:
        """Rebuild current registry records and one snapshot without private history."""
        projects, artifacts, portable, _, _ = self._current_metadata(source)
        collection = stage / "collection"
        portable = portable.model_copy(update={"source_root": str(self.store.root)})
        replace_text(collection / PORTABLE_METADATA, portable.model_dump_json(indent=2) + "\n")
        staged = Store(collection, stage / "database" / "review.sqlite3")
        _ = ArtifactStore(staged)
        with staged.connect() as db:
            for project in projects:
                folder = self.store.root / "projects" / project.id
                context = (collection / "projects" / project.id / "README.md").read_text()
                recovered = project.model_copy(
                    update={
                        "source_path": str(folder),
                        "context": context,
                        "context_hash": digest(context),
                        "artifact_count": 0,
                    }
                )
                _ = db.execute(
                    "INSERT INTO artifact_projects VALUES (?,?)",
                    (project.id, recovered.model_dump_json()),
                )
                replace_text(
                    collection / "projects" / project.id / "project.json",
                    recovered.model_dump_json(indent=2) + "\n",
                )
            for artifact in artifacts:
                relative = (
                    Path("projects")
                    / (artifact.storage_project_id or artifact.project_id)
                    / "artifacts"
                    / artifact.id
                )
                recovered = artifact.model_copy(
                    update={"original_source_path": str(self.store.root / relative / "files")}
                )
                document = recovered.model_dump_json()
                _ = db.execute(
                    "INSERT INTO artifact_items(id,project_id,document) VALUES (?,?,?)",
                    (artifact.id, artifact.project_id, document),
                )
                _ = db.execute(
                    "INSERT INTO artifact_revisions VALUES (?,?,?)",
                    (artifact.id, artifact.revision, document),
                )
                folder = collection / relative
                snapshot = folder / "revisions" / str(artifact.revision)
                snapshot.mkdir(parents=True)
                _ = shutil.copytree(folder / "files", snapshot / "files")
                _ = (snapshot / "artifact.json").write_text(document)
                replace_text(folder / "artifact.json", recovered.model_dump_json(indent=2) + "\n")
            for artifact_id, project_id in portable.legacy_projects.items():
                _ = db.execute(
                    "INSERT INTO artifact_legacy_projects VALUES (?,?)", (artifact_id, project_id)
                )
            for deck_id, archived_at in portable.archived_decks.items():
                _ = db.execute("INSERT INTO archived_decks VALUES (?,?)", (deck_id, archived_at))
            for folder_id in portable.archived_designs:
                _ = db.execute("INSERT INTO design_archives VALUES (?)", (folder_id,))
            if portable.default_design:
                _ = db.execute(
                    "INSERT INTO workspace_settings VALUES ('default_design',?)",
                    (portable.default_design,),
                )

    def restore(
        self, source: Path, expected_hash: str, *, mode: Literal["current", "archive"] = "current"
    ) -> RecoveryResult:
        """Revalidate reviewed sources, stage all state and publish without replacing content."""
        preview = self.preview(source, mode=mode)
        if preview.content_hash != expected_hash:
            raise ConflictError("Recovery source or destination changed. Preview it again.")
        if not preview.can_restore:
            raise ConflictError(" ".join(preview.conflicts))
        source = Path(preview.source)
        with tempfile.TemporaryDirectory(prefix="workspace-restore-") as temporary:
            stage = Path(temporary).resolve()
            if mode == "current":
                for item in self._current_files(source):
                    _copy_verified(source / item.path, stage / "collection" / item.path, item)
                self._rebuild_current(source, stage)
            else:
                manifest = self._archive(source)
                self._extract(source, manifest, stage)
                replacements = sorted(
                    [
                        (manifest.source_root, str(self.store.root)),
                        (manifest.source_state, str(self.store.database.parent.resolve())),
                    ],
                    key=lambda item: len(item[0]),
                    reverse=True,
                )
                _sanitize_database(stage / "database" / "review.sqlite3", replacements)
                collection = stage / "collection"
                metadata = [
                    *collection.glob("projects/*/project.json"),
                    *collection.glob("projects/*/artifacts/*/artifact.json"),
                    *collection.glob("projects/*/artifacts/*/revisions/*/artifact.json"),
                    *collection.glob(PORTABLE_METADATA),
                ]
                for path in metadata:
                    parsed = cast(object, json.loads(_read_json(path)))
                    updated = _rebase_json(parsed, replacements)
                    if parsed != updated:
                        _ = path.write_text(json.dumps(updated, indent=2), encoding="utf-8")
            fresh = self.preview(source, mode=mode)
            if fresh.content_hash != expected_hash or not fresh.can_restore:
                raise ConflictError("Recovery source or destination changed. Preview it again.")
            self._publish(stage)
        return RecoveryResult(**preview.model_dump())

    def _publish(self, stage: Path) -> None:
        """Install staged files exclusively and roll them back if the database publication fails."""
        created: list[Path] = []
        try:
            for area, destination in (
                ("collection", self.store.root),
                ("state", self.store.database.parent),
            ):
                folder = stage / area
                if not folder.exists():
                    continue
                for path in sorted(folder.rglob("*")):
                    target = destination / path.relative_to(folder)
                    if path.is_dir():
                        if target.is_symlink():
                            raise ConflictError("A recovery destination became a symbolic link.")
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        entry = _entry("collection", path.relative_to(folder).as_posix(), path)
                        _copy_verified(path, target, entry)
                        created.append(target)
            with (
                self.store.connect() as destination,
                sqlite3.connect(stage / "database" / "review.sqlite3") as source,
            ):
                source.backup(destination)
        except BaseException:
            for path in reversed(created):
                path.unlink(missing_ok=True)
            raise

    def export_archive(self, destination: Path) -> ArchiveSummary:
        """Serialize application edits while creating a local, consistent SQLite archive."""
        with self.store.connect() as db:
            _ = _database_tables(db)
        _ = self.write_portable_metadata()
        with self.store.connect() as lock:
            _ = lock.execute("BEGIN IMMEDIATE")
            return self._export_archive(destination)

    def _export_archive(self, destination: Path) -> ArchiveSummary:
        """Stream a deduplicated archive while the caller holds the workspace write lock."""
        return self._prepare_archive(destination)

    def export_archive_if_changed(
        self, destination: Path, previous_fingerprint: str
    ) -> ArchiveSummary:
        """Create an archive only when its prepared saved content differs from a checkpoint."""
        with self.store.connect() as db:
            _ = _database_tables(db)
        _ = self.write_portable_metadata()
        with self.store.connect() as lock:
            _ = lock.execute("BEGIN IMMEDIATE")
            return self._prepare_archive(destination, previous_fingerprint)

    def _prepare_archive(
        self, destination: Path, previous_fingerprint: str | None = None
    ) -> ArchiveSummary:
        """Use one locked source inventory for content comparison and archive construction."""
        destination = destination.expanduser().absolute()
        if destination.exists() or destination.is_symlink():
            raise ConflictError("Choose a new archive filename; existing files are never replaced.")
        if destination.is_relative_to(self.store.root) or destination.is_relative_to(
            self.store.database.parent.resolve()
        ):
            raise ValueError("Save the archive outside the workspace collection and private state.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="workspace-backup-") as temporary:
            stage = Path(temporary).resolve()
            database = stage / "review.sqlite3"
            with self.store.connect() as source, sqlite3.connect(database) as backup:
                source.backup(backup)
            _sanitize_database(database, [])
            sources: list[tuple[RecoveryFile, Path]] = []
            state = self.store.database.parent.resolve()
            excluded = {
                state / name
                for name in (
                    *STATE_DIRECTORIES,
                    "libraries",
                    "artifact-previews",
                    "artifact-captures",
                    "copilot-captures",
                    self.store.database.name,
                    *(self.store.database.name + suffix for suffix in ("-wal", "-shm", "-journal")),
                )
            }
            local_state = self.store.root / ".state"
            if (
                state.is_relative_to(self.store.root)
                and state != self.store.root
                and state != local_state
            ):
                excluded.add(state)
            if local_state.is_dir() and not local_state.is_symlink():
                excluded.update(
                    path for path in local_state.iterdir() if path.name != "artifact-feedback"
                )
            directories: list[str] = []
            for path in _regular_files(self.store.root, frozenset(excluded), directories):
                relative = path.relative_to(self.store.root)
                if path.is_relative_to(state):
                    state_relative = path.relative_to(state)
                    if state != self.store.root and state_relative.parts[0] != "artifact-feedback":
                        continue
                    if state_relative.parts[0] in {
                        *STATE_DIRECTORIES,
                        self.store.database.name,
                        *(
                            self.store.database.name + suffix
                            for suffix in ("-wal", "-shm", "-journal")
                        ),
                    }:
                        continue
                if relative.parts[0] == ".state" and relative.parts[1:2] != ("artifact-feedback",):
                    continue
                sources.append((_entry("collection", relative.as_posix(), path), path))
            for directory in STATE_DIRECTORIES:
                folder = state / directory
                if folder.exists():
                    sources.extend(
                        (_entry("state", path.relative_to(state).as_posix(), path), path)
                        for path in _regular_files(folder)
                    )
            sources.append((_entry("database", "review.sqlite3", database), database))
            files = [item for item, _ in sources]
            _bound(files)
            fingerprint = _archive_fingerprint(files, directories, database)
            if fingerprint == previous_fingerprint:
                return ArchiveSummary(
                    path=str(destination),
                    sha256="",
                    files=len(files),
                    objects=len({item.sha256 for item in files}),
                    bytes=sum(item.size for item in files),
                    fingerprint=fingerprint,
                    unchanged=True,
                )
            manifest = WorkspaceArchive(
                created_at=datetime.now(UTC).isoformat(),
                source_root=str(self.store.root),
                source_state=str(state),
                files=files,
                directories=directories,
            )
            archive_path = stage / "workspace.zip"
            seen: set[str] = set()
            with ZipFile(archive_path, "w", ZIP_DEFLATED, compresslevel=6) as archive:
                archive.writestr(ARCHIVE_MANIFEST, manifest.model_dump_json(indent=2))
                for item, path in sources:
                    if item.sha256 in seen:
                        continue
                    seen.add(item.sha256)
                    hasher = hashlib.sha256()
                    size = 0
                    with (
                        path.open("rb") as incoming,
                        archive.open("objects/" + item.sha256, "w", force_zip64=True) as outgoing,
                    ):
                        while chunk := incoming.read(CHUNK_BYTES):
                            size += len(chunk)
                            if size > item.size:
                                raise ConflictError(
                                    "Workspace changed during backup. Retry when edits finish."
                                )
                            hasher.update(chunk)
                            _ = outgoing.write(chunk)
                    if size != item.size or hasher.hexdigest() != item.sha256:
                        raise ConflictError(
                            "Workspace changed during backup. Retry when edits finish."
                        )
            details = archive_path.stat()
            expected = RecoveryFile(
                area="collection",
                path="workspace.zip",
                size=details.st_size,
                sha256=file_hash(archive_path),
                mtime_ns=details.st_mtime_ns,
            )
            _copy_verified(archive_path, destination, expected)
        return ArchiveSummary(
            path=str(destination),
            sha256=file_hash(destination),
            files=len(files),
            objects=len(seen),
            bytes=sum(item.size for item in files),
            fingerprint=fingerprint,
        )
