"""Agents place slides into an earlier section in one guarded, replayable MCP call."""

import os

import pytest

from studio.agent_client import Client, Invocation, execute
from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_authoring_recovery import LostWriteResponse

pytestmark = [
    pytest.mark.agents,
    pytest.mark.skipif(
        os.environ.get("SLIDE_STUDIO_AGENT_TESTS") != "1", reason="Opt-in agent integration"
    ),
]


def prepare_groups(live_app: RunningApp) -> dict[str, object]:
    """Create the existing section layout before the agent's insertion attempt."""
    initial = live_app.call("get_presentation_outline", deck_id=DECK_ID)
    _ = live_app.call(
        "set_presentation_sections",
        deck_id=DECK_ID,
        sections=[
            {"id": "intro", "title": "Introduction", "slide_ids": ["cover"]},
            {"id": "body", "title": "Body", "slide_ids": ["next"]},
        ],
        expected_slide_ids=["cover", "next"],
        expected_sections_hash=initial["sections_hash"],
    )
    return live_app.call("get_presentation_outline", deck_id=DECK_ID)


def insertion_call(outline: dict[str, object]) -> Invocation:
    """Keep the insertion target and baseline fixed through exact retries."""
    return Invocation(
        tool="add_slide",
        arguments={
            "deck_id": DECK_ID,
            "request_id": "guarded-early-section-add",
            "after_slide_id": "cover",
            "section_id": "intro",
            "expected_slide_ids": ["cover", "next"],
            "expected_sections_hash": outline["sections_hash"],
            "slide": {"id": "evidence", "title": "Evidence", "html": "<article>Evidence</article>"},
        },
    )


def test_positioned_add_recovers_lost_response_without_reordering_again(
    live_app: RunningApp,
) -> None:
    """Retain placement on the first write and replay without adding or moving another slide."""
    call = insertion_call(prepare_groups(live_app))
    lost = execute(LostWriteResponse(live_app.url + "/mcp"), [call], verify=True)
    assert not lost["ok"] and lost["next_call_index"] == 0
    current = live_app.store.read(DECK_ID)
    assert [item.id for item in current.slides] == ["cover", "evidence", "next"]
    assert current.sections[0].slide_ids == ["cover", "evidence"]
    replay = execute(Client(live_app.url + "/mcp"), [call], verify=True)
    assert replay["ok"]
    assert [item.id for item in live_app.store.read(DECK_ID).slides].count("evidence") == 1


def test_positioned_add_rejects_a_concurrent_section_change(live_app: RunningApp) -> None:
    """A renamed destination must be reread rather than silently accepting an old insertion."""
    outline = prepare_groups(live_app)
    call = insertion_call(outline)
    _ = live_app.call(
        "set_presentation_sections",
        deck_id=DECK_ID,
        sections=[
            {"id": "intro", "title": "New introduction", "slide_ids": ["cover"]},
            {"id": "body", "title": "Body", "slide_ids": ["next"]},
        ],
        expected_slide_ids=["cover", "next"],
        expected_sections_hash=outline["sections_hash"],
    )
    result = execute(Client(live_app.url + "/mcp"), [call], verify=True)
    assert not result["ok"] and "changed" in str(result["error"])
    assert [item.id for item in live_app.store.read(DECK_ID).slides] == ["cover", "next"]
    assert live_app.store.read(DECK_ID).sections[0].title == "New introduction"
