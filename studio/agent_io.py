"""Compact agent responses without weakening source, rule or revision access."""

import hashlib
import json

from .models import FeedbackFeed, SlideReceipt, SlideView


def rules_hash(instructions: str, preferences: str) -> str:
    """Fingerprint the complete primary and secondary guidance together."""
    document = json.dumps([instructions, preferences], ensure_ascii=False)
    return hashlib.sha256(document.encode()).hexdigest()


def feedback_response(feed: FeedbackFeed, known_rules_hash: str = "") -> FeedbackFeed:
    """Omit unchanged rules only when a caller explicitly retains that exact version."""
    token = rules_hash(feed.authoring_instructions, feed.learned_preferences)
    retained = bool(known_rules_hash) and known_rules_hash == token
    updates: dict[str, object] = {"rules_hash": token, "rules_included": not retained}
    if retained:
        updates |= {"authoring_instructions": "", "learned_preferences": ""}
    return feed.model_copy(update=updates)


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
