"""Hosted lifecycle adapters authenticate, isolate reviewers and need no server installation."""

import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

from artifact_studio import hook_client
from artifact_studio.artifact_models import ArtifactFeedbackInput
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.local_http import validate_hook_url
from artifact_studio.review import Reviews
from tests.integration.hosted_fixture import WORKSPACE, running_hosted_app


def test_client_only_download_is_standalone_and_preserves_hooks(tmp_path: Path) -> None:
    with running_hosted_app(tmp_path / "server", tmp_path / "server.log") as app:
        with httpx.Client(base_url=app.url, headers={"host": "studio.example.test"}) as client:
            assert client.get("/api/agent/hooks-client").status_code == 401
            assert (
                client.get(
                    "/api/agent/hooks-client", headers={"x-fixture-auth": "agent"}
                ).status_code
                == 403
            )
            response = client.get("/api/agent/hooks-client", headers={"x-fixture-auth": "member"})
        assert response.status_code == 200
        folder = tmp_path / "client only"
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert set(archive.namelist()) == {"hook_client.py", "local_http.py"}
            archive.extractall(folder)
    project = tmp_path / "work project"
    project.mkdir()
    for harness, relative in [
        ("codex", ".codex/hooks.json"),
        ("claude-code", ".claude/settings.local.json"),
    ]:
        target = project / relative
        target.parent.mkdir()
        original = {"type": "command", "command": "echo preserve-me"}
        target.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [original]}]}, "other": 42}))
        command = [
            sys.executable,
            "-S",
            str(folder / "hook_client.py"),
            "configure",
            "--harness",
            harness,
            "--project",
            str(project),
            "--url",
            WORKSPACE,
        ]
        result = subprocess.run(
            command, env={"PATH": os.environ["PATH"]}, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        before = target.read_text()
        result = subprocess.run(
            command, env={"PATH": os.environ["PATH"]}, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert target.read_text() == before
        data = json.loads(before)
        assert data["other"] == 42
        assert original in data["hooks"]["Stop"][0]["hooks"]
        assert "uv " not in before and "--directory" not in before
        assert "local_http" not in result.stderr


def test_hosted_hook_lifecycle_is_scoped_to_verified_service(tmp_path: Path) -> None:
    with running_hosted_app(tmp_path / "server", tmp_path / "server.log") as app:
        registry = ArtifactStore(app.store)
        project = registry.create_project("Fictional hosted review")
        source = tmp_path / "source"
        source.mkdir()
        (source / "index.html").write_text("<h1>Fictional review</h1>")
        artifact = registry.register_artifact(project.id, "Review", source, "index.html")
        body = {
            "harness": "codex",
            "session_id": "same-session",
            "event": "PostToolUse",
            "artifact_id": artifact.id,
            "auto_start": True,
        }
        with httpx.Client(
            base_url=app.url, headers={"host": "studio.example.test"}, timeout=35
        ) as client:
            route = "/api/review/hooks"
            agent = {"x-fixture-auth": "agent"}
            other = {"x-fixture-auth": "agent-two"}
            assert client.post(route, json=body).status_code == 401
            assert (
                client.post(
                    route, json=body, headers={"x-fixture-auth": "member", "origin": WORKSPACE}
                ).status_code
                == 403
            )
            assert (
                client.post(route, json=body, headers={**agent, "origin": WORKSPACE}).status_code
                == 403
            )
            assert client.post(route, content=b"x" * 65537, headers=agent).status_code == 413
            assert client.post(route, json=body, headers=agent).json()["listening"]
            current = Reviews(app.store).connection(artifact.id)
            assert current["review_connected"] and current["review_session"] != "same-session"
            protocol = {**agent, "accept": "application/json, text/event-stream"}
            initialized = client.post(
                "/mcp",
                headers=protocol,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "hosted-hooks-fixture", "version": "1"},
                    },
                },
            )
            initialized.raise_for_status()
            status = client.post(
                "/mcp",
                headers=protocol,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "get_live_review_status",
                        "arguments": {"artifact_id": artifact.id},
                    },
                },
            )
            status.raise_for_status()
            assert not status.json()["result"].get("isError", False)
            response = status.json()["result"]
            state = response.get("structuredContent") or json.loads(response["content"][0]["text"])
            assert state["status"] == "connected"
            files_reply = client.post(
                "/mcp",
                headers=protocol,
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "get_agent_hook_client", "arguments": {}},
                },
            )
            files_reply.raise_for_status()
            response = files_reply.json()["result"]
            bundle = response.get("structuredContent") or json.loads(response["content"][0]["text"])
            assert {file["path"] for file in bundle["files"]} == {"hook_client.py", "local_http.py"}

            stop = {"harness": "codex", "session_id": "same-session", "event": "SessionEnd"}
            assert not client.post(route, json=stop, headers=other).json()["listening"]
            assert Reviews(app.store).connection(artifact.id)["review_connected"]
            assert not client.post(route, json=body, headers=other).json()["listening"]
            registry.add_feedback(
                artifact.id,
                ArtifactFeedbackInput(
                    message="Private fixture feedback",
                    request_id="hosted-feedback-1",
                    content_hash=artifact.content_hash,
                ),
            )
            pending = client.post(route, json={**stop, "event": "Stop"}, headers=agent).json()
            assert pending["feedback_available"]
            assert "read_artifact_feedback" in pending["reason"]
            assert "read_design_requests" not in pending["reason"]
            assert "Private fixture feedback" not in pending["reason"]
            assert (
                client.post(
                    f"/api/artifacts/{artifact.id}/review/disconnect",
                    json={},
                    headers={"x-fixture-auth": "member", "origin": WORKSPACE},
                ).status_code
                == 200
            )
            assert not client.post(route, json={**stop, "event": "Stop"}, headers=agent).json()[
                "listening"
            ]
            assert not client.post(route, json=body, headers=agent).json()["listening"]
            assert client.post(route, json={**body, "start_review": True}, headers=agent).json()[
                "listening"
            ]
            client.post(route, json=stop, headers=agent).raise_for_status()
            assert not Reviews(app.store).connection(artifact.id)["review_connected"]


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.example",
        "https://user:pass@example.test",
        "https://example.test/mcp",
        "https://example.test?secret=x",
        "https://example.test/#x",
    ],
)
def test_bad_hook_destinations_fail_closed(url: str) -> None:
    with pytest.raises(ValueError):
        validate_hook_url(url)


def test_hosted_credentials_are_environment_only_and_fail_soft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET", raising=False)
    assert (
        hook_client.handle_event("codex", "Stop", WORKSPACE, "artifact-studio", {"session_id": "s"})
        == {}
    )
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "fixture-id")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "fixture-secret")
    assert hook_client._credentials(WORKSPACE)["CF-Access-Client-Secret"] == "fixture-secret"  # pyright: ignore[reportPrivateUsage]
    assert hook_client._credentials("http://127.0.0.1:8790") == {}  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "bad\r\nheader")
    with pytest.raises(ValueError):
        hook_client._credentials(WORKSPACE)  # pyright: ignore[reportPrivateUsage]
