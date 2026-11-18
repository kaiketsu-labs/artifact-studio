"""The standard deck editing API for local and grant-scoped editors."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Annotated

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from .models import (
    ApprovalInput,
    NotesInput,
    SectionLayoutInput,
    Slide,
    SlideActionInput,
    SlideOrderUpdateInput,
    SlidePatch,
    SlideRestoreInput,
    SlidesVisibilityInput,
    SlideVisibilityInput,
)
from .multiplayer_access import Role
from .sections import Sections, section_layout
from .slide_actions import SlideActionResult, SlideActions
from .storage import Store


class ImportedTextEdit(BaseModel):
    """An exact-revision edit to one imported text leaf."""

    model_config = ConfigDict(extra="forbid")
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: str = Field(min_length=8, max_length=100)
    path: list[int] = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=9000)


class ImportedTransform(BaseModel):
    """A bounded transform on one imported top-level object."""

    model_config = ConfigDict(extra="forbid")
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: str = Field(min_length=8, max_length=100)
    index: int = Field(ge=0)
    dx: float = Field(ge=-2, le=2, allow_inf_nan=False)
    dy: float = Field(ge=-2, le=2, allow_inf_nan=False)
    rotation: float = Field(ge=-180, le=180, allow_inf_nan=False)


DeckID = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")]
SlideID = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")]


def deck_edit_routes(
    access: Callable[[Request, str, Role], AbstractContextManager[Store]],
) -> APIRouter:
    """Use the caller's access guard for the duration of each edit or recovery."""
    router = APIRouter(prefix="/api/decks/{deck_id}")

    @router.post("/slides/{slide_id}/imported-text")
    def imported_text(
        request: Request, deck_id: DeckID, slide_id: SlideID, body: ImportedTextEdit
    ) -> dict[str, bool]:
        """Save an imported text leaf with the same access and revision guards as other edits."""
        from .powerpoint.edit import edit_pptx_text, synchronized_title

        def derive(slide: Slide) -> SlidePatch:
            updated = edit_pptx_text(slide.html, body.path, body.text)
            return SlidePatch(
                html=updated, title=synchronized_title(slide.html, updated, body.path, slide.title)
            )

        with access(request, deck_id, "edit") as store:
            store.revise_derived(
                deck_id,
                slide_id,
                body.expected_hash,
                body.request_id,
                "imported-text:" + body.model_dump_json(exclude={"request_id", "expected_hash"}),
                derive,
            )
        return {"saved": True}

    @router.post("/slides/{slide_id}/imported-transform")
    def imported_transform(
        request: Request, deck_id: DeckID, slide_id: SlideID, body: ImportedTransform
    ) -> dict[str, bool]:
        """Save one imported shape transform with revision and access checks."""
        from .powerpoint.edit import transform_pptx_object

        def derive(slide: Slide) -> SlidePatch:
            return SlidePatch(
                html=transform_pptx_object(slide.html, body.index, body.dx, body.dy, body.rotation)
            )

        with access(request, deck_id, "edit") as store:
            store.revise_derived(
                deck_id,
                slide_id,
                body.expected_hash,
                body.request_id,
                "imported-transform:"
                + body.model_dump_json(exclude={"request_id", "expected_hash"}),
                derive,
            )
        return {"saved": True}

    @router.post("/slides/{slide_id}/visibility")
    def slide_visibility(
        request: Request, deck_id: DeckID, slide_id: SlideID, body: SlideVisibilityInput
    ) -> dict[str, bool]:
        """Hide or unhide a slide in the slideshow without changing its review state."""
        with access(request, deck_id, "edit") as store:
            store.set_slide_hidden(
                deck_id, slide_id, hidden=body.hidden, expected_hidden=body.expected_hidden
            )
        return {"hidden": body.hidden}

    @router.post("/slide-order")
    def slide_order(
        request: Request, deck_id: DeckID, body: SlideOrderUpdateInput
    ) -> dict[str, object]:
        """Move slides without replacing concurrent content, notes or review state."""
        with access(request, deck_id, "edit") as store:
            updated = (
                Sections(store).replace(
                    deck_id,
                    body.slide_ids,
                    body.sections,
                    body.expected_slide_ids,
                    body.expected_sections_hash,
                )
                if body.sections is not None
                else store.reorder(
                    deck_id, body.slide_ids, expected_slide_ids=body.expected_slide_ids
                )
            )
        if body.sections is None and not updated.sections:
            return {"slide_ids": [slide.id for slide in updated.slides]}
        return section_layout(updated)

    @router.post("/sections")
    def presentation_sections(
        request: Request, deck_id: DeckID, body: SectionLayoutInput
    ) -> dict[str, object]:
        """Save names and grouping using the structure the human actually reviewed."""
        with access(request, deck_id, "edit") as store:
            return section_layout(
                Sections(store).replace(
                    deck_id,
                    body.slide_ids,
                    body.sections,
                    body.expected_slide_ids,
                    body.expected_sections_hash,
                )
            )

    @router.post("/slide-actions")
    def slide_action(
        request: Request, deck_id: DeckID, body: SlideActionInput
    ) -> SlideActionResult:
        """Add, copy or recoverably delete the selected slides from the human workspace."""
        with access(request, deck_id, "edit") as store:
            actions = SlideActions(store)
            if body.action == "add":
                if len(body.slide_ids) != 1:
                    raise ValueError("Choose one slide to insert after.")
                return actions.add(
                    deck_id, body.slide_ids[0], body.expected_slide_ids, body.request_id
                )
            operation = actions.duplicate if body.action == "duplicate" else actions.delete
            return operation(
                deck_id,
                body.slide_ids,
                body.expected_slide_ids,
                body.expected_hashes,
                body.request_id,
            )

    @router.get("/deleted-slides")
    def deleted_slides(request: Request, deck_id: DeckID) -> list[dict[str, object]]:
        """List recoverable deletions with titles for the private human restore picker."""
        # Listing may finish a pending structural write before reading the deletion journal.
        with access(request, deck_id, "edit") as store:
            return SlideActions(store).deletion_summaries(deck_id)

    @router.post("/restore-slides")
    def restore_slides(
        request: Request, deck_id: DeckID, body: SlideRestoreInput
    ) -> SlideActionResult:
        """Restore a deleted group at its surviving neighbors, keeping its original history."""
        with access(request, deck_id, "edit") as store:
            return SlideActions(store).restore(deck_id, body.deletion_id, body.request_id)

    @router.post("/slides-visibility")
    def slides_visibility(
        request: Request, deck_id: DeckID, body: SlidesVisibilityInput
    ) -> dict[str, bool]:
        """Hide or show the complete human selection as one conflict-checked write."""
        with access(request, deck_id, "edit") as store:
            store.set_slides_hidden(
                deck_id, body.slide_ids, hidden=body.hidden, expected_hidden=body.expected_hidden
            )
        return {"hidden": body.hidden}

    @router.post("/slides/{slide_id}/approve")
    def approve(
        request: Request, deck_id: DeckID, slide_id: SlideID, body: ApprovalInput
    ) -> dict[str, bool]:
        """Approve an exact version through the human workspace."""
        with access(request, deck_id, "edit") as store:
            store.approve(deck_id, slide_id, body.content_hash, body.approved)
        return {"saved": True}

    @router.post("/slides/{slide_id}/notes")
    def notes(
        request: Request, deck_id: DeckID, slide_id: SlideID, body: NotesInput
    ) -> dict[str, bool | int]:
        """Save speaker notes without resetting approval or overwriting newer notes."""
        with access(request, deck_id, "edit") as store:
            revision = store.save_notes(
                deck_id, slide_id, body.notes, body.expected_notes, body.expected_revision
            )
        return {"saved": True, "notes_revision": revision}

    return router
