"""Real MCP transport and generated Codex/Claude hook subprocesses, without model keys."""

import asyncio
import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Literal

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from studio.agent_client import object_value
from studio.hook_client import configure
from tests.integration.support import DECK_ID, PRIVATE_NOTE, RunningApp, markup

pytestmark = [
    pytest.mark.agents,
    pytest.mark.skipif(
        os.environ.get("SLIDE_STUDIO_AGENT_TESTS") != "1", reason="Opt-in agent integration"
    ),
]


def test_official_sdk_discovers_and_calls_tools(live_app: RunningApp) -> None:
    """Negotiate the supported protocol using the real MCP SDK, not a mocked client."""

    async def connect() -> None:
        async with (
            streamable_http_client(live_app.url + "/mcp") as (read, write),
            ClientSession(read, write, read_timeout_seconds=10) as session,
        ):
            initialized = await session.initialize()
            assert initialized.server_info.name == "Slide Studio"
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            assert {
                "get_working_context",
                "get_slide",
                "revise_slide",
                "read_feedback",
                "start_live_review",
            } <= names
            assert not any("approve" in name for name in names)
            context = await session.call_tool("get_working_context")
            assert not context.is_error
            assert context.structured_content is not None
            assert str(live_app.store.root) in json.dumps(context.structured_content)
            slide = await session.call_tool("get_slide", {"deck_id": DECK_ID, "slide_id": "cover"})
            assert not slide.is_error
            assert slide.structured_content is not None
            assert "expected_hash" in json.dumps(slide.structured_content)

    asyncio.run(connect())


def test_stateless_edit_retry_and_conflict(live_app: RunningApp) -> None:
    """Separate connections preserve optimistic concurrency, notes and idempotent retries."""
    _ = live_app.call("get_working_context")
    current = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    arguments: dict[str, object] = {
        "deck_id": DECK_ID,
        "slide_id": "cover",
        "expected_hash": current["expected_hash"],
        "request_id": "agent-idempotent-write",
        "patch": {"html": markup("An agent revision")},
    }
    first = live_app.call("revise_slide", **arguments)
    retry = live_app.call("revise_slide", **arguments)
    assert first == retry
    assert first["approved"] is False
    with pytest.raises(ValueError, match="changed|conflict|hash"):
        _ = live_app.call("revise_slide", **{**arguments, "request_id": "stale-second-write"})
    assert live_app.store.read(DECK_ID).slides[0].notes == PRIVATE_NOTE


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_generated_hooks_deliver_released_batch_and_end_session(
    live_app: RunningApp, artifacts: Path, harness: Literal["codex", "claude-code"]
) -> None:
    """Execute the generated launcher over HTTP for both native hook response formats."""
    configuration = configure(harness, live_app.project, live_app.url, "slide-studio", False)
    hooks = object_value(json.loads(configuration.read_text()))["hooks"]
    hook_map = object_value(hooks)

    def dispatch(event: str, **payload: object) -> dict[str, object]:
        """Run exactly the shell command generated for this harness and lifecycle event."""
        groups = json.loads(json.dumps(hook_map[event]))
        command = str(groups[-1]["hooks"][0]["command"])
        result = subprocess.run(
            shlex.split(command),
            cwd=live_app.project,
            input=json.dumps({"session_id": "ci-session", **payload}),
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        )
        _ = (artifacts / f"{harness}-{event}.json").write_text(result.stdout)
        return object_value(json.loads(result.stdout))

    def response(value: dict[str, object]) -> object:
        """Use the structured Codex and string-wrapped Claude result envelopes."""
        if harness == "codex":
            return {"structuredContent": value}
        return json.dumps([{"type": "text", "text": json.dumps(value)}])

    start = live_app.call("start_live_review", deck_id=DECK_ID)
    assert (
        dispatch(
            "PostToolUse",
            tool_name="mcp__slide_studio__start_live_review",
            tool_input={"deck_id": DECK_ID},
            tool_response=response(start),
        )
        == {}
    )
    assert live_app.store.workflow(DECK_ID).review_harness == harness
    _ = live_app.post(f"/api/decks/{DECK_ID}/feedback-mode", {"mode": "batch"})
    _ = live_app.post(
        f"/api/decks/{DECK_ID}/feedback",
        {
            "message": "Queued CI feedback",
            "request_id": "ci-batch-comment",
            "context_slide_id": "cover",
        },
    )
    assert not live_app.call("read_feedback", deck_id=DECK_ID)["events"]
    _ = live_app.post(f"/api/decks/{DECK_ID}/send-batch", {})
    wake = dispatch("Stop")
    assert wake["decision"] == "block"
    assert "read_feedback" in str(wake["reason"])
    feed = live_app.call("read_feedback", deck_id=DECK_ID)
    assert "Queued CI feedback" in str(feed["events"])
    assert (
        dispatch(
            "PostToolUse",
            tool_name="mcp__slide_studio__read_feedback",
            tool_input={"deck_id": DECK_ID},
            tool_response=response(feed),
        )
        == {}
    )
    assert dispatch("SessionEnd") == {}
    assert not live_app.store.workflow(DECK_ID).review_connected
    assert dispatch("Stop") == {}
