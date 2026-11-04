"""Explicit browser downloads of version-bound selected objects."""

from collections.abc import Callable

from fastapi import APIRouter, Request, Response

from .artifact_capture import export_artifact_selection
from .artifacts import ArtifactStore
from .selection_image import ArtifactSelectionImageInput, SelectionImage, SelectionImageInput
from .storage import ConflictError, Store
from .visuals import CaptureOptions, export_snapshot, snapshot


def image_response(image: SelectionImage) -> Response:
    """Offer a local attachment without storing exports or exposing review data."""
    return Response(
        image.data,
        media_type=image.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{image.filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def image_routes(get_store: Callable[[], Store]) -> APIRouter:
    """Reuse the current workspace and existing sandboxed renderers for user image downloads."""
    router = APIRouter()

    @router.post("/api/decks/{deck_id}/slides/{slide_id}/selection-image")
    def slide_image(deck_id: str, slide_id: str, body: SelectionImageInput) -> Response:
        """Export a displayed selection after checking its original slide identity."""
        store = get_store()
        deck = store.read(deck_id)
        slide = store.locate(deck, slide_id)
        if store.content_hash(deck_id, slide) != body.content_hash:
            raise ConflictError("The slide changed. Refresh and select the objects again.")
        number = next(index + 1 for index, item in enumerate(deck.slides) if item.id == slide_id)
        prepared = snapshot(
            store,
            deck_id,
            slide,
            number,
            CaptureOptions(reduced_motion=True),
            viewport=(body.width, body.height),
            preview_html=body.preview_html,
        )
        return image_response(export_snapshot(prepared, body))

    @router.post("/api/artifacts/{artifact_id}/selection-image")
    def artifact_image(
        artifact_id: str, body: ArtifactSelectionImageInput, request: Request
    ) -> Response:
        """Export only the revision displayed when the user invoked the object menu."""
        registry = ArtifactStore(get_store())
        artifact = registry.get_artifact_revision(artifact_id, body.revision)
        if artifact.content_hash != body.content_hash:
            raise ConflictError("The artifact version changed. Select the objects again.")
        origin = str(request.base_url).rstrip("/")
        return image_response(export_artifact_selection(registry, artifact, body, origin))

    return router
