"""Real MCP transport and generated Codex/Claude hook subprocesses, without model keys."""

import asyncio
import base64
import json
import os
import shlex
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from artifact_studio.agent_client import Invocation, object_value
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.design_library import DesignDetail
from artifact_studio.hook_client import configure
from artifact_studio.models import DesignRequestInput, Slide
from artifact_studio.review import ReviewHookInput, Reviews
from tests.design_factory import make_design
from tests.integration.support import DECK_ID, PRIVATE_NOTE, RunningApp, markup

pytestmark = [
    pytest.mark.agents,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_AGENT_TESTS") != "1", reason="Opt-in agent integration"
    ),
]


class ReviewHooks:
    """Run the generated native hook command against this test's real HTTP service."""

    def __init__(
        self, live_app: RunningApp, artifacts: Path, harness: Literal["codex", "claude-code"]
    ) -> None:
        """Keep hook configuration and subprocess outputs inside disposable test folders."""
        self.live_app = live_app
        self.artifacts = artifacts
        self.harness = harness
        configuration = configure(harness, live_app.project, live_app.url, "artifact-studio", False)
        self.hook_map = object_value(object_value(json.loads(configuration.read_text()))["hooks"])

    def dispatch(self, event: str, **payload: object) -> dict[str, object]:
        """Execute the exact generated launcher and retain its host-facing JSON response."""
        groups = json.loads(json.dumps(self.hook_map[event]))
        command = str(groups[-1]["hooks"][0]["command"])
        result = subprocess.run(
            shlex.split(command),
            cwd=self.live_app.project,
            input=json.dumps({"session_id": "ci-session", **payload}),
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        )
        _ = (self.artifacts / f"{self.harness}-{event}.json").write_text(result.stdout)
        return object_value(json.loads(result.stdout))

    def response(self, value: dict[str, object]) -> object:
        """Use the structured Codex and string-wrapped Claude result envelopes."""
        if self.harness == "codex":
            return {"structuredContent": value}
        return json.dumps([{"type": "text", "text": json.dumps(value)}])


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
@pytest.mark.parametrize("extension", ["html", "md"])
def test_generated_artifact_hooks_wake_on_live_and_released_batch(
    live_app: RunningApp,
    artifacts: Path,
    tmp_path: Path,
    harness: Literal["codex", "claude-code"],
    extension: str,
) -> None:
    """Wake a locally waiting native adapter for generic artifacts without exposing drafts."""
    hooks = ReviewHooks(live_app, artifacts, harness)
    project = live_app.call("create_project", title="Fictional review")
    source = tmp_path / "authored"
    source.mkdir()
    entrypoint = "index." + extension
    _ = (source / entrypoint).write_text("<h1>Example</h1>" if extension == "html" else "# Example")
    item = live_app.call(
        "register_artifact",
        project_id=project["id"],
        title="Example",
        source_path=str(source),
        entrypoint=entrypoint,
    )
    identifier = str(item["id"])
    context = live_app.call("get_working_context", artifact_id=identifier)
    assert (
        hooks.dispatch(
            "PostToolUse",
            tool_name="mcp__artifact_studio__get_working_context",
            tool_input={"artifact_id": identifier},
            tool_response=hooks.response(context),
        )
        == {}
    )
    status = live_app.call("get_live_review_status", artifact_id=identifier)
    assert status["review_session"] == "ci-session" and status["review_harness"] == harness
    with ThreadPoolExecutor(max_workers=1) as worker:
        waiting = worker.submit(hooks.dispatch, "Stop")
        _ = live_app.post(
            f"/api/artifacts/{identifier}/feedback",
            {
                "message": "A human request",
                "request_id": "artifact-live-request",
                "content_hash": item["content_hash"],
            },
        )
        wake = waiting.result(timeout=10)
    assert wake["decision"] == "block"
    assert f"read_artifact_feedback(artifact_id={identifier!r}, after_delivery_id=0)" in str(
        wake["reason"]
    )
    assert "A human request" not in str(wake["reason"])
    feed = live_app.call("read_artifact_feedback", artifact_id=identifier)
    assert "A human request" in str(feed["events"])
    _ = hooks.dispatch(
        "PostToolUse",
        tool_name="mcp__artifact_studio__read_artifact_feedback",
        tool_input={"artifact_id": identifier},
        tool_response=hooks.response(feed),
    )
    body = ReviewHookInput(harness=harness, session_id="ci-session", event="Stop")
    assert not Reviews(live_app.store).pending(body).feedback_available
    _ = live_app.call(
        "reply_to_artifact_feedback",
        artifact_id=identifier,
        message="Agent reply",
        request_id="artifact-agent-reply",
        content_hash=item["content_hash"],
    )
    assert not Reviews(live_app.store).pending(body).feedback_available
    _ = live_app.post(f"/api/artifacts/{identifier}/mode", {"mode": "batch"})
    _ = live_app.post(
        f"/api/artifacts/{identifier}/feedback",
        {
            "message": "Private until release",
            "request_id": "artifact-batch-request",
            "content_hash": item["content_hash"],
        },
    )
    assert (
        not ArtifactStore(live_app.store)
        .feedback_feed(identifier, int(str(feed["next_cursor"])))
        .events
    )
    assert not Reviews(live_app.store).pending(body).feedback_available
    _ = live_app.post(f"/api/artifacts/{identifier}/release", {})
    assert hooks.dispatch("Stop")["decision"] == "block"
    _ = live_app.post(f"/api/artifacts/{identifier}/review/disconnect", {})
    assert hooks.dispatch("Stop") == {}
    assert not ArtifactStore(live_app.store).workflow(identifier).review_connected


def test_agent_briefs_and_opening_messages_keep_their_real_author(live_app: RunningApp) -> None:
    """MCP-originated text never becomes human feedback or wakes its own author."""

    def call(tool: str, **arguments: object) -> object:
        """Accept scalar message receipts as well as structured context responses."""
        return live_app.client().call(Invocation(tool=tool, arguments=arguments))

    deck_id = "2026-11-04-agent-attribution"
    _ = call(
        "create_presentation",
        deck_id=deck_id,
        title="Authorship",
        brief="Agent-prepared working brief",
        request_id="author-created-deck",
    )
    _ = call(
        "reply_to_presentation_feedback",
        deck_id=deck_id,
        message="I have prepared the initial outline.",
        request_id="author-opening-deck",
    )
    _ = call(
        "reply_to_feedback",
        deck_id=deck_id,
        slide_id="cover",
        message="Here is the opening slide.",
        request_id="author-opening-slide",
    )
    messages = live_app.store.comments(deck_id)
    assert len(messages) == 3 and all(item.author == "agent" for item in messages)
    assert all(item.reply_to is None for item in messages)
    assert not live_app.store.feedback_feed(deck_id).events
    _ = live_app.post(
        f"/api/decks/{deck_id}/feedback",
        {"message": "Human feedback stays human.", "request_id": "human-real-comment"},
    )
    feed = live_app.store.feedback_feed(deck_id)
    assert len(feed.events) == 1 and feed.events[0].author == "user"
    _ = call(
        "reply_to_presentation_feedback",
        deck_id=deck_id,
        event_id=feed.events[0].event_id,
        message="I will update it.",
        request_id="author-real-reply",
    )
    assert live_app.store.comments(deck_id)[-1].author == "agent"


def test_official_sdk_discovers_and_calls_tools(live_app: RunningApp) -> None:
    """Negotiate the supported protocol using the real MCP SDK, not a mocked client."""

    async def connect() -> None:
        async with (
            streamable_http_client(live_app.url + "/mcp") as (read, write),
            ClientSession(read, write, read_timeout_seconds=10) as session,
        ):
            initialized = await session.initialize()
            assert initialized.server_info.name == "Artifact Studio"
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            assert {
                "get_working_context",
                "list_design_contexts",
                "get_design_context",
                "ask_slide_question",
                "get_slide_questions",
                "cancel_slide_question",
                "set_presentation_sections",
                "get_slide",
                "revise_slide",
                "read_feedback",
                "start_live_review",
                "get_live_review_status",
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
            source = object_value(slide.structured_content)
            question = await session.call_tool(
                "ask_slide_question",
                {
                    "deck_id": DECK_ID,
                    "slide_id": "cover",
                    "question": "SDK audience question?",
                    "options": ["Team", "Customers"],
                    "expected_hash": source["expected_hash"],
                    "request_id": "sdk-question-001",
                },
            )
            assert not question.is_error
            questions = await session.call_tool("get_slide_questions", {"deck_id": DECK_ID})
            assert not questions.is_error and questions.structured_content is not None
            assert "SDK audience question?" in json.dumps(questions.structured_content)
            outline = await session.call_tool("get_presentation_outline", {"deck_id": DECK_ID})
            assert outline.structured_content is not None
            structure = object_value(outline.structured_content)
            layout = await session.call_tool(
                "set_presentation_sections",
                {
                    "deck_id": DECK_ID,
                    "sections": [
                        {
                            "id": "sdk-section",
                            "title": "SDK section",
                            "slide_ids": ["cover", "next"],
                        }
                    ],
                    "expected_slide_ids": ["cover", "next"],
                    "expected_sections_hash": structure["sections_hash"],
                },
            )
            assert not layout.is_error

    asyncio.run(connect())


def test_file_design_context_over_mcp(live_app: RunningApp) -> None:
    """The agent receives the same selected files as the UI and sees their removal."""
    design = DesignDetail.model_validate(
        live_app.post(
            "/api/design-library",
            {
                "name": "Product design",
                "files": [
                    {
                        "name": "layout.fig",
                        "data_base64": base64.b64encode(b"Fictional source").decode(),
                    }
                ],
            },
        )
    )
    project = live_app.call("create_project", title="Product launch", context="Keep it clear.")
    assert live_app.call("get_design_context", design_id=design.id) == design.model_dump()
    context = live_app.call("get_working_context", project_id=project["id"], design_id=design.id)
    assert context["design_context"] == design.model_dump()
    assert "pointer returned" in json.dumps(context["sources"])
    presentation = live_app.call("get_working_context", artifact_id=DECK_ID, design_id=design.id)
    assert presentation["design_context"] == design.model_dump()
    assert '"selected_design_id": "test-brand"' in json.dumps(presentation["presentation_context"])
    file = design.files[0]
    _ = live_app.post(
        f"/api/design-library/{design.id}/files/remove",
        {"path": file.path, "expected_version": file.version},
    )
    assert live_app.call("get_design_context", design_id=design.id)["files"] == []


def test_create_and_resume_with_two_designs(live_app: RunningApp, artifacts: Path) -> None:
    """Follow the compact startup over real MCP with a non-default brand and saved brief."""
    for color in ("orange", "blue"):
        folder = live_app.store.root / f"2026-11-01-{color}"
        make_design(folder, color)
        _ = (folder / "PREFERENCES.md").write_text(f"Use {color} accents.")
    _ = live_app.store.set_default_design("orange")
    assert live_app.call("get_working_context")["preferences"] == "Use orange accents."
    chosen = live_app.call("get_working_context", design_id="blue")
    assert chosen["preferences"] == "Use blue accents."
    deck_id = "2026-11-01-blue-story"
    brief = "Explain the trial results to the board with one recommendation."
    created = live_app.call(
        "create_presentation",
        deck_id=deck_id,
        title="Blue board update",
        design_id="blue",
        brief=brief,
        files=[
            {
                "name": "results.txt",
                "data_base64": base64.b64encode(b"Trial result: 42% improvement.").decode(),
            }
        ],
    )
    assert created["design_id"] == "blue"
    context = live_app.call("get_working_context", deck_id=deck_id)
    assert context["deck_id"] == deck_id and context["selected_design_id"] == "blue"
    assert context["default_design_id"] == "orange"
    assert context["preferences"] == "Use blue accents."
    assert "live_review_guide" not in context and context["asset_inventory_included"] is False
    assert context["workspace_url"] == f"{live_app.url}/?deck={deck_id}"
    assert context["mcp_url"] == live_app.url + "/mcp"
    assert "hooks" in str(live_app.call("get_authoring_guide", topic="live_review")["markdown"])
    outline = live_app.call("get_presentation_outline", deck_id=deck_id, include_context=True)
    assert brief in str(outline["context_markdown"])
    assert "results.txt" in str(outline["brief_files"])
    assert "html" not in str(outline["slides"])
    cached = live_app.call(
        "get_working_context", deck_id=deck_id, known_rules_hash=context["rules_hash"]
    )
    assert cached["rules_included"] is False and cached["authoring_instructions"] == ""
    assert cached["preferences"] == "Use blue accents."
    _ = live_app.post(f"/api/decks/{deck_id}/feedback-mode", {"mode": "batch"})
    _ = live_app.post(
        f"/api/decks/{deck_id}/feedback",
        {
            "message": "Withheld startup feedback",
            "request_id": "withheld-startup",
            "context_slide_id": "cover",
        },
    )
    assert "Withheld startup feedback" not in json.dumps(
        live_app.call("get_working_context", deck_id=deck_id)
    )
    assert "Withheld startup feedback" not in json.dumps(
        live_app.call("get_presentation_outline", deck_id=deck_id, include_context=True)
    )
    assert "Withheld startup feedback" not in json.dumps(
        live_app.call("read_feedback", deck_id=deck_id)
    )
    requested = live_app.call("start_live_review", deck_id=deck_id)
    assert requested["status"] == "awaiting_host_hook" and requested["review_connected"] is False
    status = live_app.call("get_live_review_status", deck_id=deck_id)
    assert status["status"] == "unavailable" and "guide" not in status
    assert status["workspace_url"] == context["workspace_url"]
    assert status["mcp_url"] == context["mcp_url"]
    with pytest.raises(ValueError, match="does not match"):
        _ = live_app.call("get_working_context", deck_id=deck_id, design_id="orange")
    full = live_app.call("get_working_context", deck_id=deck_id, detail="full")
    assert full["preferences"] == context["preferences"]
    assert len(json.dumps(context)) < len(json.dumps(full)) / 2
    _ = (artifacts / "startup-simulation.json").write_text(
        json.dumps(
            {
                "default_design": "orange",
                "resumed_design": context["selected_design_id"],
                "brief_received": brief in str(outline["context_markdown"]),
                "compact_characters": len(json.dumps(context)),
                "full_characters": len(json.dumps(full)),
                "initial_review_status": status["status"],
                "workspace_url": context["workspace_url"],
            },
            indent=2,
        )
    )


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


def test_compact_authoring_and_retention_over_mcp(live_app: RunningApp, artifacts: Path) -> None:
    """Measure actual text/structured envelopes while checking complete opt-in source access."""
    client = live_app.client()
    sizes: dict[str, int] = {}

    def call(label: str, tool: str, **arguments: object) -> dict[str, object]:
        """Read the real MCP result and require compatible text and structured copies."""
        raw = client.rpc("tools/call", {"name": tool, "arguments": arguments})
        result = CallToolResult.model_validate(raw)
        assert not result.is_error and result.structured_content is not None
        text = result.content[0]
        assert isinstance(text, TextContent)
        value = object_value(json.loads(text.text))
        structured = result.structured_content
        if set(structured) == {"result"}:
            structured = object_value(structured["result"])
        assert value == structured
        sizes[label] = len(json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode())
        return value

    initial = call("startup", "get_working_context", deck_id=DECK_ID)
    retained = call(
        "startup_retained",
        "get_working_context",
        deck_id=DECK_ID,
        known_rules_hash=initial["rules_hash"],
        known_guide_hash=initial["guide_hash"],
        include_discovery=False,
    )
    assert retained["guide_included"] is False and retained["guide"] == ""
    assert retained["rules_included"] is False
    assert retained["design_references"] == initial["design_references"]
    assert retained["preferences"] == initial["preferences"]
    guide = call("guide", "get_authoring_guide", topic="live_review")
    cached = call(
        "guide_retained", "get_authoring_guide", topic="live_review", known_hash=guide["hash"]
    )
    assert cached["markdown"] == "" and cached["content_status"] == "unchanged"
    compact = call("review_start", "start_live_review", deck_id=DECK_ID)
    full = call("review_start_full", "start_live_review", deck_id=DECK_ID, include_guide=True)
    assert compact["status"] == full["status"] == "awaiting_host_hook"
    assert "guide" not in compact and full["guide"] == guide["markdown"]
    assert sizes["review_start"] < sizes["review_start_full"] / 5
    assert sizes["guide_retained"] < sizes["guide"] / 10
    created = call(
        "create",
        "create_presentation",
        deck_id="2026-11-01-compact-created",
        title="Compact created",
        brief="Full brief for the board",
    )
    assert created["slide_ids"] == ["cover"] and "slides" not in created
    full_created = call(
        "create_full",
        "create_presentation",
        deck_id="2026-11-01-full-created",
        title="Full created",
        include_content=True,
    )
    assert "slides" in full_created and "html" in str(full_created["slides"])
    _ = live_app.store.add_slide(
        DECK_ID,
        Slide(
            id="large",
            title="Large",
            html='<article class="slide">' + "Source evidence " * 6000 + "</article>",
        ),
    )
    previous = [slide.id for slide in live_app.store.read(DECK_ID).slides]
    order = list(reversed(previous))
    receipt = call(
        "reorder", "reorder_slides", deck_id=DECK_ID, slide_ids=order, expected_slide_ids=previous
    )
    expanded = call(
        "reorder_full", "reorder_slides", deck_id=DECK_ID, slide_ids=order, include_content=True
    )
    assert receipt["slide_ids"] == order and "slides" not in receipt
    assert "Source evidence" in str(expanded["slides"])
    assert sizes["reorder"] < sizes["reorder_full"] / 100
    slide = live_app.store.read(DECK_ID).slides[-1]
    for number in range(12):
        _ = live_app.store.add_feedback(
            DECK_ID,
            slide.id,
            "Discussion " * 100,
            live_app.store.content_hash(DECK_ID, slide),
            f"compact-history-{number}",
        )
    source = call("edit_source", "get_slide", deck_id=DECK_ID, slide_id=slide.id)
    history = call(
        "edit_source_full", "get_slide", deck_id=DECK_ID, slide_id=slide.id, include_feedback=True
    )
    assert source["notes"] == history["notes"] == PRIVATE_NOTE
    assert source["html"] == history["html"] and source["expected_hash"] == history["expected_hash"]
    assert source["feedback_included"] is False and history["feedback_included"] is True
    assert sizes["edit_source"] < sizes["edit_source_full"] / 5
    request = live_app.store.post_design_request(
        DesignRequestInput(
            message="Inspect a design reference " * 100, request_id="compact-design-request"
        )
    )
    feed = call(
        "feedback", "read_feedback", deck_id=DECK_ID, known_rules_hash=initial["rules_hash"]
    )
    feed_uncached = call(
        "feedback_uncached",
        "read_feedback",
        deck_id=DECK_ID,
        after_delivery_id=feed["next_cursor"],
        known_rules_hash=initial["rules_hash"],
    )
    feed_retained = call(
        "feedback_retained",
        "read_feedback",
        deck_id=DECK_ID,
        after_delivery_id=feed["next_cursor"],
        known_rules_hash=initial["rules_hash"],
        known_design_requests_hash=feed["design_requests_hash"],
    )
    assert feed_retained["design_requests_included"] is False
    assert feed_retained["events"] == feed_retained["design_requests"] == []
    assert feed_uncached["events"] == [] and feed_uncached["design_requests"]
    assert sizes["feedback_retained"] < sizes["feedback_uncached"] / 5
    assert live_app.store.design_inbox().messages[-1].event_id == request.event_id
    assert live_app.store.design_inbox().messages[-1].status == "queued"
    _ = (artifacts / "token-efficiency.json").write_text(
        json.dumps(
            {
                "measurement": (
                    "UTF-8 bytes of MCP result JSON, including text and structured copies"
                ),
                "not_billed_tokens": True,
                "sizes": sizes,
            },
            indent=2,
        )
    )


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_generated_hooks_deliver_released_batch_and_end_session(
    live_app: RunningApp, artifacts: Path, harness: Literal["codex", "claude-code"]
) -> None:
    """Execute the generated launcher over HTTP for both native hook response formats."""
    hooks = ReviewHooks(live_app, artifacts, harness)
    dispatch, response = hooks.dispatch, hooks.response

    start = live_app.call("get_working_context", deck_id=DECK_ID, include_discovery=False)
    assert not live_app.store.workflow(DECK_ID).review_connected
    assert (
        dispatch(
            "PostToolUse",
            tool_name="mcp__artifact_studio__get_working_context",
            tool_input={"deck_id": DECK_ID},
            tool_response=response(start),
        )
        == {}
    )
    assert live_app.store.workflow(DECK_ID).review_harness == harness
    connected = live_app.call("get_live_review_status", deck_id=DECK_ID)
    assert connected["status"] == "connected"
    assert connected["review_harness"] == harness and connected["review_session"] == "ci-session"
    with live_app.store.connect() as database:
        expired = time.time() - 1
        _ = database.execute("UPDATE review_sessions SET expires_at=?", (expired,))
    unavailable = live_app.call("get_live_review_status", deck_id=DECK_ID)
    assert unavailable["status"] == "unavailable"
    assert (
        dispatch(
            "PostToolUse",
            tool_name="mcp__artifact_studio__get_live_review_status",
            tool_input={"deck_id": DECK_ID},
            tool_response=response(unavailable),
        )
        == {}
    )
    with live_app.store.connect() as database:
        assert database.execute("SELECT expires_at FROM review_sessions").fetchone()[0] == expired
    _ = dispatch(
        "PostToolUse",
        tool_name="mcp__artifact_studio__start_live_review",
        tool_input={"deck_id": DECK_ID},
        tool_response=response(live_app.call("start_live_review", deck_id=DECK_ID)),
    )
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
            tool_name="mcp__artifact_studio__read_feedback",
            tool_input={"deck_id": DECK_ID},
            tool_response=response(feed),
        )
        == {}
    )
    with live_app.store.connect() as database:
        acknowledged = database.execute(
            "SELECT feedback_cursor FROM review_sessions WHERE harness=? AND session_id=?",
            (harness, "ci-session"),
        ).fetchone()
    assert acknowledged is not None
    assert acknowledged["feedback_cursor"] == feed["next_cursor"]
    cursor = feed["next_cursor"]
    source = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    question = live_app.call(
        "ask_slide_question",
        deck_id=DECK_ID,
        slide_id="cover",
        question="Which audience should this slide address?",
        options=["Team", "Customers"],
        expected_hash=source["expected_hash"],
        request_id="native-question-001",
    )
    _ = dispatch(
        "PostToolUse",
        tool_name="mcp__artifact_studio__ask_slide_question",
        tool_input={"deck_id": DECK_ID, "slide_id": "cover"},
        tool_response=response(question),
    )
    answer = live_app.post(
        f"/api/decks/{DECK_ID}/slides/cover/questions/{question['event_id']}/answer",
        {"answer": "Customers", "request_id": "native-question-answer"},
    )
    assert answer["released"] is False
    assert not live_app.call("read_feedback", deck_id=DECK_ID, after_delivery_id=cursor)["events"]
    _ = live_app.post(f"/api/decks/{DECK_ID}/send-batch", {})
    assert dispatch("Stop")["decision"] == "block"
    delivered = live_app.call("read_feedback", deck_id=DECK_ID, after_delivery_id=cursor)
    assert "Customers" in str(delivered["events"])
    assert live_app.store.feedback_feed(DECK_ID).events[-1].reply_to == question["event_id"]
    _ = dispatch(
        "PostToolUse",
        tool_name="mcp__artifact_studio__read_feedback",
        tool_input={"deck_id": DECK_ID},
        tool_response=response(delivered),
    )
    assert dispatch("SessionEnd") == {}
    assert not live_app.store.workflow(DECK_ID).review_connected
    assert live_app.call("get_live_review_status", deck_id=DECK_ID)["status"] == "unavailable"
    assert dispatch("Stop") == {}


def test_official_sdk_replays_creation(live_app: RunningApp) -> None:
    """The SDK exposes creation retry identity and returns the original receipt."""

    async def run() -> None:
        async with (
            streamable_http_client(live_app.url + "/mcp") as (read, write),
            ClientSession(read, write, read_timeout_seconds=10) as session,
        ):
            _ = await session.initialize()
            args = {
                "deck_id": "2026-11-01-sdk-creation",
                "title": "SDK creation",
                "brief": "Fictional brief",
                "request_id": "sdk-creation-retry-001",
            }
            first = await session.call_tool("create_presentation", args)
            second = await session.call_tool("create_presentation", args)
            assert not first.is_error and not second.is_error
            assert first.structured_content == second.structured_content
            assert first.structured_content is not None
            assert "sdk-creation" in str(first.structured_content)

    asyncio.run(run())
