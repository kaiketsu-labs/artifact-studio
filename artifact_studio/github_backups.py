"""Explicit, scheduled full-workspace archives in a private GitHub backup release."""

import asyncio
import fcntl
import hashlib
import json
import logging
import os
import re
import selectors
import sqlite3
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from urllib.parse import quote

from pydantic import Field

from .libraries import Libraries
from .models import StrictModel
from .storage import Store
from .workspace_recovery import CHUNK_BYTES, WorkspaceRecovery

RELEASE_TAG = "artifact-studio-workspace-backups"
RELEASE_MARKER = "Artifact Studio managed workspace backups."
RELEASE_BODY = (
    RELEASE_MARKER
    + "\n\nPrivate full-workspace recovery archives, including review history and discussions. "
    "This is a backup container, not an application release. "
    "Only archives from the same workspace series are subject to its retention setting."
)
MAX_ARCHIVE_BYTES = 2_000_000_000
MAX_ASSETS = 1000
POLL_SECONDS = 5
TRANSFER_TIMEOUT = 3600
METADATA_TIMEOUT = 180
ARCHIVE_NAME = re.compile(
    r"workspace-([a-f0-9]{32})-(\d{8}T\d{6}Z)-([a-f0-9]{32})-([a-f0-9]{64})"
    r"\.artifact-studio\.zip"
)
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*")


class FullBackupSettings(StrictModel):
    """Upload permission is separate from current-source automatic synchronization."""

    enabled: bool = False
    interval_hours: int = Field(default=24, ge=1, le=720, strict=True)
    keep_count: int = Field(default=7, ge=1, le=100, strict=True)


class BackupStatus(StrictModel):
    """A durable schedule and the last confirmed upload, suitable for local UI display."""

    library_id: str
    settings: FullBackupSettings = Field(default_factory=FullBackupSettings)
    phase: Literal["disabled", "scheduled", "queued", "backing_up", "backed_up", "error"] = (
        "disabled"
    )
    last_backup_at: str | None = None
    next_backup_at: str | None = None
    error: str = ""
    warning: str = ""
    archive_name: str = ""
    url: str = ""


class RemoteBackup(StrictModel):
    """One strictly named archive belonging to the verified managed backup release."""

    asset_id: int
    name: str
    created_at: str
    size: int
    workspace_id: str
    url: str


class _Checkpoint(StrictModel):
    status: BackupStatus
    due_at: float | None = None
    failures: int = 0
    revision: int = 0


class GitHubCommandError(ValueError):
    """A bounded CLI failure retaining only the status needed for release creation."""

    def __init__(self, message: str, *, not_found: bool = False) -> None:
        super().__init__(message)
        self.not_found = not_found


def gh(
    *args: str,
    timeout: float = METADATA_TIMEOUT,
    output: Path | None = None,
    maximum_bytes: int | None = None,
) -> str:
    """Stream authenticated CLI output with hard size, time and diagnostic bounds."""
    limit = maximum_bytes if maximum_bytes is not None else 16_000_000
    diagnostic = bytearray()
    result = bytearray()
    size = 0
    created = False
    try:
        with output.open("xb") if output is not None else tempfile.TemporaryFile() as destination:
            created = output is not None
            try:
                process = subprocess.Popen(
                    ["gh", *args],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env={
                        **os.environ,
                        "GH_HOST": "github.com",
                        "GH_PROMPT_DISABLED": "1",
                        "GIT_TERMINAL_PROMPT": "0",
                    },
                )
            except FileNotFoundError as error:
                raise ValueError("Install GitHub CLI and sign in with gh auth login.") from error
            assert process.stdout is not None and process.stderr is not None
            started = time.monotonic()
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                    while selector.get_map():
                        remaining = timeout - (time.monotonic() - started)
                        if remaining <= 0:
                            raise ValueError(
                                "GitHub operation timed out. Retry to check its state."
                            )
                        for key, _ in selector.select(timeout=min(remaining, 0.25)):
                            chunk = os.read(key.fd, CHUNK_BYTES)
                            if not chunk:
                                _ = selector.unregister(key.fileobj)
                            elif key.data == "stderr":
                                diagnostic.extend(chunk)
                                del diagnostic[:-1200]
                            else:
                                size += len(chunk)
                                if size > limit:
                                    raise ValueError("GitHub response exceeds the permitted size.")
                                if output is None:
                                    result.extend(chunk)
                                else:
                                    _ = destination.write(chunk)
                code = process.wait(timeout=max(0.01, timeout - (time.monotonic() - started)))
                if code:
                    detail = diagnostic.decode("utf-8", errors="replace").strip()
                    raise GitHubCommandError(
                        f"GitHub operation failed: {detail}",
                        not_found="HTTP 404" in detail or "(404)" in detail,
                    )
            except subprocess.TimeoutExpired as error:
                process.kill()
                _ = process.wait()
                raise ValueError("GitHub operation timed out. Retry to check its state.") from error
            except BaseException:
                process.kill()
                _ = process.wait()
                raise
            finally:
                process.stdout.close()
                process.stderr.close()
    except BaseException:
        if output is not None and created:
            output.unlink(missing_ok=True)
        raise
    return result.decode("utf-8").strip()


def _json(*args: str) -> object:
    try:
        return cast(object, json.loads(gh(*args)))
    except json.JSONDecodeError as error:
        raise ValueError("GitHub returned invalid backup metadata.") from error


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("GitHub returned invalid backup metadata.")
    return cast(dict[str, object], value)


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("GitHub returned invalid backup asset metadata.")
    return value


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


class GitHubBackups:
    """Run resumable local schedules with one full-backup worker across processes."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.libraries = Libraries(store)
        self.database = self.libraries.cache / "workspace-backups.sqlite3"
        with self._connect() as db:
            _ = db.execute(
                "CREATE TABLE IF NOT EXISTS github_backup_settings "
                "(id TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            _ = db.execute(
                "CREATE TABLE IF NOT EXISTS github_backup_state "
                "(id TEXT PRIMARY KEY, document TEXT NOT NULL)"
            )
            _ = db.execute(
                "INSERT OR IGNORE INTO github_backup_settings VALUES ('workspace_id', ?)",
                (uuid.uuid4().hex,),
            )
            row = db.execute(
                "SELECT value FROM github_backup_settings WHERE id='workspace_id'"
            ).fetchone()
            assert row is not None
            self.workspace_id = str(row[0])
        if re.fullmatch(r"[a-f0-9]{32}", self.workspace_id) is None:
            raise ValueError("Invalid local backup workspace identity.")

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        """Keep revocation responsive while recovery locks the private workspace database."""
        database = sqlite3.connect(self.database, timeout=30)
        try:
            with database:
                yield database
        finally:
            database.close()

    def _record(self, identifier: str) -> _Checkpoint:
        with self._connect() as db:
            row = db.execute(
                "SELECT document FROM github_backup_state WHERE id=?", (identifier,)
            ).fetchone()
        return (
            _Checkpoint.model_validate_json(str(row[0]))
            if row
            else _Checkpoint(status=BackupStatus(library_id=identifier))
        )

    def _change(self, identifier: str, change: Callable[[_Checkpoint], None]) -> _Checkpoint:
        """Merge worker results with settings edited while an archive was exporting."""
        with self._connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT document FROM github_backup_state WHERE id=?", (identifier,)
            ).fetchone()
            record = (
                _Checkpoint.model_validate_json(str(row[0]))
                if row
                else _Checkpoint(status=BackupStatus(library_id=identifier))
            )
            change(record)
            record.status.next_backup_at = (
                _iso(record.due_at) if record.due_at is not None else None
            )
            _ = db.execute(
                "INSERT OR REPLACE INTO github_backup_state VALUES (?, ?)",
                (identifier, record.model_dump_json()),
            )
        return record

    def _repository(self, identifier: str) -> str:
        library = self.libraries.get(identifier)
        if library.kind != "github" or REPOSITORY.fullmatch(library.location) is None:
            raise ValueError("Choose a connected private GitHub repository for full backups.")
        return library.location

    def _private(self, repository: str) -> dict[str, object]:
        metadata = _object(
            _json("repo", "view", repository, "--json", "isPrivate,defaultBranchRef")
        )
        if metadata.get("isPrivate") is not True:
            raise ValueError("Full workspace backups require a private GitHub repository.")
        return metadata

    def state(self, identifier: str) -> BackupStatus:
        """Read local status without performing network requests or granting permission."""
        _ = self._repository(identifier)
        return self._record(identifier).status

    def statuses(self) -> list[BackupStatus]:
        return [
            self._record(library.id).status
            for library in self.libraries.list()
            if library.kind == "github"
        ]

    def configure(self, library_id: str, settings: FullBackupSettings) -> BackupStatus:
        """Persist explicit opt-in; initial uploads and overdue schedules queue immediately."""
        _ = self._repository(library_id)
        now = time.time()

        def update(record: _Checkpoint) -> None:
            was_enabled = record.status.settings.enabled
            old_interval = record.status.settings.interval_hours
            record.status.settings = settings.model_copy(deep=True)
            record.revision += 1
            record.status.error = ""
            record.failures = 0
            if not settings.enabled:
                record.status.phase = "disabled"
                record.due_at = None
            elif not was_enabled or record.status.last_backup_at is None:
                record.status.phase = "queued"
                record.due_at = now
            elif old_interval != settings.interval_hours or record.due_at is None:
                previous = datetime.fromisoformat(record.status.last_backup_at).timestamp()
                record.due_at = previous + settings.interval_hours * 3600
                record.status.phase = "scheduled" if record.due_at > now else "queued"
            elif record.status.phase == "error":
                record.status.phase = "queued"
                record.due_at = now

        return self._change(library_id, update).status

    def queue(self, library_id: str) -> BackupStatus:
        _ = self._repository(library_id)

        def update(record: _Checkpoint) -> None:
            if not record.status.settings.enabled:
                raise ValueError("Enable full workspace backups for this repository first.")
            record.revision += 1
            record.due_at = time.time()
            record.status.phase = "queued"
            record.status.error = ""
            record.failures = 0

        return self._change(library_id, update).status

    def _backup_tag(self, repository: str, commit: str) -> None:
        """Create a marked tag conditionally, without attaching to an existing user tag."""
        endpoint = f"repos/{repository}/git/ref/tags/{RELEASE_TAG}"
        try:
            reference = _object(_json("api", endpoint))
        except GitHubCommandError as error:
            if not error.not_found:
                raise
            tag = _object(
                _json(
                    "api",
                    "--method",
                    "POST",
                    f"repos/{repository}/git/tags",
                    "-f",
                    f"tag={RELEASE_TAG}",
                    "-f",
                    f"message={RELEASE_MARKER}",
                    "-f",
                    f"object={commit}",
                    "-f",
                    "type=commit",
                )
            )
            sha = tag.get("sha")
            if not isinstance(sha, str) or re.fullmatch(r"[a-fA-F0-9]{40,64}", sha) is None:
                raise ValueError("GitHub returned an invalid backup tag.") from error
            try:
                _ = _json(
                    "api",
                    "--method",
                    "POST",
                    f"repos/{repository}/git/refs",
                    "-f",
                    f"ref=refs/tags/{RELEASE_TAG}",
                    "-f",
                    f"sha={sha}",
                )
                return
            except GitHubCommandError:
                # Conditional ref creation refuses a user-created tag that won the race.
                reference = _object(_json("api", endpoint))
        target = _object(reference.get("object"))
        sha = target.get("sha")
        if (
            target.get("type") != "tag"
            or not isinstance(sha, str)
            or re.fullmatch(r"[a-fA-F0-9]{40,64}", sha) is None
        ):
            raise ValueError("The backup tag already belongs to another Git tag.")
        tag = _object(_json("api", f"repos/{repository}/git/tags/{sha}"))
        if tag.get("tag") != RELEASE_TAG or tag.get("message") != RELEASE_MARKER:
            raise ValueError("The backup tag already belongs to another Git tag.")

    def _release(self, repository: str, *, create: bool = False) -> dict[str, object] | None:
        endpoint = f"repos/{repository}/releases/tags/{RELEASE_TAG}"
        try:
            release = _object(_json("api", endpoint))
        except GitHubCommandError as error:
            if not error.not_found:
                raise
            if not create:
                return None
            metadata = self._private(repository)
            branch = _object(metadata.get("defaultBranchRef"))
            name = branch.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError(
                    "The backup repository needs a default branch with a commit."
                ) from error
            commit = _object(_json("api", f"repos/{repository}/commits/{quote(name, safe='')}"))
            sha = commit.get("sha")
            if not isinstance(sha, str) or re.fullmatch(r"[a-fA-F0-9]{40,64}", sha) is None:
                raise ValueError("GitHub returned an invalid default branch commit.") from error
            self._backup_tag(repository, sha)
            try:
                release = _object(
                    _json(
                        "api",
                        "--method",
                        "POST",
                        f"repos/{repository}/releases",
                        "-f",
                        f"tag_name={RELEASE_TAG}",
                        "-f",
                        f"target_commitish={sha}",
                        "-f",
                        "name=Artifact Studio workspace backups",
                        "-f",
                        f"body={RELEASE_BODY}",
                        "-F",
                        "prerelease=true",
                        "-f",
                        "make_latest=false",
                    )
                )
            except GitHubCommandError:
                # Another Studio may have created the shared container meanwhile.
                release = _object(_json("api", endpoint))
        body = release.get("body", "")
        if (
            release.get("tag_name") != RELEASE_TAG
            or not isinstance(body, str)
            or not (body == RELEASE_MARKER or body.startswith(RELEASE_MARKER + "\n"))
        ):
            raise ValueError("The backup release tag already belongs to another release.")
        if release.get("draft") is True:
            raise ValueError("The managed backup release cannot be a draft.")
        _ = _integer(release.get("id"))
        return release

    def _assets(
        self, repository: str, release: dict[str, object], verified: set[int] | None = None
    ) -> list[RemoteBackup]:
        result: list[RemoteBackup] = []
        count = 0
        identifier = _integer(release.get("id"))
        for page in range(1, MAX_ASSETS // 100 + 2):
            data = _json(
                "api", f"repos/{repository}/releases/{identifier}/assets?per_page=100&page={page}"
            )
            if not isinstance(data, list):
                raise ValueError("GitHub returned invalid release assets.")
            rows = cast(list[object], data)
            count += len(rows)
            if count > MAX_ASSETS:
                raise ValueError("The managed backup release has more than 1000 assets.")
            for row in rows:
                asset = _object(row)
                name = asset.get("name")
                match = ARCHIVE_NAME.fullmatch(name) if isinstance(name, str) else None
                if match is None or asset.get("state", "uploaded") != "uploaded":
                    continue
                digest = asset.get("digest")
                if digest is not None and digest != "sha256:" + match[4]:
                    raise ValueError("A managed backup asset checksum does not match its filename.")
                if digest is not None and verified is not None:
                    verified.add(_integer(asset.get("id")))
                size = _integer(asset.get("size"))
                if size > MAX_ARCHIVE_BYTES:
                    continue
                created_at = asset.get("created_at")
                if not isinstance(created_at, str):
                    raise ValueError("GitHub returned an invalid backup creation time.")
                result.append(
                    RemoteBackup(
                        asset_id=_integer(asset.get("id")),
                        name=str(name),
                        created_at=created_at,
                        size=size,
                        workspace_id=match[1],
                        url=(
                            f"https://github.com/{repository}/releases/download/{RELEASE_TAG}/{name}"
                        ),
                    )
                )
            if len(rows) < 100:
                break
        return sorted(result, key=lambda item: (item.created_at, item.asset_id), reverse=True)

    def archives(self, library_id: str) -> list[RemoteBackup]:
        """List all workspace series so a new computer can recover earlier archives."""
        repository = self._repository(library_id)
        _ = self._private(repository)
        release = self._release(repository)
        return self._assets(repository, release) if release is not None else []

    def download(self, library_id: str, asset_id: int, destination: Path) -> Path:
        """Bound and checksum an authenticated numeric asset before archive validation."""
        if isinstance(asset_id, bool) or asset_id < 1:
            raise ValueError("Choose a managed backup archive.")
        repository = self._repository(library_id)
        matches = [asset for asset in self.archives(library_id) if asset.asset_id == asset_id]
        if len(matches) != 1:
            raise ValueError("The selected archive is not in the managed backup release.")
        asset = matches[0]
        if destination.exists() or destination.is_symlink():
            raise ValueError("Choose a new filename for the downloaded archive.")
        if any(parent.is_symlink() for parent in destination.parents):
            raise ValueError("Download destinations cannot contain symbolic links.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="github-workspace-download-", dir=destination.parent
        ) as tmp:
            staged = Path(tmp) / "archive.zip"
            self._download_verified(repository, asset, staged)
            # Hard linking installs without replacing an existing destination, even on a race.
            os.link(staged, destination)
        return destination

    def _download_verified(self, repository: str, asset: RemoteBackup, destination: Path) -> None:
        match = ARCHIVE_NAME.fullmatch(asset.name)
        assert match is not None
        _ = self._private(repository)
        _ = gh(
            "api",
            f"repos/{repository}/releases/assets/{asset.asset_id}",
            "--header",
            "Accept: application/octet-stream",
            output=destination,
            maximum_bytes=min(asset.size, MAX_ARCHIVE_BYTES),
            timeout=TRANSFER_TIMEOUT,
        )
        hasher = hashlib.sha256()
        size = 0
        with destination.open("rb") as incoming:
            while chunk := incoming.read(CHUNK_BYTES):
                size += len(chunk)
                if size > asset.size or size > MAX_ARCHIVE_BYTES:
                    raise ValueError("The downloaded archive exceeds its declared size.")
                hasher.update(chunk)
        if size != asset.size or hasher.hexdigest() != match[4]:
            raise ValueError("The downloaded archive checksum or size does not match.")

    def _upload(self, library_id: str, now: float) -> RemoteBackup | None:
        repository = self._repository(library_id)
        _ = self._private(repository)
        with tempfile.TemporaryDirectory(prefix="github-workspace-backup-") as temporary:
            folder = Path(temporary)
            archive = folder / "workspace.zip"
            summary = WorkspaceRecovery(self.store).export_archive(archive)
            size = archive.stat().st_size
            if size > MAX_ARCHIVE_BYTES:
                raise ValueError(
                    "Full workspace archives must be at most 2 GB for GitHub recovery."
                )
            stamp = datetime.fromtimestamp(now, UTC).strftime("%Y%m%dT%H%M%SZ")
            name = (
                f"workspace-{self.workspace_id}-{stamp}-{uuid.uuid4().hex}-{summary.sha256}"
                ".artifact-studio.zip"
            )
            destination = folder / name
            archive.rename(destination)
            if not self._record(library_id).status.settings.enabled:
                return None
            _ = self._private(repository)
            release = self._release(repository, create=True)
            assert release is not None
            if not self._record(library_id).status.settings.enabled:
                return None
            _ = self._private(repository)
            _ = gh(
                "release",
                "upload",
                RELEASE_TAG,
                str(destination),
                "--repo",
                f"https://github.com/{repository}",
                timeout=TRANSFER_TIMEOUT,
            )
            release = self._release(repository)
            if release is None:
                raise ValueError("The uploaded backup release could not be verified.")
            verified: set[int] = set()
            assets = self._assets(repository, release, verified)
            matches = [asset for asset in assets if asset.name == name and asset.size == size]
            if len(matches) != 1:
                raise ValueError(
                    "The uploaded backup asset could not be verified; no archives removed."
                )
            if matches[0].asset_id not in verified:
                self._download_verified(repository, matches[0], folder / "verified.zip")
            return matches[0]

    def _retain(self, library_id: str, uploaded: RemoteBackup) -> str:
        repository = self._repository(library_id)
        if not self._record(library_id).status.settings.enabled:
            return ""
        _ = self._private(repository)
        release = self._release(repository)
        if release is None:
            return "The uploaded backup release could not be read for retention."
        assets = self._assets(repository, release)
        if not any(
            item.asset_id == uploaded.asset_id and item.name == uploaded.name for item in assets
        ):
            return "The latest uploaded backup could not be verified for retention."
        own = [item for item in assets if item.workspace_id == self.workspace_id]
        keep_count = self._record(library_id).status.settings.keep_count
        keep = {item.asset_id for item in own[:keep_count]} | {uploaded.asset_id}
        failures = 0
        for asset in own:
            if asset.asset_id in keep:
                continue
            if not self._record(library_id).status.settings.enabled:
                break
            try:
                _ = gh(
                    "api",
                    "--method",
                    "DELETE",
                    f"repos/{repository}/releases/assets/{asset.asset_id}",
                )
            except OSError, ValueError:
                failures += 1
        return (
            f"Backup saved; {failures} older archive(s) could not be removed." if failures else ""
        )

    def tick(self, now: float | None = None) -> None:
        """Run each overdue schedule once, recovering interrupted checkpoints on restart."""
        with (self.libraries.cache / ".workspace-backups.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            try:
                self._tick(time.time() if now is None else now)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _tick(self, now: float) -> None:
        for library in self.libraries.list():
            if library.kind != "github":
                continue
            self._process(library.id, now)

    def _process(self, library_id: str, now: float) -> None:
        checkpoint = self._record(library_id)
        if not checkpoint.status.settings.enabled:
            return
        if (
            checkpoint.status.phase != "backing_up"
            and checkpoint.due_at is not None
            and checkpoint.due_at > now
        ):
            return

        def start(record: _Checkpoint) -> None:
            if record.status.settings.enabled:
                record.status.phase = "backing_up"
                record.status.error = ""
                record.status.warning = ""

        started = self._change(library_id, start)
        if not started.status.settings.enabled:
            return
        uploaded: RemoteBackup | None = None
        error = ""
        warning = ""
        try:
            uploaded = self._upload(library_id, now)
        except (OSError, ValueError) as failure:
            error = str(failure)
        if uploaded is not None:
            try:
                warning = self._retain(library_id, uploaded)
            except (OSError, ValueError) as failure:
                warning = f"Backup saved; older archives could not be checked: {failure}"

        def finish(record: _Checkpoint) -> None:
            if uploaded is not None:
                record.status.last_backup_at = _iso(now)
                record.status.archive_name = uploaded.name
                record.status.url = uploaded.url
                record.status.warning = warning
                record.failures = 0
            if not record.status.settings.enabled:
                record.status.phase = "disabled"
                record.due_at = None
            elif record.revision != started.revision and record.status.phase == "queued":
                # Preserve a manual request made while the previous backup was running.
                return
            elif error:
                record.failures += 1
                record.status.phase = "error"
                record.status.error = error
                record.due_at = now + min(300 * 2 ** min(record.failures - 1, 7), 6 * 3600)
            elif uploaded is not None:
                record.status.phase = "backed_up"
                record.status.error = ""
                record.due_at = now + record.status.settings.interval_hours * 3600

        _ = self._change(library_id, finish)

    async def run(self, stop: asyncio.Event) -> None:
        """Finish a tracked transfer before cancellation releases the server lifecycle."""
        while not stop.is_set():
            operation = asyncio.create_task(asyncio.to_thread(self.tick))
            try:
                await asyncio.shield(operation)
            except asyncio.CancelledError:
                await operation
                raise
            except Exception:
                logging.getLogger(__name__).exception("Could not run scheduled workspace backups")
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
