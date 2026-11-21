"""Agent outline reads share one canonical deck parse and review baseline."""

from typing import Annotated

from pydantic import Field

from .agent_io import rule_fields
from .delivery_reviews import begin_design_self_review
from .models import SlideReceipt, presentation_delivery_hash
from .questions import Questions
from .sections import sections_hash
from .storage import Store, digest

SourceSlideIDs = Annotated[tuple[str, ...], Field(max_length=8)]


def presentation_outline(
    store: Store,
    deck_id: str,
    source_slide_ids: SourceSlideIDs = (),
    *,
    include_context: bool = True,
    local: bool = False,
    known_rules_hash: str = "",
    include_agent: bool = True,
    begin_self_review: bool = False,
    include_design_id: bool = False,
) -> dict[str, object]:
    """Read outline fields and explicitly selected sources, without loading review history."""
    deck = store.read(deck_id)
    sources: dict[str, object] = {}
    if source_slide_ids:
        if len(source_slide_ids) > 8 or len(set(source_slide_ids)) != len(source_slide_ids):
            raise ValueError("Provide between 1 and 8 distinct slide IDs.")
        excluded = {"id", "title", "revision", "hidden"}
        if not include_agent:
            excluded.add("agent")
        for slide_id in source_slide_ids:
            sources[slide_id] = store.locate(deck, slide_id).model_dump(exclude=excluded)

    design_hash = store.visual_hash(deck_id)
    review_hashes = store.review_hashes(deck_id)
    context_file = store.folder(deck_id) / "README.md"
    context = context_file.read_text() if context_file.exists() else ""
    slides = [
        SlideReceipt(
            deck_id=deck_id,
            id=slide.id,
            title=slide.title,
            revision=slide.revision,
            hidden=slide.hidden,
            **store.slide_review_state(
                deck_id, slide, design_hash=design_hash, review_hashes=review_hashes
            ),
        )
        for slide in deck.slides
    ]
    delivery_hash = presentation_delivery_hash(
        deck.title, ((slide.id, slide.hidden, slide.content_hash) for slide in slides)
    )
    if begin_self_review:
        begin_design_self_review(store, deck_id, delivery_hash)
    result: dict[str, object] = {
        "id": deck_id,
        "title": deck.title,
        "delivery_hash": delivery_hash,
        "slides": [slide.model_dump() for slide in slides],
        "sections": [item.model_dump() for item in deck.sections],
        "sections_hash": sections_hash(deck.sections),
        "context_hash": digest(context),
    }
    if include_design_id:
        result["design_id"] = deck.design_id
    if local:
        result.update(
            design_id=deck.design_id,
            open_questions=Questions(store).open_ids(deck_id, [slide.id for slide in deck.slides]),
            context_included=include_context,
            context_characters=len(context),
            brief_files=[item.model_dump() for item in store.brief_files(deck_id)],
            **rule_fields(
                store.authoring_instructions(), store.learned_preferences(), known_rules_hash
            ),
        )
    if include_context:
        result["context_markdown"] = context
    if sources:
        result["slide_sources"] = sources
    return result
