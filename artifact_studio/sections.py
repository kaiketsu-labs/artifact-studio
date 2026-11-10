"""Named slide groups without changing slide content, review or audience state."""

import json
from typing import TYPE_CHECKING

from .models import Deck, DeckSection
from .storage import ConflictError, digest

if TYPE_CHECKING:
    from .storage import Store


def sections_hash(sections: list[DeckSection]) -> str:
    """Guard names, membership and order independently from slide content."""
    return digest(json.dumps([item.model_dump() for item in sections], sort_keys=True))


def section_layout(deck: Deck) -> dict[str, object]:
    """A compact acknowledgement shared by browser and agent layout writers."""
    return {
        "slide_ids": [slide.id for slide in deck.slides],
        "sections": [section.model_dump() for section in deck.sections],
        "sections_hash": sections_hash(deck.sections),
    }


def reconcile(sections: list[DeckSection], slide_ids: list[str]) -> list[DeckSection]:
    """Keep additions with their preceding slide and retain empty/deleted groups."""
    if not sections:
        return []
    membership = {slide_id: group.id for group in sections for slide_id in group.slide_ids}
    current = sections[0].id
    groups: dict[str, list[str]] = {group.id: [] for group in sections}
    for slide_id in slide_ids:
        current = membership.get(slide_id, current)
        groups[current].append(slide_id)
    result = [item.model_copy(update={"slide_ids": groups[item.id]}) for item in sections]
    if [item for group in result for item in group.slide_ids] != slide_ids:
        raise ConflictError("This move crosses sections. Include the updated section layout.")
    return result


class Sections:
    """Atomic guarded updates to the presentation's canonical section layout."""

    def __init__(self, store: Store) -> None:
        """Use the same edit lock as structural and agent writes."""
        self.store = store

    def replace(
        self,
        deck_id: str,
        slide_ids: list[str],
        sections: list[DeckSection],
        expected_slide_ids: list[str],
        expected_sections_hash: str,
    ) -> Deck:
        """Reorder existing slides and groups while merging current content and notes."""
        with self.store.editing(deck_id) as db:
            current = self.store.read(deck_id)
            current_ids = [slide.id for slide in current.slides]
            if len(slide_ids) != len(current_ids) or set(slide_ids) != set(current_ids):
                raise ValueError("Keep every slide exactly once in the section layout.")
            lookup = {slide.id: slide for slide in current.slides}
            updated = Deck.model_validate(
                {
                    **current.model_dump(),
                    "slides": [lookup[slide_id].model_dump() for slide_id in slide_ids],
                    "sections": [section.model_dump() for section in sections],
                }
            )
            if updated == current:
                return current
            if (
                current_ids != expected_slide_ids
                or sections_hash(current.sections) != expected_sections_hash
            ):
                raise ConflictError("Sections or slide order changed. Refresh and try again.")
            if current_ids != slide_ids:
                _ = db.execute(
                    "UPDATE feedback SET context_slide_id=? WHERE deck=? "
                    "AND COALESCE(slide,'')='' AND context_slide_id IS NULL AND reply_to IS NULL",
                    (current_ids[0], deck_id),
                )
            self.store.write(deck_id, updated)
            return updated
