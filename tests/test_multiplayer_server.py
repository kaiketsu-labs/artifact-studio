"""Real HTTP/MCP journeys using only a disposable fictional server."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import httpx
import pytest

from artifact_studio.artifact_models import ArtifactFileInput
from artifact_studio.artifacts import ArtifactStore
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


def test_deck_presence_checks_current_access_and_tab(tmp_path: Path) -> None:
    """Presence follows the current slide and vanishes when a grant is revoked."""
    with (
        running_multiplayer_app(tmp_path / "server", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        human(app, "bob") as bob,
    ):
        accept(alice, grant(owner, "alice", app.deck_id, role="view"))
        endpoint = f"/api/workspaces/default/artifacts/{app.deck_id}/presence"
        owner_tab = {"tab": "a" * 32, "slide_id": "cover"}
        first_tab = {"tab": "b" * 32, "slide_id": "cover"}
        second_tab = {"tab": "c" * 32, "slide_id": "next"}
        assert owner.post(endpoint, json=owner_tab).json() == []
        assert bob.post(endpoint, json=first_tab).status_code == 404
        assert alice.post(endpoint, json=first_tab).json() == [
            {"name": "Owner", "slide_id": "cover"}
        ]
        assert alice.post(endpoint, json=second_tab).status_code == 200
        assert alice.post(endpoint, json={**first_tab, "slide_id": None}).status_code == 200
        assert owner.post(endpoint, json=owner_tab).json() == [
            {"name": "Alice", "slide_id": "next"}
        ]
        scope = {"kind": "artifact", "resource": app.deck_id}
        grants = owner.get("/api/workspaces/default/access", params=scope).json()["grants"]
        grant_id = grants[0]["id"]
        assert owner.post(
            f"/api/workspaces/default/access/{grant_id}/revoke", json=scope
        ).is_success
        assert owner.post(endpoint, json=owner_tab).json() == []
        assert alice.post(endpoint, json=second_tab).status_code == 404


def test_five_identities_share_only_granted_deck_access(tmp_path: Path) -> None:
    """Concurrent invitees retain their own deck and discussion permissions."""
    with (
        running_multiplayer_app(tmp_path / "server", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
    ):
        roles = {"alice": "edit", "bob": "comment", "carol": "view"}
        tokens = {name: grant(owner, name, app.deck_id, role=role) for name, role in roles.items()}
        deck = f"/api/decks/{app.deck_id}"
        chat = f"/api/workspaces/default/artifacts/{app.deck_id}/chat"

        def visit(name: str) -> tuple[int, int, int, str]:
            """Try the same read and discussion endpoints with one identity."""
            with human(app, name) as client:
                if name in tokens:
                    accept(client, tokens[name])
                response = client.post(chat, json={"message": name, "request_id": "test-" + name})
                read = client.get(deck)
                return (
                    read.status_code,
                    response.status_code,
                    client.get(f"/api/artifacts/{app.sibling_id}").status_code,
                    read.headers["x-request-id"],
                )

        people = ("owner", "alice", "bob", "carol", "dana")
        with ThreadPoolExecutor(max_workers=len(people)) as pool:
            results = dict(zip(people, pool.map(visit, people), strict=True))
        assert {name: result[:3] for name, result in results.items()} == {
            "owner": (200, 200, 200),
            "alice": (200, 200, 404),
            "bob": (200, 200, 404),
            "carol": (200, 404, 404),
            "dana": (404, 404, 404),
        }
        assert len({result[3] for result in results.values()}) == 5
        assert {message["message"] for message in owner.get(chat).json()} == {
            "owner",
            "alice",
            "bob",
        }


def test_opened_revisions_are_private_and_ignore_stale_hashes(tmp_path: Path) -> None:
    """View access can clear a current marker for one reader without altering anyone else's."""
    with (
        running_multiplayer_app(tmp_path / "server", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        human(app, "bob") as bob,
    ):
        for reader, name in ((alice, "alice"), (bob, "bob")):
            accept(reader, grant(owner, name, app.deck_id, role="view"))
            accept(reader, grant(owner, name, app.artifact_id, role="view"))

        deck = f"/api/decks/{app.deck_id}"
        first = alice.get(deck).json()["slides"][0]
        slide_seen = f"{deck}/slides/{first['id']}/seen"
        original = {"content_hash": first["content_hash"]}
        assert first["has_unseen_changes"] is False
        for reader in (alice, bob):
            assert reader.post(slide_seen, json=original).json() == {"seen": True}

        theme = app.store.folder(app.deck_id) / "theme.css"
        _ = theme.write_text(theme.read_text() + "\n.slide h1 { color: navy; }\n")
        revised = alice.get(deck).json()["slides"][0]
        assert revised["revision"] == first["revision"]
        assert revised["has_unseen_changes"] is True
        assert alice.post(slide_seen, json=original).json() == {"seen": False}
        assert alice.post(slide_seen, json={"content_hash": revised["content_hash"]}).json() == {
            "seen": True
        }
        assert alice.get(deck).json()["slides"][0]["has_unseen_changes"] is False
        assert bob.get(deck).json()["slides"][0]["has_unseen_changes"] is True
        assert owner.get(deck).json()["slides"][0]["has_unseen_changes"] is True
        assert alice.post(f"{deck}/slides/missing/seen", json=original).status_code == 404

        artifact = f"/api/artifacts/{app.artifact_id}"
        before = alice.get(artifact).json()
        updated = ArtifactStore(app.store).revise_artifact(
            app.artifact_id,
            [ArtifactFileInput(path="source.md", text="# Fictional new version")],
            before["revision"],
            "fixture-opened-artifact-revision",
        )
        assert (
            alice.post(
                artifact + "/seen", json={"content_hash": before["content_hash"]}
            ).status_code
            == 409
        )
        body = {"content_hash": updated.content_hash}
        assert alice.get(artifact).json()["status"] == "updated"
        assert alice.post(artifact + "/seen", json=body).json()["status"] == "draft"
        assert bob.get(artifact).json()["status"] == "updated"
        assert owner.get(artifact).json()["status"] == "updated"
        assert alice.post(f"/api/artifacts/{app.sibling_id}/seen", json=body).status_code == 404
        cards = {entry["id"]: entry for entry in alice.get("/api/hub").json()["artifacts"]}
        assert cards[app.deck_id]["has_unseen_changes"] is False
        assert cards[app.artifact_id]["status"] == "draft"


def test_shared_artifact_approval_requires_current_edit_grant_and_version(tmp_path: Path) -> None:
    """A collaborator can approve only the artifact revision they reviewed."""
    with (
        running_multiplayer_app(tmp_path / "server", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as editor,
        human(app, "bob") as commenter,
    ):
        accept(editor, grant(owner, "alice", app.artifact_id))
        accept(commenter, grant(owner, "bob", app.artifact_id, role="comment"))
        artifact = f"/api/artifacts/{app.artifact_id}"
        before = editor.get(artifact)
        before.raise_for_status()
        reviewed = {"content_hash": before.json()["content_hash"]}
        assert commenter.post(artifact + "/approve", json=reviewed).status_code == 404
        saved = editor.post(artifact + "/approve", json=reviewed)
        saved.raise_for_status()
        assert saved.json() == {
            "id": app.artifact_id,
            **reviewed,
            "approved": True,
            "has_unseen_changes": False,
            "status": "approved",
        }
        assert commenter.get(artifact).json()["approved"] is True

        updated = ArtifactStore(app.store).revise_artifact(
            app.artifact_id,
            [ArtifactFileInput(path="source.md", text="# Revised fictional artifact")],
            before.json()["revision"],
            "fixture-approval-revision",
        )
        assert editor.post(artifact + "/approve", json=reviewed).status_code == 409
        assert commenter.get(artifact).json()["approved"] is False

        access = owner.get(
            "/api/workspaces/default/access",
            params={"kind": "artifact", "resource": app.artifact_id},
        )
        access.raise_for_status()
        grant_id = next(entry["id"] for entry in access.json()["grants"] if entry["role"] == "edit")
        revoked = owner.post(
            f"/api/workspaces/default/access/{grant_id}/revoke",
            json={"kind": "artifact", "resource": app.artifact_id},
        )
        revoked.raise_for_status()
        assert (
            editor.post(
                artifact + "/approve", json={"content_hash": updated.content_hash}
            ).status_code
            == 404
        )


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


@pytest.mark.parametrize("unified", [False, True], ids=["standalone", "unified"])
def test_real_mcp_scope_and_stale_revision(tmp_path: Path, unified: bool) -> None:
    with (
        running_multiplayer_app(
            tmp_path / "server", tmp_path / "server.log", unified=unified
        ) as app,
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


def test_five_identities_and_saturated_live_renderer_have_correlated_responses(
    tmp_path: Path,
) -> None:
    """Readers remain independent while a real Chromium capture fills the renderer queue."""
    import os

    if os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1":
        pytest.skip("Opt-in real Chromium renderer journey")
    logs = tmp_path / "server.log"
    with (
        running_multiplayer_app(
            tmp_path / "server", logs, unified=True, saturate_renderer=True, startup_timeout=30
        ) as app,
        human(app, "owner") as owner,
    ):
        for name in ("alice", "bob", "carol", "dana"):
            with human(app, name) as client:
                accept(client, grant(owner, name, app.deck_id, role="view"))
        slide = owner.get(f"/api/decks/{app.deck_id}").json()["slides"][0]

        def visit(name: str) -> tuple[int, str, str]:
            with human(app, name) as client:
                if name == "owner":
                    response = client.post(
                        f"/api/decks/{app.deck_id}/slides/{slide['id']}/image",
                        json={"content_hash": slide["content_hash"]},
                    )
                    return (
                        response.status_code,
                        response.headers["x-request-id"],
                        response.json()["code"],
                    )
                response = client.get(f"/api/decks/{app.deck_id}")
                return response.status_code, response.headers["x-request-id"], ""

        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(visit, ("owner", "alice", "bob", "carol", "dana")))
        assert results[0][0] == 503 and results[0][2] == "renderer_saturated"
        assert all(result[0] == 200 for result in results[1:])
        assert len({result[1] for result in results}) == 5
        log = logs.read_text()
        assert f"id={results[0][1]} outcome=renderer_saturation" in log
