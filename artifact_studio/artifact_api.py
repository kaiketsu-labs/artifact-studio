"""Local library and artifact review routes, separate from the legacy slide editor."""

import base64
import binascii
import mimetypes
import shutil
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import Field

from .artifact_capture import capture_artifact_image
from .artifact_context import artifact_context
from .artifact_models import ArtifactFeedback, ArtifactFeedbackInput, ArtifactView, ProjectView
from .artifact_preview import artifact_file_headers, preview_document, preview_headers
from .artifacts import ArtifactStore, relative_path
from .design_library import (
    PREVIEW_TYPES,
    DesignCreate,
    DesignDelete,
    DesignDetail,
    DesignLibrary,
    DesignTextEdit,
)
from .design_references import ReferenceInput, RemoveReferenceInput
from .design_thumbnails import THUMBNAIL_SUFFIXES
from .design_thumbnails import design_thumbnail as make_design_thumbnail
from .models import FeedbackWorkflow, ModeInput, SlideSeenInput, StrictModel
from .project_tree import ProjectTree, TreeChange, TreeView
from .storage import Store
from .visuals import capture

_THUMBNAILS = threading.Lock()


class ProjectInput(StrictModel):
    """A project name and optional shared brief."""

    title: str = Field(min_length=1, max_length=300)
    context: str = Field(default="", max_length=50_000)


class ProjectAssignment(StrictModel):
    """An explicit change to a related artifact's project membership."""

    project_id: str


class UploadFile(StrictModel):
    """A browser-selected file retaining its relative bundle path."""

    name: str = Field(min_length=1, max_length=1000)
    data_base64: str = Field(max_length=70_000_000)


class ArtifactImport(StrictModel):
    """An arbitrary owned bundle with a viewer-selecting entrypoint."""

    project_id: str
    title: str = Field(min_length=1, max_length=300)
    files: list[UploadFile] = Field(min_length=1, max_length=1000)
    entrypoint: str = ""
    kind: str = "auto"
    context: str = Field(default="", max_length=50_000)


class ArtifactApproval(SlideSeenInput):
    """Approval is granted only through the human-facing review endpoint."""

    approved: bool = True


def import_upload(artifacts: ArtifactStore, body: ArtifactImport) -> ArtifactView:
    """Validate all uploaded paths and bytes before registering a durable owned copy."""
    paths: set[str] = set()
    total = 0
    with tempfile.TemporaryDirectory(prefix="artifact-import-") as temporary:
        folder = Path(temporary)
        for file in body.files:
            name = relative_path(file.name)
            if name in paths or name in {"", "."}:
                raise ValueError("Bundle files must have unique nonempty paths.")
            paths.add(name)
            try:
                data = base64.b64decode(file.data_base64, validate=True)
            except (ValueError, binascii.Error) as error:
                raise ValueError("Invalid file encoding.") from error
            total += len(data)
            if total > 100 * 1024 * 1024:
                raise ValueError("Import at most 100 MB at once.")
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            _ = target.write_bytes(data)
        entrypoint = body.entrypoint or next(
            (name for name in ("index.html", "README.md") if name in paths), body.files[0].name
        )
        return artifacts.register_artifact(
            body.project_id,
            body.title,
            str(folder),
            entrypoint,
            kind=body.kind,
            context=body.context,
        )


def artifact_routes(get_store: Callable[[], Store]) -> APIRouter:
    """Build routes with a late-bound store so isolated workspaces remain testable."""
    router = APIRouter(tags=["Artifact Studio"])

    def artifacts() -> ArtifactStore:
        """Use the currently configured collection and durable review database."""
        return ArtifactStore(get_store())

    @router.get("/api/hub")
    def library() -> dict[str, object]:
        """Discover artifacts without loading discussion or binary source bodies."""
        service = artifacts()
        store = get_store()
        default_design = store.default_design_id()
        projects = service.list_projects()
        return {
            "projects": [item.model_dump(exclude={"context"}) for item in projects],
            "project_tree": ProjectTree(store).view([item.id for item in projects]).model_dump(),
            "artifacts": [
                item.model_dump(exclude={"files", "context", "original_source_path"})
                for item in service.list_artifacts()
            ],
            "designs": [
                {**design.model_dump(), "is_default": design.id == default_design}
                for design in DesignLibrary(store).list()
            ],
        }

    @router.post("/api/hub/projects")
    def new_project(body: ProjectInput) -> ProjectView:
        """Create a project with an optional brief and no forced design pack."""
        return artifacts().create_project(body.title, body.context)

    @router.post("/api/hub/project-tree")
    def change_project_tree(body: TreeChange) -> TreeView:
        """Organize projects and nested folders without changing artifact membership."""
        return ProjectTree(get_store()).change(body)

    @router.get("/api/design-library/deleted")
    def deleted_designs() -> list[dict[str, object]]:
        return [item.model_dump() for item in DesignLibrary(get_store()).deleted()]

    @router.get("/api/design-library/{design_id}")
    def design_context(design_id: str) -> DesignDetail:
        return DesignLibrary(get_store()).get(design_id)

    @router.post("/api/design-library")
    def add_design(body: DesignCreate) -> DesignDetail:
        return DesignLibrary(get_store()).create(body)

    @router.post("/api/design-library/{design_id}/files")
    def attach_design_files(design_id: str, body: ReferenceInput) -> DesignDetail:
        return DesignLibrary(get_store()).attach(design_id, body)

    @router.post("/api/design-library/{design_id}/files/remove")
    def remove_design_file(design_id: str, body: RemoveReferenceInput) -> DesignDetail:
        return DesignLibrary(get_store()).remove_file(design_id, body)

    @router.post("/api/design-library/{design_id}/files/edit")
    def edit_design_text(design_id: str, body: DesignTextEdit) -> DesignDetail:
        """Save a human-edited source without overwriting a newer agent or filesystem edit."""
        return DesignLibrary(get_store()).edit_text(design_id, body)

    @router.post("/api/design-library/{design_id}/delete")
    def delete_design(design_id: str, body: DesignDelete) -> dict[str, bool]:
        DesignLibrary(get_store()).delete(design_id, body)
        return {"deleted": True}

    @router.post("/api/design-library/{design_id}/restore")
    def restore_design(design_id: str) -> DesignDetail:
        return DesignLibrary(get_store()).restore(design_id)

    @router.get("/api/design-library/{design_id}/files/{path:path}")
    def download_design_file(design_id: str, path: str) -> FileResponse:
        target = DesignLibrary(get_store()).file(design_id, path)
        return FileResponse(target, filename=target.name, media_type="application/octet-stream")

    @router.get("/api/design-library/{design_id}/thumbnail/{path:path}")
    def design_thumbnail(design_id: str, path: str) -> Response:
        """Serve a bounded raster preview for an inventoried image or PowerPoint."""
        target = DesignLibrary(get_store()).file(design_id, path)
        if target.suffix.lower() not in THUMBNAIL_SUFFIXES:
            raise FileNotFoundError("No thumbnail available.")
        headers = {
            "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        }
        try:
            data = make_design_thumbnail(target)
        except FileNotFoundError:
            return Response(status_code=204, headers=headers)
        return Response(data, media_type="image/png", headers=headers)

    @router.get("/api/design-library/{design_id}/image/{path:path}")
    def design_image(design_id: str, path: str) -> FileResponse:
        """Show only inert image formats in design cards; originals remain downloads."""
        target = DesignLibrary(get_store()).file(design_id, path)
        media_type = PREVIEW_TYPES.get(target.suffix.lower())
        if not media_type:
            raise ValueError("Choose an image from the design context.")
        return FileResponse(
            target,
            media_type=media_type,
            headers={
                "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/api/hub/context")
    def context(artifact_id: str = "", project_id: str = "") -> dict[str, object]:
        """Show guidance availability and the last recorded agent delivery separately."""
        return artifact_context(get_store(), artifact_id=artifact_id, project_id=project_id)

    @router.post("/api/artifacts/import")
    def import_artifact(body: ArtifactImport) -> ArtifactView:
        """Store arbitrary supplied files without executing a build or installation."""
        return import_upload(artifacts(), body)

    @router.get("/api/artifacts/{artifact_id}")
    def artifact(artifact_id: str) -> dict[str, object]:
        """Include human-visible pending discussion only in the local review workspace."""
        service = artifacts()
        view = service.get_artifact(artifact_id)
        workflow = service.workflow(artifact_id)
        return {
            **view.model_dump(),
            "feedback": service.feedback_for(view, include_pending=True),
            "mode": workflow.mode,
            "workflow": workflow,
            "delivery": {"live_available": workflow.review_connected},
        }

    @router.post("/api/artifacts/{artifact_id}/project")
    def assign_project(artifact_id: str, body: ProjectAssignment) -> ArtifactView:
        """Group related work without relocating canonical source files."""
        return artifacts().set_artifact_project(artifact_id, body.project_id)

    @router.post("/api/artifacts/{artifact_id}/feedback")
    def comment(artifact_id: str, body: ArtifactFeedbackInput) -> ArtifactFeedback:
        """Record feedback and all selected elements against the displayed source hash."""
        return artifacts().add_feedback(artifact_id, body, author="user")

    @router.get("/api/artifacts/{artifact_id}/feedback/{event_id}/files/{index}")
    def feedback_file(artifact_id: str, event_id: int, index: int) -> FileResponse:
        """Download human-visible attachments separately from executable artifact bundles."""
        path, metadata = artifacts().resolve_feedback_file(
            artifact_id,
            event_id,
            index,
            include_pending=True,
        )
        return FileResponse(
            path,
            filename=metadata.name,
            media_type="application/octet-stream",
            headers={"Content-Security-Policy": "sandbox; default-src 'none'"},
        )

    @router.post("/api/artifacts/{artifact_id}/approve")
    def approve(artifact_id: str, body: ArtifactApproval) -> ArtifactView:
        """Approve only the exact version the human viewed."""
        return artifacts().approve(artifact_id, body.content_hash, body.approved)

    @router.post("/api/artifacts/{artifact_id}/seen")
    def seen(artifact_id: str, body: SlideSeenInput) -> ArtifactView:
        """Clear the orange unread marker only for the opened version."""
        return artifacts().mark_seen(artifact_id, body.content_hash)

    @router.post("/api/artifacts/{artifact_id}/mode")
    def mode(artifact_id: str, body: ModeInput) -> FeedbackWorkflow:
        """Switch feedback release behavior without releasing a withheld batch."""
        return artifacts().set_mode(artifact_id, body.mode)

    @router.post("/api/artifacts/{artifact_id}/release")
    def release(artifact_id: str) -> FeedbackWorkflow:
        """Explicitly release this artifact's pending human feedback."""
        return artifacts().release_batch(artifact_id)

    @router.get("/api/artifacts/{artifact_id}/preview", response_class=HTMLResponse)
    def preview(
        artifact_id: str,
        request: Request,
        revision: Annotated[int | None, Query(ge=1)] = None,
    ) -> HTMLResponse:
        """Run the owned bundle in an opaque-origin frame with version-pinned resources."""
        service = artifacts()
        version = revision or service.get_artifact(artifact_id).revision
        origin = str(request.base_url).rstrip("/")
        return HTMLResponse(
            preview_document(service, artifact_id, origin, version),
            headers=preview_headers(artifact_id, version, origin),
        )

    def serve_file(
        artifact_id: str,
        path: str,
        origin: str,
        revision: int | None = None,
    ) -> FileResponse:
        """Serve owned immutable files; sandbox direct HTML/SVG navigation too."""
        service = artifacts()
        revision = revision or service.get_artifact(artifact_id).revision
        target = service.resolve_file(artifact_id, path, revision)
        return FileResponse(
            target,
            media_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
            headers=artifact_file_headers(artifact_id, revision, origin),
        )

    @router.get("/api/artifacts/{artifact_id}/files/{path:path}")
    def file(
        artifact_id: str,
        path: str,
        request: Request,
        revision: Annotated[int | None, Query(ge=1)] = None,
    ) -> FileResponse:
        """Read an owned file at the requested version, or the current source snapshot."""
        return serve_file(artifact_id, path, str(request.base_url).rstrip("/"), revision)

    @router.get("/api/artifacts/{artifact_id}/revisions/{revision}/files/{path:path}")
    def revision_file(artifact_id: str, revision: int, path: str, request: Request) -> FileResponse:
        """Keep relative dependencies and module imports within one immutable revision."""
        return serve_file(artifact_id, path, str(request.base_url).rstrip("/"), revision)

    @router.get("/api/hub/previews/{artifact_id}")
    def thumbnail(artifact_id: str, request: Request) -> FileResponse:
        """Cache actual cover-slide captures without mounting an iframe per library card."""
        service = artifacts()
        view = service.get_artifact(artifact_id)
        if view.kind in {"image", "svg"}:
            return serve_file(
                artifact_id,
                view.entrypoint,
                str(request.base_url).rstrip("/"),
                view.revision,
            )
        store = get_store()
        cache = store.database.parent / "artifact-previews" / f"{view.content_hash}.png"
        with _THUMBNAILS:
            if not cache.exists():
                if view.legacy_deck_id:
                    deck = store.read(view.legacy_deck_id)
                    source = capture(
                        store,
                        deck.id,
                        cache.parent,
                        slide_id=deck.slides[0].id,
                    ).path
                elif view.kind in {"html", "markdown"}:
                    source = capture_artifact_image(
                        service,
                        artifact_id,
                        cache.parent,
                        width=1280,
                        height=800,
                        revision=view.revision,
                    ).path
                else:
                    raise FileNotFoundError("This file has no visual preview.")
                cache.parent.mkdir(parents=True, exist_ok=True)
                _ = shutil.copyfile(source, cache)
        return FileResponse(cache, media_type="image/png")

    return router
