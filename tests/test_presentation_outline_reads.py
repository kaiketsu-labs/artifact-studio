"""Targeted slide source reads retain the canonical brief, tokens and question visibility."""

from pathlib import Path
from typing import cast

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from artifact_studio import app as service
from artifact_studio.agent_io import rule_fields, slide_receipt
from artifact_studio.models import QuestionAnswerInput
from artifact_studio.questions import Questions
from artifact_studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    make_design(tmp_path / "collection" / TEMPLATE_ID)
    result = Store(tmp_path / "collection", tmp_path / "state" / "review.sqlite3")
    monkeypatch.setattr(service, "store", result)
    return result


def test_batched_outline_matches_full_detail_without_loading_unrequested_data(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    deck = store.read(TEMPLATE_ID)
    deck.slides[0].interactive_html = "<button>Interactive</button>"
    deck.slides[0].notes = "Original presenter notes"
    deck.slides[1].hidden = True
    store.write(TEMPLATE_ID, deck)
    original = store.view(TEMPLATE_ID, include_pending=False)
    ids = [slide.id for slide in original.slides]
    originals = {key: service.get_slide(TEMPLATE_ID, key).model_dump() for key in ids}

    def unexpected(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Outline should not hydrate full views or discussion")

    for name in ("view", "view_slide", "comments", "workflow"):
        monkeypatch.setattr(store, name, unexpected)

    result = service.get_presentation_outline(TEMPLATE_ID, include_context=True)
    assert result == {
        "id": original.id,
        "title": original.title,
        "delivery_hash": original.delivery_hash,
        "design_id": original.design_id,
        "slides": [slide_receipt(TEMPLATE_ID, slide).model_dump() for slide in original.slides],
        "sections": [item.model_dump() for item in original.sections],
        "sections_hash": original.sections_hash,
        "context_markdown": original.context_markdown,
        "context_hash": original.context_hash,
        "context_included": True,
        "context_characters": len(original.context_markdown),
        "brief_files": [item.model_dump() for item in original.brief_files],
        "open_questions": [],
        **rule_fields(original.authoring_instructions, original.learned_preferences),
    }
    batch = service.get_presentation_outline(
        TEMPLATE_ID,
        known_rules_hash=str(result["rules_hash"]),
        source_slide_ids=tuple(reversed(ids)),
    )
    assert "context_markdown" not in batch and batch["rules_included"] is False
    assert batch["slides"] == result["slides"]
    sources = cast(dict[str, dict[str, object]], batch["slide_sources"])
    assert list(sources) == list(reversed(ids))
    for key, fields in sources.items():
        assert fields.keys() == {
            "html",
            "interactive_html",
            "notes",
            "notes_revision",
            "sources",
            "agent",
        }
        assert fields == {field: originals[key][field] for field in fields}


@pytest.mark.parametrize("ids", [("cover", "cover"), ("cover", "absent"), ("cover",) * 9])
def test_invalid_batch_is_all_or_nothing(store: Store, ids: tuple[str, ...]) -> None:
    with pytest.raises(ToolError, match="distinct slide IDs|Slide ID does not exist"):
        _ = service.get_presentation_outline(TEMPLATE_ID, source_slide_ids=ids)


def test_outline_open_questions_keep_slide_order_and_withheld_answers(store: Store) -> None:
    questions = Questions(store)

    def ask(slide: str, req: str) -> int:
        base = service.get_slide(TEMPLATE_ID, slide)
        return questions.ask(
            TEMPLATE_ID, slide, "Fictional question?", [], base.expected_hash, req
        ).event_id

    message = ask("message", "message-question")
    cover = ask("cover", "cover-question")
    cancelled = ask("cover", "cancelled-question")
    _ = questions.cancel(TEMPLATE_ID, "cover", cancelled, "cancel-question")
    _ = store.set_mode(TEMPLATE_ID, "batch")
    _ = questions.answer(
        TEMPLATE_ID,
        "cover",
        cover,
        QuestionAnswerInput(answer="private", request_id="cover-answer"),
    )
    assert service.get_presentation_outline(TEMPLATE_ID)["open_questions"] == [
        {"event_id": cover, "slide_id": "cover"},
        {"event_id": message, "slide_id": "message"},
    ]
    _ = store.release_batch(TEMPLATE_ID)
    assert service.get_presentation_outline(TEMPLATE_ID)["open_questions"] == [
        {"event_id": message, "slide_id": "message"}
    ]
