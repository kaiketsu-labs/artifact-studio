"""Proposal comments retain the exact fragment and full-slide preview that was reviewed."""

import base64
from pathlib import Path

import pytest
from pydantic import ValidationError

from artifact_studio.element_alternatives import ElementAlternatives, ElementInput, ElementSet
from artifact_studio.models import CommentInput, EditIntent, ElementTarget, FileInput
from artifact_studio.projects import ClientState, open_project, save_project
from artifact_studio.slide_designs import DecideDesign, DesignInput, SlideDesigns
from artifact_studio.storage import ConflictError, Store
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def alternatives(tmp_path: Path) -> ElementAlternatives:
    """Use independently authored, static elements and private human notes."""
    root = tmp_path / "collection"
    make_design(root / TEMPLATE_ID)
    store = Store(root, tmp_path / "state/review.sqlite3")
    deck = store.read(TEMPLATE_ID)
    deck.slides[0].html = (
        '<article class="slide"><h1 id="headline">Original message</h1>'
        '<p id="body">Original supporting text.</p></article>'
    )
    deck.slides[0].notes = "PRIVATE_SPEAKER_NOTE"
    store.write(TEMPLATE_ID, deck)
    return ElementAlternatives(store)


def publish(service: ElementAlternatives) -> ElementSet:
    """Publish the same immutable headline proposal used by each feedback journey."""
    current = service.store.locate(service.store.read(TEMPLATE_ID), "cover")
    return service.publish(
        TEMPLATE_ID,
        "cover",
        "headline",
        "headline",
        "Headline",
        service.store.content_hash(TEMPLATE_ID, current),
        "publish-feedback-headlines",
        [ElementInput(name="Clearer", html='<h1 id="headline">A clearer message.</h1>')],
    )


def choose_body(service: ElementAlternatives) -> None:
    """Change a disjoint element so the headline gains a different full-slide context."""
    current = service.store.locate(service.store.read(TEMPLATE_ID), "cover")
    group = service.publish(
        TEMPLATE_ID,
        "cover",
        "body",
        "text",
        "Supporting text",
        service.store.content_hash(TEMPLATE_ID, current),
        "publish-feedback-body",
        [ElementInput(name="Concise", html='<p id="body">A shorter explanation.</p>')],
    )
    _ = service.decide(
        TEMPLATE_ID,
        "cover",
        DecideDesign(
            set_id=group.id,
            option_id=group.options[1].id,
            expected_content_hash=service.store.content_hash(TEMPLATE_ID, current),
            request_id="choose-feedback-body",
        ),
    )


def test_private_batch_files_and_preview_reference_survive_release_and_restart(
    alternatives: ElementAlternatives,
) -> None:
    """An attachment belongs to one saved preview and stays private until Batch is sent."""
    group = publish(alternatives)
    source = alternatives.read(TEMPLATE_ID, "cover", group.options[1].id)
    store = alternatives.store
    _ = store.set_mode(TEMPLATE_ID, "batch")
    event = store.add_feedback(
        TEMPLATE_ID,
        "cover",
        "Refine this wording using the attached brief.",
        source.option.source_hash,
        "private-element-feedback",
        element_option_id=source.option.id,
        element_context_hash=source.context_hash,
        files=[FileInput(name="brief.txt", data_base64=base64.b64encode(b"Brief").decode())],
        expected_mode="batch",
    )
    pending = store.comments(TEMPLATE_ID)[-1]
    assert str(pending.event_id) == event and not pending.released
    assert pending.element_option_id == source.option.id
    assert pending.element_option_name == "Clearer"
    assert pending.content_hash == source.option.source_hash
    assert pending.element_context_hash == source.context_hash
    assert pending.target is None and pending.design_option_id is None
    assert pending.files[0].name == "brief.txt"
    assert store.feedback_feed(TEMPLATE_ID).events == []
    assert all(
        item.event_id != pending.event_id
        for item in store.view(TEMPLATE_ID, include_pending=False).slides[0].feedback
    )
    assert "PRIVATE_SPEAKER_NOTE" not in source.model_dump_json()
    assert (
        "PRIVATE_SPEAKER_NOTE"
        not in alternatives.preview(
            TEMPLATE_ID, "cover", source.option.id, context_hash=source.context_hash
        ).model_dump_json()
    )
    _ = store.release_batch(TEMPLATE_ID)
    restored = Store(store.root, store.database)
    delivered = restored.feedback_feed(TEMPLATE_ID).events
    assert len(delivered) == 1 and delivered[0].released
    assert delivered[0].element_context_hash == pending.element_context_hash
    assert delivered[0].element_option_id == pending.element_option_id
    assert delivered[0].files == pending.files


def test_feedback_pins_old_composition_after_a_disjoint_choice(
    alternatives: ElementAlternatives,
) -> None:
    """Recomposing a reusable fragment never reinterprets a comment on an earlier preview."""
    group = publish(alternatives)
    source = alternatives.read(TEMPLATE_ID, "cover", group.options[1].id)
    frozen = alternatives.preview(
        TEMPLATE_ID, "cover", source.option.id, context_hash=source.context_hash
    )
    choose_body(alternatives)
    latest = alternatives.read(TEMPLATE_ID, "cover", source.option.id)
    assert latest.context_hash != source.context_hash
    assert latest.option.source_hash == source.option.source_hash
    _ = alternatives.store.add_feedback(
        TEMPLATE_ID,
        "cover",
        "I mean this earlier composition.",
        source.option.source_hash,
        "earlier-element-composition",
        element_option_id=source.option.id,
        element_context_hash=source.context_hash,
    )
    saved = alternatives.store.comments(TEMPLATE_ID)[-1]
    assert saved.element_context_hash == source.context_hash
    assert (
        alternatives.preview(
            TEMPLATE_ID, "cover", source.option.id, context_hash=saved.element_context_hash
        )
        == frozen
    )


def test_implicit_context_and_exact_retry_keep_the_first_receipt(
    alternatives: ElementAlternatives,
) -> None:
    """Legacy callers may omit the preview token; the first write pins it durably."""
    group = publish(alternatives)
    source = alternatives.read(TEMPLATE_ID, "cover", group.options[1].id)
    store = alternatives.store
    first = store.add_feedback(
        TEMPLATE_ID,
        "cover",
        "Refine the option.",
        source.option.source_hash,
        "implicit-element-context",
        element_option_id=source.option.id,
    )
    assert store.comments(TEMPLATE_ID)[-1].element_context_hash == source.context_hash
    choose_body(alternatives)
    latest = alternatives.read(TEMPLATE_ID, "cover", source.option.id)
    assert latest.context_hash != source.context_hash
    retry = store.add_feedback(
        TEMPLATE_ID,
        "cover",
        "Refine the option.",
        source.option.source_hash,
        "implicit-element-context",
        element_option_id=source.option.id,
    )
    assert retry == first
    assert [item.event_id for item in store.comments(TEMPLATE_ID) if item.author == "user"] == [
        int(first)
    ]
    with pytest.raises(ConflictError, match="Request ID"):
        _ = store.add_feedback(
            TEMPLATE_ID,
            "cover",
            "Refine the option.",
            source.option.source_hash,
            "implicit-element-context",
            element_option_id=source.option.id,
            element_context_hash=latest.context_hash,
        )


def test_invalid_proposal_context_creates_no_feedback_receipt_or_attachment(
    alternatives: ElementAlternatives,
) -> None:
    """Unknown options, a different slide, source hashes and contexts are never guessed."""
    group = publish(alternatives)
    source = alternatives.read(TEMPLATE_ID, "cover", group.options[1].id)
    store = alternatives.store
    before = store.comments(TEMPLATE_ID)
    cases = [
        ("cover", "0" * 32, source.option.source_hash, source.context_hash),
        ("message", source.option.id, source.option.source_hash, source.context_hash),
        ("cover", source.option.id, "0" * 64, source.context_hash),
        ("cover", source.option.id, source.option.source_hash, "0" * 64),
    ]
    for index, (slide_id, option_id, source_hash, context_hash) in enumerate(cases):
        with pytest.raises(ConflictError):
            _ = store.add_feedback(
                TEMPLATE_ID,
                slide_id,
                "Invalid proposal context.",
                source_hash,
                f"invalid-element-feedback-{index}",
                element_option_id=option_id,
                element_context_hash=context_hash,
                files=[
                    FileInput(name="private.txt", data_base64=base64.b64encode(b"Private").decode())
                ],
            )
    assert store.comments(TEMPLATE_ID) == before
    assert not (store.database.parent / "feedback-files" / TEMPLATE_ID).exists()
    with store.connect() as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM operations WHERE request_id LIKE 'invalid-element-feedback-%'"
            ).fetchone()[0]
            == 0
        )


def test_element_and_whole_slide_feedback_references_are_distinct(
    alternatives: ElementAlternatives,
) -> None:
    """The retained whole-slide contract remains usable beside independent element choices."""
    group = publish(alternatives)
    store = alternatives.store
    current = store.locate(store.read(TEMPLATE_ID), "cover")
    whole = SlideDesigns(store).publish(
        TEMPLATE_ID,
        "cover",
        store.content_hash(TEMPLATE_ID, current),
        "publish-whole-feedback-options",
        [DesignInput(name="Whole design", html='<article class="slide">Whole</article>')],
    )
    option = whole.options[1]
    _ = store.add_feedback(
        TEMPLATE_ID,
        "cover",
        "Refine the whole design.",
        option.source_hash,
        "whole-design-feedback-contract",
        design_option_id=option.id,
    )
    assert store.comments(TEMPLATE_ID)[-1].design_option_name == "Whole design"
    assert store.comments(TEMPLATE_ID)[-1].element_option_id is None
    with pytest.raises(ValueError, match="one saved proposal"):
        _ = store.add_feedback(
            TEMPLATE_ID,
            "cover",
            "Ambiguous reference.",
            option.source_hash,
            "ambiguous-design-context",
            design_option_id=option.id,
            element_option_id=group.options[1].id,
        )


def test_canonical_edit_targets_cannot_be_attached_to_proposal_feedback(
    alternatives: ElementAlternatives,
) -> None:
    """Canonical selections and edit intents never get reinterpreted inside an alternative."""
    group = publish(alternatives)
    target = ElementTarget(selector="#headline", label="Headline", x=0, y=0, width=1, height=1)
    for with_intent in (False, True):
        with pytest.raises(ValueError, match="chosen slide"):
            _ = alternatives.store.add_feedback(
                TEMPLATE_ID,
                "cover",
                "Move this.",
                group.options[1].source_hash,
                f"proposal-edit-intent-{with_intent}",
                target=target,
                edit_intent=EditIntent(action="move") if with_intent else None,
                element_option_id=group.options[1].id,
            )


def test_comment_input_rejects_conflicting_proposal_anchors() -> None:
    """The HTTP schema rejects mixed proposal context before any file is saved."""
    with pytest.raises(ValidationError, match="one saved proposal"):
        _ = CommentInput(
            message="Refine",
            request_id="schema-proposal-feedback",
            content_hash="x",
            design_option_id="1" * 32,
            element_option_id="2" * 32,
        )
    with pytest.raises(ValidationError, match="needs its saved option"):
        _ = CommentInput(
            message="Refine",
            request_id="schema-proposal-feedback",
            content_hash="x",
            element_context_hash="3" * 64,
        )


@pytest.mark.parametrize("voice", [False, True])
def test_portable_project_rejects_unsent_proposal_drafts_before_losing_their_context(
    voice: bool,
) -> None:
    """A project without private proposal snapshots must not turn this into canonical feedback."""
    proposal = {
        "element_option_id": "1" * 32,
        "element_option_name": "Clearer",
        "element_context_hash": "2" * 64,
    }
    typed = {
        "feedback": "Refine this alternative.",
        "slide_id": "cover",
        "content_hash": "3" * 64,
        "request_id": "portable-element-feedback",
    }
    if voice:
        document = typed | {
            "feedback": "",
            "voice_segments": [
                {
                    "request_id": "portable-element-voice",
                    "message": "Keep this option's wording.",
                    "slide_id": "cover",
                    "content_hash": "3" * 64,
                }
                | proposal
            ],
        }
    else:
        document = typed | proposal
    with pytest.raises(ValidationError, match="Finish or clear proposal feedback"):
        _ = ClientState.model_validate({"feedback": {"cover": document}})


def test_portable_history_retains_identity_without_resolving_another_decks_private_option(
    alternatives: ElementAlternatives,
) -> None:
    """Imported discussion retains its original reference, while private source stays scoped."""
    group = publish(alternatives)
    source = alternatives.read(TEMPLATE_ID, "cover", group.options[1].id)
    store = alternatives.store
    _ = store.add_feedback(
        TEMPLATE_ID,
        "cover",
        "Refine this earlier option.",
        source.option.source_hash,
        "portable-historic-element-feedback",
        element_option_id=source.option.id,
        element_context_hash=source.context_hash,
    )
    archive = save_project(store, TEMPLATE_ID, ClientState())
    copied, _ = open_project(store, archive)
    assert copied.id != TEMPLATE_ID
    # The name on an imported comment belongs to the archive, never another deck's option row.
    with store.connect() as db:
        _ = db.execute(
            "UPDATE element_alternative_options SET name=? WHERE id=?",
            ("PRIVATE_OTHER_DECK_NAME", source.option.id),
        )
    imported = store.comments(copied.id)[-1]
    assert imported.element_option_id == source.option.id
    assert imported.element_context_hash == source.context_hash
    assert imported.element_option_name == "Clearer"
    assert "PRIVATE_OTHER_DECK_NAME" not in imported.model_dump_json()
    with pytest.raises(ValueError):
        _ = alternatives.read(copied.id, "cover", source.option.id)
