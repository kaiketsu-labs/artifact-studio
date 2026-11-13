"""Project-local lifecycle hooks for Artifact Studio review sessions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import sys
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import cast
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

if __package__:
    from .local_http import NoRedirect as _NoRedirect
    from .local_http import validate_hook_url as _validate_url
else:
    from local_http import NoRedirect as _NoRedirect
    from local_http import validate_hook_url as _validate_url

APP_DIR = Path(__file__).resolve().parent.parent
CLAUDE_STOP_LIFETIME = 6 * 60 * 60
CLAUDE_STOP_TIMEOUT = CLAUDE_STOP_LIFETIME + 60
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
PASSIVE_CHECK_TOOLS = (
    "get_working_context",
    "start_live_review",
    "revise_slide",
    "add_slide",
    "revise_artifact",
    "publish_slide_designs",
    "publish_element_alternatives",
)


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
    if arguments[-2:] == ["--claude-mode", "passive"]:
        arguments = arguments[:-2]
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
    entries: list[object], harness: str, event: str, server_name: str, *, remove_obsolete: bool
) -> tuple[list[object], int]:
    """Migrate generated handlers while retaining other handlers and settings."""
    migrated: list[object] = []
    obsolete = 0
    for value in entries:
        group = _object(value)
        handlers = group.get("hooks")
        if not isinstance(handlers, list) or set(group) - {"hooks", "matcher"}:
            migrated.append(value)
            continue
        original = cast("list[object]", handlers)
        kept: list[object] = []
        for item in original:
            if _generated_handler(item, harness, event, server_name):
                continue
            if harness == "claude-code" and _obsolete_slide_studio_handler(item, event):
                obsolete += 1
                if remove_obsolete:
                    continue
            kept.append(item)
        if len(kept) == len(original):
            migrated.append(value)
        elif kept:
            migrated.append({**group, "hooks": kept})
    return migrated, obsolete


def _obsolete_slide_studio_handler(value: object, event: str) -> bool:
    """Recognize the old generated launcher when its local installation is missing."""
    handler = _object(value)
    command = handler.get("command")
    if handler.get("type") != "command" or not isinstance(command, str):
        return False
    try:
        arguments = shlex.split(command)
    except ValueError:
        return False
    if arguments[1:2] == ["--no-cache"]:
        _ = arguments.pop(1)
    if (
        len(arguments) != 17
        or Path(arguments[0]).name != "uv"
        or arguments[1] != "--directory"
        or arguments[3:14]
        != [
            "run",
            "--no-sync",
            "python",
            "-m",
            "studio.hook_client",
            "event",
            "--harness",
            "claude-code",
            "--event",
            event,
            "--url",
        ]
        or arguments[15:] != ["--server-name", "slide-studio"]
    ):
        return False
    installation = Path(arguments[2])
    if not installation.is_absolute() or installation.name != "slide-studio":
        return False
    try:
        _validate_url(arguments[14])
    except ValueError:
        return False
    return urlsplit(arguments[14]).scheme == "http" and not installation.exists()


def configure(
    harness: str,
    project: Path | None,
    url: str,
    server_name: str,
    dry_run: bool,
    *,
    passive: bool = False,
    remove_obsolete: bool = False,
) -> Path:
    """Merge project hooks, or user-level hooks when project is None, keeping other settings."""
    _validate_url(url)
    if passive and harness != "claude-code":
        raise ValueError("Passive mode is available only for Claude Code")
    if remove_obsolete and harness != "claude-code":
        raise ValueError("Obsolete Slide Studio hook cleanup is available only for Claude Code")
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
    obsolete_count = 0
    for event, timeout in events.items():
        existing = hooks.setdefault(event, [])
        if not isinstance(existing, list):
            raise ValueError(f"{event} hooks must be a list")
        entries = cast("list[object]", existing)
        kept, obsolete = _replace_generated(
            entries, harness, event, server_name, remove_obsolete=remove_obsolete
        )
        entries[:] = kept
        obsolete_count += obsolete
        if passive and event != "PostToolUse":
            if not entries:
                del hooks[event]
            continue
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
        handler: dict[str, object] = {"type": "command", "command": command, "timeout": timeout}
        if harness == "claude-code" and event == "Stop" and not passive and os.name != "nt":
            handler["asyncRewake"] = True
            handler["timeout"] = CLAUDE_STOP_TIMEOUT
        if passive:
            handler["command"] = command + " --claude-mode passive"
            handler["async"] = True
        entry: dict[str, object] = {"hooks": [handler]}
        if event == "PostToolUse":
            server_pattern = "".join(
                "[-_]" if char in "-_" else re.escape(char) for char in server_name
            )
            tool_pattern = f"({'|'.join(PASSIVE_CHECK_TOOLS)})" if passive else ".*"
            entry["matcher"] = f"^mcp__{server_pattern}__{tool_pattern}$"
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
    if obsolete_count:
        noun = "hook" if obsolete_count == 1 else "hooks"
        if remove_obsolete:
            action = "Would remove" if dry_run else "Removed"
            print(f"{action} {obsolete_count} obsolete Slide Studio {noun}.", file=sys.stderr)
        else:
            print(
                f"Found {obsolete_count} obsolete Slide Studio {noun}. "
                "Review cleanup with --remove-obsolete-slide-studio-hooks --dry-run.",
                file=sys.stderr,
            )
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
    token = os.environ.get("ARTIFACT_STUDIO_AGENT_TOKEN", "")
    if token:
        if not 32 <= len(token) <= 200 or any(ord(char) < 33 or ord(char) > 126 for char in token):
            raise ValueError("Invalid multiplayer agent token in the environment")
        return {"Authorization": "Bearer " + token}
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
    harness: str,
    event: str,
    url: str,
    server_name: str,
    payload: dict[str, object],
    *,
    passive: bool = False,
) -> dict[str, object]:
    """Keep idle polling local; continue only for feedback or a bounded review renewal."""
    try:
        _validate_url(url)
    except ValueError:
        return {}
    message = normalize(harness, event, server_name, payload)
    if not message:
        return {}
    if passive:
        name = payload.get("tool_name")
        if (
            harness != "claude-code"
            or event != "PostToolUse"
            or not isinstance(name, str)
            or name.rsplit("__", 1)[-1] not in PASSIVE_CHECK_TOOLS
            or not (message.get("artifact_id") or message.get("deck_id"))
        ):
            return {}
        message["passive"] = True
    async_stop = harness == "claude-code" and event == "Stop" and os.name != "nt"
    deadline = time.monotonic() + (CLAUDE_STOP_LIFETIME if async_stop else 540)
    failures = 0
    while True:
        try:
            result = _post(url, message, 30 if event == "Stop" else 2)
        except OSError as error:
            transient = not isinstance(error, HTTPError) or error.code in {429, 502, 503, 504}
            if event != "Stop" or not transient or failures >= 2:
                return {}
            failures += 1
            delay = min(0.5 * failures, max(0.0, deadline - time.monotonic()))
            if not delay:
                return {}
            time.sleep(delay)
            continue
        except ValueError:
            return {}
        failures = 0
        reason = result.get("reason")
        if event == "PostToolUse" and isinstance(reason, str) and reason:
            if passive and result.get("feedback_available") is not True:
                return {}
            return {
                "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": reason}
            }
        if event != "Stop" or result.get("listening") is not True:
            return {}
        if not isinstance(reason, str) or not reason:
            return {}
        if result.get("feedback_available") is True:
            return {"decision": "block", "reason": reason}
        if time.monotonic() >= deadline:
            return {} if async_stop else {"decision": "block", "reason": reason}
        if result.get("feedback_available") is not False:
            return {}
        time.sleep(min(2.0 if async_stop else 0.25, max(0.0, deadline - time.monotonic())))


@contextmanager
def _claude_stop_lock(url: str, server_name: str, session: str) -> Generator[bool]:
    """Keep one listener per server and host session; the OS releases it on exit."""
    digest = hashlib.sha256(
        json.dumps([url.rstrip("/"), server_name, session]).encode()
    ).hexdigest()
    # POSIX directory descriptors prevent replacement of the directory while opening the file.
    # Windows needs a separate ACL-aware implementation before it can safely use this lock.
    if os.name == "nt":
        yield False
        return
    import fcntl

    directory = Path(tempfile.gettempdir()) / f"artifact-studio-hooks-{os.getuid()}"
    try:
        directory.mkdir(mode=0o700, exist_ok=True)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            directory_info = os.fstat(directory_fd)
            if (
                not stat.S_ISDIR(directory_info.st_mode)
                or directory_info.st_uid != os.getuid()
                or stat.S_IMODE(directory_info.st_mode) != 0o700
            ):
                raise OSError("Unsafe hook lock directory")
            fd = os.open(digest, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
        finally:
            os.close(directory_fd)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
            ):
                raise OSError("Unsafe hook lock file")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise
    except OSError:
        yield False
        return
    try:
        yield True
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def main() -> int:
    """Run the configuration or lifecycle command."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    config = subparsers.add_parser("configure")
    scope = config.add_mutually_exclusive_group(required=True)
    scope.add_argument("--project", type=Path)
    scope.add_argument("--global", dest="project", action="store_const", const=None)
    config.add_argument("--dry-run", action="store_true")
    config.add_argument(
        "--remove-obsolete-slide-studio-hooks",
        action="store_true",
        help="remove generated Claude hooks for a missing Slide Studio installation",
    )
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
        subparser.add_argument("--claude-mode", choices=["live", "passive"], default="live")
    args = parser.parse_args()
    passive = args.claude_mode == "passive"
    if args.command == "configure":
        try:
            target = configure(
                args.harness,
                args.project,
                args.url,
                args.server_name,
                args.dry_run,
                passive=passive,
                remove_obsolete=args.remove_obsolete_slide_studio_hooks,
            )
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
            session = payload.get("session_id")
            if (
                args.harness == "claude-code"
                and args.event == "Stop"
                and isinstance(session, str)
                and session
            ):
                with _claude_stop_lock(args.url, args.server_name, session) as acquired:
                    result = (
                        handle_event(
                            args.harness,
                            args.event,
                            args.url,
                            args.server_name,
                            payload,
                            passive=passive,
                        )
                        if acquired
                        else {}
                    )
            else:
                result = handle_event(
                    args.harness, args.event, args.url, args.server_name, payload, passive=passive
                )
        except (OSError, ValueError, RecursionError):
            result = {}
        if args.harness == "claude-code" and args.event == "Stop" and os.name != "nt":
            reason = result.get("reason")
            if result.get("decision") == "block" and isinstance(reason, str):
                print(reason, file=sys.stderr)
                return 2
        else:
            print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
