"""Synchronize explicitly paired, owned source bundles with private GitHub branches."""

import asyncio
import base64
import fcntl
import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager, suppress
from pathlib import Path
from subprocess import Popen
from typing import Literal, cast

from pydantic import Field, TypeAdapter, model_validator

from .artifact_models import ArtifactFileInput
from .artifacts import MAX_BUNDLE_SIZE, MAX_FILE_SIZE, MAX_FILES, ArtifactStore, relative_path
from .libraries import snapshot_allowed
from .models import StrictModel
from .multiplayer_access import AccessDenied, Actor, Scope, Sharing
from .storage import SAFE_ID, ConflictError, digest

Commit = str
Choice = Literal["studio", "github"]
Action = Literal["unchanged", "upload", "download", "merge", "conflict", "blocked"]
PROJECT_SYNC_INTERVAL = 60
MAX_ARTIFACTS = 20
SYNC_SCRATCH = Path("/run/artifact-studio-project-sync")
SYNC_SCRATCH_BYTES = 512 * 1024 * 1024
SYNC_SCRATCH_INODES = 32768
MOUNT_INFO = Path("/proc/self/mountinfo")
COMMAND_TIMEOUT = 60
PROJECT_TIMEOUT = 300


def _verify_scratch(folder: Path) -> None:
    """Require a dedicated kernel-bounded mount before staging remote Git objects."""
    message = "GitHub sync needs its dedicated, size-limited staging mount."
    try:
        mount_lines = MOUNT_INFO.read_text().splitlines()
        details = folder.stat()
        capacity = os.statvfs(folder)
    except OSError as exc:
        raise ConflictError(message) from exc
    mounts: list[tuple[list[str], str]] = []
    for line in mount_lines:
        mount, _, filesystem = line.partition(" - ")
        fields = mount.split()
        if len(fields) >= 6 and fields[4] == str(folder):
            mounts.append((fields, filesystem.split(" ", 1)[0]))
    if (
        len(mounts) != 1
        or mounts[0][0][3] != "/"
        or mounts[0][1] != "tmpfs"
        or not {"rw", "noexec", "nodev", "nosuid"}.issubset(mounts[0][0][5].split(","))
        or folder.resolve() != folder
        or not stat.S_ISDIR(details.st_mode)
        or stat.S_IMODE(details.st_mode) != 0o700
        or details.st_uid != os.geteuid()
        or not 0 < capacity.f_frsize * capacity.f_blocks <= SYNC_SCRATCH_BYTES
        or not 0 < capacity.f_files <= SYNC_SCRATCH_INODES
    ):
        raise ConflictError(message)


def _file_path(value: str) -> str:
    """Exclude server state, credentials and GitHub control files from synced source."""
    path = Path(relative_path(value))
    if not snapshot_allowed(Path("projects/project/artifacts/artifact/files") / path):
        raise ValueError("Move private or unsupported files outside the synchronized bundle.")
    if path.name.casefold().endswith((".pem", ".key", ".p12", ".pfx")):
        raise ValueError("Move private key files outside the synchronized bundle.")
    if any(part.casefold().startswith(".") for part in path.parts):
        raise ValueError("Hidden files are not supported in synchronized bundles.")
    return value


class GitHubProject(StrictModel):
    """Operator-approved destinations, stored outside the workspace collection."""

    workspace: str
    project: str
    repository: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*/[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    branch: str = Field(default="main", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,120}$")
    artifacts: dict[str, str] = Field(min_length=1, max_length=MAX_ARTIFACTS)

    @model_validator(mode="after")
    def validate_paths(self) -> GitHubProject:
        """Keep all target folders distinct and below the repository root."""
        if not SAFE_ID.fullmatch(self.project):
            raise ValueError("Use a registered project ID.")
        prefixes: list[str] = []
        for artifact, path in self.artifacts.items():
            if not SAFE_ID.fullmatch(artifact):
                raise ValueError("Use a registered artifact ID.")
            prefixes.append(_file_path(path).casefold() + "/")
        if any(
            a.startswith(b)
            for i, a in enumerate(prefixes)
            for b in prefixes[:i] + prefixes[i + 1 :]
        ):
            raise ValueError("Each artifact needs a separate repository folder.")
        return self


class ProjectSyncSettings(StrictModel):
    """A workspace owner's opt-in to the operator-approved binding."""

    enabled: bool


class ProjectSyncResolution(StrictModel):
    """Resolve exactly one previously inspected conflict."""

    conflict: str = Field(pattern=r"^[a-f0-9]{64}$")
    choice: Choice


class SyncFileChange(StrictModel):
    """Describe how the two copies differ without exposing their contents."""

    path: str
    studio: Literal["present", "missing"]
    github: Literal["present", "missing"]
    conflict: bool


class SyncReport(StrictModel):
    """Report both sides of one bundle comparison without returning source contents."""

    artifact: str
    action: Action
    commit: str
    studio: str
    github: str | None
    conflict: str | None = None
    can_use_github: bool = False
    detail: str = ""
    title: str = ""
    studio_revision: int = 0
    baseline_commit: str = ""
    changed_files: list[str] = Field(default_factory=list)
    changes: list[SyncFileChange] = Field(default_factory=list)
    changed_count: int = 0


class Baseline(StrictModel):
    """A bundle known to match at a specific remote commit."""

    commit: str
    fingerprint: str
    files: dict[str, str] | None = None


class ProjectState(StrictModel):
    """Private checkpoint and last owner-visible result for one approved binding."""

    binding: str
    enabled: bool = False
    last_checked: float = 0
    reports: list[SyncReport] = Field(default_factory=list)
    baselines: dict[str, Baseline] = Field(default_factory=dict)
    error: str = ""


class ProjectSyncView(StrictModel):
    """Expose the configured destination and owner-visible progress."""

    repository: str
    branch: str
    artifacts: dict[str, str]
    enabled: bool
    last_checked: float
    reports: list[SyncReport]
    error: str


def _file_hashes(files: dict[str, bytes]) -> dict[str, str]:
    """Describe the exact source bytes without retaining another copy."""
    return {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}


def _fingerprint(files: dict[str, str]) -> str:
    """Compare an entire bundle so entrypoints and assets stay together."""
    return digest(json.dumps(sorted(files.items())))


def _merge_independent(
    local: dict[str, bytes],
    remote: dict[str, bytes],
    local_files: dict[str, str],
    remote_files: dict[str, str],
    baseline: dict[str, str],
) -> dict[str, bytes] | None:
    """Merge disjoint edits while preserving deletions and rejecting shared-file conflicts."""
    merged: dict[str, bytes] = {}
    for name in sorted(local.keys() | remote.keys()):
        left, right, previous = local_files.get(name), remote_files.get(name), baseline.get(name)
        if left == right or right == previous:
            if name in local:
                merged[name] = local[name]
        elif left == previous:
            if name in remote:
                merged[name] = remote[name]
        else:
            return None
    try:
        _checked_bundle(merged)
    except ValueError:
        return None
    return merged


def _checked_bundle(files: dict[str, bytes]) -> None:
    """Apply source limits and reject path collisions across operating systems."""
    if (
        len(files) > MAX_FILES
        or any(len(data) > MAX_FILE_SIZE for data in files.values())
        or sum(map(len, files.values())) > MAX_BUNDLE_SIZE
    ):
        raise ValueError("Synchronized source exceeds the artifact bundle limits.")
    for name in files:
        _ = _file_path(name)
    paths = {name.casefold() for name in files}
    if len(paths) != len(files) or any(
        parent.as_posix() in paths for name in paths for parent in Path(name).parents
    ):
        raise ValueError("Synchronized source contains conflicting file paths.")


class GitTree:
    """Read and publish bounded Git objects in a disposable repository without checking out code."""

    def __init__(self, folder: Path, binding: GitHubProject) -> None:
        """Fetch the exact approved branch after verifying private repository status."""
        self.folder = folder
        self.deadline = time.monotonic() + PROJECT_TIMEOUT
        self.ref = f"refs/heads/{binding.branch}"
        try:
            private = self._command(
                (
                    "gh",
                    "repo",
                    "view",
                    binding.repository,
                    "--json",
                    "isPrivate",
                    "--jq",
                    ".isPrivate",
                )
            )
        except subprocess.CalledProcessError as exc:
            raise ConflictError("GitHub sync could not verify the private repository.") from exc
        if private.strip() != b"true":
            raise ValueError("Project sync requires a private GitHub repository.")
        self.remote = f"https://github.com/{binding.repository}.git"
        _ = self.run("init", "--bare", "--template=")
        _ = self.run("check-ref-format", self.ref)
        _ = self.run("remote", "add", "origin", self.remote)
        _ = self.run("fetch", "--depth=1", "--no-tags", "origin", self.ref)
        self.tip = self.run("rev-parse", "FETCH_HEAD").decode().strip()

    def run(self, *args: str, data: bytes | None = None) -> bytes:
        """Run Git without a working tree, user configuration, or executable hooks."""
        command = (
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "protocol.file.allow=never",
            "-c",
            "submodule.recurse=false",
            "-c",
            "credential.helper=",
            "-c",
            "credential.helper=!gh auth git-credential",
            "-c",
            "commit.gpgsign=false",
            *args,
        )
        try:
            return self._command(command, data=data)
        except subprocess.CalledProcessError as exc:
            if args[0] == "push":
                raise ConflictError(
                    "GitHub changed or rejected the push. Check sync again."
                ) from exc
            raise ConflictError(
                "GitHub sync could not read the branch within the staging limits."
            ) from exc

    def _command(self, command: tuple[str, ...], *, data: bytes | None = None) -> bytes:
        """Keep writable state and command output under the same filesystem limit."""
        timeout = min(COMMAND_TIMEOUT, self.deadline - time.monotonic())
        if timeout <= 0:
            raise ConflictError("GitHub sync timed out. Check sync again.")
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_") or key in {"GIT_SSL_CAINFO", "GIT_SSL_CAPATH"}
        }
        env.update(
            TMPDIR=str(self.folder),
            GIT_TERMINAL_PROMPT="0",
            GIT_ALLOW_PROTOCOL="https",
            GH_PROMPT_DISABLED="1",
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_AUTHOR_NAME="Artifact Studio",
            GIT_AUTHOR_EMAIL="artifact-studio@users.noreply.github.com",
            GIT_COMMITTER_NAME="Artifact Studio",
            GIT_COMMITTER_EMAIL="artifact-studio@users.noreply.github.com",
        )
        with (
            tempfile.TemporaryFile(dir=self.folder) as output,
            Popen(
                command,
                cwd=self.folder,
                stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            ) as process,
        ):
            try:
                _ = process.communicate(input=data, timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                _ = process.wait()
                raise ConflictError("GitHub sync timed out. Check sync again.") from exc
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, command)
            _ = output.seek(0)
            result = output.read(MAX_FILE_SIZE + 1)
            if len(result) > MAX_FILE_SIZE:
                raise ConflictError("GitHub sync command output exceeded its limit.")
            return result

    def read(self, prefix: str) -> dict[str, bytes]:
        """Read a literal subtree, validating metadata before loading blob data."""
        folder = self.run("ls-tree", "-z", self.tip, "--", f":(literal){prefix}")
        if folder and not folder.startswith(b"040000 tree "):
            raise ValueError("The GitHub destination must be a source folder.")
        raw = self.run("ls-tree", "-r", "-z", "-l", self.tip, "--", f":(literal){prefix}/")
        blobs: list[tuple[str, str, int]] = []
        total = 0
        for entry in raw.split(b"\0")[:-1]:
            metadata, name = entry.split(b"\t", 1)
            mode, kind, oid, size = metadata.decode("ascii").split()
            if mode not in {"100644", "100755"} or kind != "blob":
                raise ValueError("Symlinks and submodules cannot be synchronized.")
            path = name.decode("utf-8").removeprefix(prefix + "/")
            _ = _file_path(path)
            length = int(size)
            total += length
            blobs.append((path, oid, length))
            if length > MAX_FILE_SIZE or total > MAX_BUNDLE_SIZE or len(blobs) > MAX_FILES:
                raise ValueError("GitHub source exceeds the artifact bundle limits.")
        files: dict[str, bytes] = {}
        for name, oid, length in blobs:
            data = self.run("cat-file", "blob", oid)
            if len(data) != length:
                raise ValueError("GitHub returned an invalid source object.")
            files[name] = data
        _checked_bundle(files)
        return files

    def publish(self, prefix: str, previous: dict[str, bytes], source: dict[str, bytes]) -> Commit:
        """Publish a direct child of the exact fetched tip while preserving all unrelated paths."""
        _ = self.run("read-tree", self.tip)
        index: list[bytes] = []
        for path in previous.keys() - source.keys():
            index.append(f"0 {'0' * len(self.tip)}\t{prefix}/{path}\0".encode())
        for path, data in source.items():
            if previous.get(path) == data:
                continue
            oid = self.run("hash-object", "-w", "--stdin", data=data).decode().strip()
            index.append(f"100644 {oid}\t{prefix}/{path}\0".encode())
        _ = self.run("update-index", "-z", "--index-info", data=b"".join(index))
        tree = self.run("write-tree").decode().strip()
        commit = (
            self.run(
                "commit-tree",
                tree,
                "-p",
                self.tip,
                "-m",
                "chore(project-sync): synchronize Artifact Studio sources",
            )
            .decode()
            .strip()
        )
        parent = self.run("rev-parse", f"{commit}^").decode().strip()
        if parent != self.tip:
            raise ConflictError("GitHub sync could not verify the prepared commit's parent.")
        _ = self.run(
            "push", f"--force-with-lease={self.ref}:{self.tip}", "origin", f"{commit}:{self.ref}"
        )
        self.tip = commit
        return commit


class ProjectGitHubSync:
    """Run owner-configured synchronization without exporting review data or workspace state."""

    def __init__(
        self, sharing: Sharing, bindings: list[GitHubProject], *, scratch: Path = SYNC_SCRATCH
    ) -> None:
        """Reject overlapping external paths before installing durable project checkpoints."""
        self.sharing = sharing
        self.scratch = scratch
        self.bindings: dict[tuple[str, str], GitHubProject] = {}
        used: dict[tuple[str, str], list[str]] = {}
        for binding in bindings:
            key = (binding.workspace, binding.project)
            if key in self.bindings:
                raise ValueError("Each project needs exactly one GitHub destination.")
            paths = used.setdefault((binding.repository.casefold(), binding.branch), [])
            for value in binding.artifacts.values():
                path = value.casefold() + "/"
                if any(path.startswith(other) or other.startswith(path) for other in paths):
                    raise ValueError("GitHub sync destinations must not overlap.")
                paths.append(path)
            self.bindings[key] = binding
        if bindings:
            with sharing.connect() as db:
                _ = db.execute(
                    "CREATE TABLE IF NOT EXISTS project_github_sync "
                    "(workspace TEXT, project TEXT, document TEXT NOT NULL, "
                    "PRIMARY KEY(workspace,project))"
                )

    @classmethod
    def from_env(cls, sharing: Sharing) -> ProjectGitHubSync:
        """Load explicit per-artifact destinations from the private operator file."""
        config = os.environ.get("ARTIFACT_STUDIO_PROJECT_GITHUB_SYNC", "")
        if not config:
            return cls(sharing, [])
        path = Path(config).resolve(strict=True)
        if sharing.contains_collection_path(path):
            raise ValueError("Keep GitHub sync bindings outside the collection.")
        return cls(sharing, TypeAdapter(list[GitHubProject]).validate_json(path.read_bytes()))

    def _binding(self, actor: Actor, scope: Scope) -> GitHubProject:
        """Disclose a binding only to the owning human."""
        binding = self.bindings.get((scope.workspace, scope.resource))
        if binding is None or scope.kind != "project":
            raise AccessDenied
        if not self.sharing.owner(actor, scope.workspace):
            raise AccessDenied
        self.sharing.manage(actor, scope)
        return binding

    def _load(self, binding: GitHubProject) -> ProjectState:
        """Refuse to reuse checkpoints after the operator changes the pairing."""
        fingerprint = digest(json.dumps(binding.model_dump(), sort_keys=True))
        with self.sharing.connect() as db:
            row = db.execute(
                "SELECT document FROM project_github_sync WHERE workspace=? AND project=?",
                (binding.workspace, binding.project),
            ).fetchone()
        if row:
            state = ProjectState.model_validate_json(cast(str, row[0]))
            if state.binding == fingerprint:
                return state
        return ProjectState(binding=fingerprint)

    def _save(self, binding: GitHubProject, state: ProjectState) -> None:
        """Commit the last result and confirmed per-artifact baselines together."""
        with self.sharing.connect() as db:
            _ = db.execute(
                "INSERT OR REPLACE INTO project_github_sync VALUES (?,?,?)",
                (binding.workspace, binding.project, state.model_dump_json()),
            )

    @contextmanager
    def _locked(self, binding: GitHubProject) -> Generator[None]:
        """Serialize one project across threads and processes without locking unrelated edits."""
        key = digest(f"{binding.workspace}/{binding.project}")
        with (self.sharing.root / f"github-project-{key}.lock").open("a+b") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ConflictError("GitHub sync is already running for this project.") from exc
            yield

    def status(self, actor: Actor, scope: Scope) -> ProjectSyncView:
        """Read the last result without fetching or changing any source."""
        binding = self._binding(actor, scope)
        return self._view(binding, self._load(binding))

    def projects(self, actor: Actor, workspace: str) -> list[str]:
        """List bound project IDs only in a workspace the actor owns."""
        if not self.bindings or not self.sharing.owner(actor, workspace):
            return []
        return [
            project for owner_workspace, project in self.bindings if owner_workspace == workspace
        ]

    @staticmethod
    def _view(binding: GitHubProject, state: ProjectState) -> ProjectSyncView:
        """Describe the destination without exposing private checkpoint metadata."""
        return ProjectSyncView(
            repository=binding.repository,
            branch=binding.branch,
            artifacts=binding.artifacts,
            enabled=state.enabled,
            last_checked=state.last_checked,
            reports=state.reports,
            error=state.error,
        )

    @contextmanager
    def _staging_slot(self) -> Generator[Path]:
        """Share one staging slot across projects and worker processes."""
        _verify_scratch(self.scratch)
        try:
            with (self.scratch / ".sync.lock").open("a+b") as lock:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise ConflictError(
                        "Another project is syncing. Check sync again shortly."
                    ) from exc
                for leftover in self.scratch.glob("studio-github-*"):
                    if leftover.is_dir() and not leftover.is_symlink():
                        shutil.rmtree(leftover)
                with tempfile.TemporaryDirectory(prefix="studio-github-", dir=self.scratch) as raw:
                    yield Path(raw)
        except OSError as exc:
            raise ConflictError("GitHub sync could not use its staging mount.") from exc

    def configure(self, actor: Actor, scope: Scope, enabled: bool) -> ProjectSyncView:
        """Allow the project owner to start and pause the synchronization worker."""
        binding = self._binding(actor, scope)
        if enabled:
            _verify_scratch(self.scratch)
        with self._locked(binding):
            state = self._load(binding)
            state.enabled = enabled
            self._save(binding, state)
            return self._view(binding, state)

    def _compare(
        self,
        artifact: str,
        local: str,
        remote: str | None,
        commit: str,
        baseline: Baseline | None,
        *,
        complete: bool,
    ) -> SyncReport:
        """Classify independent changes; preserve missing or incompatible source for review."""
        action: Action = "conflict"
        detail = ""
        if local == remote:
            action = "unchanged"
        elif not complete:
            if baseline is None and remote is None:
                action = "upload"
            else:
                detail = (
                    "The GitHub bundle or its entrypoint was removed. Studio source is retained."
                )
        elif baseline and remote == baseline.fingerprint:
            action = "upload"
        elif baseline and local == baseline.fingerprint:
            action = "download"
        else:
            detail = "The Studio and GitHub bundles differ. Choose which source to retain."
        return SyncReport(
            artifact=artifact,
            action=action,
            studio=local,
            github=remote,
            commit=commit,
            can_use_github=complete,
            detail=detail,
        )

    def _sync_artifact(
        self,
        actor: Actor,
        binding: GitHubProject,
        state: ProjectState,
        tree: GitTree,
        artifact: str,
        resolution: ProjectSyncResolution | None,
    ) -> SyncReport:
        """Recheck both snapshots before publishing and record only confirmed matches."""
        scope = Scope(binding.workspace, "artifact", artifact)
        with self.sharing.guard(actor, scope, "edit") as store:
            service = ArtifactStore(store)
            item, local = service.verified_snapshot(artifact)
            if item.project_id != binding.project:
                raise AccessDenied
        _checked_bundle(local)
        prefix = binding.artifacts[artifact]
        remote = tree.read(prefix)
        local_files, remote_files = _file_hashes(local), _file_hashes(remote)
        local_hash = _fingerprint(local_files)
        remote_hash = _fingerprint(remote_files) if remote else None
        complete = item.entrypoint in remote
        baseline = state.baselines.get(artifact)
        report = self._compare(
            artifact, local_hash, remote_hash, tree.tip, baseline, complete=complete
        )
        report.title = item.title
        report.studio_revision = item.revision
        merged = None
        if (
            report.action == "conflict"
            and not resolution
            and complete
            and baseline
            and baseline.files
        ):
            merged = _merge_independent(local, remote, local_files, remote_files, baseline.files)
            if merged is not None and item.entrypoint in merged:
                report.action = "merge"
        if report.action == "conflict":
            changed = sorted(
                name for name in local.keys() | remote.keys() if local.get(name) != remote.get(name)
            )
            report.changed_files = changed[:50]
            base = baseline.files if baseline else None
            report.changes = [
                SyncFileChange(
                    path=name,
                    studio="present" if name in local else "missing",
                    github="present" if name in remote else "missing",
                    conflict=base is None
                    or (
                        local_files.get(name) != base.get(name)
                        and remote_files.get(name) != base.get(name)
                    ),
                )
                for name in report.changed_files
            ]
            report.changed_count = len(changed)
            report.baseline_commit = baseline.commit if baseline else ""
            report.conflict = digest(
                json.dumps(
                    [
                        state.binding,
                        artifact,
                        item.revision,
                        item.content_hash,
                        local_hash,
                        remote_hash,
                        baseline.model_dump() if baseline else None,
                    ]
                )
            )
        if resolution:
            previous = next((item for item in state.reports if item.artifact == artifact), None)
            if (
                previous is None
                or previous.conflict != resolution.conflict
                or resolution.conflict != report.conflict
            ):
                raise ConflictError("This conflict changed. Check sync again before resolving it.")
            if resolution.choice == "github" and not complete:
                raise ConflictError(
                    "GitHub needs a complete bundle with its entrypoint before it can be used."
                )
            report.action = "upload" if resolution.choice == "studio" else "download"
        with self.sharing.guard(actor, scope, "edit") as store:
            service = ArtifactStore(store)
            latest = service.get_artifact(artifact)
            if (
                latest.project_id != binding.project
                or latest.revision != item.revision
                or latest.content_hash != item.content_hash
            ):
                raise ConflictError("Studio changed during sync. Check sync again.")
            if report.action == "download":
                self._apply_source(service, artifact, local, remote, item.revision, tree.tip)
        if report.action == "conflict":
            return report
        agreed = remote if report.action == "download" else local
        if report.action == "merge":
            assert merged is not None
            agreed = merged
        if agreed != remote:
            report.commit = tree.publish(prefix, remote, agreed)
        if report.action == "merge" and agreed != local:
            with self.sharing.guard(actor, scope, "edit") as store:
                service = ArtifactStore(store)
                latest = service.get_artifact(artifact)
                if latest.project_id != binding.project:
                    raise AccessDenied
                self._apply_source(service, artifact, local, agreed, item.revision, report.commit)
        files = _file_hashes(agreed)
        state.baselines[artifact] = Baseline(
            commit=report.commit, fingerprint=_fingerprint(files), files=files
        )
        report.conflict = None
        report.detail = ""
        return report

    def _apply_source(
        self,
        service: ArtifactStore,
        artifact: str,
        local: dict[str, bytes],
        source: dict[str, bytes],
        revision: int,
        commit: str,
    ) -> None:
        """Apply a verified bundle as a new Studio revision if the source stayed current."""
        files = [
            ArtifactFileInput(path=name, data_base64=base64.b64encode(data).decode())
            for name, data in source.items()
            if local.get(name) != data
        ]
        files.extend(
            ArtifactFileInput(path=name, delete=True)
            for name in sorted(local.keys() - source.keys())
        )
        service.revise_artifact(
            artifact,
            files,
            revision,
            "github-sync-" + digest(f"{commit}:{revision}:{_fingerprint(_file_hashes(source))}"),
        )

    def run(
        self,
        actor: Actor,
        scope: Scope,
        artifact: str = "",
        resolution: ProjectSyncResolution | None = None,
        *,
        automatic: bool = False,
    ) -> ProjectSyncView:
        """Synchronize the selected project or resolve one recorded conflict."""
        binding = self._binding(actor, scope)
        if artifact and artifact not in binding.artifacts:
            raise AccessDenied
        with self._locked(binding):
            state = self._load(binding)
            if automatic and (
                not state.enabled or time.time() - state.last_checked < PROJECT_SYNC_INTERVAL
            ):
                return self._view(binding, state)
            with self._staging_slot() as folder:
                state.last_checked = time.time()
                state.error = ""
                self._save(binding, state)
                try:
                    tree = GitTree(folder, binding)
                    for identifier in [artifact] if artifact else binding.artifacts:
                        try:
                            result = self._sync_artifact(
                                actor, binding, state, tree, identifier, resolution
                            )
                        except AccessDenied, OSError, ValueError, ConflictError:
                            if resolution is not None:
                                raise
                            result = SyncReport(
                                artifact=identifier,
                                action="blocked",
                                studio="",
                                github=None,
                                commit=tree.tip,
                                detail="Check that this source is still paired with this project "
                                "and that both copies contain valid files. Run sync again.",
                            )
                        state.reports = [
                            item for item in state.reports if item.artifact != identifier
                        ]
                        state.reports.append(result)
                        self._save(binding, state)
                except OSError, ValueError, ConflictError, subprocess.SubprocessError:
                    state.error = "GitHub sync stopped. Check the binding, repository and versions."
                    self._save(binding, state)
                    raise
            return self._view(binding, state)

    @asynccontextmanager
    async def worker(self) -> AsyncGenerator[None]:
        """Start polling only on deployments with an operator binding file."""
        if not self.bindings:
            yield
            return

        async def poll() -> None:
            while True:
                await asyncio.sleep(PROJECT_SYNC_INTERVAL)
                await asyncio.to_thread(self.tick)

        task = asyncio.create_task(poll())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def tick(self) -> None:
        """Poll opted-in bindings with the current workspace owner, one project at a time."""
        for binding in self.bindings.values():
            state = self._load(binding)
            if not state.enabled or time.time() - state.last_checked < PROJECT_SYNC_INTERVAL:
                continue
            with self.sharing.connect() as db:
                row = db.execute(
                    "SELECT owner FROM workspaces WHERE id=?", (binding.workspace,)
                ).fetchone()
            if row:
                try:
                    self.run(
                        Actor(cast(str, row[0])),
                        Scope(binding.workspace, "project", binding.project),
                        automatic=True,
                    )
                except AccessDenied, OSError, ValueError, ConflictError, subprocess.SubprocessError:
                    continue
