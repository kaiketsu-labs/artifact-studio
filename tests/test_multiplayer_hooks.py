from pathlib import Path

import httpx
import pytest

from artifact_studio import hook_client
from tests.integration.multiplayer_fixture import running_multiplayer_app


def test_hosted_hook_credentials_prefer_agent_token(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "fictional-agent-token-0123456789abcdef"
    monkeypatch.setenv("ARTIFACT_STUDIO_AGENT_TOKEN", token)
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "fixture-id")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "fixture-secret")
    assert hook_client._credentials("https://studio.example.test") == {  # pyright: ignore[reportPrivateUsage]
        "Authorization": "Bearer " + token
    }
    monkeypatch.delenv("ARTIFACT_STUDIO_AGENT_TOKEN")
    assert hook_client._credentials("https://studio.example.test") == {  # pyright: ignore[reportPrivateUsage]
        "CF-Access-Client-Id": "fixture-id",
        "CF-Access-Client-Secret": "fixture-secret",
    }


def test_native_client_uses_running_hook_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with running_multiplayer_app(tmp_path / "server", tmp_path / "server.log") as app:
        headers = {
            "host": "studio.example.test",
            "origin": "https://studio.example.test",
            "x-fixture-auth": "owner",
        }
        with httpx.Client(base_url=app.url, headers=headers, timeout=10) as client:
            result = client.post(
                "/api/workspaces/default/agents",
                json={"kind": "artifact", "resource": app.artifact_id, "name": "Fictional agent"},
            )
            result.raise_for_status()
            token = str(result.json()["token"])
            identifier = str(result.json()["id"])
            response = client.post(
                f"/api/workspaces/default/artifacts/{app.artifact_id}/chat",
                json={
                    "channel": "private",
                    "message": "Please update the fictional page.",
                    "request_id": "fixture-request",
                    "assigned": identifier,
                    "released": True,
                },
            )
            response.raise_for_status()

        # The fixture listens on loopback HTTP, so supply its authenticated proxy
        # headers while exercising the real stdlib _post and server endpoint.
        def credentials(_url: str) -> dict[str, str]:
            return {"Host": "studio.example.test", "Authorization": "Bearer " + token}

        monkeypatch.setattr(hook_client, "_credentials", credentials)
        payload: dict[str, object] = {
            "session_id": "fixture-session",
            "tool_name": "mcp__artifact_studio__start_live_review",
            "tool_input": {"artifact_id": app.artifact_id},
            "tool_response": {"status": "awaiting_host_hook"},
        }
        started = hook_client.handle_event(
            "codex", "PostToolUse", app.url, "artifact-studio", payload
        )
        assert started == {}
        payload["tool_name"] = "mcp__artifact_studio__read_feedback"
        payload["tool_response"] = {"events": [], "next_cursor": 1}
        assert (
            hook_client.handle_event("codex", "PostToolUse", app.url, "artifact-studio", payload)
            == {}
        )
        stopped = hook_client.handle_event("codex", "Stop", app.url, "artifact-studio", payload)
        assert stopped["decision"] == "block"
        assert (
            stopped["reason"]
            == f"Studio feedback: call read_feedback(artifact_id={app.artifact_id!r})."
        )
        assert (
            hook_client.handle_event("codex", "SessionEnd", app.url, "artifact-studio", payload)
            == {}
        )
        assert hook_client.handle_event("codex", "Stop", app.url, "artifact-studio", payload) == {}


def test_passive_claude_only_checks_its_released_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check fictional assigned feedback without wake-up, delivery or a live lease."""
    with running_multiplayer_app(tmp_path / "server", tmp_path / "server.log") as app:
        headers = {
            "host": "studio.example.test",
            "origin": "https://studio.example.test",
            "x-fixture-auth": "owner",
        }
        with httpx.Client(base_url=app.url, headers=headers, timeout=10) as owner:
            agents: list[tuple[str, str]] = []
            for name in ("Fictional Claude", "Fictional other agent"):
                response = owner.post(
                    "/api/workspaces/default/agents",
                    json={"kind": "artifact", "resource": app.artifact_id, "name": name},
                )
                response.raise_for_status()
                agents.append((str(response.json()["id"]), str(response.json()["token"])))
            (identifier, token), (other, _) = agents
            scope = f"/api/workspaces/default/artifacts/{app.artifact_id}"
            for assigned, released, request_id in (
                (identifier, False, "held-request"),
                (other, True, "someone-elses"),
            ):
                response = owner.post(
                    scope + "/chat",
                    json={
                        "channel": "private",
                        "message": "Fictional private draft",
                        "request_id": request_id,
                        "assigned": assigned,
                        "released": released,
                    },
                )
                response.raise_for_status()

            def credentials(_url: str) -> dict[str, str]:
                return {"Host": "studio.example.test", "Authorization": "Bearer " + token}

            monkeypatch.setattr(hook_client, "_credentials", credentials)
            payload: dict[str, object] = {
                "session_id": "fixture-claude",
                "tool_name": "mcp__artifact_studio__get_working_context",
                "tool_input": {"artifact_id": app.artifact_id},
                "tool_response": {"artifact": {"id": app.artifact_id}},
            }

            def check(event: str = "PostToolUse") -> dict[str, object]:
                return hook_client.handle_event(
                    "claude-code", event, app.url, "artifact-studio", payload, passive=True
                )

            assert check() == {}
            response = owner.post(scope + "/release?channel=private")
            response.raise_for_status()
            expected = {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": (
                        "Artifact Studio has assigned feedback. Use read_feedback with "
                        f"artifact_id={app.artifact_id!r}, then claim each request before acting."
                    ),
                },
            }
            assert check() == expected
            payload["tool_name"] = "mcp__artifact_studio__revise_slide"
            payload["tool_input"] = {"deck_id": app.artifact_id, "slide_id": "cover"}
            payload["tool_response"] = {"id": "cover", "revision": 2}
            assert check() == expected
            payload["tool_name"] = "mcp__artifact_studio__read_feedback"
            assert check() == {}
            assert check("Stop") == {}
            agents_response = owner.get(scope + "/state")
            agents_response.raise_for_status()
            assert all(agent["status"] != "listening" for agent in agents_response.json()["agents"])
