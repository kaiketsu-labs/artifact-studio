"""Tests for project hooks and review event handling."""

import json
import shlex
from pathlib import Path
from typing import cast

import pytest

from studio import hook_client as client


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
    client.configure(harness, tmp_path, "http://127.0.0.1:8790", "slide studio", False)
    before = target.read_text()
    client.configure(harness, tmp_path, "http://127.0.0.1:8790", "slide studio", False)
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
    target = client.configure(harness, None, "http://127.0.0.1:8790", "slide-studio", False)
    assert target == tmp_path / path
    assert set(json.loads(target.read_text())["hooks"]) >= {"PostToolUse", "Stop", "SessionEnd"}


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_migrate_cache_dependent_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, harness: str
) -> None:
    """Replace legacy launchers without duplicate listeners or lost unrelated hooks."""
    monkeypatch.setattr(client.shutil, "which", lambda _: "/tmp/uv")  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    target = client.configure(harness, tmp_path, "http://localhost:8790", "slide-studio", False)
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
    _ = client.configure(harness, tmp_path, "http://localhost:8799", "slide-studio", False)
    migrated = json.loads(target.read_text())
    assert migrated["other"] == {"keep": True}
    for groups in migrated["hooks"].values():
        handlers = [handler for group in groups for handler in group["hooks"]]
        assert handlers[0] == unrelated
        assert len(handlers) == 2
        assert "--no-cache" in shlex.split(handlers[1]["command"])
        assert "http://localhost:8799" in shlex.split(handlers[1]["command"])
    before = target.read_text()
    _ = client.configure(harness, tmp_path, "http://localhost:8799", "slide-studio", False)
    assert target.read_text() == before


def test_normalize_cursors_and_failures() -> None:
    """Bind only successful matching tools and retain observed cursors."""
    payload: dict[str, object] = {
        "session_id": "s",
        "tool_name": "mcp__slide_studio__get_presentation",
        "tool_input": {"deck_id": "d"},
        "tool_response": {
            "structuredContent": {
                "feedback": [{"delivery_id": 2}],
                "slides": [{"feedback": [{"delivery_id": 5}]}],
            }
        },
    }
    assert "seen_cursor" not in client.normalize("codex", "PostToolUse", "slide-studio", payload)
    payload["tool_name"] = "mcp__slide-studio__read_feedback"
    payload["tool_response"] = {"content": [{"type": "text", "text": '{"next_cursor": 7}'}]}
    assert client.normalize("codex", "PostToolUse", "slide-studio", payload)["seen_cursor"] == 7
    for harness in ("codex", "claude-code"):
        for shape in ('{"next_cursor": 7}', [{"type": "text", "text": '{"next_cursor": 7}'}]):
            payload["tool_response"] = shape
            assert (
                client.normalize(harness, "PostToolUse", "slide-studio", payload)["seen_cursor"]
                == 7
            )
        payload["tool_response"] = "not json"
        assert client.normalize(harness, "PostToolUse", "slide-studio", payload) == {}
    payload["tool_response"] = {"content": [{"type": "text", "text": '{"next_cursor": 7}'}]}
    payload["tool_name"] = "mcp__slide_studio__read_design_requests"
    payload["tool_input"] = {}
    assert (
        client.normalize("codex", "PostToolUse", "slide-studio", payload)["seen_design_cursor"] == 7
    )
    payload["tool_response"] = {"isError": True, "structuredContent": {"next_cursor": 8}}
    assert client.normalize("codex", "PostToolUse", "slide-studio", payload) == {}
    payload["tool_name"] = "mcp__slide_studio_extra__read_feedback"
    assert client.normalize("codex", "PostToolUse", "slide-studio", payload) == {}


def test_stop_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Continue for feedback and release unbound sessions immediately."""
    results = iter(
        [
            {"listening": True, "feedback_available": False, "reason": "waiting"},
            {"listening": True, "feedback_available": True, "reason": "new feedback"},
        ]
    )
    monkeypatch.setattr(client, "_post", lambda *_: next(results))  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    monkeypatch.setattr(client.time, "sleep", lambda _: None)  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    assert client.handle_event(
        "codex", "Stop", "http://localhost", "slide-studio", {"session_id": "s"}
    ) == {"decision": "block", "reason": "new feedback"}
    monkeypatch.setattr(
        client,
        "_post",
        lambda *_: {"listening": False, "feedback_available": False, "reason": "unbound"},  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )
    assert (
        client.handle_event(
            "codex", "Stop", "http://localhost", "slide-studio", {"session_id": "s"}
        )
        == {}
    )


def test_stop_renews_after_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return the server reason when a long idle review must be renewed."""
    times = iter([0.0, 541.0])
    monkeypatch.setattr(client.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        client,
        "_post",
        lambda *_: {"listening": True, "feedback_available": False, "reason": "renew"},  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )
    result = client.handle_event(
        "codex", "Stop", "http://localhost", "slide-studio", {"session_id": "s"}
    )
    assert cast("str", result["reason"]) == "renew"


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
        "slide-studio",
        {
            "session_id": "s",
            "tool_name": "mcp__slide_studio__start_live_review",
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
        client.configure("codex", tmp_path, "http://localhost:8790", "slide-studio", False)
    assert target.read_text() == '["keep"]'
    target.unlink()
    client.configure("codex", tmp_path, "http://localhost:8790", "slide-studio", False)
    data = json.loads(target.read_text())
    matcher = data["hooks"]["PostToolUse"][0]["matcher"]
    assert re.match(matcher, "mcp__slide_studio__read_feedback")
    assert re.match(matcher, "mcp__slide-studio__read_feedback")
    assert not re.match(matcher, "mcp__slide_studio_other__read_feedback")


def test_remote_url_and_inbox(tmp_path: Path) -> None:
    """Reject remote endpoints and bind an empty-deck live review to the inbox."""
    with pytest.raises(ValueError, match="local HTTP"):
        client.configure("codex", tmp_path, "http://example.com", "slide-studio", True)
    assert (
        client.handle_event(
            "codex", "Stop", "http://example.com", "slide-studio", {"session_id": "s"}
        )
        == {}
    )
    event: dict[str, object] = {
        "session_id": "s",
        "tool_name": "mcp__slide_studio__start_live_review",
        "tool_input": {"deck_id": ""},
        "tool_response": {"structuredContent": {"ok": True}},
    }
    assert client.normalize("codex", "PostToolUse", "slide-studio", event)["deck_id"] is None
    assert client.normalize("codex", "PostToolUse", "slide-studio", event)["start_review"] is True
