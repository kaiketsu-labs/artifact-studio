"""Compact agent responses without weakening source, rule or revision access."""

import hashlib
import json

from .models import Deck, DeckReceipt, FeedbackFeed, SlideReceipt, SlideView


def rules_hash(instructions: str, preferences: str) -> str:
    """Fingerprint the complete primary and secondary guidance together."""
    document = json.dumps([instructions, preferences], ensure_ascii=False)
    return hashlib.sha256(document.encode()).hexdigest()


def rule_fields(
    instructions: str, preferences: str, known_rules_hash: str = ""
) -> dict[str, object]:
    """Return complete guidance unless the caller explicitly retains its exact version."""
    token = rules_hash(instructions, preferences)
    retained = bool(known_rules_hash) and known_rules_hash == token
    return {
        "authoring_instructions": "" if retained else instructions,
        "learned_preferences": "" if retained else preferences,
        "rules_hash": token,
        "rules_included": not retained,
    }


def guide_fields(markdown: str, known_hash: str = "") -> dict[str, object]:
    """Omit only a guide the caller explicitly retains, independently of user rules."""
    token = hashlib.sha256(markdown.encode()).hexdigest()
    retained = bool(known_hash) and known_hash == token
    return {
        "guide": "" if retained else markdown,
        "guide_hash": token,
        "guide_included": not retained,
    }


def feedback_response(
    feed: FeedbackFeed, known_rules_hash: str = "", known_design_requests_hash: str = ""
) -> FeedbackFeed:
    """Omit explicitly retained rules or queued requests without acknowledging either."""
    document = json.dumps(
        [item.model_dump(mode="json") for item in feed.design_requests],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    token = hashlib.sha256(document.encode()).hexdigest()
    retained = bool(known_design_requests_hash) and known_design_requests_hash == token
    return feed.model_copy(
        update={
            **rule_fields(feed.authoring_instructions, feed.learned_preferences, known_rules_hash),
            "design_requests": [] if retained else feed.design_requests,
            "design_requests_hash": token,
            "design_requests_included": not retained,
        }
    )


def deck_receipt(deck: Deck) -> DeckReceipt:
    """Retain stable deck and slide identities without source, notes or embedded binaries."""
    return DeckReceipt(
        id=deck.id,
        title=deck.title,
        design_id=deck.design_id,
        slide_ids=[slide.id for slide in deck.slides],
    )


def slide_receipt(deck_id: str, slide: SlideView) -> SlideReceipt:
    """Acknowledge a mutation without echoing source code or conversation history."""
    return SlideReceipt(
        deck_id=deck_id,
        id=slide.id,
        title=slide.title,
        revision=slide.revision,
        expected_hash=slide.expected_hash,
        content_hash=slide.content_hash,
        approved=slide.approved,
        hidden=slide.hidden,
        has_unseen_changes=slide.has_unseen_changes,
    )
