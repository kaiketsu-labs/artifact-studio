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


class LostWriteResponse(Client):
    """Lose one client acknowledgement after the real server has committed the write."""

    lost: bool = False
    tool_to_lose: str = "add_slide"

    def call(self, invocation: Invocation) -> object:
        result = super().call(invocation)
        if invocation.tool == self.tool_to_lose and not self.lost:
            self.lost = True
            raise OSError(f"Response lost after completed {invocation.tool}")
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
    lost = execute(LostWriteResponse(live_app.url + "/mcp"), calls, verify=True)
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
    lost = execute(LostWriteResponse(live_app.url + "/mcp"), calls, verify=True)
    assert lost["ok"] is False
    theme = live_app.store.folder(DECK_ID) / "theme.css"
    _ = theme.write_text(theme.read_text() + "\n.slide{background:#fff;}\n")
    result = execute(Client(live_app.url + "/mcp"), calls, verify=True)
    assert result["ok"] is False
    assert object_value(result["verification"])["matched"] == 0
    assert "visual-drift-add" in str(object_value(result["verification"])["changed_since_write"])


@pytest.mark.parametrize("drift", ["none", "theme", "asset", "notes", "deleted"])
def test_lost_revision_response_replays_original_receipt_over_actual_mcp(
    live_app: RunningApp, drift: str
) -> None:
    """Recover transport failures without blessing later changes or restoring a deleted slide."""
    before = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    calls = [
        Invocation(
            tool="revise_slide",
            arguments={
                "deck_id": DECK_ID,
                "slide_id": "cover",
                "expected_hash": before["expected_hash"],
                "request_id": "lost-revision-response",
                "patch": {"title": "Recorded revision"},
            },
        )
    ]
    client = LostWriteResponse(live_app.url + "/mcp")
    client.tool_to_lose = "revise_slide"
    lost = execute(client, calls, verify=True)
    assert lost["ok"] is False and lost["next_call_index"] == 0
    assert "Response lost" in str(lost["error"])
    written = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    assert written["revision"] == int(str(before["revision"])) + 1
    folder = live_app.store.folder(DECK_ID)
    if drift == "theme":
        theme = folder / "theme.css"
        _ = theme.write_text(theme.read_text() + "\n.slide{background:#fff;}\n")
    elif drift == "asset":
        _ = (folder / "assets/new-reference.svg").write_text("<svg>New artwork</svg>")
    elif drift == "notes":
        live_app.store.save_notes(DECK_ID, "cover", "Human changed notes", str(written["notes"]))
    elif drift == "deleted":
        live_app.store.remove_slide(DECK_ID, "cover", str(written["expected_hash"]))
    result = execute(Client(live_app.url + "/mcp"), calls, verify=True)
    verification = object_value(result["verification"])
    assert result["ok"] is (drift == "none")
    assert verification["matched"] == (1 if drift == "none" else 0)
    if drift != "none":
        assert "cover" in str(verification["changed_since_write"])
    if drift == "deleted":
        assert "cover" not in [slide.id for slide in live_app.store.read(DECK_ID).slides]
    else:
        current = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
        assert current["revision"] == written["revision"]
        if drift == "notes":
            assert current["notes"] == "Human changed notes"
        else:
            assert current["notes"] == before["notes"]


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
