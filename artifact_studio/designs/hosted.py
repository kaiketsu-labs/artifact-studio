"""Private design access and bounded binary reference uploads for hosted owner agents."""

import base64
import hashlib
import os
import re
import stat
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from shutil import disk_usage
from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from ..multiplayer_access import AccessDenied, Actor, Scope, Sharing
from ..recovery_api import ArchiveDownload
from ..storage import ConflictError, Store
from .inputs import MAX_REFERENCE_BYTES, DesignFileInput
from .library import ContextFile, DesignLibrary
from .references import DesignReferences, ReferenceInput

AGENT_REFERENCE_PATH = re.compile(r"/api/agent/designs/([a-z0-9][a-z0-9-]{0,99})/references")
AGENT_DESIGN_FILE_PATH = re.compile(r"/api/agent/designs/([a-z0-9][a-z0-9-]{0,99})/files")


@contextmanager
def owner_design_store(sharing: Sharing, actor: Actor) -> Generator[Store]:
    """Recheck the current owner and workspace-wide agent binding for every operation."""
    record = sharing.agent_record(actor)
    workspace = cast(str, record["workspace"])
    if record["kind"] != "workspace" or not sharing.owner(Actor(actor.person), workspace):
        raise AccessDenied
    with sharing.guard(actor, Scope(workspace), "edit") as store:
        yield store


def reference_receipt(
    design_id: str, name: str, data: bytes, files: list[ContextFile]
) -> dict[str, object]:
    """Return only this saved source; unrelated inventories and binary bytes stay out of context."""
    filename = DesignReferences.filename(Path(name.replace("\\", "/")).name)
    digest = hashlib.sha256(data).hexdigest()
    return {
        "design_id": design_id,
        "references": [
            item.model_dump(include={"name", "path", "size", "sha256", "url", "version"})
            for item in files
            if item.name == filename and item.sha256 == digest
        ],
    }


def reference_routes(sharing: Sharing, *, disk_reserve: int) -> APIRouter:
    """Use the owner upload admission pool; references stay inert and content-addressed."""
    router = APIRouter()

    @router.post("/api/agent/designs/{design_id}/references")
    async def attach(
        request: Request,
        design_id: str,
        name: Annotated[str, Query(min_length=1, max_length=250)],
    ) -> dict[str, object]:
        if request.headers.get("content-type") != "application/octet-stream":
            raise HTTPException(415, "Send the original reference as application/octet-stream.")
        # MultiplayerAuth validates the binding before reading, limits the stream to 50 MB,
        # spools it with a 120-second timeout, and allows only one owner upload at a time.
        # Recheck authorization after the upload before modifying the selected workspace.
        data = await request.body()
        if not data or len(data) > MAX_REFERENCE_BYTES:
            raise HTTPException(413, "Send a non-empty reference no larger than 50 MB.")

        def install() -> dict[str, object]:
            actor = cast(Actor, request.state.actor)
            with owner_design_store(sharing, actor) as store:
                _ = DesignLibrary(store).get(design_id)
                if disk_usage(store.root).free < disk_reserve + 4 * len(data):
                    raise HTTPException(507, "Not enough workspace storage for this reference.")
                files = DesignLibrary(store).attach(
                    design_id,
                    ReferenceInput(
                        files=[
                            DesignFileInput(name=name, data_base64=base64.b64encode(data).decode())
                        ]
                    ),
                )
                return reference_receipt(design_id, name, data, files.files)

        return await run_in_threadpool(install)

    @router.get("/api/agent/designs/{design_id}/files")
    def download(
        request: Request,
        design_id: str,
        path: Annotated[str, Query(min_length=1, max_length=1000)],
        sha256: Annotated[str, Query(pattern=r"^[a-f0-9]{64}$")],
    ) -> ArchiveDownload:
        """Stream one hash-pinned original without sending binary data through model context."""
        actor = cast(Actor, request.state.actor)
        with owner_design_store(sharing, actor) as store:
            target = DesignLibrary(store).file(design_id, path)
            descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            temporary: tempfile.TemporaryDirectory[str] | None = None
            try:
                with os.fdopen(descriptor, "rb") as incoming:
                    before = os.fstat(incoming.fileno())
                    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_REFERENCE_BYTES:
                        raise HTTPException(413, "Download design files up to 50 MB.")
                    if disk_usage(sharing.root).free < disk_reserve + before.st_size:
                        raise HTTPException(507, "Not enough storage for this download.")
                    temporary = tempfile.TemporaryDirectory(
                        prefix=".design-download-", dir=sharing.root
                    )
                    snapshot = Path(temporary.name) / target.name
                    digest, remaining = hashlib.sha256(), before.st_size
                    with snapshot.open("xb") as outgoing:
                        while remaining:
                            chunk = incoming.read(min(64 * 1024, remaining))
                            if not chunk:
                                raise ConflictError(
                                    "The design source changed. Read its metadata again."
                                )
                            remaining -= len(chunk)
                            digest.update(chunk)
                            _ = outgoing.write(chunk)
                    after = os.fstat(incoming.fileno())
                    if (
                        incoming.read(1)
                        or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                        != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                        or digest.hexdigest() != sha256
                    ):
                        raise ConflictError("The design source changed. Read its metadata again.")
                return ArchiveDownload(
                    snapshot, temporary.cleanup, media_type="application/octet-stream"
                )
            except BaseException:
                if temporary is not None:
                    temporary.cleanup()
                raise

    return router
