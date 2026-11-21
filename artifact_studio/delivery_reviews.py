"""Voluntary, per-presentation measurements of visual direction and correction rounds."""

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import Field, model_validator

from .deck_edit_api import DeckID
from .models import StrictModel, presentation_delivery_hash
from .multiplayer_access import Role
from .storage import ConflictError, Store, digest

Direction = Literal["unrated", "accepted", "redirect"]
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
RequestID = Annotated[str, Field(min_length=8, max_length=100)]


class DeliveryJudgment(StrictModel):
    """One human decision about the visual direction of a delivered presentation."""

    stage: Literal["first", "follow_up"]
    direction: Direction
    factual: bool = Field(default=False, strict=True)
    legibility: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def has_observation(self) -> DeliveryJudgment:
        """Allow correction-only rounds without treating them as acceptance."""
        if self.direction == "unrated" and not (self.factual or self.legibility):
            raise ValueError("Choose a visual direction or a correction to record.")
        return self


class DeliveryReviewInput(DeliveryJudgment):
    delivery_hash: Hash
    request_id: RequestID


class DeliverySummary(StrictModel):
    """Only aggregate counts and the current judgment leave the review database."""

    deck_id: str
    delivery_hash: str
    previous_review_count: int
    first_delivery: Direction
    visual_redirect_rounds: int
    factual_correction_rounds: int
    legibility_correction_rounds: int
    agent_self_revisions: int
    current_review: DeliveryJudgment | None


class SelfRevision(StrictModel):
    before_delivery_hash: Hash
    after_delivery_hash: Hash
    request_id: RequestID


def begin_design_self_review(store: Store, deck_id: str, delivery_hash: str) -> None:
    """Remember the observed version when an author starts measuring a review pass."""
    with store.connect() as db:
        _ = db.execute(
            "INSERT OR IGNORE INTO design_self_review_starts VALUES (?,?)", (deck_id, delivery_hash)
        )


def _current_hash(store: Store, deck_id: str) -> str:
    deck = store.read(deck_id)
    design = store.visual_hash(deck_id)
    return presentation_delivery_hash(
        deck.title,
        ((s.id, s.hidden, store.content_hash(deck_id, s, design_hash=design)) for s in deck.slides),
    )


def _summary(db: sqlite3.Connection, deck_id: str, current_hash: str) -> DeliverySummary:
    totals = db.execute(
        "SELECT COUNT(*), COALESCE(MAX(CASE WHEN stage='first' THEN direction END),'unrated'), "
        "COUNT(*) FILTER (WHERE direction='redirect'), COALESCE(SUM(factual),0), "
        "COALESCE(SUM(legibility),0) FROM delivery_reviews WHERE deck=?",
        (deck_id,),
    ).fetchone()
    current = db.execute(
        "SELECT stage,direction,factual,legibility FROM delivery_reviews "
        "WHERE deck=? AND delivery_hash=?",
        (deck_id, current_hash),
    ).fetchone()
    agent_count = db.execute(
        "SELECT COUNT(DISTINCT after_hash) FROM design_self_revisions WHERE deck=?", (deck_id,)
    ).fetchone()[0]
    return DeliverySummary(
        deck_id=deck_id,
        delivery_hash=current_hash,
        previous_review_count=totals[0],
        first_delivery=totals[1],
        visual_redirect_rounds=totals[2],
        factual_correction_rounds=totals[3],
        legibility_correction_rounds=totals[4],
        agent_self_revisions=agent_count,
        current_review=DeliveryJudgment(
            stage=current[0],
            direction=current[1],
            factual=bool(current[2]),
            legibility=bool(current[3]),
        )
        if current
        else None,
    )


def read_delivery_review(store: Store, deck_id: str) -> DeliverySummary:
    """Read the selected deck's measurements from a single database snapshot."""
    with store.connect() as db:
        _ = db.execute("BEGIN")
        return _summary(db, deck_id, _current_hash(store, deck_id))


def record_delivery_review(
    store: Store, deck_id: str, body: DeliveryReviewInput
) -> DeliverySummary:
    """Record one human judgment per delivery and replay exact requests after later edits."""
    signature = digest(body.model_dump_json(exclude={"request_id"}))
    judgment = DeliveryJudgment.model_validate(
        body.model_dump(exclude={"request_id", "delivery_hash"})
    )
    with store.connect() as db:
        _ = db.execute("BEGIN IMMEDIATE")
        current_hash = _current_hash(store, deck_id)
        summary = _summary(db, deck_id, current_hash)
        receipt = db.execute(
            "SELECT payload_hash FROM delivery_review_receipts WHERE deck=? AND request_id=?",
            (deck_id, body.request_id),
        ).fetchone()
        if receipt:
            if receipt[0] != signature:
                raise ConflictError("This request ID was already used for a different review.")
            return summary
        if current_hash != body.delivery_hash:
            raise ConflictError(
                "The presentation changed. Reopen the review for the latest delivery."
            )
        if summary.current_review:
            if summary.current_review != judgment:
                raise ConflictError("This delivery already has a review. Refresh to see it.")
        else:
            if body.stage == "first" and summary.previous_review_count:
                raise ConflictError("Earlier reviews exist. Record this delivery as a follow-up.")
            _ = db.execute(
                "INSERT INTO delivery_reviews VALUES (?,?,?,?,?,?)",
                (deck_id, current_hash, body.stage, body.direction, body.factual, body.legibility),
            )
        _ = db.execute(
            "INSERT INTO delivery_review_receipts VALUES (?,?,?)",
            (deck_id, body.request_id, signature),
        )
        return _summary(db, deck_id, current_hash)


def record_self_revision(store: Store, deck_id: str, body: SelfRevision) -> dict[str, bool]:
    """Count a reported changed delivery once without recording or reading human judgments."""
    if body.before_delivery_hash == body.after_delivery_hash:
        raise ValueError("An inspection without a visual change is not a revision.")
    with store.connect() as db:
        _ = db.execute("BEGIN IMMEDIATE")
        receipt = db.execute(
            "SELECT before_hash,after_hash FROM design_self_revisions "
            "WHERE deck=? AND request_id=?",
            (deck_id, body.request_id),
        ).fetchone()
        if receipt:
            if tuple(receipt) != (body.before_delivery_hash, body.after_delivery_hash):
                raise ConflictError("This request ID was already used for a different self-review.")
        else:
            baseline = db.execute(
                "SELECT 1 FROM design_self_review_starts WHERE deck=? AND delivery_hash=?",
                (deck_id, body.before_delivery_hash),
            ).fetchone()
            if not baseline:
                raise ValueError(
                    "Start the pass with get_presentation_outline(begin_self_review=True)."
                )
            if body.after_delivery_hash != _current_hash(store, deck_id):
                raise ConflictError("The delivery changed. Recheck the current presentation.")
            _ = db.execute(
                "INSERT INTO design_self_revisions VALUES (?,?,?,?)",
                (deck_id, body.request_id, body.before_delivery_hash, body.after_delivery_hash),
            )
    return {"recorded": True}


def delivery_review_routes(
    access: Callable[[Request, str, Role], AbstractContextManager[Store]],
) -> APIRouter:
    """Human review UI. MCP exposes only the separate self-revision operation."""
    router = APIRouter(prefix="/api/decks/{deck_id}/delivery-reviews")

    @router.get("")
    def review(request: Request, deck_id: DeckID) -> DeliverySummary:
        with access(request, deck_id, "view") as store:
            return read_delivery_review(store, deck_id)

    @router.post("")
    def record(request: Request, deck_id: DeckID, body: DeliveryReviewInput) -> DeliverySummary:
        with access(request, deck_id, "comment") as store:
            return record_delivery_review(store, deck_id, body)

    return router
