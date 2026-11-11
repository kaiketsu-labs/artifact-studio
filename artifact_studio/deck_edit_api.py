"""The standard deck editing API for local and grant-scoped editors."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Annotated

from fastapi import APIRouter, Path, Request

from .models import (
    ApprovalInput,
    NotesInput,
    SectionLayoutInput,
    SlideActionInput,
    SlideOrderUpdateInput,
    SlideRestoreInput,
    SlidesVisibilityInput,
    SlideVisibilityInput,
)
from .multiplayer_access import Role
from .sections import Sections, section_layout
from .slide_actions import SlideActionResult, SlideActions
from .storage import Store

DeckID = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")]
SlideID = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")]


def deck_edit_routes(
    access: Callable[[Request, str, Role], AbstractContextManager[Store]],
) -> APIRouter:
    """Use the caller's access guard for the duration of each edit or recovery."""
    router = APIRouter(prefix="/api/decks/{deck_id}")

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
