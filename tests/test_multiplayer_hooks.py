from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Event
from typing import cast

import httpx
import pytest

from artifact_studio import hook_client
from tests.integration.multiplayer_fixture import running_multiplayer_app
from tests.test_multiplayer_server import agent, human
from tests.test_multiplayer_simulation import Peer


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
            peer = Peer(app, token)
            with closing(peer.client):
                payload["tool_input"] = {"artifact_id": app.artifact_id}
                payload["tool_name"] = "mcp__artifact_studio__stop_live_review"
                payload["tool_response"] = peer.value(
                    "stop_live_review", artifact_id=app.artifact_id
                )
                assert "stopped" in str(check())
                payload["tool_name"] = "mcp__artifact_studio__get_working_context"
                payload["tool_response"] = {"artifact": {"id": app.artifact_id}}
                assert check() == {}  # pause also suppresses passive assigned-feedback notices
                payload["tool_name"] = "mcp__artifact_studio__start_live_review"
                payload["tool_response"] = peer.value(
                    "start_live_review", artifact_id=app.artifact_id
                )
                assert check() == expected  # explicit resume stays passive: no listener lease
            agents_response = owner.get(scope + "/state")
            agents_response.raise_for_status()
            assert all(agent["status"] != "listening" for agent in agents_response.json()["agents"])


def test_stop_tool_is_native_session_scoped_and_cancels_live_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two agents and two host sessions share a fictional artifact without cross-disconnect."""
    with (
        running_multiplayer_app(
            tmp_path / "server", tmp_path / "server.log", unified=True, startup_timeout=30
        ) as app,
        human(app, "owner") as owner,
    ):
        token, other_token = agent(owner, app.artifact_id), agent(owner, app.artifact_id)
        peer, other = Peer(app, token), Peer(app, other_token)
        with closing(peer.client), closing(other.client), ThreadPoolExecutor(max_workers=1) as pool:
            args = {"artifact_id": app.artifact_id}

            def post(
                session: str, response: dict[str, object], *, stop: bool = False
            ) -> dict[str, object]:
                message = hook_client.normalize(
                    "codex",
                    "PostToolUse",
                    "artifact-studio",
                    {
                        "session_id": session,
                        "tool_name": "mcp__artifact_studio__"
                        + ("stop_live_review" if stop else "start_live_review"),
                        "tool_input": args,
                        "tool_response": response,
                    },
                )
                result = peer.client.post("/api/review/hooks", json=message)
                assert result.status_code == 200, result.text
                return cast(dict[str, object], result.json())

            start = peer.value("start_live_review", **args)
            assert post("session-one", start)["listening"]
            assert post("session-two", peer.value("start_live_review", **args))["listening"]
            other_start = other.value("start_live_review", **args)
            other_body = hook_client.normalize(
                "codex",
                "PostToolUse",
                "artifact-studio",
                {
                    "session_id": "session-one",
                    "tool_name": "mcp__artifact_studio__start_live_review",
                    "tool_input": args,
                    "tool_response": other_start,
                },
            )
            assert other.client.post("/api/review/hooks", json=other_body).json()["listening"]
            # Held feedback is not delivered merely by start, stop, resume, or ordinary discovery.
            endpoint = f"/api/workspaces/default/artifacts/{app.artifact_id}/chat"
            agent_id = peer.value("get_live_review_status", **args)["agent_id"]
            saved = owner.post(
                endpoint,
                json={
                    "channel": "private",
                    "message": "Unreleased fictional note",
                    "request_id": "stop-fixture-draft",
                    "assigned": agent_id,
                    "released": False,
                },
            )
            saved.raise_for_status()
            before = owner.get(endpoint, params={"channel": "private"}).json()
            in_flight = Event()
            real_post = hook_client._post  # pyright: ignore[reportPrivateUsage]

            def credentials(_url: str) -> dict[str, str]:
                return {"Host": "studio.example.test", "Authorization": "Bearer " + token}

            def observed_post(
                url: str, message: dict[str, object], timeout: int
            ) -> dict[str, object]:
                in_flight.set()
                return real_post(url, message, timeout)

            monkeypatch.setattr(hook_client, "_credentials", credentials)
            monkeypatch.setattr(hook_client, "_post", observed_post)
            waiter = pool.submit(
                hook_client.handle_event,
                "codex",
                "Stop",
                app.url,
                "artifact-studio",
                {"session_id": "session-one"},
            )
            assert in_flight.wait(3)
            stop = peer.value("stop_live_review", **args)
            assert stop["status"] == "awaiting_host_hook"
            assert not waiter.done()
            assert "stopped" in str(post("session-one", stop, stop=True)["reason"])
            assert waiter.result(timeout=3) == {}  # no model wake or idle-renewal message
            assert (
                peer.value("get_live_review_status", **args)["status"] == "listening"
            )  # session two
            assert other.value("get_live_review_status", **args)["status"] == "listening"
            # Ordinary auto-attach cannot reopen the stopped session.
            auto = {
                "harness": "codex",
                "session_id": "session-one",
                "event": "PostToolUse",
                "artifact_id": app.artifact_id,
                "auto_start": True,
            }
            assert not peer.client.post("/api/review/hooks", json=auto).json()["listening"]
            # A receipt from another credential cannot mutate this native session.
            foreign = {
                **auto,
                "event": "StopReview",
                "session_id": "session-two",
                "review_control": other.value("stop_live_review", **args)["review_control"],
            }
            assert (
                "superseded" in peer.client.post("/api/review/hooks", json=foreign).json()["reason"]
            )
            assert peer.value("get_live_review_status", **args)["status"] == "listening"
            # Denied scopes cannot issue or apply a stop; ordinary authentication is unchanged.
            assert peer.call("stop_live_review", artifact_id=app.sibling_id).is_error
            denied = {**foreign, "artifact_id": app.sibling_id}
            assert peer.client.post("/api/review/hooks", json=denied).status_code == 404
            assert (
                peer.client.post(
                    "/api/review/hooks",
                    json=foreign,
                    headers={"Origin": "https://evil.example.test"},
                ).status_code
                == 403
            )
            assert not peer.value("read_feedback", **args)["events"]
            assert owner.get(endpoint, params={"channel": "private"}).json() == before
            assert not post("session-one", start)["listening"]  # delayed earlier start is fenced
            assert post("session-one", peer.value("start_live_review", **args))["listening"]
            assert "superseded" in str(post("session-one", stop, stop=True)["reason"])
            # Stop both own sessions without revoking the independent collaborator's credential.
            assert not post("session-one", peer.value("stop_live_review", **args), stop=True)[
                "listening"
            ]
            assert not post("session-two", peer.value("stop_live_review", **args), stop=True)[
                "listening"
            ]
            assert peer.value("get_live_review_status", **args)["status"] != "listening"
            assert other.value("get_live_review_status", **args)["status"] == "listening"
            assert peer.value("get_working_context", **args)
