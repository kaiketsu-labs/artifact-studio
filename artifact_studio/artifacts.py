"""Owned artifact bundles, immutable snapshots and in-place presentation adapters."""

import base64
import binascii
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from .artifact_models import (
    ArtifactFeedback,
    ArtifactFeedbackFeed,
    ArtifactFeedbackInput,
    ArtifactFile,
    ArtifactFileContent,
    ArtifactFileInput,
    ArtifactManifest,
    ArtifactTarget,
    ArtifactView,
    ProjectView,
)
from .models import ElementGroupTarget, Feedback, FeedbackFile, FeedbackWorkflow
from .storage import SAFE_ID, ConflictError, Store, decode_attachments, digest, replace_text

LEGACY_PROJECT = "legacy-presentations"
MAX_FILES = 5000
MAX_FILE_SIZE = 50_000_000
MAX_BUNDLE_SIZE = 300_000_000
REMOTE_URL = re.compile(r"(?:https?:)?//[^\s\"'<>)}]+")
TEXT_TYPES = {".html", ".htm", ".css", ".js", ".mjs", ".svg", ".md", ".json"}


def relative_path(value: str) -> str:
    """Validate portable relative paths before any filesystem access."""
    path = PurePosixPath(value)
    if (
        not value
        or len(value) > 1000
        or value != path.as_posix()
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or ":" in value
        or any(ord(character) < 32 for character in value)
        or value == "."
    ):
        raise ValueError("Use a normalized relative file path without traversal.")
    return value


def file_inventory(files: dict[str, bytes]) -> list[ArtifactFile]:
    """Hash every owned byte, including assets unrelated to the entrypoint extension."""
    return [
        ArtifactFile(
            path=name,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            mime_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
        )
        for name, data in sorted(files.items())
    ]


def _retained_mime_types(
    inventory: list[ArtifactFile], retained: list[ArtifactFile]
) -> list[ArtifactFile]:
    """Keep saved interpretation for known paths across host MIME registry differences."""
    types = {item.path: item.mime_type for item in retained}
    return [
        item.model_copy(update={"mime_type": types[item.path]}) if item.path in types else item
        for item in inventory
    ]


def _snapshot_matches(manifest: ArtifactManifest, inventory: list[ArtifactFile]) -> bool:
    """Check bytes and saved metadata without reinterpreting MIME on this host."""
    return (
        bundle_hash(manifest.files, manifest.entrypoint, manifest.kind) == manifest.content_hash
        and _retained_mime_types(inventory, manifest.files) == manifest.files
    )


def legacy_inventory(folder: Path) -> tuple[list[ArtifactFile], bool, float]:
    """Bound legacy discovery to file stats, skipping links without changing existing files."""
    inventory: list[ArtifactFile] = []
    updated = 0.0
    for directory, subdirectories, filenames in folder.walk(follow_symlinks=False):
        subdirectories[:] = sorted(
            name for name in subdirectories if not (directory / name).is_symlink()
        )
        for name in sorted(filenames):
            path = directory / name
            try:
                details = path.lstat()
                relative = relative_path(path.relative_to(folder).as_posix())
            except OSError, ValueError:
                continue
            if not stat.S_ISREG(details.st_mode):
                continue
            if len(inventory) == MAX_FILES:
                return inventory, False, updated
            updated = max(updated, details.st_mtime)
            inventory.append(
                ArtifactFile(
                    path=relative,
                    size=details.st_size,
                    sha256="",
                    mime_type=mimetypes.guess_type(relative)[0] or "application/octet-stream",
                )
            )
    return inventory, True, updated


def bundle_hash(files: list[ArtifactFile], entrypoint: str, kind: str) -> str:
    """Include dependencies and the versioned viewer contract in review identity."""
    return digest(
        json.dumps(
            ["artifact-viewer-v1", kind, entrypoint, [item.model_dump() for item in files]],
            sort_keys=True,
        )
    )


def artifact_revision_hash(artifact_id: str, revision: int, content_hash: str) -> str:
    """Bind restore concurrency to a distinct save even when bundle bytes repeat."""
    return digest(json.dumps(["artifact-revision", artifact_id, revision, content_hash]))


def read_tree(folder: Path) -> dict[str, bytes]:
    """Capture a bounded bundle without following symlinks or accepting special files."""
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("An artifact bundle must be a real directory.")
    result: dict[str, bytes] = {}
    total = 0
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise ValueError("Artifact bundles cannot contain symlinks.")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Artifact bundles contain only ordinary files.")
        name = relative_path(path.relative_to(folder).as_posix())
        if path.stat().st_size > MAX_FILE_SIZE:
            raise ValueError("Keep individual artifact files at or below 50 MB.")
        data = path.read_bytes()
        total += len(data)
        result[name] = data
        if len(data) > MAX_FILE_SIZE or total > MAX_BUNDLE_SIZE or len(result) > MAX_FILES:
            raise ValueError("Keep bundles below 300 MB and 5,000 files.")
    return result


def tree_inventory(folder: Path) -> list[ArtifactFile]:
    """Verify owned bundle identity with bounded buffers instead of retaining every file."""
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("An artifact bundle must be a real directory.")
    result: list[ArtifactFile] = []
    total = 0
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise ValueError("Artifact bundles cannot contain symlinks.")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Artifact bundles contain only ordinary files.")
        name = relative_path(path.relative_to(folder).as_posix())
        if path.stat().st_size > MAX_FILE_SIZE:
            raise ValueError("Keep individual artifact files at or below 50 MB.")
        checksum = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                total += len(chunk)
                if size > MAX_FILE_SIZE or total > MAX_BUNDLE_SIZE:
                    raise ValueError("Keep bundles below 300 MB and 5,000 files.")
                checksum.update(chunk)
        result.append(
            ArtifactFile(
                path=name,
                size=size,
                sha256=checksum.hexdigest(),
                mime_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
            )
        )
        if len(result) > MAX_FILES:
            raise ValueError("Keep bundles below 300 MB and 5,000 files.")
    # Manifest identity uses POSIX filename order, not Path component order:
    # "assets.js" sorts before "assets/icon.svg" in existing saved manifests.
    return sorted(result, key=lambda item: item.path)


def write_tree(folder: Path, files: dict[str, bytes]) -> None:
    """Write already-validated bytes into a newly owned staging directory."""
    folder.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        path = folder / relative_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(data)


def detect_kind(entrypoint: str) -> str:
    """Choose a viewer without limiting the file types that can be stored."""
    suffix = PurePosixPath(entrypoint).suffix.lower()
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix == ".svg":
        return "svg"
    return "image" if (mimetypes.guess_type(entrypoint)[0] or "").startswith("image/") else "file"


class ArtifactStore:
    """Share the local database while keeping new bundles and legacy decks independent."""

    def __init__(self, store: Store) -> None:
        """Install additive registry tables without moving or rewriting personal files."""
        self.store = store
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS artifact_projects (
                    id TEXT PRIMARY KEY, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS artifact_items (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document TEXT NOT NULL,
                    approved_hash TEXT NOT NULL DEFAULT '', seen_hash TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT 'live');
                CREATE TABLE IF NOT EXISTS artifact_revisions (
                    artifact_id TEXT NOT NULL, revision INTEGER NOT NULL, document TEXT NOT NULL,
                    PRIMARY KEY(artifact_id, revision));
                CREATE TABLE IF NOT EXISTS artifact_operations (
                    artifact_id TEXT NOT NULL, request_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    result TEXT NOT NULL, PRIMARY KEY(artifact_id, request_id));
                CREATE TABLE IF NOT EXISTS artifact_legacy_projects (
                    artifact_id TEXT PRIMARY KEY, project_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS artifact_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, artifact_id TEXT NOT NULL,
                    document TEXT NOT NULL, released INTEGER NOT NULL, author TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS artifact_feedback_pending
                    ON artifact_feedback(artifact_id,released,author);
                CREATE TABLE IF NOT EXISTS artifact_delivery (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER UNIQUE NOT NULL);
            """)
            from .version_history import install_artifact_history

            install_artifact_history(db)

    def _owned(self, path: Path) -> Path:
        """Reject redirected registry directories before reading or writing owned state."""
        if not path.is_relative_to(self.store.root):
            raise ValueError("Artifact storage must remain inside the workspace.")
        for parent in [path, *path.parents]:
            if parent == self.store.root:
                break
            if parent.is_symlink():
                raise ValueError("Artifact storage cannot pass through symlinks.")
        return path

    @staticmethod
    def _id(value: str) -> str:
        """Accept opaque identifiers without allowing them to become arbitrary paths."""
        if not SAFE_ID.fullmatch(value):
            raise ValueError("Invalid artifact or project ID.")
        return value

    def _folder(self, artifact: ArtifactManifest) -> Path:
        """Locate this artifact's owned working source and immutable revision collection."""
        return self._owned(
            self.store.root
            / "projects"
            / self._id(artifact.storage_project_id or artifact.project_id)
            / "artifacts"
            / self._id(artifact.id)
        )

    def _snapshot(self, artifact: ArtifactManifest) -> Path:
        """Return the immutable source directory for one recorded revision."""
        return self._owned(self._folder(artifact) / "revisions" / str(artifact.revision) / "files")

    def _project(self, db: sqlite3.Connection, project_id: str) -> ProjectView:
        """Read a registered project without recursively loading its artifacts."""
        self._id(project_id)
        if project_id == LEGACY_PROJECT:
            return ProjectView(id=project_id, title="Presentations", legacy=True)
        row = db.execute(
            "SELECT document FROM artifact_projects WHERE id=?", (project_id,)
        ).fetchone()
        if row is None:
            raise FileNotFoundError("Project does not exist.")
        project = ProjectView.model_validate_json(cast(str, row[0]))
        brief = self._owned(self.store.root / "projects" / project_id / "README.md")
        if brief.stat().st_size > 400_000:
            raise ValueError("Keep the project brief at or below 100,000 characters.")
        context = brief.read_text(encoding="utf-8")
        if len(context) > 100_000:
            raise ValueError("Keep the project brief at or below 100,000 characters.")
        return project.model_copy(update={"context": context, "context_hash": digest(context)})

    def presentation_summaries(self) -> list[dict[str, str]]:
        """Keep registered design examples out of work listings without moving their files."""
        designs = {design.folder_id for design in self.store.designs(include_archived=True)}
        return [deck for deck in self.store.list_decks() if deck["id"] not in designs]

    def list_projects(
        self, *, presentations: list[dict[str, str]] | None = None
    ) -> list[ProjectView]:
        """List briefs and counts while keeping the legacy collection virtual and in place."""
        with self.store.connect() as db:
            projects = [
                self._project(db, cast(str, row[0]))
                for row in db.execute("SELECT id FROM artifact_projects ORDER BY id")
            ]
            bindings = {
                cast(str, row[0]): cast(str, row[1])
                for row in db.execute("SELECT artifact_id,project_id FROM artifact_legacy_projects")
            }
            counts = {
                cast(str, row[0]): cast(int, row[1])
                for row in db.execute(
                    "SELECT project_id,COUNT(*) FROM artifact_items GROUP BY project_id"
                )
            }
        for deck in self.presentation_summaries() if presentations is None else presentations:
            project_id = bindings.get(deck["id"], LEGACY_PROJECT)
            counts[project_id] = counts.get(project_id, 0) + 1
        if counts.get(LEGACY_PROJECT):
            projects.insert(0, ProjectView(id=LEGACY_PROJECT, title="Presentations", legacy=True))
        return [
            project.model_copy(update={"artifact_count": counts.get(project.id, 0)})
            for project in projects
        ]

    def create_project(self, title: str, context: str = "") -> ProjectView:
        """Create a real project and shared brief without applying any design template."""
        if not title.strip() or len(title) > 200 or len(context) > 100_000:
            raise ValueError("Use a project title up to 200 characters and brief up to 100,000.")
        project_id = "project-" + uuid.uuid4().hex[:16]
        folder = self._owned(self.store.root / "projects" / project_id)
        project = ProjectView(
            id=project_id,
            title=title.strip(),
            context=context,
            context_hash=digest(context),
            source_path=str(folder),
        )
        folder.mkdir(parents=True, exist_ok=False)
        _ = (folder / "README.md").write_text(context, encoding="utf-8")
        replace_text(folder / "project.json", project.model_dump_json(indent=2) + "\n")
        with self.store.connect() as db:
            _ = db.execute(
                "INSERT INTO artifact_projects VALUES (?,?)",
                (project_id, project.model_dump_json()),
            )
        return project

    def set_artifact_project(self, artifact_id: str, project_id: str) -> ArtifactView:
        """Change project membership while preserving source paths and historical snapshots."""
        with self.store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            project = self._project(db, project_id)
            row = self._row(db, artifact_id)
            if row is not None:
                if project.legacy:
                    raise ValueError("Owned artifacts require a real project.")
                original = ArtifactManifest.model_validate_json(cast(str, row["document"]))
                mirror = self._folder(original) / "artifact.json"
                mirror_bytes = mirror.read_bytes() if mirror.exists() else None
                try:
                    current = self._synchronize(db, row)
                    moved = current.model_copy(
                        update={
                            "project_id": project_id,
                            "storage_project_id": current.storage_project_id or current.project_id,
                        }
                    )
                    _ = db.execute(
                        "UPDATE artifact_items SET project_id=?,document=? WHERE id=?",
                        (project_id, moved.model_dump_json(), artifact_id),
                    )
                    self._mirror(moved)
                    view = self._view(db, moved)
                    db.commit()
                    return view
                except BaseException:
                    if mirror_bytes is None:
                        mirror.unlink(missing_ok=True)
                    else:
                        _ = mirror.write_bytes(mirror_bytes)
                    db.rollback()
                    raise
            _ = self.store.read(artifact_id)
            _ = db.execute(
                "INSERT INTO artifact_legacy_projects VALUES (?,?) ON CONFLICT(artifact_id) "
                "DO UPDATE SET project_id=excluded.project_id",
                (artifact_id, project_id),
            )
        return self.get_artifact(artifact_id)

    def _manifest(
        self,
        *,
        artifact_id: str,
        project_id: str,
        title: str,
        kind: str,
        entrypoint: str,
        revision: int,
        files: dict[str, bytes],
        context: str,
        original_source_path: str,
        storage_project_id: str = "",
        retained_files: list[ArtifactFile] | None = None,
    ) -> ArtifactManifest:
        """Build a dependency-complete identity and flag potentially mutable remote inputs."""
        inventory = file_inventory(files)
        if retained_files is not None:
            inventory = _retained_mime_types(inventory, retained_files)
        remote = sorted(
            {
                url
                for name, data in files.items()
                if PurePosixPath(name).suffix.lower() in TEXT_TYPES
                for url in REMOTE_URL.findall(data.decode("utf-8", errors="ignore"))
                if not url.startswith(("http://www.w3.org/", "https://www.w3.org/"))
            }
        )
        return ArtifactManifest(
            id=artifact_id,
            project_id=project_id,
            title=title,
            kind=kind,
            entrypoint=entrypoint,
            revision=revision,
            content_hash=bundle_hash(inventory, entrypoint, kind),
            files=inventory,
            context=context,
            context_hash=digest(context),
            original_source_path=original_source_path,
            storage_project_id=storage_project_id,
            external_dependencies=remote,
            created_at=datetime.now(UTC).isoformat(),
        )

    def _save_snapshot(self, manifest: ArtifactManifest, files: dict[str, bytes]) -> None:
        """Share verified immutable files while keeping working sources on separate inodes."""
        destination = self._snapshot(manifest)
        destination.parent.parent.mkdir(parents=True, exist_ok=True)
        previous = destination.parent.parent / str(manifest.revision - 1) / "files"
        with tempfile.TemporaryDirectory(dir=destination.parent.parent) as temporary:
            staged = Path(temporary) / "revision"
            staged.mkdir()
            (staged / "files").mkdir()
            for item in manifest.files:
                target = staged / "files" / relative_path(item.path)
                target.parent.mkdir(parents=True, exist_ok=True)
                candidate = self._owned(previous / item.path)
                if candidate.is_file() and not candidate.is_symlink():
                    with candidate.open("rb") as source:
                        unchanged = hashlib.file_digest(source, "sha256").hexdigest() == item.sha256
                    if unchanged:
                        try:
                            os.link(candidate, target, follow_symlinks=False)
                        except OSError:
                            pass
                        else:
                            continue
                _ = target.write_bytes(files[item.path])
            _ = (staged / "artifact.json").write_text(
                manifest.model_dump_json(indent=2), encoding="utf-8"
            )
            if destination.parent.exists():
                shutil.rmtree(destination.parent)
            staged.replace(destination.parent)

    def _mirror(self, manifest: ArtifactManifest) -> None:
        """Keep a readable manifest beside owned sources; SQLite remains the atomic pointer."""
        replace_text(
            self._folder(manifest) / "artifact.json", manifest.model_dump_json(indent=2) + "\n"
        )

    @staticmethod
    def _record(db: sqlite3.Connection, manifest: ArtifactManifest) -> None:
        """Publish a new current pointer and retained revision in the same transaction."""
        document = manifest.model_dump_json()
        _ = db.execute("UPDATE artifact_items SET document=? WHERE id=?", (document, manifest.id))
        _ = db.execute(
            "INSERT INTO artifact_revisions VALUES (?,?,?)",
            (manifest.id, manifest.revision, document),
        )

    def _row(self, db: sqlite3.Connection, artifact_id: str) -> sqlite3.Row | None:
        """Retrieve registry state while validating the identifier even for missing rows."""
        return db.execute(
            "SELECT * FROM artifact_items WHERE id=?", (self._id(artifact_id),)
        ).fetchone()

    def _view(self, db: sqlite3.Connection, manifest: ArtifactManifest) -> ArtifactView:
        """Compute status without revealing discussion drafts to metadata readers."""
        row = self._row(db, manifest.id)
        if row is None:
            raise FileNotFoundError("Artifact does not exist.")
        approved = row["approved_hash"] == manifest.content_hash
        unseen = row["seen_hash"] != manifest.content_hash
        timestamps = db.execute(
            """SELECT
                (SELECT json_extract(document, '$.created_at') FROM artifact_revisions
                 WHERE artifact_id=? AND revision<=? ORDER BY revision LIMIT 1) AS created_at,
                (SELECT json_extract(current.document, '$.created_at')
                 FROM artifact_revisions AS current
                 WHERE current.artifact_id=? AND current.revision<=? AND
                   json_extract(current.document, '$.content_hash') IS NOT
                   (SELECT json_extract(previous.document, '$.content_hash')
                    FROM artifact_revisions AS previous
                    WHERE previous.artifact_id=current.artifact_id
                      AND previous.revision<current.revision
                    ORDER BY previous.revision DESC LIMIT 1)
                 ORDER BY current.revision DESC LIMIT 1) AS updated_at""",
            (manifest.id, manifest.revision, manifest.id, manifest.revision),
        ).fetchone()
        created_at = cast(str | None, timestamps["created_at"]) or manifest.created_at
        updated_at = cast(str | None, timestamps["updated_at"]) or created_at
        return ArtifactView(
            **manifest.model_copy(update={"created_at": created_at}).model_dump(),
            updated_at=updated_at,
            project_title=self._project(db, manifest.project_id).title,
            approved=approved,
            has_unseen_changes=unseen,
            status="approved" if approved else "updated" if unseen else "draft",
            source_path=str(self._folder(manifest) / "files"),
        )

    def _synchronize(self, db: sqlite3.Connection, row: sqlite3.Row) -> ArtifactManifest:
        """Capture out-of-band working edits before reviewing or accepting another write."""
        manifest = ArtifactManifest.model_validate_json(cast(str, row["document"]))
        self._verify_snapshot(manifest)
        folder = self._owned(self._folder(manifest) / "files")
        inventory = _retained_mime_types(tree_inventory(folder), manifest.files)
        if manifest.entrypoint not in {item.path for item in inventory}:
            raise ConflictError("The artifact entrypoint is missing from its working source.")
        if bundle_hash(inventory, manifest.entrypoint, manifest.kind) != manifest.content_hash:
            files = read_tree(folder)
            manifest = self._manifest(
                artifact_id=manifest.id,
                project_id=manifest.project_id,
                title=manifest.title,
                kind=manifest.kind,
                entrypoint=manifest.entrypoint,
                revision=manifest.revision + 1,
                files=files,
                context=manifest.context,
                original_source_path=manifest.original_source_path,
                storage_project_id=manifest.storage_project_id,
                retained_files=manifest.files,
            )
            self._save_snapshot(manifest, files)
            self._record(db, manifest)
            self._mirror(manifest)
        return manifest

    def _verify_snapshot(self, manifest: ArtifactManifest) -> None:
        """Detect direct snapshot tampering instead of silently blessing changed history."""
        inventory = tree_inventory(self._snapshot(manifest))
        if not _snapshot_matches(manifest, inventory):
            raise ConflictError("An immutable artifact snapshot was modified outside the hub.")

    def _snapshot_contents(self, manifest: ArtifactManifest) -> dict[str, bytes]:
        """Capture and verify snapshot bytes once for bounded downstream dependency reads."""
        files = read_tree(self._snapshot(manifest))
        if not _snapshot_matches(manifest, file_inventory(files)):
            raise ConflictError("An immutable artifact snapshot was modified outside the hub.")
        return files

    def snapshot_files(self, artifact_id: str, revision: int | None = None) -> dict[str, bytes]:
        """Pin a complete verified snapshot in memory for isolated capture and export tooling."""
        return self.verified_snapshot(artifact_id, revision)[1]

    def _selected_snapshot_contents(
        self, artifact: ArtifactManifest, paths: list[str]
    ) -> dict[str, bytes]:
        """Read declared regular files and validate the exact bytes against saved hashes."""
        metadata = {file.path: file for file in artifact.files}
        contents: dict[str, bytes] = {}
        for path in paths:
            path = relative_path(path)
            if path not in metadata:
                raise FileNotFoundError("File is not owned by this artifact revision.")
            recorded = metadata[path]
            if not 0 <= recorded.size <= MAX_FILE_SIZE:
                raise ValueError("Keep individual artifact files at or below 50 MB.")
            target = self._owned(self._snapshot(artifact) / path)
            if not stat.S_ISREG(target.lstat().st_mode):
                raise ValueError("Snapshot resources must be regular files.")
            flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_BINARY", 0)
            )
            descriptor = os.open(target, flags)
            with os.fdopen(descriptor, "rb") as source:
                details = os.fstat(source.fileno())
                if not stat.S_ISREG(details.st_mode) or details.st_size != recorded.size:
                    raise ConflictError("An immutable artifact resource was modified.")
                data = source.read(recorded.size + 1)
            if len(data) != recorded.size or hashlib.sha256(data).hexdigest() != recorded.sha256:
                raise ConflictError("An immutable artifact snapshot was modified outside the hub.")
            contents[path] = data
        return contents

    def verified_snapshot(
        self,
        artifact_id: str,
        revision: int | None = None,
        *,
        paths: list[str] | Literal["entrypoint", "preview"] | None = None,
    ) -> tuple[ArtifactView, dict[str, bytes]]:
        """Verify every file; preview mode retains text while images remain revision URLs."""

        def selection(manifest: ArtifactManifest) -> list[str] | None:
            if paths == "entrypoint":
                return [manifest.entrypoint]
            if paths == "preview":
                entry = next(item for item in manifest.files if item.path == manifest.entrypoint)
                return [] if entry.mime_type.startswith("image/") else [manifest.entrypoint]
            return paths

        if revision is None:
            artifact = self.get_artifact(artifact_id)
            if artifact.legacy_deck_id:
                raise ValueError("Use presentation-specific capture for legacy decks.")
            selected = selection(artifact)
            contents = (
                self._snapshot_contents(artifact)
                if selected is None
                else self._selected_snapshot_contents(artifact, selected)
            )
            return artifact, contents
        with self.store.connect() as db:
            row = db.execute(
                "SELECT document FROM artifact_revisions WHERE artifact_id=? AND revision=?",
                (self._id(artifact_id), revision),
            ).fetchone()
            if row is None:
                raise FileNotFoundError("Artifact revision does not exist.")
            manifest = ArtifactManifest.model_validate_json(cast(str, row[0]))
            selected = selection(manifest)
            if selected is None:
                contents = self._snapshot_contents(manifest)
            else:
                self._verify_snapshot(manifest)
                contents = self._selected_snapshot_contents(manifest, selected)
            return self._view(db, manifest), contents

    def register_artifact(
        self,
        project_id: str,
        title: str,
        source_path: str | Path,
        entrypoint: str,
        kind: str = "auto",
        context: str = "",
    ) -> ArtifactView:
        """Copy a local file or bundle into owned storage while retaining the original source."""
        if not title.strip() or len(title) > 200 or len(context) > 100_000:
            raise ValueError(
                "Use an artifact title up to 200 characters and context up to 100,000."
            )
        entrypoint = relative_path(entrypoint)
        source = Path(source_path).expanduser().absolute()
        if source.is_symlink():
            raise ValueError("Artifact sources cannot be symlinks.")
        if source.is_file():
            if source.stat().st_size > MAX_FILE_SIZE:
                raise ValueError("Keep individual artifact files at or below 50 MB.")
            files = {entrypoint: source.read_bytes()}
        else:
            files = read_tree(source)
        if entrypoint not in files:
            raise ValueError("The entrypoint must name a file inside the supplied bundle.")
        if kind == "auto":
            kind = detect_kind(entrypoint)
        if not kind or len(kind) > 100 or kind == "presentation":
            raise ValueError(
                "Use an artifact viewer kind; presentations use the existing deck tools."
            )
        artifact_id = "artifact-" + uuid.uuid4().hex[:16]
        with self.store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            project = self._project(db, project_id)
            if project.legacy:
                raise ValueError("Create a project before registering new artifacts.")
            manifest = self._manifest(
                artifact_id=artifact_id,
                project_id=project_id,
                title=title.strip(),
                kind=kind,
                entrypoint=entrypoint,
                revision=1,
                files=files,
                context=context,
                original_source_path=str(source),
            )
            folder = self._folder(manifest)
            folder.mkdir(parents=True, exist_ok=False)
            write_tree(folder / "files", files)
            self._save_snapshot(manifest, files)
            self._mirror(manifest)
            _ = db.execute(
                "INSERT INTO artifact_items(id,project_id,document,seen_hash) VALUES (?,?,?,?)",
                (artifact_id, project_id, manifest.model_dump_json(), manifest.content_hash),
            )
            _ = db.execute(
                "INSERT INTO artifact_revisions VALUES (?,?,?)",
                (artifact_id, 1, manifest.model_dump_json()),
            )
            return self._view(db, manifest)

    def _legacy(self, artifact_id: str) -> ArtifactView:
        """Adapt legacy review identities without scanning asset bytes or imposing bundle limits."""
        deck = self.store.read(artifact_id)
        design_hash = self.store.visual_hash(artifact_id)
        hashes = [
            (slide.id, self.store.content_hash(artifact_id, slide, design_hash=design_hash))
            for slide in deck.slides
        ]
        approved_hashes, seen_hashes = self.store.review_hashes(artifact_id)
        folder = self.store.folder(artifact_id)
        context_file = folder / "README.md"
        context = context_file.read_text() if context_file.exists() else ""
        files, files_complete, updated = legacy_inventory(folder)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT project_id FROM artifact_legacy_projects WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
            project_id = cast(str, row[0]) if row else LEGACY_PROJECT
            project = self._project(db, project_id)
            history = db.execute(
                "SELECT MIN(created_at) FROM slide_version_metadata WHERE deck=?",
                (artifact_id,),
            ).fetchone()
        folder_stat = folder.stat()
        birth = getattr(folder_stat, "st_birthtime", None)
        history_created = cast(str | None, history[0]) if history else None
        candidates = [history_created] if history_created else []
        if birth is not None:
            candidates.append(datetime.fromtimestamp(birth, UTC).isoformat())
        if not candidates:
            candidates.append(
                datetime.fromtimestamp((folder / "index.html").stat().st_mtime, UTC).isoformat()
            )
        created_at = min(candidates)
        updated_at = datetime.fromtimestamp(updated, UTC).isoformat() if updated else created_at
        manifest = ArtifactManifest(
            id=artifact_id,
            project_id=project_id,
            title=deck.title,
            kind="presentation",
            entrypoint="index.html",
            revision=max(slide.revision for slide in deck.slides),
            content_hash=digest(
                json.dumps(
                    [
                        deck.title,
                        design_hash,
                        hashes,
                    ]
                )
            ),
            files=files,
            context=context,
            context_hash=digest(context),
            original_source_path=str(folder),
            created_at=created_at,
            viewer_version="legacy-presentation",
        )
        approved = all(approved_hashes.get(identifier) == value for identifier, value in hashes)
        unseen = any(seen_hashes.get(identifier) != value for identifier, value in hashes)
        return ArtifactView(
            **manifest.model_dump(),
            updated_at=updated_at,
            project_title=project.title,
            source_path=str(folder),
            legacy_deck_id=artifact_id,
            snapshot_mode="legacy-in-place",
            files_complete=files_complete,
            approved=approved,
            has_unseen_changes=unseen,
            status="approved" if approved else "updated" if unseen else "draft",
        )

    def get_artifact(self, artifact_id: str) -> ArtifactView:
        """Return current metadata after reconciling external edits into a new snapshot."""
        with self.store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            row = self._row(db, artifact_id)
            if row is not None:
                return self._view(db, self._synchronize(db, row))
        return self._legacy(artifact_id)

    def get_artifact_revision(self, artifact_id: str, revision: int) -> ArtifactView:
        """Resolve historical metadata without substituting the latest working source."""
        with self.store.connect() as db:
            row = db.execute(
                "SELECT document FROM artifact_revisions WHERE artifact_id=? AND revision=?",
                (self._id(artifact_id), revision),
            ).fetchone()
            if row is None:
                raise FileNotFoundError("Artifact revision does not exist.")
            manifest = ArtifactManifest.model_validate_json(cast(str, row[0]))
            self._verify_snapshot(manifest)
            return self._view(db, manifest)

    def verified_revision_file(self, artifact_id: str, revision: int, path: str) -> bytes:
        """Return only declared bytes matching this immutable revision's recorded hash.

        Resource requests must not hash every sibling asset. Verify the requested
        file itself and retain those exact bytes for the response, avoiding both
        bundle-sized work for tiny resources and a later unchecked file reopen.
        Whole-snapshot inspection and capability issuance still verify the bundle.
        """
        path = relative_path(path)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT document FROM artifact_revisions WHERE artifact_id=? AND revision=?",
                (self._id(artifact_id), revision),
            ).fetchone()
            if row is None:
                raise FileNotFoundError("Artifact revision does not exist.")
            manifest = ArtifactManifest.model_validate_json(cast(str, row[0]))
            return self._selected_snapshot_contents(manifest, [path])[path]

    def list_artifacts(
        self, project_id: str = "", *, presentations: list[dict[str, str]] | None = None
    ) -> list[ArtifactView]:
        """List work without reusable design examples, source bodies or feedback."""
        with self.store.connect() as db:
            if project_id:
                _ = self._project(db, project_id)
            ids = [
                cast(str, row[0])
                for row in db.execute(
                    "SELECT id FROM artifact_items "
                    + ("WHERE project_id=? " if project_id else "")
                    + "ORDER BY id",
                    (project_id,) if project_id else (),
                )
            ]
            bindings = {
                cast(str, row[0]): cast(str, row[1])
                for row in db.execute("SELECT artifact_id,project_id FROM artifact_legacy_projects")
            }
        items = [self.get_artifact(artifact_id) for artifact_id in ids]
        if project_id and project_id != LEGACY_PROJECT and project_id not in bindings.values():
            return items
        items.extend(
            self._legacy(deck["id"])
            for deck in (self.presentation_summaries() if presentations is None else presentations)
            if not project_id or bindings.get(deck["id"], LEGACY_PROJECT) == project_id
        )
        return items

    def resolve_file(self, artifact_id: str, path: str, revision: int | None = None) -> Path:
        """Resolve declared files, rejecting traversal, symlinks and altered snapshots."""
        path = relative_path(path)
        artifact = (
            self.get_artifact(artifact_id)
            if revision is None
            else self.get_artifact_revision(artifact_id, revision)
        )
        if not artifact.legacy_deck_id and path not in {file.path for file in artifact.files}:
            raise FileNotFoundError("File is not owned by this artifact revision.")
        root = Path(artifact.source_path) if artifact.legacy_deck_id else self._snapshot(artifact)
        file = self._owned(root / path)
        if not file.is_file():
            raise FileNotFoundError("Artifact file is unavailable.")
        return file

    def read_artifact_files(
        self,
        artifact_id: str,
        paths: list[str],
        revision: int | None = None,
    ) -> list[ArtifactFileContent]:
        """Return only requested files, preserving arbitrary binary formats as base64."""
        if len(paths) > 100:
            raise ValueError("Read at most 100 artifact files per request.")
        artifact = (
            self.get_artifact(artifact_id)
            if revision is None
            else self.get_artifact_revision(artifact_id, revision)
        )
        metadata = {file.path: file for file in artifact.files}
        contents = (
            {} if artifact.legacy_deck_id else self._selected_snapshot_contents(artifact, paths)
        )
        result: list[ArtifactFileContent] = []
        for path in paths:
            path = relative_path(path)
            if artifact.legacy_deck_id:
                source = self._owned(Path(artifact.source_path) / path)
                if not source.is_file():
                    raise FileNotFoundError("Legacy source file is unavailable.")
                contents[path] = source.read_bytes()
                metadata[path] = file_inventory({path: contents[path]})[0]
            if path not in metadata or path not in contents:
                raise FileNotFoundError("File is not owned by this artifact revision.")
            data = contents[path]
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                result.append(
                    ArtifactFileContent(
                        **metadata[path].model_dump(), data_base64=base64.b64encode(data).decode()
                    )
                )
            else:
                result.append(ArtifactFileContent(**metadata[path].model_dump(), text=text))
        return result

    def revise_artifact(
        self,
        artifact_id: str,
        files: list[ArtifactFileInput],
        expected_revision: int | str,
        request_id: str,
    ) -> ArtifactView:
        """Publish a complete changed-file set with optimistic concurrency and exact retries."""
        return self._revise_artifact(artifact_id, files, expected_revision, request_id)

    def restore_artifact(
        self, artifact_id: str, revision: int, expected_hash: str, request_id: str
    ) -> ArtifactView:
        """Publish retained source as a new revision without recovering old approval state."""
        if revision < 1:
            raise ValueError("Choose an existing positive artifact revision.")
        return self._revise_artifact(
            artifact_id, [], expected_hash, request_id, restore_revision=revision
        )

    def _revise_artifact(
        self,
        artifact_id: str,
        files: list[ArtifactFileInput],
        expected_revision: int | str,
        request_id: str,
        *,
        restore_revision: int | None = None,
    ) -> ArtifactView:
        """Share locked snapshot publication, rollback and idempotency for edits and restores."""
        if (
            not 8 <= len(request_id) <= 100
            or (not files and restore_revision is None)
            or len(files) > MAX_FILES
        ):
            raise ValueError("Provide changed files and a request ID of 8–100 characters.")
        names = [relative_path(file.path) for file in files]
        if len(set(names)) != len(names):
            raise ValueError("Each changed file path must occur once.")
        payload_hash = digest(
            json.dumps(
                ["restore", expected_revision, restore_revision]
                if restore_revision is not None
                else ["revise", expected_revision, [file.model_dump() for file in files]],
                sort_keys=True,
            )
        )
        with self.store.connect() as db:
            prior = self._operation(db, artifact_id, request_id, payload_hash)
            if prior is not None:
                return ArtifactView.model_validate_json(prior)
        current = self.get_artifact(artifact_id)
        if current.legacy_deck_id:
            raise ValueError("Revise presentations with the existing presentation-specific tools.")
        with self.store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            temporary: Path | None = None
            backup: Path | None = None
            working: Path | None = None
            mirror: Path | None = None
            mirror_bytes: bytes | None = None
            committed = False
            try:
                row = self._row(db, artifact_id)
                if row is not None:
                    initial = ArtifactManifest.model_validate_json(cast(str, row["document"]))
                    mirror = self._folder(initial) / "artifact.json"
                    mirror_bytes = mirror.read_bytes() if mirror.exists() else None
                prior = self._operation(db, artifact_id, request_id, payload_hash)
                if prior is not None:
                    return ArtifactView.model_validate_json(prior)
                row = self._row(db, artifact_id)
                if row is None:
                    raise FileNotFoundError("Artifact does not exist.")
                current = self._view(db, self._synchronize(db, row))
                accepted: tuple[int | str, ...] = (
                    (current.revision, current.content_hash)
                    if restore_revision is None
                    else (
                        artifact_revision_hash(current.id, current.revision, current.content_hash),
                    )
                )
                if expected_revision not in accepted:
                    raise ConflictError(
                        "Artifact changed. Read its current revision before editing."
                    )
                selected: ArtifactManifest = current
                if restore_revision is not None:
                    retained = db.execute(
                        "SELECT document FROM artifact_revisions "
                        "WHERE artifact_id=? AND revision=?",
                        (artifact_id, restore_revision),
                    ).fetchone()
                    if retained is None:
                        raise FileNotFoundError("Artifact revision does not exist.")
                    selected = ArtifactManifest.model_validate_json(cast(str, retained[0]))
                contents = self._snapshot_contents(selected)
                for file in files:
                    if file.delete:
                        if file.path not in contents:
                            raise ValueError("Cannot delete a file absent from this artifact.")
                        del contents[file.path]
                    elif file.text is not None:
                        contents[file.path] = file.text.encode("utf-8")
                    else:
                        try:
                            contents[file.path] = base64.b64decode(
                                file.data_base64 or "", validate=True
                            )
                        except (binascii.Error, ValueError) as error:
                            raise ValueError("Artifact binary data is not valid base64.") from error
                if selected.entrypoint not in contents:
                    raise ValueError("The artifact entrypoint cannot be deleted.")
                if (
                    any(len(data) > MAX_FILE_SIZE for data in contents.values())
                    or sum(map(len, contents.values())) > MAX_BUNDLE_SIZE
                    or len(contents) > MAX_FILES
                ):
                    raise ValueError("Artifact bundle exceeds the supported file or size limits.")
                manifest = self._manifest(
                    artifact_id=current.id,
                    project_id=current.project_id,
                    title=current.title,
                    kind=selected.kind,
                    entrypoint=selected.entrypoint,
                    revision=current.revision + 1,
                    files=contents,
                    context=current.context,
                    original_source_path=current.original_source_path,
                    storage_project_id=current.storage_project_id,
                    retained_files=selected.files,
                )
                if manifest.content_hash != current.content_hash or restore_revision is not None:
                    self._save_snapshot(manifest, contents)
                    folder = self._folder(manifest)
                    temporary = Path(tempfile.mkdtemp(dir=folder))
                    staged = temporary / "files"
                    write_tree(staged, contents)
                    backup = temporary / "previous"
                    (folder / "files").replace(backup)
                    working = folder / "files"
                    staged.replace(working)
                    self._record(db, manifest)
                    if restore_revision is not None:
                        _ = db.execute(
                            "UPDATE artifact_items SET approved_hash='' WHERE id=?", (artifact_id,)
                        )
                    self._mirror(manifest)
                    current = self._view(db, manifest)
                _ = db.execute(
                    "INSERT INTO artifact_operations VALUES (?,?,?,?)",
                    (artifact_id, request_id, payload_hash, current.model_dump_json()),
                )
                db.commit()
                committed = True
                return current
            except BaseException:
                if backup is not None and backup.exists():
                    if working is not None and working.exists():
                        shutil.rmtree(working)
                    backup.replace(working or backup.parent / "files")
                if mirror is not None:
                    if mirror_bytes is None:
                        mirror.unlink(missing_ok=True)
                    else:
                        _ = mirror.write_bytes(mirror_bytes)
                db.rollback()
                raise
            finally:
                if temporary is not None and (committed or backup is None or not backup.exists()):
                    shutil.rmtree(temporary, ignore_errors=True)

    @staticmethod
    def _operation(
        db: sqlite3.Connection, artifact_id: str, request_id: str, payload_hash: str
    ) -> str | None:
        """Recognize exact retries without letting a reused ID perform another operation."""
        row = db.execute(
            "SELECT payload_hash,result FROM artifact_operations "
            "WHERE artifact_id=? AND request_id=?",
            (artifact_id, request_id),
        ).fetchone()
        if row is None:
            return None
        if row[0] != payload_hash:
            raise ConflictError("Request ID already belongs to another artifact operation.")
        return cast(str, row[1])

    def approve(self, artifact_id: str, content_hash: str, approved: bool) -> ArtifactView:
        """Record a human approval of the exact reviewed snapshot; expose only through human UI."""
        return self._review(artifact_id, content_hash, approved=approved)

    def mark_seen(self, artifact_id: str, content_hash: str) -> ArtifactView:
        """Acknowledge the version actually opened without hiding a later concurrent revision."""
        return self._review(artifact_id, content_hash)

    def _review(
        self, artifact_id: str, content_hash: str, *, approved: bool | None = None
    ) -> ArtifactView:
        """Compare the complete bundle before changing review markers."""
        current = self.get_artifact(artifact_id)
        if current.content_hash != content_hash:
            raise ConflictError("Artifact changed. Review its current version first.")
        if current.legacy_deck_id:
            deck = self.store.view(artifact_id, include_pending=False)
            for slide in deck.slides:
                if approved is None:
                    _ = self.store.mark_slide_seen(artifact_id, slide.id, slide.content_hash)
                else:
                    self.store.approve(artifact_id, slide.id, slide.content_hash, approved)
            return self.get_artifact(artifact_id)
        with self.store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            row = self._row(db, artifact_id)
            if row is None:
                raise FileNotFoundError("Artifact does not exist.")
            manifest = self._synchronize(db, row)
            if manifest.content_hash != content_hash:
                raise ConflictError("Artifact changed. Review its current version first.")
            if approved is not None:
                _ = db.execute(
                    "UPDATE artifact_items SET approved_hash=? WHERE id=?",
                    (content_hash if approved else "", artifact_id),
                )
            if approved is not False:
                _ = db.execute(
                    "UPDATE artifact_items SET seen_hash=? WHERE id=?", (content_hash, artifact_id)
                )
            return self._view(db, manifest)

    def workflow(self, artifact_id: str) -> FeedbackWorkflow:
        """Read delivery preferences and this artifact's bounded host-hook connection."""
        with self.store.connect() as db:
            row = self._row(db, artifact_id)
            if row is not None:
                pending = db.execute(
                    "SELECT COUNT(*) FROM artifact_feedback "
                    "WHERE artifact_id=? AND released=0 AND author='user'",
                    (artifact_id,),
                ).fetchone()
                return self.store.workflow(artifact_id).model_copy(
                    update={
                        "mode": cast(Literal["live", "batch"], row["mode"]),
                        "pending": cast(int, pending[0]),
                    }
                )
        _ = self.store.read(artifact_id)
        return self.store.workflow(artifact_id)

    def set_mode(self, artifact_id: str, mode: Literal["live", "batch"]) -> FeedbackWorkflow:
        """Apply delivery mode to future comments without releasing pending drafts."""
        if mode not in {"live", "batch"}:
            raise ValueError("Feedback mode must be live or batch.")
        with self.store.connect() as db:
            if self._row(db, artifact_id) is None:
                return self.store.set_mode(artifact_id, mode)
            _ = db.execute("UPDATE artifact_items SET mode=? WHERE id=?", (mode, artifact_id))
        return self.workflow(artifact_id)

    def release_batch(self, artifact_id: str) -> FeedbackWorkflow:
        """Release private pending comments together with a delivery cursor assigned now."""
        with self.store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            if self._row(db, artifact_id) is None:
                db.rollback()
                return self.store.release_batch(artifact_id)
            rows = db.execute(
                "SELECT id,document FROM artifact_feedback "
                "WHERE artifact_id=? AND released=0 AND author='user' ORDER BY id",
                (artifact_id,),
            ).fetchall()
            batch_id = uuid.uuid4().hex
            for row in rows:
                feedback = ArtifactFeedback.model_validate_json(cast(str, row["document"]))
                feedback.released = True
                feedback.batch_id = batch_id
                _ = db.execute(
                    "UPDATE artifact_feedback SET released=1,document=? WHERE id=?",
                    (feedback.model_dump_json(), feedback.event_id),
                )
                _ = db.execute(
                    "INSERT INTO artifact_delivery(event_id) VALUES (?)", (feedback.event_id,)
                )
        return self.workflow(artifact_id)

    def _legacy_feedback(self, artifact: ArtifactView, event: Feedback) -> ArtifactFeedback:
        """Preserve legacy event IDs and targets in the common review envelope."""
        target = ArtifactTarget(kind="slide", slide_id=event.slide_id) if event.slide_id else None
        if event.target:
            anchors = (
                event.target.elements
                if isinstance(event.target, ElementGroupTarget)
                else [event.target]
            )
            elements = [
                ArtifactTarget(
                    kind="element",
                    slide_id=event.slide_id,
                    selector=anchor.selector,
                    label=anchor.label,
                    quote=anchor.quote,
                    x=anchor.x,
                    y=anchor.y,
                    width=anchor.width,
                    height=anchor.height,
                )
                for anchor in anchors
            ]
            target = (
                ArtifactTarget(kind="group", label=event.target.label, elements=elements)
                if isinstance(event.target, ElementGroupTarget)
                else elements[0]
            )
        return ArtifactFeedback(
            event_id=event.event_id,
            artifact_id=artifact.id,
            project_id=artifact.project_id,
            revision=artifact.revision,
            content_hash=event.content_hash,
            author=event.author,
            message=event.message,
            created_at=event.created_at,
            target=target,
            edit_intent=event.edit_intent,
            reply_to=event.reply_to,
            released=event.released,
            delivery_id=event.delivery_id,
            batch_id=event.batch_id,
            files=event.files,
        )

    def feedback(
        self, artifact_id: str, *, include_pending: bool = False
    ) -> list[ArtifactFeedback]:
        """Read discussion; pending human drafts require an explicit human-only call path."""
        artifact = self.get_artifact(artifact_id)
        return self.feedback_for(artifact, include_pending=include_pending)

    def feedback_for(
        self,
        artifact: ArtifactView,
        *,
        include_pending: bool = False,
        after_delivery_id: int | None = None,
    ) -> list[ArtifactFeedback]:
        """Read discussion against a just-validated view without rescanning its source trees."""
        artifact_id = artifact.id
        if artifact.legacy_deck_id:
            return [
                self._legacy_feedback(artifact, event)
                for event in self.store.comments(artifact_id, after_delivery_id=after_delivery_id)
                if include_pending or event.released
            ]
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT artifact_feedback.document,artifact_delivery.id AS delivery_id "
                "FROM artifact_feedback LEFT JOIN artifact_delivery "
                "ON artifact_feedback.id=artifact_delivery.event_id "
                "WHERE artifact_id=? AND (? OR released=1) "
                + (
                    "AND artifact_delivery.id>? AND released=1 AND author='user' "
                    "ORDER BY artifact_delivery.id"
                    if after_delivery_id is not None
                    else "ORDER BY artifact_feedback.id"
                ),
                (artifact_id, include_pending, after_delivery_id)
                if after_delivery_id is not None
                else (artifact_id, include_pending),
            ).fetchall()
        events: list[ArtifactFeedback] = []
        for row in rows:
            event = ArtifactFeedback.model_validate_json(cast(str, row["document"]))
            event.delivery_id = cast(int | None, row["delivery_id"])
            event.stale = event.content_hash != artifact.content_hash
            events.append(event)
        return events

    def feedback_feed(self, artifact_id: str, after_delivery_id: int = 0) -> ArtifactFeedbackFeed:
        """Read released human feedback in delivery order, never exposing pending drafts."""
        artifact = self.get_artifact(artifact_id)
        events = self.feedback_for(artifact, after_delivery_id=after_delivery_id)
        return ArtifactFeedbackFeed(
            events=events,
            next_cursor=max(
                (event.delivery_id or 0 for event in events), default=after_delivery_id
            ),
            mode=self.workflow(artifact_id).mode,
        )

    def add_feedback(
        self,
        artifact_id: str,
        body: ArtifactFeedbackInput,
        *,
        author: Literal["user", "agent"] = "user",
    ) -> ArtifactFeedback:
        """Save a version-bound message exactly once, isolating draft replies and attachments."""
        artifact = self.get_artifact(artifact_id)
        if artifact.legacy_deck_id:
            raise ValueError("Use the existing presentation feedback endpoints for legacy decks.")
        decoded = decode_attachments(body.files)
        if not body.message.strip() and not decoded:
            raise ValueError("Add a message or attach a file.")
        payload = body.model_dump(exclude={"expected_mode"})
        payload_hash = digest(json.dumps(["feedback", author, payload], sort_keys=True))
        with self.store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            prior = self._operation(db, artifact_id, body.request_id, payload_hash)
            if prior is not None:
                return ArtifactFeedback.model_validate_json(prior)
            row = self._row(db, artifact_id)
            if row is None:
                raise FileNotFoundError("Artifact does not exist.")
            if body.expected_mode is not None and row["mode"] != body.expected_mode:
                raise ConflictError(
                    "Feedback mode changed. "
                    "Review the current Live or Batch setting before sending."
                )
            revision_row = db.execute(
                "SELECT document FROM artifact_revisions WHERE artifact_id=? "
                "AND json_extract(document,'$.content_hash')=? ORDER BY revision DESC LIMIT 1",
                (artifact_id, body.content_hash),
            ).fetchone()
            if revision_row is None:
                raise ConflictError("Feedback must reference an existing artifact snapshot.")
            reviewed = ArtifactManifest.model_validate_json(cast(str, revision_row[0]))
            self._verify_snapshot(reviewed)
            if body.reply_to is not None:
                parent = db.execute(
                    "SELECT released FROM artifact_feedback WHERE id=? AND artifact_id=?",
                    (body.reply_to, artifact_id),
                ).fetchone()
                if parent is None or (author == "agent" and not parent[0]):
                    raise ValueError("Reply must reference visible feedback on the same artifact.")
            released = author == "agent" or row["mode"] == "live"
            cursor = db.execute(
                "INSERT INTO artifact_feedback(artifact_id,document,released,author) "
                "VALUES (?,'{}',?,?)",
                (artifact_id, released, author),
            )
            event_id = cursor.lastrowid
            if event_id is None:
                raise RuntimeError("Feedback event allocation failed.")
            attachments: list[FeedbackFile] = []
            for index, (name, data) in enumerate(decoded):
                folder = self._owned(
                    self.store.root / ".state" / "artifact-feedback" / artifact_id / str(event_id)
                )
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / str(index)
                _ = path.write_bytes(data)
                attachments.append(
                    FeedbackFile(
                        name=name,
                        path=str(path),
                        size=len(data),
                        sha256=hashlib.sha256(data).hexdigest(),
                        download_url=f"/api/artifacts/{artifact_id}/feedback/{event_id}/files/{index}",
                    )
                )
            event = ArtifactFeedback(
                event_id=event_id,
                artifact_id=artifact_id,
                project_id=artifact.project_id,
                revision=reviewed.revision,
                content_hash=body.content_hash,
                author=author,
                message=body.message,
                created_at=datetime.now(UTC).isoformat(),
                target=body.target,
                edit_intent=body.edit_intent,
                reply_to=body.reply_to,
                released=released,
                files=attachments,
                stale=body.content_hash != artifact.content_hash,
            )
            if released and author == "user":
                delivery = db.execute(
                    "INSERT INTO artifact_delivery(event_id) VALUES (?)", (event_id,)
                )
                event.delivery_id = delivery.lastrowid
            _ = db.execute(
                "UPDATE artifact_feedback SET document=? WHERE id=?",
                (event.model_dump_json(), event_id),
            )
            _ = db.execute(
                "INSERT INTO artifact_operations VALUES (?,?,?,?)",
                (artifact_id, body.request_id, payload_hash, event.model_dump_json()),
            )
            return event

    def resolve_feedback_file(
        self, artifact_id: str, event_id: int, index: int, *, include_pending: bool = False
    ) -> tuple[Path, FeedbackFile]:
        """Resolve private attachment bytes only through their visible scoped feedback event."""
        event = next(
            (
                item
                for item in self.feedback(artifact_id, include_pending=include_pending)
                if item.event_id == event_id
            ),
            None,
        )
        if event is None or index < 0 or index >= len(event.files):
            raise FileNotFoundError("Feedback attachment does not exist.")
        metadata = event.files[index]
        path = self._owned(
            self.store.root
            / ".state"
            / "artifact-feedback"
            / self._id(artifact_id)
            / str(event_id)
            / str(index)
        )
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != metadata.sha256:
            raise ConflictError("Feedback attachment is unavailable or was modified.")
        return path, metadata
