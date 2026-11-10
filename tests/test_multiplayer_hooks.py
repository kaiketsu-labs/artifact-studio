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
        assert "hookSpecificOutput" in started
        stopped = hook_client.handle_event("codex", "Stop", app.url, "artifact-studio", payload)
        assert stopped["decision"] == "block"
        assert "read_feedback" in str(stopped["reason"])
        assert (
            hook_client.handle_event("codex", "SessionEnd", app.url, "artifact-studio", payload)
            == {}
        )
        assert hook_client.handle_event("codex", "Stop", app.url, "artifact-studio", payload) == {}
