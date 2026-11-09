"""Project-local lifecycle hooks for Artifact Studio review sessions."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import cast
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

if __package__:
    from .local_http import NoRedirect as _NoRedirect
    from .local_http import validate_hook_url as _validate_url
else:
    from local_http import NoRedirect as _NoRedirect
    from local_http import validate_hook_url as _validate_url

APP_DIR = Path(__file__).resolve().parent.parent
INBOX_TOOLS = {
    "get_working_context",
    "read_design_requests",
    "wait_for_design_requests",
    "start_live_review",
}
AUTO_REVIEW_TOOLS = {
    "get_working_context",
    "create_presentation",
    "get_presentation",
    "get_slide",
    "revise_slide",
    "add_slide",
    "append_slides",
    "reply_to_feedback",
    "reply_to_presentation_feedback",
    "get_artifact",
    "register_artifact",
    "revise_artifact",
    "capture_artifact",
    "read_artifact_feedback",
    "reply_to_artifact_feedback",
}


def _object(value: object) -> dict[str, object]:
    """Return a JSON object or an empty object."""
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _generated_handler(value: object, harness: str, event: str, server_name: str) -> bool:
    """Recognize our exact command shape without touching unrelated user hooks."""
    handler = _object(value)
    command = handler.get("command")
    if handler.get("type") != "command" or not isinstance(command, str):
        return False
    try:
        arguments = shlex.split(command)
    except ValueError:
        return False
    if len(arguments) > 1 and arguments[1] == "--no-cache":
        _ = arguments.pop(1)
    if (
        len(arguments) == 11
        and Path(arguments[1]).name == "hook_client.py"
        and arguments[2:7] == ["event", "--harness", harness, "--event", event]
        and arguments[7] == "--url"
        and arguments[9:] == ["--server-name", server_name]
    ):
        return True
    return (
        len(arguments) == 17
        and arguments[1] == "--directory"
        and arguments[3:9]
        == ["run", "--no-sync", "python", "-m", "artifact_studio.hook_client", "event"]
        and arguments[9:13] == ["--harness", harness, "--event", event]
        and arguments[13] == "--url"
        and arguments[15:] == ["--server-name", server_name]
    )


def _replace_generated(
    entries: list[object], harness: str, event: str, server_name: str
) -> list[object]:
    """Migrate generated handlers while retaining other handlers and settings."""
    migrated: list[object] = []
    for value in entries:
        group = _object(value)
        handlers = group.get("hooks")
        if not isinstance(handlers, list) or set(group) - {"hooks", "matcher"}:
            migrated.append(value)
            continue
        original = cast("list[object]", handlers)
        kept = [
            item for item in original if not _generated_handler(item, harness, event, server_name)
        ]
        if len(kept) == len(original):
            migrated.append(value)
        elif kept:
            migrated.append({**group, "hooks": kept})
    return migrated


def configure(
    harness: str, project: Path | None, url: str, server_name: str, dry_run: bool
) -> Path:
    """Merge project hooks, or user-level hooks when project is None, keeping other settings."""
    _validate_url(url)
    if project is not None and (not project.is_absolute() or not project.is_dir()):
        raise ValueError("project must be an existing absolute directory")
    standalone = not __package__ or urlsplit(url).scheme == "https"
    uv = shutil.which("uv")
    if not standalone and uv is None:
        raise ValueError("uv executable not found")
    if project is None:
        target = Path.home() / (
            ".codex/hooks.json" if harness == "codex" else ".claude/settings.json"
        )
    else:
        target = project / (
            ".codex/hooks.json" if harness == "codex" else ".claude/settings.local.json"
        )
    raw: object = json.loads(target.read_text()) if target.exists() else {}
    if not isinstance(raw, dict):
        raise ValueError("settings must be an object")
    data = cast("dict[str, object]", raw)
    hooks = _object(data.setdefault("hooks", {}))
    if hooks is not data["hooks"]:
        raise ValueError("hooks must be an object")
    events = {"PostToolUse": 10, "Stop": 600, "SessionEnd": 3}
    if harness == "codex":
        events["Interrupt"] = 3
    for event, timeout in events.items():
        command = shlex.join(
            [
                str(Path(uv or "uv").resolve()),
                "--no-cache",
                "--directory",
                str(APP_DIR),
                "run",
                "--no-sync",
                "python",
                "-m",
                "artifact_studio.hook_client",
                "event",
                "--harness",
                harness,
                "--event",
                event,
                "--url",
                url,
                "--server-name",
                server_name,
            ]
        )
        if standalone:
            command = shlex.join(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "event",
                    "--harness",
                    harness,
                    "--event",
                    event,
                    "--url",
                    url,
                    "--server-name",
                    server_name,
                ]
            )
        entry: dict[str, object] = {
            "hooks": [{"type": "command", "command": command, "timeout": timeout}]
        }
        if event == "PostToolUse":
            server_pattern = "".join(
                "[-_]" if char in "-_" else re.escape(char) for char in server_name
            )
            entry["matcher"] = f"^mcp__{server_pattern}__.*$"
        existing = hooks.setdefault(event, [])
        if not isinstance(existing, list):
            raise ValueError(f"{event} hooks must be a list")
        entries = cast("list[object]", existing)
        entries[:] = _replace_generated(entries, harness, event, server_name)
        entries.append(entry)
    rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if dry_run:
        print(f"{target}\n{rendered}", end="")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = target.stat().st_mode if target.exists() else 0o600
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=target.parent, delete=False
            ) as stream:
                temporary = stream.name
                stream.write(rendered)
            os.chmod(temporary, mode)
            os.replace(temporary, target)
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)
    return target


def _result(value: object) -> dict[str, object]:
    """Decode structured or text-wrapped MCP responses.

    Claude Code passes MCP results as a JSON string or a bare content-block list.
    """
    if isinstance(value, str):
        try:
            return _result(json.loads(value))
        except json.JSONDecodeError:
            return {}
    if isinstance(value, list):
        blocks: list[object] = [*value]
        return _result({"content": blocks})
    result = _object(value)
    if result.get("isError") is True or result.get("is_error") is True:
        return {}
    structured = _object(result.get("structuredContent"))
    if structured:
        return structured
    content = result.get("content")
    if isinstance(content, list):
        for item in cast("list[object]", content):
            text = _object(item).get("text")
            if isinstance(text, str):
                try:
                    decoded = _object(json.loads(text))
                except json.JSONDecodeError:
                    continue
                if decoded:
                    return decoded
        return {}
    return result


def normalize(
    harness: str, event: str, server_name: str, payload: dict[str, object]
) -> dict[str, object]:
    """Normalize eligible hook input for the local review server."""
    session = payload.get("session_id")
    if not isinstance(session, str) or not session:
        return {}
    message: dict[str, object] = {"harness": harness, "session_id": session, "event": event}
    if event != "PostToolUse":
        return message
    name = payload.get("tool_name")
    if not isinstance(name, str):
        return {}
    prefix = f"mcp__{server_name.replace('-', '_')}__"
    normalized_name = name.replace("-", "_")
    if not normalized_name.startswith(prefix):
        return {}
    tool = normalized_name[len(prefix) :]
    if tool == "get_live_review_status":
        return {}
    response = _result(payload.get("tool_response"))
    if not response:
        return {}
    arguments = _object(payload.get("tool_input"))
    if tool == "start_live_review":
        message["start_review"] = True
    deck = arguments.get("deck_id")
    artifact = arguments.get("artifact_id")
    if tool == "get_working_context" and not deck:
        deck = _object(response.get("presentation_context")).get("deck_id")
        artifact = artifact or _object(response.get("artifact")).get("id")
        if not deck and not artifact and arguments.get("project_id"):
            return {}
    if tool == "get_artifact":
        deck = response.get("legacy_deck_id") or deck
    if tool == "register_artifact":
        artifact = response.get("id")
    if isinstance(deck, str) and deck:
        message["deck_id"] = deck
        if tool in AUTO_REVIEW_TOOLS:
            message["auto_start"] = True
    elif isinstance(artifact, str) and artifact:
        message["artifact_id"] = artifact
        if tool in AUTO_REVIEW_TOOLS:
            message["auto_start"] = True
    elif tool in INBOX_TOOLS:
        message["deck_id"] = None
    else:
        return {}
    if tool in {"read_feedback", "wait_for_feedback", "read_artifact_feedback"}:
        cursor = response.get("next_cursor")
        if isinstance(cursor, int) and not isinstance(cursor, bool):
            message["seen_cursor"] = cursor
    elif tool in {"read_design_requests", "wait_for_design_requests"}:
        cursor = response.get("next_cursor")
        if isinstance(cursor, int) and not isinstance(cursor, bool):
            message["seen_design_cursor"] = cursor
    return message


def _credentials(url: str) -> dict[str, str]:
    """Read machine credentials only from the process environment, never hook input."""
    if urlsplit(url).scheme != "https":
        return {}
    headers: dict[str, str] = {}
    for header, variable in (
        ("CF-Access-Client-Id", "CF_ACCESS_CLIENT_ID"),
        ("CF-Access-Client-Secret", "CF_ACCESS_CLIENT_SECRET"),
    ):
        value = os.environ.get(variable, "")
        if not value or any(ord(char) < 32 or ord(char) > 126 for char in value):
            raise ValueError("Hosted hooks require service credentials in the environment")
        headers[header] = value
    return headers


def _post(url: str, message: dict[str, object], timeout: int) -> dict[str, object]:
    """Send one event to the local review endpoint."""
    request = Request(
        url.rstrip("/") + "/api/review/hooks",
        data=json.dumps(message).encode(),
        headers={"Content-Type": "application/json", **_credentials(url)},
        method="POST",
    )
    with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
        return _object(json.load(response))


def handle_event(
    harness: str, event: str, url: str, server_name: str, payload: dict[str, object]
) -> dict[str, object]:
    """Keep idle polling local; continue only for feedback or a bounded review renewal."""
    try:
        _validate_url(url)
    except ValueError:
        return {}
    message = normalize(harness, event, server_name, payload)
    if not message:
        return {}
    deadline = time.monotonic() + 540
    while True:
        try:
            result = _post(url, message, 30 if event == "Stop" else 2)
        except (OSError, URLError, ValueError, json.JSONDecodeError):
            return {}
        reason = result.get("reason")
        if event == "PostToolUse" and isinstance(reason, str) and reason:
            return {
                "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": reason}
            }
        if event != "Stop" or result.get("listening") is not True:
            return {}
        if not isinstance(reason, str) or not reason:
            return {}
        if result.get("feedback_available") is True or time.monotonic() >= deadline:
            return {"decision": "block", "reason": reason}
        if result.get("feedback_available") is not False:
            return {}
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


def main() -> int:
    """Run the configuration or lifecycle command."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    config = subparsers.add_parser("configure")
    scope = config.add_mutually_exclusive_group(required=True)
    scope.add_argument("--project", type=Path)
    scope.add_argument("--global", dest="project", action="store_const", const=None)
    config.add_argument("--dry-run", action="store_true")
    event_parser = subparsers.add_parser("event")
    event_parser.add_argument(
        "--event",
        required=True,
        choices=["PostToolUse", "Stop", "SessionEnd", "Interrupt", "StopFailure"],
    )
    for subparser in (config, event_parser):
        subparser.add_argument("--harness", required=True, choices=["codex", "claude-code"])
        subparser.add_argument("--url", default="http://127.0.0.1:8790")
        subparser.add_argument("--server-name", default="artifact-studio")
    args = parser.parse_args()
    if args.command == "configure":
        try:
            target = configure(args.harness, args.project, args.url, args.server_name, args.dry_run)
            if not args.dry_run:
                print(f"Saved {target}. Reload the agent session and review hooks in /hooks.")
        except (OSError, ValueError) as exc:
            print(f"hook configuration: {exc}", file=sys.stderr)
            return 1
    else:
        try:
            raw = sys.stdin.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("Hook input exceeds the client budget")
            payload = _object(json.loads(raw))
            result = handle_event(args.harness, args.event, args.url, args.server_name, payload)
        except (OSError, ValueError, RecursionError):
            result = {}
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
