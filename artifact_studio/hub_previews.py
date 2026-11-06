"""Persistent exact-version library covers, prepared by one bounded background worker."""

import hashlib
import json
import math
import queue
import re
import shutil
import tempfile
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import quote

from .artifact_models import ArtifactView
from .artifacts import ArtifactStore
from .projects import RootSettings
from .renderer_health import (
    RendererUnavailable,
    check_renderer_available,
    ensure_renderer_ready,
)
from .storage import SAFE_ID, Store, digest, replace_text

TOKEN = re.compile(r"^[a-f0-9]{64}$")
CONTRACT = "library-cover-v2-full-layout"
MAX_COVERS = 256
MAX_CACHE_BYTES = 128 * 1024 * 1024
MAX_COVER_BYTES = 8 * 1024 * 1024


def cache_folder(store: Store) -> Path:
    return store.database.parent / "artifact-previews"


def cover_path(store: Store, artifact_id: str, token: str) -> Path:
    if not SAFE_ID.fullmatch(artifact_id) or not TOKEN.fullmatch(token):
        raise ValueError("Invalid cover identity.")
    return cache_folder(store) / f"{artifact_id}-{token}.png"


def cover_url(artifact_id: str, token: str) -> str:
    return f"/api/hub/previews/{quote(artifact_id, safe='')}?v={token}"


class _CoverRootSettings(RootSettings):
    """Stop at the document root, before buffering a potentially large embedded deck."""

    found = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self.found:
            super().handle_starttag(tag, attrs)
            self.found = tag == "html"


def _root_identity(path: Path) -> str:
    settings = _CoverRootSettings()
    with path.open(encoding="utf-8") as source:
        while chunk := source.read(16 * 1024):
            settings.feed(chunk)
            if settings.found:
                break
    return json.dumps(settings.attributes)


def cover_token(store: Store, view: ArtifactView) -> str:
    """Keep review identity separate from exact visual dependency identity for legacy decks."""
    parts = [CONTRACT, view.id, view.content_hash]
    if view.legacy_deck_id:
        folder = store.folder(view.legacy_deck_id)
        checksum = hashlib.sha256()
        # Legacy review hashes use asset timestamps. Covers additionally hash bytes,
        # so externally replaced images still refresh when size/time were preserved.
        for path in sorted(folder.rglob("*")):
            relative = path.relative_to(folder)
            if any(part.startswith(".") for part in relative.parts):
                continue
            if path.is_symlink():
                continue
            if not path.is_file():
                continue
            if relative.as_posix() in {"README.md", "AGENT-GUIDE.md", "PREFERENCES.md"}:
                continue
            checksum.update(relative.as_posix().encode())
            checksum.update(b"\0")
            if relative.as_posix() == "index.html":
                # Audience content is already in view.content_hash; private notes,
                # revision counters and discussion must not regenerate the cover.
                checksum.update(_root_identity(path).encode())
            else:
                with path.open("rb") as source:
                    while data := source.read(1024 * 1024):
                        checksum.update(data)
            checksum.update(b"\0")
        parts.append(checksum.hexdigest())
    return digest(json.dumps(parts))


def cached(store: Store, artifact_id: str, token: str) -> Path | None:
    path = cover_path(store, artifact_id, token)
    return path if path.is_file() and not path.is_symlink() else None


def cover_metadata(store: Store, view: ArtifactView) -> dict[str, str | bool]:
    """Expose current identity and a clearly separate last-good preview, never source bytes."""
    if view.kind not in {"presentation", "html", "markdown", "image", "svg"}:
        return {"preview_url": "", "preview_fallback_url": "", "preview_cached": False}
    token = cover_token(store, view)
    ready = cached(store, view.id, token) is not None
    fallback = ""
    pointer = cache_folder(store) / f"last-{view.id}.json"
    try:
        if not ready and pointer.is_file() and pointer.stat().st_size < 1024:
            previous = json.loads(pointer.read_text())
            old = previous.get("token", "")
            if isinstance(old, str) and TOKEN.fullmatch(old) and cached(store, view.id, old):
                fallback = cover_url(view.id, old)
    except OSError, ValueError, TypeError:
        pass
    return {
        "preview_url": cover_url(view.id, token),
        "preview_fallback_url": fallback,
        "preview_cached": ready,
    }


@dataclass(frozen=True)
class CoverReply:
    """One immediate HTTP outcome; queueing never holds a request until Chromium finishes."""

    code: str
    message: str
    status: int = 202
    retry_after: int = 1
    path: Path | None = None


@dataclass(frozen=True)
class _Job:
    store: Store
    artifact_id: str
    token: str
    kind: str
    render: Callable[[ArtifactView, Path], Path]

    @property
    def key(self) -> tuple[str, str, str]:
        return (str(self.store.database.resolve()), self.artifact_id, self.token)


class CoverWorker:
    """Bound queued identities and start only one daemon worker, across all request threads."""

    def __init__(self, capacity: int = 8, failure_seconds: float = 30) -> None:
        self.jobs: queue.Queue[_Job] = queue.Queue(maxsize=capacity)
        self.lock = threading.Lock()
        self.pending: set[tuple[str, str, str]] = set()
        self.worker: threading.Thread | None = None
        self.failure_seconds = failure_seconds
        self.failed_writes: OrderedDict[tuple[str, str, str], tuple[float, CoverReply]] = (
            OrderedDict()
        )

    def _failure_path(self, job: _Job) -> Path:
        return cache_folder(job.store) / f"failure-{job.artifact_id}-{job.token}.json"

    def _failure(self, job: _Job) -> CoverReply | None:
        with self.lock:
            local = self.failed_writes.get(job.key)
            if local is not None:
                remaining = math.ceil(local[0] - time.monotonic())
                if remaining > 0:
                    reply = local[1]
                    return CoverReply(reply.code, reply.message, reply.status, remaining)
                del self.failed_writes[job.key]
        path = self._failure_path(job)
        try:
            if not path.is_file() or path.stat().st_size > 4096:
                return None
            value = json.loads(path.read_text())
            seconds = math.ceil(float(value["retry_at"]) - time.time())
            if seconds <= 0:
                path.unlink(missing_ok=True)
                return None
            code = str(value["code"])
            if code not in {"cover_unavailable", "cover_changed", "renderer_unavailable"}:
                return None
            return CoverReply(
                code,
                str(value["message"])[:500],
                409 if code == "cover_changed" else 503,
                min(seconds, 300),
            )
        except OSError, ValueError, TypeError, KeyError:
            return None

    def request(self, job: _Job) -> CoverReply:
        """Read immutable cached bytes first, then coalesce or enqueue a bounded cold job."""
        if path := cached(job.store, job.artifact_id, job.token):
            with suppress(OSError):
                path.touch()
            return CoverReply("cover_ready", "", 200, path=path)
        if failure := self._failure(job):
            return failure
        if job.kind != "image":
            try:
                check_renderer_available()
            except RendererUnavailable as error:
                return CoverReply(
                    "renderer_unavailable", str(error), 503, error.retry_after_seconds
                )
        with self.lock:
            if job.key in self.pending:
                return CoverReply("cover_pending", "Updating preview.")
            try:
                self.jobs.put_nowait(job)
            except queue.Full:
                return CoverReply("cover_busy", "Previews are updating. Try again shortly.")
            self.pending.add(job.key)
            if self.worker is None or not self.worker.is_alive():
                self.worker = threading.Thread(
                    target=self._run, name="artifact-library-covers", daemon=True
                )
                self.worker.start()
        return CoverReply("cover_pending", "Updating preview.")

    def _remember(self, job: _Job, code: str, message: str, seconds: float) -> None:
        try:
            folder = cache_folder(job.store)
            folder.mkdir(parents=True, exist_ok=True)
            replace_text(
                self._failure_path(job),
                json.dumps(
                    {"code": code, "message": message[:500], "retry_at": time.time() + seconds}
                ),
            )
            failures = sorted(
                folder.glob("failure-*.json"), key=lambda path: path.stat().st_mtime_ns
            )
            for old in failures[:-MAX_COVERS]:
                old.unlink(missing_ok=True)
        except OSError:
            # A full disk must not turn repeated card visits into a render loop.
            with self.lock:
                self.failed_writes[job.key] = (
                    time.monotonic() + seconds,
                    CoverReply(code, message[:500], 409 if code == "cover_changed" else 503),
                )
                self.failed_writes.move_to_end(job.key)
                while len(self.failed_writes) > MAX_COVERS:
                    self.failed_writes.popitem(last=False)

    def _run(self) -> None:
        while True:
            job = self.jobs.get()
            try:
                self._produce(job)
            except RendererUnavailable as error:
                self._remember(job, "renderer_unavailable", str(error), error.retry_after_seconds)
            except (OSError, ValueError) as error:
                self._remember(job, "cover_unavailable", str(error), self.failure_seconds)
            except Exception:
                # An optional cover must not terminate the worker or affect canonical files.
                self._remember(
                    job,
                    "cover_unavailable",
                    "This preview could not be prepared. Try again.",
                    self.failure_seconds,
                )
            finally:
                with self.lock:
                    self.pending.discard(job.key)
                self.jobs.task_done()

    def _produce(self, job: _Job) -> None:
        if cached(job.store, job.artifact_id, job.token):
            return
        if job.kind != "image":
            ensure_renderer_ready()
        service = ArtifactStore(job.store)
        view = service.get_artifact(job.artifact_id)
        if cover_token(job.store, view) != job.token:
            self._remember(job, "cover_changed", "The artifact changed. Refresh its preview.", 30)
            return
        folder = cache_folder(job.store)
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="pending-", dir=folder) as temporary:
            source = job.render(view, Path(temporary))
            if not source.is_file() or source.stat().st_size > MAX_COVER_BYTES:
                raise ValueError("The generated preview exceeds the image size limit.")
            latest = service.get_artifact(job.artifact_id)
            if cover_token(job.store, latest) != job.token:
                self._remember(
                    job, "cover_changed", "The artifact changed while its preview was prepared.", 30
                )
                return
            staged = Path(temporary) / "complete.png"
            _ = shutil.copyfile(source, staged)
            _ = staged.replace(cover_path(job.store, job.artifact_id, job.token))
        replace_text(folder / f"last-{job.artifact_id}.json", json.dumps({"token": job.token}))
        self._failure_path(job).unlink(missing_ok=True)
        self._prune(folder, cover_path(job.store, job.artifact_id, job.token))

    @staticmethod
    def _prune(folder: Path, current: Path) -> None:
        images = sorted(folder.glob("*.png"), key=lambda path: path.stat().st_mtime_ns)
        sizes = {path: path.stat().st_size for path in images}
        total = sum(sizes.values())
        count = len(images)
        for old in images:
            if count <= MAX_COVERS and total <= MAX_CACHE_BYTES:
                break
            if old != current:
                old.unlink(missing_ok=True)
                count -= 1
                total -= sizes[old]


WORKER = CoverWorker()


def request_cover(
    store: Store,
    artifact_id: str,
    token: str,
    render: Callable[[ArtifactView, Path], Path],
) -> CoverReply:
    """Validate an existing artifact without reading its bundle or private discussion."""
    if not SAFE_ID.fullmatch(artifact_id):
        raise ValueError("Invalid artifact ID.")
    with store.connect() as db:
        row = db.execute(
            "SELECT json_extract(document, '$.kind') AS kind FROM artifact_items WHERE id=?",
            (artifact_id,),
        ).fetchone()
    if row is not None:
        kind = cast(str, row["kind"])
    elif (store.folder(artifact_id) / "index.html").is_file():
        kind = "presentation"
    else:
        raise FileNotFoundError("Artifact does not exist.")
    if not TOKEN.fullmatch(token):
        return CoverReply("cover_changed", "Refresh the library to load this preview.", 409)
    return WORKER.request(_Job(store, artifact_id, token, kind, render))
