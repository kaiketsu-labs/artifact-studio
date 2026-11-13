"""Two independent MCP peers collaborate on real presentation source over HTTP."""

import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import cast

import httpx
from mcp.types import LATEST_PROTOCOL_VERSION, CallToolResult, TextContent

from artifact_studio.multiplayer_access import Sharing
from artifact_studio.storage import Store
from tests.integration.multiplayer_fixture import MultiplayerApp, running_multiplayer_app
from tests.test_multiplayer_server import accept, grant, human


class Peer:
    def __init__(self, app: MultiplayerApp, token: str) -> None:
        self.client = httpx.Client(
            base_url=app.url,
            timeout=30,
            headers={
                "host": "studio.example.test",
                "authorization": "Bearer " + token,
                "accept": "application/json, text/event-stream",
            },
        )
        self.sequence = 0
        self.rpc(
            "initialize",
            {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "simulation", "version": "1"},
            },
        )
        response = self.client.post(
            "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        assert response.status_code == 202

    def rpc(self, method: str, params: dict[str, object]) -> dict[str, object]:
        self.sequence += 1
        response = self.client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params}
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert not result.get("error"), result
        return cast(dict[str, object], result["result"])

    def call(self, name: str, **arguments: object) -> CallToolResult:
        return CallToolResult.model_validate(
            self.rpc("tools/call", {"name": name, "arguments": arguments})
        )

    def value(self, name: str, **arguments: object) -> dict[str, object]:
        result = self.call(name, **arguments)
        assert not result.is_error, result
        if result.structured_content is not None:
            return result.structured_content
        text = next(item.text for item in result.content if isinstance(item, TextContent))
        return cast(dict[str, object], json.loads(text))


def test_slide_activity_is_explicit_scoped_and_revocable(tmp_path: Path) -> None:
    """Publish anonymous activity only for current readers of the slide's deck."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        human(app, "bob") as bob,
    ):
        accept(alice, grant(owner, "alice", app.deck_id, role="view"))
        connections = "/api/workspaces/default/agents"
        request = {"kind": "artifact", "resource": app.deck_id, "name": "Private agent label"}
        editor = owner.post(connections, json=request).json()
        reader = alice.post(connections, json=request).json()
        endpoint = f"/api/workspaces/default/artifacts/{app.deck_id}/presence"
        slide = {"tab": "b" * 32, "slide_id": "cover"}
        marker = {"name": "Agent working", "slide_id": "cover", "agent": True}
        peer = Peer(app, editor["token"])
        reader_peer = Peer(app, reader["token"])
        with closing(peer.client), closing(reader_peer.client):
            args = {"deck_id": app.deck_id, "slide_id": "cover"}
            assert not peer.call("get_slide", **args).is_error
            assert alice.post(endpoint, json=slide).json() == []
            assert reader_peer.call("set_slide_activity", **args, active=True).is_error
            assert peer.call(
                "set_slide_activity", **{**args, "slide_id": "absent"}, active=True
            ).is_error
            assert peer.call(
                "set_slide_activity", **{**args, "deck_id": app.sibling_id}, active=True
            ).is_error
            assert peer.value("set_slide_activity", **args, active=True) == {"expires_in": 90}
            assert alice.post(endpoint, json=slide).json() == [marker]
            assert bob.post(endpoint, json=slide).status_code == 404
            assert peer.value("set_slide_activity", **args, active=False) == {"expires_in": 0}
            assert alice.post(endpoint, json=slide).json() == []
            peer.value("set_slide_activity", **args, active=True)
            assert owner.post(f"/api/agents/{editor['id']}/revoke").is_success
            assert alice.post(endpoint, json=slide).json() == []


def test_shared_history_requires_owner_opt_in_and_preserves_agent_tasks(tmp_path: Path) -> None:
    """Check live permission changes and isolation of private and unreleased messages."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
    ):
        accept(alice, grant(owner, "alice", app.deck_id))
        agents = "/api/workspaces/default/agents"
        body = {"kind": "artifact", "resource": app.deck_id, "name": "Fictional assistant"}
        connection = owner.post(agents, json=body)
        connection.raise_for_status()
        connection_id, token = connection.json()["id"], connection.json()["token"]
        guest_connection = alice.post(agents, json=body)
        guest_connection.raise_for_status()
        base = f"/api/workspaces/default/artifacts/{app.deck_id}"
        setting = f"{base}/agents/{connection_id}/shared-history"
        assert owner.get(setting).json() == {"enabled": False}
        assert alice.post(setting, json={"enabled": True}).status_code == 404
        foreign = f"{base}/agents/{guest_connection.json()['id']}/shared-history"
        assert owner.post(foreign, json={"enabled": True}).status_code == 404
        state = owner.get(base + "/state").json()
        shared = alice.post(
            base + "/chat",
            json={
                "message": "Fictional shared context",
                "request_id": "shared-context",
                "content_hash": state["content_hash"],
                "files": [{"name": "context.txt", "data_base64": "ZmljdGlvbg=="}],
            },
        )
        shared.raise_for_status()
        for channel in ("shared", "private"):
            owner.post(
                base + "/chat",
                json={
                    "message": f"Fictional {channel} request",
                    "channel": channel,
                    "request_id": f"assigned-{channel}",
                    "assigned": connection_id,
                    "content_hash": state["content_hash"],
                },
            ).raise_for_status()
        for channel, released in (("shared", False), ("private", True)):
            alice.post(
                base + "/chat",
                json={
                    "message": f"Hidden {channel}",
                    "channel": channel,
                    "request_id": f"hidden-{channel}",
                    "released": released,
                    "content_hash": state["content_hash"],
                },
            ).raise_for_status()
        with closing((peer := Peer(app, token)).client):
            args = {"artifact_id": app.deck_id}
            attachment = {**args, "event_id": shared.json()["id"], "file_index": 0}

            def history(channel: str) -> list[str]:
                result = peer.call("read_chat", **args, channel=channel)
                assert not result.is_error
                return [
                    cast(str, json.loads(block.text)["message"])
                    for block in result.content
                    if isinstance(block, TextContent)
                ]

            assert history("shared") == ["Fictional shared request"]
            assert peer.call("read_chat_attachment", **attachment).is_error
            assert owner.post(setting, json={"enabled": True}).json() == {"enabled": True}
            assert history("shared") == ["Fictional shared context", "Fictional shared request"]
            assert history("private") == ["Fictional private request"]
            assert peer.value("read_chat_attachment", **attachment)["text"] == "fiction"
            events = cast(list[dict[str, object]], peer.value("read_feedback", **args)["events"])
            assert {event["channel"] for event in events} == {"shared", "private"}
            for event in events:
                event_id = event["id"]
                claim = peer.value(
                    "claim_feedback", **args, event_id=event_id, session_id="fictional-peer"
                )
                reply = peer.value(
                    "reply_to_feedback",
                    **args,
                    event_id=event_id,
                    claim=claim["claim"],
                    message="Fictional response",
                    request_id=f"response-{event_id}",
                )
                assert reply["channel"] == event["channel"]
            assert owner.post(setting, json={"enabled": False}).json() == {"enabled": False}
            assert history("shared") == ["Fictional shared request", "Fictional response"]
            assert peer.call("read_chat_attachment", **attachment).is_error


def test_two_people_two_agents_two_workspaces_and_conflicting_slides(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "server", tmp_path / "server.log") as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
    ):
        accept(alice, grant(owner, "alice", app.deck_id))
        peers: list[Peer] = []
        agent_ids: list[str] = []
        for client, name in ((owner, "Owner agent"), (alice, "Alice agent")):
            response = client.post(
                "/api/workspaces/default/agents",
                json={
                    "kind": "artifact",
                    "resource": app.deck_id,
                    "name": name,
                },
            )
            assert response.status_code == 200
            peers.append(Peer(app, response.json()["token"]))
            agent_ids.append(response.json()["id"])
        first, second = peers
        try:
            outline = first.value("get_presentation_outline", deck_id=app.deck_id)
            slides = cast(list[dict[str, object]], outline["slides"])
            slide_id = cast(str, slides[0]["id"])
            sources = [
                peer.value("get_slide", deck_id=app.deck_id, slide_id=slide_id) for peer in peers
            ]
            assert sources[0]["expected_hash"] == sources[1]["expected_hash"]

            def edit(index: int) -> CallToolResult:
                return peers[index].call(
                    "revise_slide",
                    deck_id=app.deck_id,
                    slide_id=slide_id,
                    expected_hash=sources[index]["expected_hash"],
                    request_id=f"concurrent-change-{index}",
                    patch_json=json.dumps({"title": f"Concurrent title {index}"}),
                )

            with ThreadPoolExecutor(2) as executor:
                results = list(executor.map(edit, [0, 1]))
            assert sum(bool(result.is_error) for result in results) == 1
            loser = next(index for index, result in enumerate(results) if result.is_error)
            winner_title = f"Concurrent title {1 - loser}"
            latest = peers[loser].value("get_slide", deck_id=app.deck_id, slide_id=slide_id)
            assert latest["title"] == winner_title
            peers[loser].value(
                "revise_slide",
                deck_id=app.deck_id,
                slide_id=slide_id,
                expected_hash=latest["expected_hash"],
                request_id="reconcile-after-conflict",
                patch_json=json.dumps({"title": winner_title + " + reconciled"}),
            )
            assert app.store.read(app.deck_id).slides[0].title == winner_title + " + reconciled"

            # Different-slide edits preserve both changes; no deck-wide false conflict.
            reads = [
                peer.value("get_slide", deck_id=app.deck_id, slide_id=identifier)
                for peer, identifier in zip(peers, [slide_id, "next"], strict=True)
            ]
            with ThreadPoolExecutor(2) as executor:
                futures = [
                    executor.submit(
                        peer.value,
                        "revise_slide",
                        deck_id=app.deck_id,
                        slide_id=identifier,
                        expected_hash=source["expected_hash"],
                        request_id=f"different-slide-{index}",
                        patch_json=json.dumps({"notes": f"Shared note {index}"}),
                    )
                    for index, (peer, identifier, source) in enumerate(
                        zip(peers, [slide_id, "next"], reads, strict=True)
                    )
                ]
                for future in futures:
                    future.result()
            assert [item.notes for item in app.store.read(app.deck_id).slides] == [
                "Shared note 0",
                "Shared note 1",
            ]

            # Same native session ID does not collide across the two credentials.
            for peer in peers:
                result = peer.client.post(
                    "/api/review/hooks",
                    json={
                        "harness": "codex",
                        "session_id": "same-native-session",
                        "event": "PostToolUse",
                        "deck_id": app.deck_id,
                        "start_review": True,
                    },
                )
                assert result.json()["listening"]
            scope = f"/api/workspaces/default/artifacts/{app.deck_id}"
            assert [item["status"] for item in owner.get(scope + "/state").json()["agents"]] == [
                "listening",
                "listening",
            ]
            message = alice.post(
                scope + "/chat",
                json={
                    "channel": "private",
                    "message": "Fictional private ask",
                    "assigned": agent_ids[1],
                    "request_id": "private-request-1",
                },
            ).json()
            assert not first.value("read_feedback", deck_id=app.deck_id)["events"]
            assert (
                len(
                    cast(list[object], second.value("read_feedback", deck_id=app.deck_id)["events"])
                )
                == 1
            )
            claimed = second.value(
                "claim_feedback",
                artifact_id=app.deck_id,
                event_id=message["id"],
                session_id="alice-session",
            )
            reply = second.value(
                "reply_to_feedback",
                artifact_id=app.deck_id,
                event_id=message["id"],
                claim=claimed["claim"],
                message="Fictional private reply",
                request_id="private-reply-1",
            )
            assert reply == second.value(
                "reply_to_feedback",
                artifact_id=app.deck_id,
                event_id=message["id"],
                claim=claimed["claim"],
                message="Fictional private reply",
                request_id="private-reply-1",
            )
            assert owner.get(scope + "/chat?channel=private").json() == []
            assert owner.get(scope + "/chat?channel=shared").json() == []
            first.client.post(
                "/api/review/hooks",
                json={
                    "harness": "codex",
                    "session_id": "same-native-session",
                    "event": "SessionEnd",
                },
            )
            assert (
                second.value("get_live_review_status", deck_id=app.deck_id)["status"] == "listening"
            )

            # A new workspace deliberately contains the identical presentation ID.
            created = owner.post(
                "/api/workspaces", json={"title": "Second fictional workspace"}
            ).json()["id"]
            sharing = Sharing(tmp_path / "server/sharing", app.store, "owner@example.test")
            other: Store = sharing.store(created)
            shutil.copytree(app.store.folder(app.deck_id), other.root / app.deck_id)
            token = owner.post(
                f"/api/workspaces/{created}/agents",
                json={
                    "kind": "artifact",
                    "resource": app.deck_id,
                    "name": "Isolated second workspace",
                },
            ).json()["token"]
            isolated = Peer(app, token)
            try:
                source = isolated.value("get_slide", deck_id=app.deck_id, slide_id=slide_id)
                isolated.value(
                    "revise_slide",
                    deck_id=app.deck_id,
                    slide_id=slide_id,
                    expected_hash=source["expected_hash"],
                    request_id="workspace-specific-edit",
                    patch_json=json.dumps({"title": "Only in second workspace"}),
                )
                assert (
                    first.value("get_slide", deck_id=app.deck_id, slide_id=slide_id)["title"]
                    != "Only in second workspace"
                )
                assert (
                    alice.get(
                        f"/api/workspaces/{created}/artifacts/{app.deck_id}/state"
                    ).status_code
                    == 404
                )
            finally:
                isolated.client.close()
        finally:
            for peer in peers:
                peer.client.close()
