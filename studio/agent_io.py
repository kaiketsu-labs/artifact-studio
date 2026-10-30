"""Compact agent responses without weakening source, rule or revision access."""

import hashlib
import json

from .models import FeedbackFeed, SlideReceipt, SlideView


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


def feedback_response(feed: FeedbackFeed, known_rules_hash: str = "") -> FeedbackFeed:
    """Apply shared exact-version guidance handling to released feedback."""
    return feed.model_copy(
        update=rule_fields(feed.authoring_instructions, feed.learned_preferences, known_rules_hash)
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
