"""Polling reads only newly delivered human messages and actionable design handoffs."""

import json
from pathlib import Path
from typing import Literal

import pytest
from pydantic import TypeAdapter

from artifact_studio.models import (
    DesignMessage,
    DesignRequestInput,
    EditIntent,
    ElementTarget,
    FeedbackFile,
    QuestionPrompt,
)
from artifact_studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def store(tmp_path: Path) -> Store:
    make_design(tmp_path / "collection" / TEMPLATE_ID)
    return Store(tmp_path / "collection", tmp_path / "state" / "review.sqlite3")


def append(
    store: Store, message: str, *, author: Literal["user", "agent"] = "user", released: bool = True
) -> int:
    with store.connect() as db:
        return int(
            store.append_feedback(
                db, TEMPLATE_ID, "cover", author, message, "original-slide-hash", released=released
            )
        )


def test_caught_up_poll_does_not_deserialize_feedback_history(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """History can be large while each empty live poll stays independent of payload size."""
    with store.connect() as db:
        for index in range(250):
            _ = store.append_feedback(
                db,
                TEMPLATE_ID,
                "cover",
                "user",
                f"Old message {index}: " + "x" * 8000,
                "original-slide-hash",
                released=True,
            )
    cursor = store.feedback_feed(TEMPLATE_ID).next_cursor
    _ = append(store, "Unreleased draft", released=False)
    _ = append(store, "Agent response", author="agent")

    def forbidden(*args: object, **kwargs: object) -> list[FeedbackFile]:
        raise AssertionError("Caught-up polls must not hydrate old, withheld or agent messages")

    monkeypatch.setattr(Store, "feedback_files", forbidden)
    feed = store.feedback_feed(TEMPLATE_ID, cursor)
    assert feed.events == [] and feed.next_cursor == cursor


def test_delivery_order_retains_late_batches_and_excludes_private_drafts(store: Store) -> None:
    first = append(store, "Earlier batch draft", released=False)
    second = append(store, "Second batch draft", released=False)
    live = append(store, "Later live request")
    _ = append(store, "Agent reply", author="agent")
    initial = store.feedback_feed(TEMPLATE_ID)
    assert [item.event_id for item in initial.events] == [live]
    _ = store.release_batch(TEMPLATE_ID)
    after_batch = append(store, "Live after batch")
    held = append(store, "Still withheld", released=False)
    delivered = store.feedback_feed(TEMPLATE_ID, initial.next_cursor)
    assert [item.event_id for item in delivered.events] == [first, second, after_batch]
    assert all(item.author == "user" and item.released for item in delivered.events)
    assert delivered.events[0].batch_id == delivered.events[1].batch_id
    assert delivered.events[0].batch_id
    assert [item.event_id for item in store.feedback_feed(TEMPLATE_ID).events] == [
        live,
        first,
        second,
        after_batch,
    ]
    assert store.feedback_feed(TEMPLATE_ID, delivered.next_cursor).events == []
    history = store.comments(TEMPLATE_ID)
    assert [item.event_id for item in history] == sorted(item.event_id for item in history)
    assert history[-1].event_id == held and not history[-1].released
    assert [item.event_id for item in store.comments(TEMPLATE_ID, after_batch)] == [held]


def test_filtered_delivery_keeps_attachment_target_and_saved_option_metadata(store: Store) -> None:
    files = store.save_feedback_files(TEMPLATE_ID, [("brief.txt", b"Fictional reference")])
    target = ElementTarget(
        selector="h1", label="Headline", quote="Original", x=0.1, y=0.2, width=0.6, height=0.1
    )
    intent = EditIntent(action="replace_text", values={"text": "Revised"})
    with store.connect() as db:
        event_id = int(
            store.append_feedback(
                db,
                TEMPLATE_ID,
                "cover",
                "user",
                "Use this retained alternative",
                "original-slide-hash",
                released=True,
                target_json=target.model_dump_json(),
                context_slide_id="cover",
                files_json=TypeAdapter(list[FeedbackFile]).dump_json(files).decode(),
                intent_json=intent.model_dump_json(),
                element_option_id="retained-option-id",
                element_option_name="Saved wording",
                element_context_hash="retained-context-hash",
            )
        )
        _ = store.append_feedback(
            db,
            TEMPLATE_ID,
            "cover",
            "agent",
            "Which tone?",
            "original-slide-hash",
            released=True,
            question_json=QuestionPrompt(options=["Direct", "Friendly"]).model_dump_json(),
        )
    event = store.feedback_feed(TEMPLATE_ID).events[0]
    assert event == next(item for item in store.comments(TEMPLATE_ID) if item.event_id == event_id)
    assert event.target == target and event.edit_intent == intent
    assert event.element_option_name == "Saved wording"
    assert event.element_context_hash == "retained-context-hash"
    assert Path(event.files[0].path).read_bytes() == b"Fictional reference"
    assert event.files[0].download_url.endswith(f"/feedback/{event_id}/files/0")
    assert store.comments(TEMPLATE_ID)[-1].question == QuestionPrompt(
        options=["Direct", "Friendly"]
    )


def test_poll_parses_only_queued_design_requests_and_keeps_guidance(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    completed = store.post_design_request(
        DesignRequestInput(message="Earlier handoff", request_id="earlier-handoff-001")
    )
    _ = store.reply_design(completed.event_id, "Working on it", "earlier-response-001", "working")
    queued = store.post_design_request(
        DesignRequestInput(message="Current handoff", request_id="current-handoff-001")
    )
    # Earlier stored messages may omit a default-valued status field.
    with store.connect() as db:
        document = queued.model_dump(exclude={"status"})
        _ = db.execute(
            "UPDATE design_chat SET document=? WHERE id=?", (json.dumps(document), queued.event_id)
        )
    (store.root / "INSTRUCTIONS.md").write_text("Keep authored layouts and user notes.")
    (store.root / "LEARNED-PREFERENCES.md").write_text("Use concise prose.")
    original = DesignMessage.model_validate_json
    parsed: list[int] = []

    def tracked(document: str) -> DesignMessage:
        message = original(document)
        parsed.append(message.event_id)
        return message

    monkeypatch.setattr(DesignMessage, "model_validate_json", tracked)
    feed = store.feedback_feed(TEMPLATE_ID)
    assert parsed == [queued.event_id]
    assert feed.design_requests == [queued]
    assert feed.authoring_instructions == "Keep authored layouts and user notes."
    assert feed.learned_preferences == "Use concise prose."
    assert len(store.design_inbox().messages) == 3
