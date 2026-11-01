"""Real MCP authoring recovery preserves identity, source ownership and visual baselines."""

import os

import pytest

from studio.agent_client import Client, Invocation, execute, object_value
from tests.integration.support import DECK_ID, RunningApp

pytestmark = [
    pytest.mark.agents,
    pytest.mark.skipif(
        os.environ.get("SLIDE_STUDIO_AGENT_TESTS") != "1", reason="Opt-in agent integration"
    ),
]


class LostAddResponse(Client):
    """Lose one client acknowledgement after the real server has committed the write."""

    lost: bool = False

    def call(self, invocation: Invocation) -> object:
        result = super().call(invocation)
        if invocation.tool == "add_slide" and not self.lost:
            self.lost = True
            raise OSError("Response lost after completed add")
        return result


def add_call(slide_id: str) -> Invocation:
    """Keep source and operation identity fixed across the whole retry experiment."""
    return Invocation(
        tool="add_slide",
        arguments={
            "deck_id": DECK_ID,
            "request_id": "retry-" + slide_id,
            "slide": {
                "id": slide_id,
                "title": "Evidence",
                "html": '<article class="slide">Evidence</article>',
            },
        },
    )


def test_lost_response_and_partial_batch_replay_over_actual_mcp(live_app: RunningApp) -> None:
    """A retry reuses the recorded prefix and reaches the original remaining call."""
    baseline = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    add = add_call("lost-add")
    revise = Invocation(
        tool="revise_slide",
        arguments={
            "deck_id": DECK_ID,
            "slide_id": "cover",
            "expected_hash": baseline["expected_hash"],
            "request_id": "recovery-revise",
            "patch": {"title": "Recovered authoring"},
        },
    )
    calls = [add, revise]
    lost = execute(LostAddResponse(live_app.url + "/mcp"), calls, verify=True)
    assert lost["ok"] is False and lost["next_call_index"] == 0
    assert "Response lost" in str(lost["error"])
    assert live_app.store.read(DECK_ID).slides[-1].id == "lost-add"
    recovered = execute(Client(live_app.url + "/mcp"), calls, verify=True)
    assert recovered["ok"] is True
    assert object_value(recovered["verification"])["matched"] == 2
    assert [slide.id for slide in live_app.store.read(DECK_ID).slides].count("lost-add") == 1
    bad_second = revise.model_copy(
        update={
            "arguments": {
                **revise.arguments,
                "request_id": "conflicting-second",
                "expected_hash": "stale",
            }
        }
    )
    partial_calls = [add_call("prefix-add"), bad_second]
    for _ in range(2):
        partial = execute(Client(live_app.url + "/mcp"), partial_calls, verify=True)
        assert partial["ok"] is False and partial["next_call_index"] == 1
        assert "prefix-add" in str(partial["completed"])
        assert "changed" in str(partial["error"])
    assert [slide.id for slide in live_app.store.read(DECK_ID).slides].count("prefix-add") == 1


def test_replayed_add_verification_detects_post_write_visual_changes(live_app: RunningApp) -> None:
    """Do not recompute an original acknowledgement against newer theme/assets."""
    calls = [add_call("visual-drift-add")]
    lost = execute(LostAddResponse(live_app.url + "/mcp"), calls, verify=True)
    assert lost["ok"] is False
    theme = live_app.store.folder(DECK_ID) / "theme.css"
    _ = theme.write_text(theme.read_text() + "\n.slide{background:#fff;}\n")
    result = execute(Client(live_app.url + "/mcp"), calls, verify=True)
    assert result["ok"] is False
    assert object_value(result["verification"])["matched"] == 0
    assert "visual-drift-add" in str(object_value(result["verification"])["changed_since_write"])


def test_create_schema_explains_id_and_returns_explicit_destinations(live_app: RunningApp) -> None:
    """Advertise the dated ID before a write and return the actual request origin in URLs."""
    tools = live_app.client().rpc("tools/list", {})
    assert "YYYY-MM-DD" in str(tools)
    assert "maxLength" in str(tools) and "lowercase-slug" in str(tools)
    with pytest.raises(ValueError, match="pattern"):
        _ = live_app.call("create_presentation", deck_id="undated-title", title="Invalid")
    created = live_app.call("create_presentation", deck_id="2026-11-01-url-receipt", title="URLs")
    assert created["workspace_url"] == live_app.url + "/?deck=2026-11-01-url-receipt"
    assert created["audience_url"] == live_app.url + "/api/decks/2026-11-01-url-receipt/audience"
