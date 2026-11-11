"""Native design library routes bound to the current workspace."""

import errno
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from shutil import disk_usage

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from .design_library import (
    PREVIEW_TYPES,
    DesignCreate,
    DesignDelete,
    DesignDetail,
    DesignLibrary,
    DesignTextEdit,
)
from .design_references import (
    DesignReference,
    DesignReferences,
    ReferenceInput,
    RemoveReferenceInput,
)
from .design_thumbnails import THUMBNAIL_SUFFIXES, design_thumbnail
from .models import FileInput
from .storage import Store

INERT_FILE_HEADERS = {
    "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, no-store",
}


def design_library_routes(
    access: Callable[[Request], AbstractContextManager[Store]], *, disk_reserve: int = 0
) -> APIRouter:
    """Use the regular design library while keeping authorization active through each operation."""
    router = APIRouter()
    library = APIRouter(prefix="/api/design-library")
    scoped = disk_reserve > 0

    @contextmanager
    def write_access(request: Request, files: list[FileInput]) -> Generator[Store]:
        """Budget the destination filesystem under the same ownership lock as the write."""
        with access(request) as store:
            if disk_reserve:
                needed = disk_reserve + 3 * sum(len(file.data_base64) for file in files)
                if disk_usage(store.root).free < needed:
                    raise HTTPException(507, "Not enough workspace storage for these design files.")
            try:
                yield store
            except OSError as error:
                if disk_reserve and error.errno == errno.ENOSPC:
                    raise HTTPException(
                        507, "Not enough workspace storage for these design files."
                    ) from None
                raise

    def response(detail: DesignDetail) -> dict[str, object]:
        """Keep local filesystem addresses out of hosted browser metadata."""
        if scoped:
            return detail.model_dump(exclude={"files": {"__all__": {"local_path"}}})
        return detail.model_dump()

    @library.get("/deleted")
    def deleted_designs(request: Request) -> list[dict[str, object]]:
        with access(request) as store:
            return [item.model_dump() for item in DesignLibrary(store).deleted()]

    @library.get("/{design_id}")
    def design_context(request: Request, design_id: str) -> dict[str, object]:
        with access(request) as store:
            return response(DesignLibrary(store).get(design_id))

    @library.post("/{design_id}/default")
    def default_design(request: Request, design_id: str) -> dict[str, str]:
        """Choose the default for future presentations in the selected workspace."""
        with access(request) as store:
            return {"default_design_id": store.set_default_design(design_id)}

    @library.post("")
    def add_design(request: Request, body: DesignCreate) -> dict[str, object]:
        with write_access(request, body.files) as store:
            return response(DesignLibrary(store).create(body))

    @library.post("/{design_id}/files")
    def attach_design_files(
        request: Request, design_id: str, body: ReferenceInput
    ) -> dict[str, object]:
        with write_access(request, body.files) as store:
            return response(DesignLibrary(store).attach(design_id, body))

    @library.post("/{design_id}/files/remove")
    def remove_design_file(
        request: Request, design_id: str, body: RemoveReferenceInput
    ) -> dict[str, object]:
        with access(request) as store:
            return response(DesignLibrary(store).remove_file(design_id, body))

    @library.post("/{design_id}/files/edit")
    def edit_design_text(
        request: Request, design_id: str, body: DesignTextEdit
    ) -> dict[str, object]:
        """Save a human-edited source against the content hash they reviewed."""
        with access(request) as store:
            return response(DesignLibrary(store).edit_text(design_id, body))

    @library.post("/{design_id}/delete")
    def delete_design(request: Request, design_id: str, body: DesignDelete) -> dict[str, bool]:
        with access(request) as store:
            DesignLibrary(store).delete(design_id, body)
            return {"deleted": True}

    @library.post("/{design_id}/restore")
    def restore_design(request: Request, design_id: str) -> dict[str, object]:
        with access(request) as store:
            return response(DesignLibrary(store).restore(design_id))

    @library.get("/{design_id}/files/{path:path}")
    def download_design_file(request: Request, design_id: str, path: str) -> FileResponse:
        with access(request) as store:
            target = DesignLibrary(store).file(design_id, path)
            return FileResponse(
                target,
                filename=target.name,
                media_type="application/octet-stream",
                headers=INERT_FILE_HEADERS,
            )

    @library.get("/{design_id}/thumbnail/{path:path}")
    def thumbnail(request: Request, design_id: str, path: str) -> Response:
        """Render only bounded images or PowerPoint thumbnails from the design inventory."""
        with access(request) as store:
            target = DesignLibrary(store).file(design_id, path)
            if target.suffix.lower() not in THUMBNAIL_SUFFIXES:
                raise FileNotFoundError("No thumbnail available.")
            try:
                data = design_thumbnail(target)
            except FileNotFoundError:
                return Response(status_code=204, headers=INERT_FILE_HEADERS)
            return Response(data, media_type="image/png", headers=INERT_FILE_HEADERS)

    @library.get("/{design_id}/image/{path:path}")
    def design_image(request: Request, design_id: str, path: str) -> FileResponse:
        """Serve only supported image formats under an inert response policy."""
        with access(request) as store:
            target = DesignLibrary(store).file(design_id, path)
            media_type = PREVIEW_TYPES.get(target.suffix.lower())
            if not media_type:
                raise ValueError("Choose an image from the design context.")
            return FileResponse(target, media_type=media_type, headers=INERT_FILE_HEADERS)

    def reference_response(references: list[DesignReference]) -> list[dict[str, object]]:
        if not scoped:
            return [item.model_dump() for item in references]
        result: list[dict[str, object]] = []
        for item in references:
            payload: dict[str, object] = item.model_dump(exclude={"local_path"})
            payload["url"] = item.url.replace("/api/designs/", "/api/design-library/", 1)
            result.append(payload)
        return result

    @router.get("/api/designs/{design_id}/references")
    def references(request: Request, design_id: str) -> list[dict[str, object]]:
        with access(request) as store:
            if scoped:
                _ = store.design(design_id, include_archived=False)
            return reference_response(DesignReferences(store).list(design_id))

    @router.post("/api/designs/{design_id}/references")
    def attach_references(
        request: Request, design_id: str, body: ReferenceInput
    ) -> list[dict[str, object]]:
        with write_access(request, body.files) as store:
            if scoped:
                _ = store.design(design_id, include_archived=False)
            return reference_response(DesignReferences(store).attach(design_id, body))

    @router.post("/api/designs/{design_id}/references/remove")
    def remove_reference(
        request: Request, design_id: str, body: RemoveReferenceInput
    ) -> list[dict[str, object]]:
        with access(request) as store:
            if scoped:
                _ = store.design(design_id, include_archived=False)
            return reference_response(DesignReferences(store).remove(design_id, body))

    router.include_router(library)
    return router
