"""Delivery decisions belong to humans; a recorded visual self-revision belongs to the agent."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError

from artifact_studio import app as service
from artifact_studio.models import SlidePatch
from artifact_studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design
from tests.integration.multiplayer_fixture import running_multiplayer_app
from tests.test_hosted_design_authoring import peer, value
from tests.test_multiplayer_server import accept, agent, grant, human


def test_local_human_rounds_and_agent_self_revisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "review.sqlite3")
    deck_id = "2026-11-21-fictional-river-survey"
    _ = store.create(deck_id, "Fictional river survey")
    monkeypatch.setattr(service, "store", store)
    deck = f"/api/decks/{deck_id}"
    review = deck + "/delivery-reviews"
    with closing(TestClient(service.app)) as client:
        first = client.get(review).raise_for_status().json()
        before = client.get(deck).raise_for_status().json()["delivery_hash"]
        assert before == first["delivery_hash"] and first["first_delivery"] == "unrated"
        assert first["previous_review_count"] == first["agent_self_revisions"] == 0
        body = dict(
            stage="first",
            direction="redirect",
            factual=True,
            legibility=False,
            delivery_hash=before,
            request_id="first-direction",
        )
        assert (
            client.post(review, json={**body, "direction": "unrated", "factual": False}).status_code
            == 422
        )
        with ThreadPoolExecutor(max_workers=3) as pool:

            def post_first(i: int) -> int:
                return client.post(
                    review, json={**body, "request_id": f"first-direction-{i}"}
                ).status_code

            statuses = list(pool.map(post_first, range(3)))
        assert statuses == [200, 200, 200]
        assert client.get(review).json()["visual_redirect_rounds"] == 1
        assert client.post(review, json={**body, "direction": "accepted"}).status_code == 409
        assert client.get(review).json()["current_review"]["factual"] is True
        tracked = service.get_presentation_outline(deck_id, begin_self_review=True)
        assert tracked["delivery_hash"] == before
        slide = store.view(deck_id).slides[0]
        _ = store.save_notes(deck_id, slide.id, "Private rehearsal note", slide.notes)
        assert client.get(review).json()["delivery_hash"] == before
        _ = store.revise_view(
            deck_id,
            slide.id,
            store.view(deck_id).slides[0].expected_hash,
            "edited-title",
            SlidePatch(title="The river rose in March"),
        )
        after = cast(str, service.get_presentation_outline(deck_id)["delivery_hash"])
        assert before != after
        assert client.post(review, json=body).status_code == 409
        assert client.post(review, json={**body, "request_id": "first-direction-1"}).is_success
        assert (
            client.post(
                review, json={**body, "delivery_hash": after, "request_id": "repeat-first-delivery"}
            ).status_code
            == 409
        )
        for record_id, previous in (
            ("invalid-no-change", after),
            ("never-observed-before", "a" * 64),
        ):
            with pytest.raises(ToolError, match="visual change|Start the pass"):
                service.record_design_self_revision(deck_id, previous, after, record_id)
        assert service.record_design_self_revision(deck_id, before, after, "first-agent-pass")[
            "recorded"
        ]
        assert service.record_design_self_revision(
            deck_id, before, after, "second-request-same-delivery"
        )["recorded"]
        accepted = {
            **body,
            "stage": "follow_up",
            "direction": "accepted",
            "factual": False,
            "legibility": True,
            "delivery_hash": after,
            "request_id": "followup-decision",
        }
        summary = client.post(review, json=accepted).raise_for_status().json()
        assert summary["first_delivery"] == "redirect"
        assert summary["agent_self_revisions"] == 1
        assert summary["previous_review_count"] == 2
        assert (
            summary["visual_redirect_rounds"],
            summary["factual_correction_rounds"],
            summary["legibility_correction_rounds"],
        ) == (1, 1, 1)
        assert "Private rehearsal note" not in str(summary)
        (store.folder(deck_id) / "theme.css").write_text(".slide { color: navy }")
        new_hash = client.get(deck).json()["delivery_hash"]
        assert new_hash != after
        corrections_only = {
            **accepted,
            "delivery_hash": new_hash,
            "request_id": "followup-corrections",
            "direction": "unrated",
            "legibility": False,
            "factual": True,
        }
        corrected = client.post(review, json=corrections_only).raise_for_status().json()
        assert corrected["first_delivery"] == "redirect"
        assert corrected["factual_correction_rounds"] == 2
        assert corrected["visual_redirect_rounds"] == corrected["agent_self_revisions"] == 1
        with pytest.raises(ToolError, match="delivery changed"):
            service.record_design_self_revision(deck_id, before, after, "different-late-request")
        assert service.record_design_self_revision(deck_id, before, after, "first-agent-pass")[
            "recorded"
        ]
        assert client.post(review, json={**accepted, "legibility": False}).status_code == 409
        # A later project's first observed follow-up never becomes first-delivery success.
        other = "2026-11-21-fictional-existing-deck"
        _ = store.create(other, "Existing project")
        endpoint = f"/api/decks/{other}/delivery-reviews"
        token = client.get(endpoint).json()["delivery_hash"]
        late = (
            client.post(endpoint, json={**accepted, "delivery_hash": token})
            .raise_for_status()
            .json()
        )
        assert late["first_delivery"] == "unrated" and late["previous_review_count"] == 1


def test_hosted_delivery_scopes_and_human_agent_separation(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "server", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as commenter,
        human(app, "bob") as viewer,
    ):
        deck = f"/api/decks/{app.deck_id}"
        reviews = deck + "/delivery-reviews"
        assert commenter.get(reviews).status_code == 404
        accept(commenter, grant(owner, "alice", app.deck_id, "comment"))
        accept(viewer, grant(owner, "bob", app.deck_id, "view"))
        current = owner.get(reviews).raise_for_status().json()["delivery_hash"]
        assert current == viewer.get(deck).raise_for_status().json()["delivery_hash"]
        body = {
            "stage": "first",
            "direction": "accepted",
            "factual": False,
            "legibility": False,
            "request_id": "fictional-direction",
            "delivery_hash": current,
        }
        assert viewer.post(reviews, json=body).status_code == 404
        first = {**body, "factual": True}
        proposed = {**body, "direction": "redirect", "legibility": True}
        assert (
            commenter.post(reviews, json=first).raise_for_status().json()["first_delivery"]
            == "accepted"
        )
        assert owner.get(reviews).raise_for_status().json()["personal_review"] is None
        assert owner.post(reviews, json={**proposed, "stage": "follow_up"}).status_code == 409
        # Two independent people may reuse their own operation ID and disagree on the same delivery.
        for human_client, verdict in ((owner, proposed), (commenter, first), (owner, proposed)):
            round_state = human_client.post(reviews, json=verdict).raise_for_status().json()
            assert round_state["personal_review"]["direction"] == verdict["direction"]
            assert round_state["previous_review_count"] == 1
            assert round_state["current_review"] == {
                "stage": "first",
                "direction": "redirect",
                "factual": True,
                "legibility": True,
            }
            assert round_state["first_delivery"] == "redirect"
            assert (
                round_state["visual_redirect_rounds"],
                round_state["factual_correction_rounds"],
                round_state["legibility_correction_rounds"],
            ) == (1, 1, 1)
        aggregate = viewer.get(reviews).raise_for_status().json()
        assert aggregate["personal_review"] is None
        assert set(aggregate) == {
            "deck_id",
            "delivery_hash",
            "previous_review_count",
            "first_delivery",
            "visual_redirect_rounds",
            "factual_correction_rounds",
            "legibility_correction_rounds",
            "agent_self_revisions",
            "current_review",
            "personal_review",
        }
        assert (
            owner.post(reviews, json={**first, "request_id": "reconsidered-by-owner"}).status_code
            == 409
        )
        token = agent(owner, app.deck_id)
        with httpx.Client(
            base_url=app.url,
            headers={
                "host": "studio.example.test",
                "authorization": "Bearer " + token,
                "origin": "https://studio.example.test",
            },
        ) as bot:
            assert bot.post(reviews, json=body).status_code in {401, 403}

        async def exercise() -> None:
            async with peer(app, token) as session:
                tools = {tool.name for tool in (await session.list_tools()).tools}
                assert "record_design_self_revision" in tools
                assert "record_delivery_review" not in tools
                before = value(
                    await session.call_tool(
                        "get_presentation_outline",
                        {"deck_id": app.deck_id, "begin_self_review": True},
                    )
                )["delivery_hash"]
                slide = app.store.view(app.deck_id).slides[0]
                _ = app.store.revise_view(
                    app.deck_id,
                    slide.id,
                    slide.expected_hash,
                    "mock-agent-revision",
                    SlidePatch(title="Observed river level"),
                )
                after = value(
                    await session.call_tool("get_presentation_outline", {"deck_id": app.deck_id})
                )["delivery_hash"]
                args = {
                    "deck_id": app.deck_id,
                    "before_delivery_hash": before,
                    "after_delivery_hash": after,
                    "request_id": "self-review-pass",
                }
                assert value(await session.call_tool("record_design_self_revision", args)) == {
                    "recorded": True
                }
                assert (
                    await session.call_tool(
                        "record_design_self_revision", {**args, "deck_id": "fictional-inaccessible"}
                    )
                ).is_error

        asyncio.run(exercise())
        observed = owner.get(reviews).raise_for_status().json()
        assert observed["first_delivery"] == "redirect" and observed["agent_self_revisions"] == 1
        assert observed["personal_review"] is None
        assert owner.post(reviews, json=proposed).raise_for_status().json() == observed
        assert observed["previous_review_count"] == 1 and observed["current_review"] is None


def test_prior_anonymous_judgment_survives_personal_reviews(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "review.sqlite3")
    deck = "2026-11-21-fictional-existing-trial"
    _ = store.create(deck, "Existing trial")
    monkeypatch.setattr(service, "store", store)
    path = f"/api/decks/{deck}/delivery-reviews"
    with closing(TestClient(service.app)) as client:
        current = client.get(path).raise_for_status().json()["delivery_hash"]
        with store.connect() as db:
            _ = db.execute(
                "INSERT INTO delivery_reviews VALUES (?,?,?,?,?,?)",
                (deck, current, "first", "unrated", 1, 0),
            )
        old = client.get(path).raise_for_status().json()
        assert old["first_delivery"] == "unrated" and old["personal_review"] is None
        result = (
            client.post(
                path,
                json={
                    "stage": "first",
                    "direction": "unrated",
                    "legibility": True,
                    "delivery_hash": current,
                    "request_id": "personal-observation",
                },
            )
            .raise_for_status()
            .json()
        )
        assert result["previous_review_count"] == 1 and result["first_delivery"] == "unrated"
        assert result["current_review"]["factual"] and result["current_review"]["legibility"]
        assert not result["personal_review"]["factual"] and result["personal_review"]["legibility"]
        with store.connect() as db:
            anonymous = db.execute(
                "SELECT direction,factual,legibility FROM delivery_personal_reviews "
                "WHERE deck=? AND delivery_hash=? AND reviewer='legacy'",
                (deck, current),
            ).fetchone()
        assert tuple(anonymous) == ("unrated", 1, 0)
