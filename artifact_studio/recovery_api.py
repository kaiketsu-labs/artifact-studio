"""Explicit local recovery downloads and bounded, previewed restores into new folders."""

import asyncio
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse
from pydantic import Field
from starlette.concurrency import run_in_threadpool
from starlette.types import Receive, Scope, Send

from .github_backups import GitHubBackups
from .libraries import Libraries, file_hash, inventory
from .models import StrictModel
from .storage import ConflictError, Store
from .workspace_recovery import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    RecoveryPreview,
    RecoveryResult,
    WorkspaceRecovery,
)

MAX_UPLOAD_BYTES = 2_000_000_000
CANDIDATE_SECONDS = 3600


class ArchiveDownload(FileResponse):
    """Clean private scratch even when a range request or disconnected client aborts delivery."""

    def __init__(self, path: Path, cleanup: Callable[[], None]) -> None:
        """Retain only the file path and an unconditional cleanup callback."""
        super().__init__(
            path,
            media_type="application/zip",
            filename=path.name,
            headers={"Cache-Control": "no-store"},
        )
        self.cleanup = cleanup

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Release temporary files and the export slot for every ASGI exit path."""
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.cleanup()


class BackupSource(StrictModel):
    """Select an already connected source and a new sibling workspace name."""

    library_id: str = Field(min_length=1, max_length=100)
    name: str = Field(default="Recovered workspace", min_length=1, max_length=100)


class RecoveryCommit(StrictModel):
    """Confirm the exact source and empty destination displayed in preview."""

    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class GitHubArchiveSource(BackupSource):
    """Select a managed release asset in a connected repository for verified recovery."""

    asset_id: int = Field(gt=0)


class PreparedRecovery(StrictModel):
    """A short-lived local preparation, never an arbitrary server filesystem path."""

    token: str
    destination: str
    database: str
    preview: RecoveryPreview


class CompletedRecovery(StrictModel):
    """A complete separate workspace without switching or altering the running server."""

    destination: str
    database: str
    result: RecoveryResult


@dataclass
class Candidate:
    """Keep a frozen source and owned empty destination until confirmation or expiry."""

    directory: Path
    source: Path
    store: Store
    mode: Literal["current", "archive"]
    created_at: float
    initial_database_hash: str
    complete: bool = False
    result: CompletedRecovery | None = None

    def cleanup(self) -> None:
        """Remove scratch sources and only a still untouched, empty destination."""
        shutil.rmtree(self.directory, ignore_errors=True)
        if self.complete:
            return
        root = self.store.root
        allowed = {
            self.store.database,
            Path(str(self.store.database) + "-wal"),
            Path(str(self.store.database) + "-shm"),
        }
        files = {path for path in root.rglob("*") if not path.is_dir()}
        if (
            files <= allowed
            and not any(path.is_symlink() for path in root.rglob("*"))
            and self.store.database.is_file()
            and file_hash(self.store.database) == self.initial_database_hash
        ):
            for path in allowed:
                path.unlink(missing_ok=True)
            for folder in sorted(root.rglob("*"), reverse=True):
                if folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
            if not any(root.iterdir()):
                root.rmdir()


def recovery_routes(current_store: Callable[[], Store]) -> APIRouter:
    """Keep archive payloads on disk and bind writes to reviewed temporary candidates."""
    candidates: dict[str, Candidate] = {}
    lock = threading.Lock()
    export_slot = threading.BoundedSemaphore()
    uploads = 0

    def expire(*, reserve: bool = True) -> None:
        """Bound abandoned preparations without touching successful recovered workspaces."""
        for token, candidate in list(candidates.items()):
            if time.monotonic() - candidate.created_at > CANDIDATE_SECONDS:
                candidate.cleanup()
                del candidates[token]
        if reserve and uploads + sum(not item.complete for item in candidates.values()) >= 4:
            raise ValueError("Close an unfinished recovery before preparing another.")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        """Expire abandoned private sources while idle and remove scratch on normal shutdown."""

        def sweep() -> None:
            """Serialize expiry against uploads and restore publication."""
            with lock:
                expire(reserve=False)

        async def housekeeping() -> None:
            """Check only local temporary preparations, without polling user artifacts."""
            while True:
                await asyncio.sleep(60)
                await run_in_threadpool(sweep)

        task = asyncio.create_task(housekeeping())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            with lock:
                for candidate in candidates.values():
                    candidate.cleanup()
                candidates.clear()

    router = APIRouter(lifespan=lifespan)

    def prepare(
        source: Path, directory: Path, name: str, mode: Literal["current", "archive"]
    ) -> PreparedRecovery:
        """Reserve a new sibling directory and validate all input before showing Restore."""
        name = name.strip()
        if (
            not name
            or name in {".", ".."}
            or name.startswith(".")
            or any(char in name for char in "/\\\x00\r\n")
            or len(name) > 100
        ):
            raise ValueError("Use a folder name without slashes or leading dots.")
        parent = current_store().root.parent
        if any(path.name.casefold() == name.casefold() for path in parent.iterdir()):
            raise ConflictError("That folder already exists. Choose a new workspace name.")
        target = parent / name
        target.mkdir()
        candidate: Candidate | None = None
        try:
            store = Store(target, target / ".artifact-studio" / "review.sqlite3")
            recovery = WorkspaceRecovery(store)
            candidate = Candidate(
                directory, source, store, mode, time.monotonic(), file_hash(store.database)
            )
            preview = recovery.preview(source, mode=mode)
            token = uuid.uuid4().hex
            candidates[token] = candidate
            return PreparedRecovery(
                token=token, destination=str(target), database=str(store.database), preview=preview
            )
        except BaseException:
            if candidate is not None:
                candidate.cleanup()
            raise

    @router.get("/api/workspace/archive")
    def archive(request: Request) -> FileResponse:
        """Download a local full archive without JSON/base64 or a second in-memory copy."""
        if request.headers.get("sec-fetch-site", "none") not in {"none", "same-origin"}:
            raise ValueError("Open Storage and backup in Artifact Studio to save this archive.")
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise ValueError("Workspace archives require a same-origin request.")
        if not export_slot.acquire(blocking=False):
            raise ValueError("A workspace archive is being saved. Wait for it to finish.")
        directory = Path(tempfile.mkdtemp(prefix="artifact-workspace-export-"))
        path = directory / "workspace.artifact-studio.zip"

        def cleanup() -> None:
            """Delete generated private bytes and release the single-export slot."""
            shutil.rmtree(directory, ignore_errors=True)
            export_slot.release()

        try:
            _ = WorkspaceRecovery(current_store()).export_archive(path)
        except BaseException:
            cleanup()
            raise
        return ArchiveDownload(path, cleanup)

    @router.post("/api/workspace/recovery/library")
    def library_source(body: BackupSource) -> PreparedRecovery:
        """Freeze a registered reference checkout; never import directly over current work."""
        with lock:
            expire()
            libraries = Libraries(current_store())
            library = libraries.get(body.library_id)
            if library.id == "collection":
                raise ValueError("Choose a backup repository or a local reference folder.")
            if library.kind == "github":
                library = libraries.refresh(library.id)
            directory = Path(tempfile.mkdtemp(prefix="artifact-workspace-recovery-")).resolve()
            source = directory / "snapshot"
            source.mkdir()
            try:
                with libraries.locked():
                    files = inventory(Path(library.path), snapshot=True)
                    if (
                        len(files) > MAX_FILES
                        or sum(item.size for item in files) > MAX_TOTAL_BYTES
                        or any(item.size > MAX_FILE_BYTES for item in files)
                    ):
                        raise ValueError("The backup exceeds workspace recovery limits.")
                    for item in files:
                        original = Path(item.local_path)
                        if original.is_symlink():
                            raise ValueError("Recovery sources cannot contain links.")
                        target = source / item.path
                        target.parent.mkdir(parents=True, exist_ok=True)
                        size = 0
                        with original.open("rb") as incoming, target.open("xb") as outgoing:
                            while chunk := incoming.read(1024 * 1024):
                                size += len(chunk)
                                if size > item.size:
                                    raise ConflictError("The backup changed. Preview it again.")
                                _ = outgoing.write(chunk)
                        if size != item.size:
                            raise ConflictError("The backup changed. Preview it again.")
                return prepare(source, directory, body.name, "current")
            except BaseException:
                shutil.rmtree(directory, ignore_errors=True)
                raise

    @router.post("/api/workspace/recovery/github-archive")
    def github_archive_source(body: GitHubArchiveSource) -> PreparedRecovery:
        """Download and validate a full archive before exposing the normal restore preview."""
        with lock:
            expire()
            directory = Path(tempfile.mkdtemp(prefix="artifact-github-recovery-")).resolve()
            try:
                source = GitHubBackups(current_store()).download(
                    body.library_id, body.asset_id, directory / "workspace.zip"
                )
                return prepare(source, directory, body.name, "archive")
            except BaseException:
                shutil.rmtree(directory, ignore_errors=True)
                raise

    @router.post("/api/workspace/recovery/upload")
    async def upload_source(
        request: Request, name: str = "Recovered workspace"
    ) -> PreparedRecovery:
        """Stream a selected ZIP to bounded local scratch storage before validating it."""
        nonlocal uploads
        declared = request.headers.get("content-length")
        if declared and (not declared.isdecimal() or int(declared) > MAX_UPLOAD_BYTES):
            raise ValueError("Keep uploaded workspace archives below 2 GB.")
        with lock:
            expire()
            uploads += 1
        directory = Path(tempfile.mkdtemp(prefix="artifact-workspace-upload-")).resolve()
        source = directory / "workspace.zip"
        try:
            size = 0
            async with asyncio.timeout(300):
                with source.open("xb") as output:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > MAX_UPLOAD_BYTES:
                            raise ValueError("Keep uploaded workspace archives below 2 GB.")
                        _ = output.write(chunk)

            def review() -> PreparedRecovery:
                """Validate archives outside the asynchronous request loop."""
                with lock:
                    expire(reserve=False)
                    return prepare(source, directory, name, "archive")

            return await run_in_threadpool(review)
        except TimeoutError as error:
            shutil.rmtree(directory, ignore_errors=True)
            raise ValueError("The upload timed out. Choose the archive again.") from error
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        finally:
            with lock:
                uploads -= 1

    @router.post("/api/workspace/recovery/{token}/restore")
    def restore(token: str, body: RecoveryCommit) -> CompletedRecovery:
        """Install exactly the reviewed input and retain the running workspace unchanged."""
        with lock:
            candidate = candidates.get(token)
            if candidate is None or time.monotonic() - candidate.created_at > CANDIDATE_SECONDS:
                raise ConflictError("This recovery preview expired. Choose the backup again.")
            if candidate.complete:
                if candidate.result and candidate.result.result.content_hash == body.content_hash:
                    return candidate.result
                raise ConflictError("This workspace was already restored.")
            result = WorkspaceRecovery(candidate.store).restore(
                candidate.source, body.content_hash, mode=candidate.mode
            )
            candidate.complete = True
            shutil.rmtree(candidate.directory, ignore_errors=True)
            candidate.result = CompletedRecovery(
                destination=str(candidate.store.root),
                database=str(candidate.store.database),
                result=result,
            )
            return candidate.result

    @router.delete("/api/workspace/recovery/{token}")
    def discard(token: str) -> dict[str, bool]:
        """Cancel a preview without deleting any restored or independently edited files."""
        with lock:
            candidate = candidates.pop(token, None)
            if candidate is not None:
                candidate.cleanup()
        return {"discarded": True}

    return router
