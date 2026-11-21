import pytest

from artifact_studio.delivery_reviews import DeliverySummary
from scripts.measure_design_reviews import summarize


def test_human_direction_rate_excludes_unrated_and_counts_self_revisions_separately() -> None:
    accepted = DeliverySummary(
        previous_review_count=1,
        current_review=None,
        delivery_hash="a" * 64,
        deck_id="clinic",
        first_delivery="accepted",
        visual_redirect_rounds=0,
        factual_correction_rounds=1,
        legibility_correction_rounds=1,
        agent_self_revisions=2,
    )
    unrated = accepted.model_copy(update={"deck_id": "onboarding", "first_delivery": "unrated"})
    redirect = accepted.model_copy(
        update={"deck_id": "research", "first_delivery": "redirect", "visual_redirect_rounds": 2},
    )
    assert summarize([unrated])["first_delivery_acceptance_rate"] is None
    assert summarize([accepted, unrated, redirect]) == {
        "decks": 3,
        "first_deliveries_rated": 2,
        "first_deliveries_unrated": 1,
        "first_deliveries_accepted": 1,
        "first_deliveries_redirected": 1,
        "first_delivery_acceptance_rate": 0.5,
        "human_visual_redirect_rounds": 2,
        "human_factual_correction_rounds": 3,
        "human_legibility_correction_rounds": 3,
        "reported_agent_self_revisions": 6,
    }
    with pytest.raises(ValueError, match="Repeated deck"):
        summarize([accepted, accepted])
