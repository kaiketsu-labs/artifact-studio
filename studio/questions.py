"""Slide-local question threads using the existing private feedback and delivery log."""

import json
import sqlite3
import uuid
from typing import cast

from .models import Feedback, QuestionAnswerInput, QuestionPrompt, SlideQuestionView
from .storage import ConflictError, Store, digest


class Questions:
    """Ask, answer and retire questions without changing slide source or approval."""

    def __init__(self, store: Store) -> None:
        """Share structural edit locks and the durable comment/idempotency records."""
        self.store = store

    @staticmethod
    def summarize(events: list[Feedback]) -> list[SlideQuestionView]:
        """Derive state only from the events visible to the caller."""
        answers = {
            item.reply_to: item
            for item in events
            if item.author == "user" and item.reply_to is not None
        }
        result: list[SlideQuestionView] = []
        for event in events:
            if event.question is None or event.author != "agent" or not event.slide_id:
                continue
            answer = answers.get(event.event_id)
            if answer and answer.slide_id != event.slide_id:
                answer = None
            result.append(
                SlideQuestionView(
                    event_id=event.event_id,
                    slide_id=event.slide_id,
                    question=event.message,
                    options=event.question.options,
                    content_hash=event.content_hash,
                    status="answered"
                    if answer
                    else "cancelled"
                    if event.question.cancelled
                    else "open",
                    answer=answer.message if answer else "",
                    answer_event_id=answer.event_id if answer else None,
                )
            )
        return result

    def list(
        self, deck_id: str, slide_id: str = "", *, include_resolved: bool = False
    ) -> list[SlideQuestionView]:
        """Return current questions without exposing withheld Batch answers or their existence."""
        deck = self.store.read(deck_id)
        if slide_id:
            _ = self.store.locate(deck, slide_id)
        live = {slide.id for slide in deck.slides}
        events = [item for item in self.store.comments(deck_id) if item.released]
        return [
            item
            for item in self.summarize(events)
            if item.slide_id in live
            and (not slide_id or item.slide_id == slide_id)
            and (include_resolved or item.status == "open")
        ]

    @staticmethod
    def prior(db: sqlite3.Connection, deck_id: str, request_id: str, payload: str) -> str | None:
        """Reuse an operation only when its original request is retained exactly."""
        if not 8 <= len(request_id) <= 100:
            raise ValueError("Use a unique request_id of 8–100 characters.")
        row = db.execute(
            "SELECT payload_hash,result FROM operations WHERE deck=? AND request_id=?",
            (deck_id, request_id),
        ).fetchone()
        if row is not None and row[0] != digest(payload):
            raise ConflictError("This request_id already belongs to another action.")
        return str(row[1]) if row else None

    @staticmethod
    def record(
        db: sqlite3.Connection, deck_id: str, request_id: str, payload: str, event_id: str
    ) -> None:
        """Keep numeric event IDs compatible with portable project remapping."""
        _ = db.execute(
            "INSERT INTO operations VALUES (?,?,?,?)",
            (deck_id, request_id, digest(payload), event_id),
        )

    def ask(
        self,
        deck_id: str,
        slide_id: str,
        question: str,
        options: list[str],
        expected_hash: str,
        request_id: str,
    ) -> SlideQuestionView:
        """Create a visible agent question once, anchored to the slide the agent read."""
        if not 1 <= len(question.strip()) <= 2000:
            raise ValueError("Ask a question of 1–2000 characters.")
        prompt = QuestionPrompt(options=options)
        payload = json.dumps(
            ["ask-question", slide_id, question, prompt.model_dump(), expected_hash]
        )
        with self.store.editing(deck_id) as db:
            event_id = self.prior(db, deck_id, request_id, payload)
            if event_id is None:
                slide = self.store.locate(self.store.read(deck_id), slide_id)
                if digest(slide.model_dump_json()) != expected_hash:
                    raise ConflictError("The slide changed. Read it again before asking.")
                open_count = sum(item.status == "open" for item in self.list(deck_id, slide_id))
                if open_count >= 5:
                    raise ValueError("Resolve or cancel an existing question before adding more.")
                prompt.key = uuid.uuid4().hex
                event_id = self.store.append_feedback(
                    db,
                    deck_id,
                    slide_id,
                    "agent",
                    question,
                    self.store.content_hash(deck_id, slide),
                    released=True,
                    context_slide_id=slide_id,
                    question_json=prompt.model_dump_json(),
                )
                self.record(db, deck_id, request_id, payload, event_id)
        return next(
            item
            for item in self.list(deck_id, slide_id, include_resolved=True)
            if item.event_id == int(event_id)
        )

    @staticmethod
    def prompt(db: sqlite3.Connection, deck_id: str, slide_id: str, event_id: int) -> sqlite3.Row:
        """A question cannot be answered or cancelled from another slide or deck."""
        row = db.execute(
            "SELECT * FROM feedback WHERE deck=? AND slide=? AND id=? "
            "AND author='agent' AND question IS NOT NULL",
            (deck_id, slide_id, event_id),
        ).fetchone()
        if row is None:
            raise ValueError("That question does not belong to this slide.")
        return row

    @staticmethod
    def identity(row: sqlite3.Row) -> str:
        """Keep retry identity stable when project import remaps numeric event IDs."""
        prompt = QuestionPrompt.model_validate_json(str(row["question"]))
        return prompt.key or digest(
            json.dumps(
                [
                    row["slide"],
                    row["created_at"],
                    row["message"],
                    row["content_hash"],
                    prompt.options,
                ]
            )
        )

    def answer(
        self, deck_id: str, slide_id: str, event_id: int, body: QuestionAnswerInput
    ) -> Feedback:
        """Accept one human answer, following the existing Live/Batch delivery preference."""
        if not body.answer.strip():
            raise ValueError("Choose an option or write an answer.")
        with self.store.editing(deck_id) as db:
            row = self.prompt(db, deck_id, slide_id, event_id)
            payload = json.dumps(["answer-question", slide_id, self.identity(row), body.answer])
            result = self.prior(db, deck_id, body.request_id, payload)
            if result is None:
                _ = self.store.locate(self.store.read(deck_id), slide_id)
                prompt = QuestionPrompt.model_validate_json(str(row["question"]))
                if prompt.cancelled:
                    raise ConflictError(
                        "This question was cancelled. Your answer has not been sent."
                    )
                if db.execute(
                    "SELECT id FROM feedback WHERE deck=? AND reply_to=? AND author='user'",
                    (deck_id, event_id),
                ).fetchone():
                    raise ConflictError(
                        "This question has already been answered in another window."
                    )
                mode = db.execute("SELECT mode FROM workflow WHERE deck=?", (deck_id,)).fetchone()
                result = self.store.append_feedback(
                    db,
                    deck_id,
                    slide_id,
                    "user",
                    body.answer,
                    cast(str, row["content_hash"]),
                    released=not mode or mode[0] == "live",
                    reply_to=event_id,
                    context_slide_id=slide_id,
                )
                self.record(db, deck_id, body.request_id, payload, result)
        return next(item for item in self.store.comments(deck_id) if item.event_id == int(result))

    def cancel(
        self, deck_id: str, slide_id: str, event_id: int, request_id: str
    ) -> SlideQuestionView:
        """Retire an obsolete question without discarding any answer already submitted."""
        with self.store.editing(deck_id) as db:
            row = self.prompt(db, deck_id, slide_id, event_id)
            payload = json.dumps(["cancel-question", slide_id, self.identity(row)])
            if self.prior(db, deck_id, request_id, payload) is None:
                _ = self.store.locate(self.store.read(deck_id), slide_id)
                prompt = QuestionPrompt.model_validate_json(str(row["question"]))
                prompt.cancelled = True
                _ = db.execute(
                    "UPDATE feedback SET question=? WHERE id=?",
                    (prompt.model_dump_json(), event_id),
                )
                self.record(db, deck_id, request_id, payload, str(event_id))
        return next(
            item
            for item in self.list(deck_id, slide_id, include_resolved=True)
            if item.event_id == event_id
        )
