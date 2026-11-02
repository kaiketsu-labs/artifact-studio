"""Local share preparation and explicit, unsent Outlook handoff."""

import secrets
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import Field

from .artifacts import ArtifactStore
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
    draft_state: Literal["new", "opening", "opened", "failed"] = "new"
    draft_error: str = ""


def sharing_routes(get_store: Callable[[], Store]) -> APIRouter:
    """Build local-only share routes with bounded immutable export storage."""
    router = APIRouter(prefix="/api/shares", tags=["Sharing"])
    cache: dict[str, CachedShare] = {}
    lock = threading.RLock()

    def current(token: str) -> CachedShare:
        """Resolve a live share only in the workspace that prepared it."""
        for key, item in tuple(cache.items()):
            if item.expires < time.monotonic() and item.draft_state != "opening":
                del cache[key]
        item = cache.get(token)
        if item is None or item.workspace != get_store().database.resolve():
            raise FileNotFoundError("The shared file expired; prepare it again.")
        return item

    @router.get("/capabilities")
    def capabilities() -> dict[str, bool]:
        """Offer Outlook only where a local installation can receive attachments."""
        return {
            "outlook": sys.platform == "darwin"
            and Path("/Applications/Microsoft Outlook.app").is_dir()
        }

    @router.post("")
    def prepare(body: ShareInput) -> ShareReceipt:
        """Capture audience bytes without downloading or contacting another app."""
        store = get_store()
        artifacts = ArtifactStore(store)
        if (
            body.scope == "artifact"
            and body.expected_hash is not None
            and artifacts.get_artifact(body.id).content_hash != body.expected_hash
        ):
            raise ConflictError("This artifact changed. Open the update before sharing.")
        service = SharingService(store, artifacts)
        file = (
            service.prepare_artifact(body.id, body.format)
            if body.scope == "artifact"
            else service.prepare_project(body.id, body.format)
        )
        if (
            body.scope == "artifact"
            and body.expected_hash is not None
            and artifacts.get_artifact(body.id).content_hash != body.expected_hash
        ):
            raise ConflictError("This artifact changed while preparing. Open the update first.")
        if len(file.data) > MAX_SHARE_BYTES:
            raise ValueError("Share files up to 100 MB. Export a smaller selection.")
        token = secrets.token_urlsafe(24)
        with lock:
            # Purge expired exports before retaining another bounded attachment.
            with suppress(FileNotFoundError):
                current("")
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
            cache[token] = CachedShare(file, store.database.resolve(), time.monotonic() + SHARE_TTL)
        return ShareReceipt(
            token=token,
            filename=file.filename,
            media_type=file.media_type,
            size=len(file.data),
            download_url=f"/api/shares/{token}/file",
            excluded=file.excluded,
        )

    @router.get("/{token}/file")
    def download(token: str) -> Response:
        """Download exactly the immutable file prepared in the share panel."""
        with lock:
            file = current(token).file
        return Response(
            file.data,
            media_type=file.media_type,
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''" + quote(file.filename),
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Cache-Control": "no-store",
            },
        )

    @router.post("/{token}/outlook")
    def outlook(token: str) -> dict[str, bool]:
        """Open one draft on explicit request; repeat requests never duplicate it."""
        with lock:
            item = current(token)
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
