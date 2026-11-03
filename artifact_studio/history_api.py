"""Human-facing, lazy version previews and optimistic, non-destructive restores."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import Field

from .artifact_models import ArtifactView
from .artifacts import ArtifactStore
from .history_thumbnails import history_thumbnail
from .models import SlideView, StrictModel
from .storage import Store
from .version_history import HistoryPage, VersionHistory
from .visuals import CaptureOptions, snapshot


class VersionRestore(StrictModel):
    """An explicit restore anchored to the currently reviewed source."""

    version_id: str = Field(min_length=1, max_length=100)
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: str = Field(min_length=8, max_length=100)


def history_routes(current_store: Callable[[], Store]) -> APIRouter:
    """Expose metadata separately from selected previews and write operations."""
    router = APIRouter()

    def history() -> VersionHistory:
        """Resolve the configured workspace for every request."""
        store = current_store()
        return VersionHistory(store, ArtifactStore(store))

    @router.get("/api/decks/{deck_id}/slides/{slide_id}/history")
    def slide_history(
        deck_id: str,
        slide_id: str,
        cursor: str | None = None,
        include_all: bool = False,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ) -> HistoryPage:
        """Page through small save summaries without returning slide source."""
        return history().list_slide(
            deck_id, slide_id, cursor=cursor, limit=limit, include_all=include_all
        )

    @router.get("/api/decks/{deck_id}/slides/{slide_id}/history/preview")
    def slide_preview(deck_id: str, slide_id: str, version_id: str) -> HTMLResponse:
        """Render one saved slide without private notes, context or conversations."""
        service = history()
        selected = service.get_slide_version(deck_id, slide_id, version_id)
        deck = service.store.read(deck_id)
        number = next(i + 1 for i, slide in enumerate(deck.slides) if slide.id == slide_id)
        rendered = snapshot(service.store, deck_id, selected.slide, number, CaptureOptions())
        return HTMLResponse(
            rendered.document,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "sandbox allow-scripts; frame-ancestors 'self'",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post("/api/decks/{deck_id}/slides/{slide_id}/history/restore")
    def restore_slide(deck_id: str, slide_id: str, body: VersionRestore) -> SlideView:
        """Create a new save while preserving present notes and slide position."""
        return history().restore_slide(
            deck_id, slide_id, body.version_id, body.expected_hash, body.request_id
        )

    @router.get("/api/decks/{deck_id}/slides/{slide_id}/history/thumbnail")
    def slide_thumbnail(deck_id: str, slide_id: str, version_id: str) -> FileResponse:
        """Return a cached thumbnail of retained slide content using the current shared design."""
        path = history_thumbnail(
            history(), deck_id=deck_id, slide_id=slide_id, version_id=version_id
        )
        return FileResponse(path, media_type="image/png")

    @router.get("/api/artifacts/{artifact_id}/history")
    def artifact_history(
        artifact_id: str,
        cursor: str | None = None,
        include_all: bool = False,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ) -> HistoryPage:
        """Read a source-free, cursor-paginated bundle timeline."""
        return history().list_artifact(
            artifact_id, cursor=cursor, limit=limit, include_all=include_all
        )

    @router.post("/api/artifacts/{artifact_id}/history/restore")
    def restore_artifact(artifact_id: str, body: VersionRestore) -> ArtifactView:
        """Copy a retained bundle into a new current revision with concurrency checks."""
        return history().restore_artifact(
            artifact_id, body.version_id, body.expected_hash, body.request_id
        )

    @router.get("/api/artifacts/{artifact_id}/history/thumbnail")
    def artifact_thumbnail(artifact_id: str, version_id: str) -> FileResponse:
        """Return a small image of an exact retained visual bundle."""
        path = history_thumbnail(history(), artifact_id=artifact_id, version_id=version_id)
        return FileResponse(path, media_type="image/png")

    return router
