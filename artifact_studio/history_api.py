"""Human-facing, lazy version previews and optimistic, non-destructive restores."""

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import Field

from .artifact_models import ArtifactView
from .artifacts import ArtifactStore
from .history_thumbnails import history_thumbnail
from .models import Slide, SlideView, StrictModel
from .multiplayer_access import Role
from .storage import SlideMarkup, Store
from .version_history import HistoryPage, VersionHistory
from .visuals import CaptureOptions, ScopedDeckEmbedder, snapshot

type ScopedHistoryAccess = Callable[
    [Request, Literal["deck", "artifact"], str, Role], AbstractContextManager[Store]
]

SHARED_SLIDE_FIDELITY = (
    "This preview uses the current theme and assets. Interactive versions show their static slide."
)


class VersionRestore(StrictModel):
    """An explicit restore anchored to the currently reviewed source."""

    version_id: str = Field(min_length=1, max_length=100)
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: str = Field(min_length=8, max_length=100)


def history_routes(
    current_store: Callable[[], Store], *, scoped_access: ScopedHistoryAccess | None = None
) -> APIRouter:
    """Expose metadata separately from selected previews and write operations."""
    router = APIRouter()

    def history(store: Store) -> VersionHistory:
        """Use only the workspace resolved for this request."""
        return VersionHistory(store, ArtifactStore(store))

    def access(
        request: Request, identifier: str, role: Role, *, kind: Literal["deck", "artifact"] = "deck"
    ) -> AbstractContextManager[Store]:
        """Keep the selected grant active for the duration of a read or write."""
        if scoped_access is not None:
            return scoped_access(request, kind, identifier, role)
        return nullcontext(current_store())

    @router.get("/api/decks/{deck_id}/slides/{slide_id}/history")
    def slide_history(
        request: Request,
        deck_id: str,
        slide_id: str,
        cursor: str | None = None,
        include_all: bool = False,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ) -> HistoryPage:
        """Page through small save summaries without returning slide source."""
        with access(request, deck_id, "view") as store:
            page = history(store).list_slide(
                deck_id, slide_id, cursor=cursor, limit=limit, include_all=include_all
            )
        if scoped_access is not None:
            page.fidelity_warning = SHARED_SLIDE_FIDELITY
            for item in page.items:
                item.size_bytes = 0
        return page

    @router.get("/api/decks/{deck_id}/slides/{slide_id}/history/preview")
    def slide_preview(
        request: Request, deck_id: str, slide_id: str, version_id: str
    ) -> HTMLResponse:
        """Render one saved slide without private notes, context or conversations."""
        with access(request, deck_id, "view") as store:
            service = history(store)
            slide = service.get_slide_version(deck_id, slide_id, version_id).slide
            deck = store.read(deck_id)
            number = next(i + 1 for i, slide in enumerate(deck.slides) if slide.id == slide_id)
            embedder = ScopedDeckEmbedder(store, deck_id) if scoped_access is not None else None
            rendered = snapshot(
                store, deck_id, slide, number, CaptureOptions(), scoped_embedder=embedder
            )
        headers = {
            "Cache-Control": "no-store",
            "Content-Security-Policy": "sandbox allow-scripts; frame-ancestors 'self'",
            "X-Content-Type-Options": "nosniff",
        }
        if scoped_access is not None:
            headers["Content-Security-Policy"] += "; worker-src 'none'; " + rendered.policy
            headers["Referrer-Policy"] = "no-referrer"
        return HTMLResponse(rendered.document, headers=headers)

    @router.post(
        "/api/decks/{deck_id}/slides/{slide_id}/history/restore",
        response_model=SlideView if scoped_access is None else dict[str, bool],
    )
    def restore_slide(
        request: Request, deck_id: str, slide_id: str, body: VersionRestore
    ) -> SlideView | dict[str, bool]:
        """Create a new save while preserving present notes and slide position."""
        with access(request, deck_id, "edit") as store:
            service = history(store)
            if scoped_access is None:
                return service.restore_slide(
                    deck_id, slide_id, body.version_id, body.expected_hash, body.request_id
                )

            def validate_audience(slide: Slide) -> None:
                """Keep references to former private assets out of the restored presentation."""
                SlideMarkup().feed(slide.html)
                embedder = ScopedDeckEmbedder(store, deck_id)
                _ = embedder.css(embedder.local_path("theme.css").read_text())
                _ = embedder.markup(slide.html)
                if slide.interactive_html:
                    _ = embedder.markup(slide.interactive_html)

            _ = service.restore_slide(
                deck_id,
                slide_id,
                body.version_id,
                body.expected_hash,
                body.request_id,
                validate_audience=validate_audience,
            )
            return {"saved": True}

    @router.get("/api/decks/{deck_id}/slides/{slide_id}/history/thumbnail")
    def slide_thumbnail(
        request: Request, deck_id: str, slide_id: str, version_id: str
    ) -> FileResponse:
        """Return a cached thumbnail of retained slide content using the current shared design."""
        with access(request, deck_id, "view") as store:
            embedder = ScopedDeckEmbedder(store, deck_id) if scoped_access is not None else None
            path = history_thumbnail(
                history(store),
                deck_id=deck_id,
                slide_id=slide_id,
                version_id=version_id,
                scoped_embedder=embedder,
            )
        return FileResponse(path, media_type="image/png")

    @router.get("/api/artifacts/{artifact_id}/history")
    def artifact_history(
        request: Request,
        artifact_id: str,
        cursor: str | None = None,
        include_all: bool = False,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ) -> HistoryPage:
        """Read a source-free, cursor-paginated bundle timeline."""
        with access(request, artifact_id, "view", kind="artifact") as store:
            return history(store).list_artifact(
                artifact_id, cursor=cursor, limit=limit, include_all=include_all
            )

    @router.post(
        "/api/artifacts/{artifact_id}/history/restore",
        response_model=ArtifactView if scoped_access is None else dict[str, bool],
    )
    def restore_artifact(
        request: Request, artifact_id: str, body: VersionRestore
    ) -> ArtifactView | dict[str, bool]:
        """Copy a retained bundle into a new current revision with concurrency checks."""
        with access(request, artifact_id, "edit", kind="artifact") as store:
            saved = history(store).restore_artifact(
                artifact_id, body.version_id, body.expected_hash, body.request_id
            )
            return saved if scoped_access is None else {"saved": True}

    @router.get("/api/artifacts/{artifact_id}/history/thumbnail")
    def artifact_thumbnail(request: Request, artifact_id: str, version_id: str) -> FileResponse:
        """Return a small image of an exact retained visual bundle."""
        with access(request, artifact_id, "view", kind="artifact") as store:
            service = history(store)
        path = history_thumbnail(service, artifact_id=artifact_id, version_id=version_id)
        if scoped_access is not None:
            # Rendering can take several seconds. Recheck access before returning its bytes.
            with access(request, artifact_id, "view", kind="artifact"):
                return FileResponse(
                    path, media_type="image/png", headers={"Cache-Control": "no-store"}
                )
        return FileResponse(path, media_type="image/png")

    return router
