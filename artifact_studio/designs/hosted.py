"""Private design access and bounded binary reference uploads for hosted owner agents."""

import base64
import hashlib
import re
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from shutil import disk_usage
from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from ..models import FileInput
from ..multiplayer_access import AccessDenied, Actor, Scope, Sharing
from ..storage import Store
from .library import DesignLibrary
from .references import DesignReference, DesignReferences, ReferenceInput

MAX_REFERENCE_BYTES = 25_000_000
AGENT_REFERENCE_PATH = re.compile(r"/api/agent/designs/([a-z0-9][a-z0-9-]{0,99})/references")


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
    design_id: str, name: str, data: bytes, files: list[DesignReference]
) -> dict[str, object]:
    """Return only this saved source; unrelated inventories and binary bytes stay out of context."""
    filename = DesignReferences.filename(Path(name.replace("\\", "/")).name)
    digest = hashlib.sha256(data).hexdigest()
    return {
        "design_id": design_id,
        "references": [
            item.model_dump(exclude={"local_path"})
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
        # MultiplayerAuth validates the binding before reading, limits the stream to 25 MB,
        # spools it with a 120-second timeout, and allows only one owner upload at a time.
        # Recheck authorization after the upload before modifying the selected workspace.
        data = await request.body()
        if not data or len(data) > MAX_REFERENCE_BYTES:
            raise HTTPException(413, "Send a non-empty reference no larger than 25 MB.")

        def install() -> dict[str, object]:
            actor = cast(Actor, request.state.actor)
            with owner_design_store(sharing, actor) as store:
                _ = DesignLibrary(store).get(design_id)
                if disk_usage(store.root).free < disk_reserve + 4 * len(data):
                    raise HTTPException(507, "Not enough workspace storage for this reference.")
                files = DesignReferences(store).attach(
                    design_id,
                    ReferenceInput(
                        files=[FileInput(name=name, data_base64=base64.b64encode(data).decode())]
                    ),
                )
                return reference_receipt(design_id, name, data, files)

        return await run_in_threadpool(install)

    return router
