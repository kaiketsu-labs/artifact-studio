"""Real HTTP/MCP journeys using only a disposable fictional server."""

import json
import re
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import httpx

from tests.integration.multiplayer_fixture import WORKSPACE, MultiplayerApp, running_multiplayer_app


def human(app: MultiplayerApp, name: str) -> httpx.Client:
    return httpx.Client(
        base_url=app.url,
        headers={"host": "studio.example.test", "x-fixture-auth": name, "origin": WORKSPACE},
        timeout=15,
    )


def grant(
    owner: httpx.Client, recipient: str, resource: str, role: str = "edit", kind: str = "artifact"
) -> str:
    response = owner.post(
        "/api/workspaces/default/invitations",
        json={
            "kind": kind,
            "resource": resource,
            "email": recipient + "@example.test",
            "role": role,
        },
    )
    assert response.status_code == 200, response.text
    return cast(str, response.json()["url"]).split("#invite=")[1]


def accept(client: httpx.Client, token: str) -> None:
    response = client.post("/api/invitations/accept", json={"token": token})
    assert response.status_code == 200, response.text


def agent(client: httpx.Client, artifact: str) -> str:
    response = client.post(
        "/api/workspaces/default/agents",
        json={"kind": "artifact", "resource": artifact, "name": "Fictional agent"},
    )
    assert response.status_code == 200, response.text
    return cast(str, response.json()["token"])


def test_http_grants_preview_revoke_and_legacy_denial(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "server", tmp_path / "server.log") as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        human(app, "bob") as bob,
    ):
        token = grant(owner, "alice", app.artifact_id, "view")
        assert bob.post("/api/invitations/accept", json={"token": token}).status_code == 404
        accept(alice, token)
        assert alice.post("/api/invitations/accept", json={"token": token}).status_code == 404
        prefix = "/api/workspaces/default/artifacts/"
        assert alice.get(prefix + app.artifact_id + "/state").status_code == 200
        assert alice.get(prefix + app.sibling_id + "/state").status_code == 404
        catalog = alice.get("/api/workspaces/default/catalog").json()
        assert [item["id"] for item in catalog["artifacts"]] == [app.artifact_id]
        assert catalog["projects"] == []
        assert (
            alice.post(
                prefix + app.artifact_id + "/chat",
                json={"message": "Forbidden view comment", "request_id": "fixture-request-1"},
            ).status_code
            == 404
        )
        for path in [
            "/api/hub",
            "/api/decks",
            "/api/artifacts/" + app.sibling_id,
            "/decks/" + app.deck_id + "/index.html",
        ]:
            assert alice.get(path).status_code == 404
        preview = alice.get(
            prefix + app.artifact_id + "/preview", headers={"sec-fetch-dest": "iframe"}
        )
        assert preview.status_code == 200, preview.text
        match = re.search(r'https://preview\.example\.test/_preview/[^/"\s]+', preview.text)
        assert match, preview.text
        root = urlsplit(match.group()).path
        file_path = root + f"/api/artifacts/{app.artifact_id}/revisions/1/files/source.md"
        assert alice.get(file_path, headers={"host": "preview.example.test"}).status_code == 200
        access = owner.get(
            "/api/workspaces/default/access",
            params={"kind": "artifact", "resource": app.artifact_id},
        ).json()
        grant_id = access["grants"][0]["id"]
        assert (
            owner.post(
                "/api/workspaces/default/access/" + grant_id + "/revoke",
                json={"kind": "artifact", "resource": app.artifact_id},
            ).status_code
            == 200
        )
        assert alice.get(prefix + app.artifact_id + "/state").status_code == 404
        assert alice.get(file_path, headers={"host": "preview.example.test"}).status_code == 404


def test_real_mcp_scope_and_stale_revision(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "server", tmp_path / "server.log") as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
    ):
        accept(alice, grant(owner, "alice", app.artifact_id))
        token = agent(alice, app.artifact_id)
        # Origin must be absent entirely for agent requests.
        with httpx.Client(
            base_url=app.url, headers={"host": "studio.example.test"}, timeout=15
        ) as mcp:

            def invoke(name: str, arguments: dict[str, object]) -> dict[str, object]:
                response = mcp.post(
                    "/mcp",
                    headers={
                        "authorization": "Bearer " + token,
                        "accept": "application/json, text/event-stream",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments},
                    },
                )
                assert response.status_code == 200, response.text
                return cast(dict[str, object], response.json()["result"])

            context = invoke("get_working_context", {})
            assert not context.get("isError"), context
            denied = invoke("get_artifact", {"artifact_id": app.sibling_id})
            assert denied.get("isError"), denied

            def revise(expected: int, message: str, request_id: str) -> dict[str, object]:
                return invoke(
                    "revise_artifact",
                    {
                        "artifact_id": app.artifact_id,
                        "files_json": json.dumps([{"path": "source.md", "text": message}]),
                        "expected_revision": expected,
                        "request_id": request_id,
                    },
                )

            first = revise(1, "First edit", "fictional-request-1")
            assert not first.get("isError"), first
            stale = revise(1, "Stale edit", "fictional-request-2")
            assert stale.get("isError"), stale
            reread = invoke("get_artifact", {"artifact_id": app.artifact_id})
            assert not reread.get("isError"), reread
            result = revise(2, "Reconciled edit", "fictional-request-3")
            assert not result.get("isError"), result
            access = owner.get(
                "/api/workspaces/default/access",
                params={"kind": "artifact", "resource": app.artifact_id},
            ).json()
            grant_id = access["grants"][0]["id"]
            owner.post(
                "/api/workspaces/default/access/" + grant_id + "/revoke",
                json={"kind": "artifact", "resource": app.artifact_id},
            )
            assert invoke("get_working_context", {}).get("isError")


def test_project_inheritance_and_comment_role(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "server", tmp_path / "server.log") as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
    ):
        accept(alice, grant(owner, "alice", app.project_id, "comment", "project"))
        prefix = "/api/workspaces/default/artifacts/"
        assert alice.get(prefix + app.artifact_id + "/state").status_code == 200
        assert alice.get(prefix + app.sibling_id + "/state").status_code == 200
        assert (
            alice.post(
                prefix + app.artifact_id + "/chat",
                json={"message": "Fictional comment", "request_id": "fixture-comment-1"},
            ).status_code
            == 200
        )
        token = agent(alice, app.artifact_id)
        with httpx.Client(
            base_url=app.url, headers={"host": "studio.example.test"}, timeout=15
        ) as mcp:
            response = mcp.post(
                "/mcp",
                headers={
                    "authorization": "Bearer " + token,
                    "accept": "application/json, text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "revise_artifact",
                        "arguments": {
                            "artifact_id": app.artifact_id,
                            "files_json": json.dumps([{"path": "source.md", "text": "Forbidden"}]),
                            "expected_revision": 1,
                            "request_id": "fixture-denied-edit",
                        },
                    },
                },
            )
            assert response.status_code == 200
            assert response.json()["result"].get("isError")
