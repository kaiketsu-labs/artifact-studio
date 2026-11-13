"""Tests for project hooks and review event handling."""

import json
import re
import shlex
from pathlib import Path
from urllib.error import HTTPError

import pytest

from artifact_studio import hook_client as client


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
@pytest.mark.parametrize("tool", ["get_working_context", "create_presentation", "revise_slide"])
def test_successful_presentation_work_requests_automatic_binding(harness: str, tool: str) -> None:
    """Attach the actual selected deck without an explicit start tool or extra context text."""
    payload: dict[str, object] = {
        "session_id": "author",
        "tool_name": "mcp__artifact_studio__" + tool,
        "tool_input": {"deck_id": "2026-11-04-story"},
        "tool_response": {"structuredContent": {"id": "2026-11-04-story"}},
    }
    normalized = client.normalize(harness, "PostToolUse", "artifact-studio", payload)
    assert normalized["auto_start"] is True
    assert normalized["deck_id"] == "2026-11-04-story"
    payload["tool_response"] = {"isError": True, "content": []}
    assert client.normalize(harness, "PostToolUse", "artifact-studio", payload) == {}


def test_artifact_context_binds_the_selected_artifact_or_legacy_deck() -> None:
    """Attach only the selected artifact, preserving legacy deck and discovery behavior."""
    payload: dict[str, object] = {
        "session_id": "author",
        "tool_name": "mcp__artifact_studio__get_working_context",
        "tool_input": {"artifact_id": "artifact-one"},
        "tool_response": {"structuredContent": {"artifact": {"id": "artifact-one"}}},
    }
    normalized = client.normalize("codex", "PostToolUse", "artifact-studio", payload)
    assert normalized["artifact_id"] == "artifact-one" and normalized["auto_start"] is True
    payload["tool_response"] = {
        "structuredContent": {
            "presentation_context": {"deck_id": "2026-11-04-story"},
        }
    }
    normalized = client.normalize("codex", "PostToolUse", "artifact-studio", payload)
    assert normalized["deck_id"] == "2026-11-04-story" and normalized["auto_start"] is True
    payload["tool_input"] = {}
    payload["tool_response"] = {"structuredContent": {"designs": []}}
    assert "auto_start" not in client.normalize("codex", "PostToolUse", "artifact-studio", payload)


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
@pytest.mark.parametrize(
    "tool",
    [
        "get_artifact",
        "register_artifact",
        "revise_artifact",
        "capture_artifact",
        "read_artifact_feedback",
        "reply_to_artifact_feedback",
    ],
)
def test_generic_authoring_and_feedback_attach_without_echoing_content(
    harness: str, tool: str
) -> None:
    """Normalize common artifact tools and acknowledge only successful delivery-feed reads."""
    arguments = {} if tool == "register_artifact" else {"artifact_id": "artifact-one"}
    payload: dict[str, object] = {
        "session_id": "author",
        "tool_name": "mcp__artifact_studio__" + tool,
        "tool_input": arguments,
        "tool_response": {
            "structuredContent": {
                "id": "artifact-one",
                "next_cursor": 17,
                "message": "private body",
            }
        },
    }
    result = client.normalize(harness, "PostToolUse", "artifact-studio", payload)
    assert result["artifact_id"] == "artifact-one" and result["auto_start"] is True
    assert ("seen_cursor" in result) is (tool == "read_artifact_feedback")
    assert "private body" not in json.dumps(result)
    payload["tool_response"] = {"isError": True, "structuredContent": {"id": "artifact-one"}}
    assert client.normalize(harness, "PostToolUse", "artifact-studio", payload) == {}


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_configure_merge_and_quote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, harness: str
) -> None:
    """Preserve unrelated hooks and generate stable shell-safe commands."""
    monkeypatch.setattr(client.shutil, "which", lambda _: "/tmp/uv with spaces")  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    target = tmp_path / (
        ".codex/hooks.json" if harness == "codex" else ".claude/settings.local.json"
    )
    target.parent.mkdir()
    target.write_text(json.dumps({"other": 1, "hooks": {"Stop": [{"hooks": []}]}}))
    client.configure(harness, tmp_path, "http://127.0.0.1:8790", "artifact studio", False)
    before = target.read_text()
    client.configure(harness, tmp_path, "http://127.0.0.1:8790", "artifact studio", False)
    assert target.read_text() == before
    data = json.loads(before)
    assert data["other"] == 1
    assert data["hooks"]["Stop"][0] == {"hooks": []}
    handler = data["hooks"]["Stop"][1]["hooks"][0]
    assert handler["timeout"] == 600
    assert shlex.split(handler["command"])[0] == str(Path("/tmp/uv with spaces").resolve())
    assert shlex.split(handler["command"])[1] == "--no-cache"
    assert ("Interrupt" in data["hooks"]) is (harness == "codex")
    assert "StopFailure" not in data["hooks"]


@pytest.mark.parametrize(
    ("harness", "path"), [("codex", ".codex/hooks.json"), ("claude-code", ".claude/settings.json")]
)
def test_configure_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, harness: str, path: str
) -> None:
    """Write user-level hooks so a session in any folder can join live review."""
    monkeypatch.setattr(client.shutil, "which", lambda _: "/tmp/uv")  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    monkeypatch.setattr(client.Path, "home", lambda: tmp_path)  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    target = client.configure(harness, None, "http://127.0.0.1:8790", "artifact-studio", False)
    assert target == tmp_path / path
    assert set(json.loads(target.read_text())["hooks"]) >= {"PostToolUse", "Stop", "SessionEnd"}


def test_configure_passive_claude_mode(tmp_path: Path) -> None:
    """Replace live Claude hooks with a background check and preserve unrelated hooks."""
    url = "https://studio.example.test"
    target = client.configure("claude-code", tmp_path, url, "artifact-studio", False)
    original = json.loads(target.read_text())
    unrelated = {"type": "command", "command": "echo keep-this", "timeout": 1}
    original["hooks"]["Stop"][0]["hooks"].append(unrelated)
    target.write_text(json.dumps(original))

    _ = client.configure("claude-code", tmp_path, url, "artifact-studio", False, passive=True)
    configured = json.loads(target.read_text())
    assert set(configured["hooks"]) == {"PostToolUse", "Stop"}
    assert configured["hooks"]["Stop"] == [{"hooks": [unrelated]}]
    group = configured["hooks"]["PostToolUse"][0]
    matcher = re.compile(group["matcher"])
    for tool in ("get_working_context", "revise_slide", "publish_element_alternatives"):
        assert matcher.fullmatch(f"mcp__artifact_studio__{tool}")
    assert not matcher.fullmatch("mcp__artifact_studio__read_feedback")
    assert len(group["hooks"]) == 1
    handler = group["hooks"][0]
    assert handler["async"] is True
    assert "asyncRewake" not in handler
    assert shlex.split(handler["command"])[-2:] == ["--claude-mode", "passive"]

    before = target.read_text()
    _ = client.configure("claude-code", tmp_path, url, "artifact-studio", False, passive=True)
    assert target.read_text() == before
    _ = client.configure("claude-code", tmp_path, url, "artifact-studio", False)
    restored = json.loads(target.read_text())
    assert restored["hooks"]["PostToolUse"] == original["hooks"]["PostToolUse"]
    assert restored["hooks"]["Stop"][0] == {"hooks": [unrelated]}
    assert len(restored["hooks"]["Stop"]) == 2


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_migrate_cache_dependent_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, harness: str
) -> None:
    """Replace legacy launchers without duplicate listeners or lost unrelated hooks."""
    monkeypatch.setattr(client.shutil, "which", lambda _: "/tmp/uv")  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    target = client.configure(harness, tmp_path, "http://localhost:8790", "artifact-studio", False)
    data = json.loads(target.read_text())
    unrelated = {"type": "command", "command": "echo keep-this", "timeout": 1}
    for groups in data["hooks"].values():
        generated = groups[0]["hooks"][0]
        arguments = shlex.split(generated["command"])
        arguments.remove("--no-cache")
        generated["command"] = shlex.join(arguments)
        groups[0]["hooks"].append(unrelated)
    data["other"] = {"keep": True}
    target.write_text(json.dumps(data))
    _ = client.configure(harness, tmp_path, "http://localhost:8799", "artifact-studio", False)
    migrated = json.loads(target.read_text())
    assert migrated["other"] == {"keep": True}
    for groups in migrated["hooks"].values():
        handlers = [handler for group in groups for handler in group["hooks"]]
        assert handlers[0] == unrelated
        assert len(handlers) == 2
        assert "--no-cache" in shlex.split(handlers[1]["command"])
        assert "http://localhost:8799" in shlex.split(handlers[1]["command"])
    before = target.read_text()
    _ = client.configure(harness, tmp_path, "http://localhost:8799", "artifact-studio", False)
    assert target.read_text() == before


def test_normalize_cursors_and_failures() -> None:
    """Bind only successful matching tools and retain observed cursors."""
    payload: dict[str, object] = {
        "session_id": "s",
        "tool_name": "mcp__artifact_studio__get_presentation",
        "tool_input": {"deck_id": "d"},
        "tool_response": {
            "structuredContent": {
                "feedback": [{"delivery_id": 2}],
                "slides": [{"feedback": [{"delivery_id": 5}]}],
            }
        },
    }
    assert "seen_cursor" not in client.normalize("codex", "PostToolUse", "artifact-studio", payload)
    payload["tool_name"] = "mcp__artifact-studio__read_feedback"
    payload["tool_response"] = {"content": [{"type": "text", "text": '{"next_cursor": 7}'}]}
    assert client.normalize("codex", "PostToolUse", "artifact-studio", payload)["seen_cursor"] == 7
    for harness in ("codex", "claude-code"):
        for shape in ('{"next_cursor": 7}', [{"type": "text", "text": '{"next_cursor": 7}'}]):
            payload["tool_response"] = shape
            assert (
                client.normalize(harness, "PostToolUse", "artifact-studio", payload)["seen_cursor"]
                == 7
            )
        payload["tool_response"] = "not json"
        assert client.normalize(harness, "PostToolUse", "artifact-studio", payload) == {}
    payload["tool_response"] = {"content": [{"type": "text", "text": '{"next_cursor": 7}'}]}
    payload["tool_name"] = "mcp__artifact_studio__read_design_requests"
    payload["tool_input"] = {}
    assert (
        client.normalize("codex", "PostToolUse", "artifact-studio", payload)["seen_design_cursor"]
        == 7
    )
    payload["tool_response"] = {"isError": True, "structuredContent": {"next_cursor": 8}}
    assert client.normalize("codex", "PostToolUse", "artifact-studio", payload) == {}
    payload["tool_name"] = "mcp__artifact_studio_extra__read_feedback"
    assert client.normalize("codex", "PostToolUse", "artifact-studio", payload) == {}


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_stop_polling(monkeypatch: pytest.MonkeyPatch, harness: str) -> None:
    """Keep quiet polls local, forward one wake-up and release unbound sessions."""
    reason = "Artifact Studio feedback: call read_feedback(deck_id='d', after_delivery_id=7)."
    results = iter(
        [
            {"listening": True, "feedback_available": False, "reason": "waiting"},
            {"listening": True, "feedback_available": False, "reason": "waiting"},
            {"listening": True, "feedback_available": True, "reason": reason},
        ]
    )
    monkeypatch.setattr(client, "_post", lambda *_: next(results))  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    monkeypatch.setattr(client.time, "sleep", lambda _: None)  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    assert client.handle_event(
        harness, "Stop", "http://localhost", "artifact-studio", {"session_id": "s"}
    ) == {"decision": "block", "reason": reason}
    monkeypatch.setattr(
        client,
        "_post",
        lambda *_: {"listening": False, "feedback_available": False, "reason": "unbound"},  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )
    assert (
        client.handle_event(
            harness, "Stop", "http://localhost", "artifact-studio", {"session_id": "s"}
        )
        == {}
    )


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_stop_renews_after_deadline(monkeypatch: pytest.MonkeyPatch, harness: str) -> None:
    """Forward one compact renewal after the bounded local idle wait."""
    reason = "Artifact Studio idle renewal: call read_design_requests(after_event_id=3)."
    times = iter([0.0, 541.0])
    monkeypatch.setattr(client.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        client,
        "_post",
        lambda *_: {"listening": True, "feedback_available": False, "reason": reason},  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )
    result = client.handle_event(
        harness, "Stop", "http://localhost", "artifact-studio", {"session_id": "s"}
    )
    assert result == {"decision": "block", "reason": reason}


@pytest.mark.parametrize(("status", "retries"), [(429, True), (503, True), (401, False)])
def test_stop_retries_transient_failures_only(
    monkeypatch: pytest.MonkeyPatch, status: int, retries: bool
) -> None:
    """Keep waiting through transient failures without retrying rejected credentials."""
    calls = 0

    def poll(url: str, _message: dict[str, object], _timeout: int) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPError(url, status, "Fixture response", None, None)
        return {"listening": True, "feedback_available": True, "reason": "New feedback"}

    monkeypatch.setattr(client, "_post", poll)
    monkeypatch.setattr(client.time, "sleep", lambda _seconds: None)
    result = client.handle_event(
        "claude-code", "Stop", "http://localhost", "artifact-studio", {"session_id": "fixture"}
    )
    assert calls == (2 if retries else 1)
    assert result == ({"decision": "block", "reason": "New feedback"} if retries else {})


def test_conflicting_reviewer_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tell the requesting host when another session owns the review."""

    def refusal(*_: object) -> dict[str, object]:
        """Simulate the local server's ownership refusal."""
        return {"listening": False, "reason": "Already has a live reviewer."}

    monkeypatch.setattr(client, "_post", refusal)
    result = client.handle_event(
        "claude-code",
        "PostToolUse",
        "http://localhost",
        "artifact-studio",
        {
            "session_id": "s",
            "tool_name": "mcp__artifact_studio__start_live_review",
            "tool_input": {"deck_id": "d"},
            "tool_response": {"ok": True},
        },
    )
    assert result == {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "Already has a live reviewer.",
        }
    }


def test_matcher_and_malformed_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scope hook invocations and preserve malformed settings on failure."""
    import re

    monkeypatch.setattr(client.shutil, "which", lambda _: "/tmp/uv")  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    target = tmp_path / ".codex/hooks.json"
    target.parent.mkdir()
    target.write_text('["keep"]')
    with pytest.raises(ValueError, match="settings must be an object"):
        client.configure("codex", tmp_path, "http://localhost:8790", "artifact-studio", False)
    assert target.read_text() == '["keep"]'
    target.unlink()
    client.configure("codex", tmp_path, "http://localhost:8790", "artifact-studio", False)
    data = json.loads(target.read_text())
    matcher = data["hooks"]["PostToolUse"][0]["matcher"]
    assert re.match(matcher, "mcp__artifact_studio__read_feedback")
    assert re.match(matcher, "mcp__artifact-studio__read_feedback")
    assert not re.match(matcher, "mcp__artifact_studio_other__read_feedback")


def test_remote_url_and_inbox(tmp_path: Path) -> None:
    """Reject remote endpoints and bind an empty-deck live review to the inbox."""
    with pytest.raises(ValueError, match="local HTTP"):
        client.configure("codex", tmp_path, "http://example.com", "artifact-studio", True)
    assert (
        client.handle_event(
            "codex", "Stop", "http://example.com", "artifact-studio", {"session_id": "s"}
        )
        == {}
    )
    event: dict[str, object] = {
        "session_id": "s",
        "tool_name": "mcp__artifact_studio__start_live_review",
        "tool_input": {"deck_id": ""},
        "tool_response": {"structuredContent": {"ok": True}},
    }
    assert client.normalize("codex", "PostToolUse", "artifact-studio", event)["deck_id"] is None
    assert (
        client.normalize("codex", "PostToolUse", "artifact-studio", event)["start_review"] is True
    )
