"""Local share preparation and explicit, unsent Outlook handoff."""

import secrets
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import Field

from .artifacts import ArtifactStore
from .deployment import DeploymentConfig
from .models import StrictModel
from .share_outlook import create_outlook_draft
from .sharing import PreparedShare, ShareFormat, SharingService
from .storage import ConflictError, Store

MAX_SHARE_BYTES = 100 * 1024 * 1024
MAX_CACHE_BYTES = 300 * 1024 * 1024
SHARE_TTL = 3600


class ShareInput(StrictModel):
    """An explicit artifact or project audience export selected by the human."""

    scope: Literal["artifact", "project"]
    id: str = Field(min_length=1, max_length=200)
    format: ShareFormat | None = None
    expected_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ShareReceipt(StrictModel):
    """Compact metadata for one immutable local attachment."""

    token: str
    filename: str
    media_type: str
    size: int
    download_url: str
    excluded: tuple[str, ...]


@dataclass
class CachedShare:
    """An expiring audience export, isolated to its workspace and draft attempt."""

    file: PreparedShare
    workspace: Path
    expires: float
    request: ShareInput
    reader: str
    draft_state: Literal["new", "opening", "opened", "failed"] = "new"
    draft_error: str = ""


@dataclass(frozen=True)
class ShareWorkspace:
    """The authenticated workspace and human that can receive an export."""

    store: Store
    reader: str = ""
    home: str = "/"


type ScopedShareAccess = Callable[
    [Request, ShareInput, tuple[str, ...]], AbstractContextManager[ShareWorkspace]
]


def sharing_routes(
    get_store: Callable[[], Store], *, scoped: ScopedShareAccess | None = None
) -> APIRouter:
    """Build the same file-sharing routes for local and scoped workspaces."""
    router = APIRouter(prefix="/api/shares", tags=["Sharing"])
    cache: dict[str, CachedShare] = {}
    lock = threading.RLock()
    prepare_slot = threading.BoundedSemaphore(1)

    def access(
        request: Request, body: ShareInput, artifacts: tuple[str, ...] = ()
    ) -> AbstractContextManager[ShareWorkspace]:
        """Hold the current grant while selecting or releasing an attachment."""
        return (
            scoped(request, body, artifacts) if scoped else nullcontext(ShareWorkspace(get_store()))
        )

    def purge() -> None:
        """Discard expired attachments after their draft operation has settled."""
        for key, item in tuple(cache.items()):
            if item.expires < time.monotonic() and item.draft_state != "opening":
                del cache[key]

    def current(token: str) -> CachedShare:
        """Resolve an expiring share before checking its destination identity."""
        purge()
        item = cache.get(token)
        if item is None:
            raise FileNotFoundError("The shared file expired; prepare it again.")
        return item

    @router.get("/capabilities")
    def capabilities(request: Request) -> dict[str, bool]:
        """Offer Outlook only where a local installation can receive attachments."""
        deployment = getattr(request.app.state, "deployment", None)
        return {
            "outlook": scoped is None
            and (not isinstance(deployment, DeploymentConfig) or deployment.mode == "local")
            and sys.platform == "darwin"
            and Path("/Applications/Microsoft Outlook.app").is_dir()
        }

    def capture(request: Request, body: ShareInput) -> ShareReceipt:
        """Keep expensive rendering outside the collaboration lock and recheck before caching."""
        with access(request, body) as workspace:
            store = workspace.store
            artifacts = ArtifactStore(store)
            service = SharingService(
                store, artifacts, scoped_limit=MAX_SHARE_BYTES if scoped else None
            )
            selection = service.select(body.scope, body.id)
            expected = body.expected_hash if body.scope == "artifact" else None
            if expected is not None and selection.artifacts[0].content_hash != expected:
                raise ConflictError("This artifact changed. Open the update before sharing.")
        file = service.prepare(selection, body.format)
        if len(file.data) > MAX_SHARE_BYTES:
            raise ValueError("Share files up to 100 MB. Export a smaller selection.")
        with access(request, body, tuple(identifier for identifier, _ in file.revisions)):
            if expected is not None and artifacts.get_artifact(body.id).content_hash != expected:
                raise ConflictError("This artifact changed while preparing. Open the update first.")
            token = secrets.token_urlsafe(24)
            with lock:
                purge()
                while cache and (
                    len(cache) >= 20
                    or sum(len(item.file.data) for item in cache.values()) + len(file.data)
                    > MAX_CACHE_BYTES
                ):
                    oldest = next(
                        (key for key, item in cache.items() if item.draft_state != "opening"), None
                    )
                    if oldest is None:
                        raise ValueError("Other attachments are opening. Try again in a moment.")
                    del cache[oldest]
                cache[token] = CachedShare(
                    file,
                    store.database.resolve(),
                    time.monotonic() + SHARE_TTL,
                    body,
                    workspace.reader,
                )
        return ShareReceipt(
            token=token,
            filename=file.filename,
            media_type=file.media_type,
            size=len(file.data),
            download_url=f"{workspace.home}api/shares/{token}/file",
            excluded=file.excluded,
        )

    @router.post("")
    def prepare(request: Request, body: ShareInput) -> ShareReceipt:
        """Bound simultaneous hosted exports before allocating attachment bytes."""
        if scoped is None:
            return capture(request, body)
        if not prepare_slot.acquire(blocking=False):
            raise HTTPException(429, "An export is already being prepared. Try again shortly.")
        try:
            return capture(request, body)
        finally:
            prepare_slot.release()

    @router.get("/{token}/file")
    def download(request: Request, token: str) -> Response:
        """Download exactly the immutable file prepared in the share panel."""
        with lock:
            item = current(token)
        file = item.file
        with access(
            request, item.request, tuple(identifier for identifier, _ in file.revisions)
        ) as workspace:
            if (
                item.workspace != workspace.store.database.resolve()
                or item.reader != workspace.reader
            ):
                raise FileNotFoundError("The shared file expired; prepare it again.")
            return Response(
                file.data,
                media_type=file.media_type,
                headers={
                    "Content-Disposition": "attachment; filename*=UTF-8''" + quote(file.filename),
                    "Content-Security-Policy": "sandbox; default-src 'none'",
                    "Cache-Control": "no-store",
                },
            )

    if scoped is not None:
        return router

    @router.post("/{token}/outlook")
    def outlook(token: str) -> dict[str, bool]:
        """Open one draft on explicit request; repeat requests never duplicate it."""
        with lock:
            item = current(token)
            if item.workspace != get_store().database.resolve():
                raise FileNotFoundError("The shared file expired; prepare it again.")
            if item.draft_state == "opened":
                return {"opened": True}
            if item.draft_state == "opening":
                raise ConflictError("The Outlook draft is already opening.")
            if item.draft_state == "failed":
                raise ValueError(item.draft_error)
            item.draft_state = "opening"
        try:
            with tempfile.TemporaryDirectory(prefix="artifact-studio-share-") as folder:
                attachment = Path(folder) / item.file.filename
                _ = attachment.write_bytes(item.file.data)
                create_outlook_draft(attachment)
        except (OSError, ValueError) as error:
            with lock:
                item.draft_state = "failed"
                item.draft_error = str(error)
            raise ValueError(str(error)) from error
        with lock:
            item.draft_state = "opened"
        return {"opened": True}

    return router
