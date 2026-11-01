"""Durable slide question threads, private delivery and portable answer drafts."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import app as service
from studio.models import QuestionAnswerInput
from studio.projects import ClientState, QuestionDraft, audience_html, open_project, save_project
from studio.questions import Questions
from studio.storage import ConflictError, Store, digest
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    """Keep private questions outside the source tree and use fictional slide content."""
    make_design(tmp_path / "presentations" / TEMPLATE_ID)
    result = Store(tmp_path / "presentations", tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", result)
    return result


def ask(store: Store, *, slide_id: str = "cover", request_id: str = "ask-question-001") -> int:
    """Ask through the same service used by the MCP interface."""
    slide = store.locate(store.read(TEMPLATE_ID), slide_id)
    question = Questions(store).ask(
        TEMPLATE_ID,
        slide_id,
        "Who is the audience?",
        ["Team leads", "Customers"],
        digest(slide.model_dump_json()),
        request_id,
    )
    return question.event_id


def test_choices_and_written_answers_are_slide_local_and_idempotent(store: Store) -> None:
    """A question and answer preserve slide review state and use the human delivery queue."""
    before = store.view(TEMPLATE_ID).slides[0]
    store.approve(TEMPLATE_ID, before.id, before.content_hash, True)
    event_id = ask(store)
    assert ask(store) == event_id
    question = service.get_slide_questions(TEMPLATE_ID)[0]
    assert question.status == "open" and question.options == ["Team leads", "Customers"]
    assert service.get_presentation_outline(TEMPLATE_ID)["open_questions"] == [
        {"event_id": event_id, "slide_id": "cover"}
    ]
    body = {"answer": "Team leads", "request_id": "answer-question-001"}
    url = f"/api/decks/{TEMPLATE_ID}/slides/cover/questions/{event_id}/answer"
    client = TestClient(service.app)
    result = client.post(url, json=body)
    assert result.status_code == 200
    answer = result.json()
    assert answer["reply_to"] == event_id and answer["author"] == "user"
    assert answer["released"] and answer["message"] == "Team leads"
    assert client.post(url, json=body).json() == answer
    assert (
        client.post(url, json={"answer": "Customers", "request_id": body["request_id"]}).status_code
        == 409
    )
    assert (
        client.post(
            url, json={"answer": "Customers", "request_id": "another-answer-001"}
        ).status_code
        == 409
    )
    assert service.get_slide_questions(TEMPLATE_ID) == []
    resolved = service.get_slide_questions(TEMPLATE_ID, include_resolved=True)[0]
    assert resolved.status == "answered" and resolved.answer_event_id == answer["event_id"]
    assert resolved.answer == "Team leads"
    after = store.view(TEMPLATE_ID).slides[0]
    assert after.approved and after.expected_hash == before.expected_hash
    assert after.content_hash == before.content_hash and after.revision == before.revision
    feed = store.feedback_feed(TEMPLATE_ID)
    assert [item.event_id for item in feed.events] == [answer["event_id"]]
    assert store.feedback_feed(TEMPLATE_ID, feed.next_cursor).events == []
    second = ask(store, slide_id="message", request_id="ask-question-002")
    written = Questions(store).answer(
        TEMPLATE_ID,
        "message",
        second,
        QuestionAnswerInput(
            answer="A mixed audience, with a technical appendix.",
            request_id="written-answer-001",
        ),
    )
    assert written.slide_id == "message" and written.reply_to == second
    assert (
        service.get_slide_questions(TEMPLATE_ID, "message", include_resolved=True)[0].answer
        == written.message
    )


def test_batch_answer_remains_private_until_explicit_release(store: Store) -> None:
    """Neither answer text nor the fact that it exists leaks to any agent read."""
    event_id = ask(store)
    _ = store.set_mode(TEMPLATE_ID, "batch")
    answer = Questions(store).answer(
        TEMPLATE_ID,
        "cover",
        event_id,
        QuestionAnswerInput(
            answer="PRIVATE_BATCH_ANSWER_28aa",
            request_id="batch-answer-001",
        ),
    )
    assert not answer.released
    human = store.view(TEMPLATE_ID).slides[0].feedback
    assert Questions.summarize(human)[0].status == "answered"
    agent = service.get_slide_questions(TEMPLATE_ID, include_resolved=True)[0]
    assert agent.status == "open" and agent.answer == "" and agent.answer_event_id is None
    assert store.feedback_feed(TEMPLATE_ID).events == []
    assert "PRIVATE_BATCH_ANSWER_28aa" not in str(service.get_presentation(TEMPLATE_ID))
    assert "PRIVATE_BATCH_ANSWER_28aa" not in str(service.get_slide(TEMPLATE_ID, "cover", True))
    assert service.get_presentation_outline(TEMPLATE_ID)["open_questions"]
    _ = store.release_batch(TEMPLATE_ID)
    assert service.get_slide_questions(TEMPLATE_ID) == []
    feed = store.feedback_feed(TEMPLATE_ID)
    assert [item.message for item in feed.events] == ["PRIVATE_BATCH_ANSWER_28aa"]
    assert feed.events[0].reply_to == event_id and feed.events[0].batch_id


def test_question_cancellation_is_safe_and_does_not_discard_submitted_answers(store: Store) -> None:
    """Cancelled questions cannot take a new answer, while an already queued answer survives."""
    event_id = ask(store)
    first = service.cancel_slide_question(TEMPLATE_ID, "cover", event_id, "cancel-question-001")
    assert first.status == "cancelled"
    assert (
        service.cancel_slide_question(TEMPLATE_ID, "cover", event_id, "cancel-question-001")
        == first
    )
    with pytest.raises(ConflictError, match="cancelled"):
        _ = Questions(store).answer(
            TEMPLATE_ID,
            "cover",
            event_id,
            QuestionAnswerInput(
                answer="Too late",
                request_id="cancelled-answer-001",
            ),
        )
    assert service.get_slide_questions(TEMPLATE_ID) == []
    second = ask(store, request_id="ask-question-002")
    _ = store.set_mode(TEMPLATE_ID, "batch")
    _ = Questions(store).answer(
        TEMPLATE_ID,
        "cover",
        second,
        QuestionAnswerInput(
            answer="Private queued answer",
            request_id="queued-answer-001",
        ),
    )
    assert (
        service.cancel_slide_question(TEMPLATE_ID, "cover", second, "cancel-question-002").status
        == "cancelled"
    )
    assert store.feedback_feed(TEMPLATE_ID).events == []
    _ = store.release_batch(TEMPLATE_ID)
    assert (
        service.get_slide_questions(TEMPLATE_ID, include_resolved=True)[1].answer
        == "Private queued answer"
    )


def test_question_validation_is_atomic_and_confined_to_its_slide(store: Store) -> None:
    """Reject stale prompts, ambiguous choices and attempts to answer another context."""
    current = store.view(TEMPLATE_ID).slides[0]
    questions = Questions(store)
    for options in (["Same", " same "], [" "], [str(i) for i in range(7)]):
        with pytest.raises(ValueError):
            _ = questions.ask(
                TEMPLATE_ID,
                "cover",
                "Choose",
                options,
                current.expected_hash,
                "invalid-question-001",
            )
    with pytest.raises(ConflictError, match="changed"):
        _ = questions.ask(TEMPLATE_ID, "cover", "Choose", [], "stale", "stale-question-001")
    assert store.comments(TEMPLATE_ID) == []
    event_id = ask(store)
    client = TestClient(service.app)
    body = {"answer": "A response", "request_id": "wrong-context-001"}
    assert (
        client.post(
            f"/api/decks/{TEMPLATE_ID}/slides/message/questions/{event_id}/answer", json=body
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/decks/{TEMPLATE_ID}/slides/cover/questions/{event_id}/answer",
            json={"answer": " ", "request_id": "blank-answer-001"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/decks/{TEMPLATE_ID}/slides/cover/questions/{event_id}/answer",
            json=body,
            headers={"Origin": "https://other.invalid"},
        ).status_code
        == 403
    )
    assert questions.list(TEMPLATE_ID)[0].status == "open"


def test_question_history_and_drafts_round_trip_without_audience_export(
    store: Store, tmp_path: Path
) -> None:
    """Remap question/answer IDs and unsent drafts in a portable private project."""
    event_id = ask(store)
    second = ask(store, slide_id="message", request_id="ask-question-002")
    third = ask(store, request_id="ask-question-003")
    _ = Questions(store).cancel(TEMPLATE_ID, "cover", third, "portable-cancel-001")
    _ = Questions(store).answer(
        TEMPLATE_ID,
        "cover",
        event_id,
        QuestionAnswerInput(
            answer="PRIVATE_PORTABLE_ANSWER",
            request_id="portable-answer-001",
        ),
    )
    client = ClientState(
        question_answers={str(second): QuestionDraft(slide_id="message", text="Unsent answer")}
    )
    archive = save_project(store, TEMPLATE_ID, client)
    target = Store(tmp_path / "imported", tmp_path / "imported-state/review.sqlite3")
    # Occupy unrelated event IDs, so the restore must remap question and draft identities.
    make_design(target.root / "2026-11-02-other-design", "other")
    _ = Questions(target).ask(
        "2026-11-02-other-design",
        "cover",
        "Unrelated?",
        [],
        digest(target.read("2026-11-02-other-design").slides[0].model_dump_json()),
        "unrelated-question",
    )
    restored, drafts = open_project(target, archive)
    history = Questions(target).list(restored.id, include_resolved=True)
    answered = next(item for item in history if item.status == "answered")
    pending = next(item for item in history if item.status == "open")
    assert answered.answer == "PRIVATE_PORTABLE_ANSWER"
    assert pending.options == ["Team leads", "Customers"]
    assert drafts.question_answers[str(pending.event_id)].text == "Unsent answer"
    assert pending.event_id != second
    replay = Questions(target).answer(
        restored.id,
        "cover",
        answered.event_id,
        QuestionAnswerInput(answer="PRIVATE_PORTABLE_ANSWER", request_id="portable-answer-001"),
    )
    assert replay.event_id == answered.answer_event_id
    cancelled = next(item for item in history if item.status == "cancelled")
    assert (
        Questions(target)
        .cancel(restored.id, "cover", cancelled.event_id, "portable-cancel-001")
        .status
        == "cancelled"
    )
    assert "PRIVATE_PORTABLE_ANSWER" not in audience_html(target, restored.id)
    assert "Who is the audience?" not in audience_html(target, restored.id)
