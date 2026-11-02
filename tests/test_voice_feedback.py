"""Portable unsent dictated comments retain their original selection and retry identity."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from studio.models import ElementGroupTarget, ElementTarget
from studio.projects import (
    ClientState,
    FeedbackDraft,
    VoiceFeedbackDraft,
    open_project,
    save_project,
)
from studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design


def voice_draft() -> VoiceFeedbackDraft:
    """Make a bounded fictional multi-object draft without submitting feedback."""
    return VoiceFeedbackDraft(
        request_id="original-voice-request",
        message="Align both objects.",
        slide_id="cover",
        context_slide_id="cover",
        content_hash="original-visual-hash",
        target=ElementGroupTarget(
            label="2 objects",
            elements=[
                ElementTarget(
                    selector="#heading", label="Heading", x=0.1, y=0.2, width=0.5, height=0.1
                ),
                ElementTarget(
                    selector="#chart", label="Chart", x=0.1, y=0.4, width=0.7, height=0.4
                ),
            ],
        ),
    )


def test_unsent_voice_context_survives_portable_project_copy(tmp_path: Path) -> None:
    """Copying to a different deck preserves voice anchors without releasing feedback."""
    make_design(tmp_path / "source" / TEMPLATE_ID)
    store = Store(tmp_path / "source", tmp_path / "source-state/review.sqlite3")
    segment = voice_draft()
    draft = FeedbackDraft(
        feedback="",
        slide_id="cover",
        context_slide_id="cover",
        content_hash="current-draft-hash",
        request_id="typed-draft-request",
        voice_segments=[segment],
    )
    state = ClientState(active_slide_id="cover", feedback={"cover": draft})
    archive = save_project(store, TEMPLATE_ID, state)
    target = Store(tmp_path / "target", tmp_path / "target-state/review.sqlite3")
    make_design(target.root / TEMPLATE_ID)
    restored, recovered = open_project(target, archive)
    assert restored.id != TEMPLATE_ID
    assert recovered.feedback["cover"].voice_segments == [segment]
    assert recovered.feedback["cover"].feedback == ""
    assert store.feedback_feed(TEMPLATE_ID).events == []
    assert target.feedback_feed(restored.id).events == []


def test_portable_voice_drafts_reject_changed_shapes_and_ambiguous_ids() -> None:
    """Malformed saved state must not acquire an unintended target or retry identity."""
    segment = voice_draft()
    for changes in (
        {"request_id": "short"},
        {"message": "x" * 10_001},
        {"content_hash": "x" * 129},
        {"slide_id": "../wrong"},
        {"unknown": "extra"},
    ):
        with pytest.raises(ValidationError):
            _ = VoiceFeedbackDraft.model_validate(segment.model_dump() | changes)
    for segments in ([segment, segment], [segment] * 101):
        with pytest.raises(ValidationError):
            _ = FeedbackDraft(
                feedback="",
                slide_id="cover",
                content_hash="hash",
                request_id="typed-draft-request",
                voice_segments=segments,
            )
