"""Tests for project hooks and review event handling."""

import io
import json
import shlex
from pathlib import Path

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


def test_artifact_context_binds_only_an_explicit_legacy_presentation() -> None:
    """Do not attach the design inbox or a generic artifact from an unrelated context read."""
    payload: dict[str, object] = {
        "session_id": "author",
        "tool_name": "mcp__artifact_studio__get_working_context",
        "tool_input": {"artifact_id": "artifact-one"},
        "tool_response": {"structuredContent": {"artifact": {"id": "artifact-one"}}},
    }
    assert client.normalize("codex", "PostToolUse", "artifact-studio", payload) == {}
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
    assert handler["timeout"] == client.STOP_TIMEOUT_SECONDS == 28800
    assert handler.get("asyncRewake") is (True if harness == "claude-code" else None)
    assert "async" not in handler
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
    data["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 600
    data["hooks"]["Stop"][0]["hooks"][0].pop("asyncRewake", None)
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
    assert migrated["hooks"]["Stop"][-1]["hooks"][0]["timeout"] == 28800
    assert migrated["hooks"]["Stop"][-1]["hooks"][0].get("asyncRewake") is (
        True if harness == "claude-code" else None
    )
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
def test_stop_registers_one_waiter_and_reuses_its_identity(
    monkeypatch: pytest.MonkeyPatch, harness: str
) -> None:
    """Only the initial poll supersedes prior listeners; later polls keep the same token."""
    messages: list[dict[str, object]] = []

    def post(_: str, message: dict[str, object], __: int) -> dict[str, object]:
        messages.append(message.copy())
        return {
            "listening": True,
            "feedback_available": len(messages) == 3,
            "reason": "Feedback ready" if len(messages) == 3 else "waiting",
        }

    monkeypatch.setattr(client, "_post", post)
    monkeypatch.setattr(client.time, "sleep", lambda _: None)  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    assert client.handle_event(
        harness, "Stop", "http://localhost", "artifact-studio", {"session_id": "s"}
    ) == {"decision": "block", "reason": "Feedback ready"}
    assert [message["begin_wait"] for message in messages] == [True, False, False]
    ids = [message["wait_id"] for message in messages]
    assert len(set(ids)) == 1 and isinstance(ids[0], str) and len(ids[0]) == 32


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_stop_expires_quietly_after_old_renewal_deadline(
    monkeypatch: pytest.MonkeyPatch, harness: str
) -> None:
    """Quiet polls past nine minutes never request a model turn, even at expiry."""
    times = iter([0.0, 541.0, 541.0, float(client.STOP_WAIT_SECONDS)])
    monkeypatch.setattr(client.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(client.time, "sleep", lambda _: None)  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    monkeypatch.setattr(
        client,
        "_post",
        lambda *_: {"listening": True, "feedback_available": False, "reason": "waiting"},  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )
    result = client.handle_event(
        harness, "Stop", "http://localhost", "artifact-studio", {"session_id": "s"}
    )
    assert result == {}


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_feedback_at_quiet_expiry_still_wakes(
    monkeypatch: pytest.MonkeyPatch, harness: str
) -> None:
    """A delivery observed in the final poll takes precedence over quiet expiry."""
    times = iter([0.0, float(client.STOP_WAIT_SECONDS)])
    monkeypatch.setattr(client.time, "monotonic", lambda: next(times))
    reason = "Read released feedback."

    def final_delivery(*_: object) -> dict[str, object]:
        """The long poll returns feedback just as the quiet wait reaches its deadline."""
        assert client.time.monotonic() == client.STOP_WAIT_SECONDS
        return {"listening": True, "feedback_available": True, "reason": reason}

    monkeypatch.setattr(
        client,
        "_post",
        final_delivery,
    )
    assert client.handle_event(
        harness, "Stop", "http://localhost", "artifact-studio", {"session_id": "s"}
    ) == {"decision": "block", "reason": reason}


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_stop_process_output_uses_host_wake_contract(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], harness: str
) -> None:
    """Claude wakes via exit 2 and stderr; Codex consumes a JSON blocking decision."""
    monkeypatch.setattr(
        client.sys, "argv", ["hook", "event", "--event", "Stop", "--harness", harness]
    )
    monkeypatch.setattr(client.sys, "stdin", io.StringIO('{"session_id":"s"}'))
    reason = "Read released feedback."
    monkeypatch.setattr(
        client,
        "handle_event",
        lambda *_: {"decision": "block", "reason": reason},  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )
    code = client.main()
    output = capsys.readouterr()
    if harness == "claude-code":
        assert code == 2 and output.err.strip() == reason and output.out == ""
    else:
        assert code == 0 and output.err == ""
        assert json.loads(output.out) == {"decision": "block", "reason": reason}


@pytest.mark.parametrize("input_text", ['{"session_id":"s"}', "invalid json"])
def test_claude_quiet_or_malformed_stop_never_rewakes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], input_text: str
) -> None:
    """A quiet listener and invalid host input exit successfully without a wake message."""
    monkeypatch.setattr(
        client.sys, "argv", ["hook", "event", "--event", "Stop", "--harness", "claude-code"]
    )
    monkeypatch.setattr(client.sys, "stdin", io.StringIO(input_text))
    monkeypatch.setattr(client, "handle_event", lambda *_: {})  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    assert client.main() == 0
    output = capsys.readouterr()
    assert output.err == "" and json.loads(output.out) == {}


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
