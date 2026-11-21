"""Summarize human design feedback for explicitly selected decks from one workspace."""

import argparse
import json
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import quote

import httpx

from artifact_studio.delivery_reviews import DeliverySummary
from artifact_studio.local_http import validate_local_url


def summarize(samples: Iterable[DeliverySummary]) -> dict[str, int | float | None]:
    decks: set[str] = set()
    accepted = redirected = visual = factual = legibility = agent = 0
    for sample in samples:
        if sample.deck_id in decks:
            raise ValueError(f"Repeated deck ID: {sample.deck_id}. Use one snapshot per deck.")
        decks.add(sample.deck_id)
        accepted += sample.first_delivery == "accepted"
        redirected += sample.first_delivery == "redirect"
        visual += sample.visual_redirect_rounds
        factual += sample.factual_correction_rounds
        legibility += sample.legibility_correction_rounds
        agent += sample.agent_self_revisions
    rated = accepted + redirected
    return {
        "decks": len(decks),
        "first_deliveries_rated": rated,
        "first_deliveries_unrated": len(decks) - rated,
        "first_deliveries_accepted": accepted,
        "first_deliveries_redirected": redirected,
        "first_delivery_acceptance_rate": accepted / rated if rated else None,
        "human_visual_redirect_rounds": visual,
        "human_factual_correction_rounds": factual,
        "human_legibility_correction_rounds": legibility,
        "reported_agent_self_revisions": agent,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--decks", nargs="+", metavar="ID", help="Deck IDs in the local Studio")
    sources.add_argument(
        "--snapshots",
        type=Path,
        nargs="+",
        metavar="FILE",
        help="Saved delivery-reviews JSON responses from a single workspace",
    )
    parser.add_argument("--origin", default="http://127.0.0.1:8790", help="Local Studio origin")
    args = parser.parse_args()
    try:
        if args.snapshots:
            samples = [
                DeliverySummary.model_validate_json(path.read_bytes()) for path in args.snapshots
            ]
        else:
            validate_local_url(args.origin)
            with httpx.Client(base_url=args.origin, trust_env=False, timeout=15) as client:
                samples = []
                for deck_id in args.decks:
                    response = client.get(f"/api/decks/{quote(deck_id, safe='')}/delivery-reviews")
                    response.raise_for_status()
                    samples.append(DeliverySummary.model_validate_json(response.content))
        print(json.dumps(summarize(samples), indent=2, allow_nan=False))
    except (OSError, ValueError, httpx.HTTPError) as exc:
        parser.exit(1, f"Cannot measure design reviews: {exc}\n")


if __name__ == "__main__":
    main()
