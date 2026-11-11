"""Exercise editor and collaboration boundaries through one real HTTP listener."""

import fcntl
import json
import re
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from artifact_studio.deployment import DeploymentConfig
from artifact_studio.multiplayer_access import Sharing
from artifact_studio.unified import create_app
from tests.design_factory import TEMPLATE_ID, make_design
from tests.integration.multiplayer_fixture import (
    PREVIEW,
    WORKSPACE,
    MultiplayerApp,
    running_multiplayer_app,
)
from tests.test_multiplayer_server import accept, agent, grant, human
from tests.test_multiplayer_simulation import Peer


def test_shared_members_cannot_enable_the_full_editor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = DeploymentConfig(
        "hosted", WORKSPACE, PREVIEW, member_emails=("owner@example.test", "alice@example.test")
    )
    monkeypatch.setenv("ARTIFACT_STUDIO_MULTIPLAYER_OWNER", "owner@example.test")
    monkeypatch.setattr(DeploymentConfig, "from_env", lambda: config)
    with pytest.raises(ValueError, match="only the owner"):
        create_app()


def test_owner_editor_and_guest_access_use_the_same_store(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as guest,
        httpx.Client(base_url=app.url, headers={"host": "studio.example.test"}) as anonymous,
    ):
        assert anonymous.get("/").status_code == 401
        assert anonymous.get("/api/hub").status_code == 401
        assert 'id="hub-content"' in owner.get("/").text
        assert 'id="slide-title"' in owner.get("/", params={"deck": app.deck_id}).text
        assert owner.get("/api/session").json()["collaboration_url"] == "/together"
        assert owner.get("/api/together/session").json()["editor_available"] is True
        assert "data-shared-editor" in guest.get("/").text
        assert guest.get("/api/hub").json()["artifacts"] == []
        assert guest.get("/api/together/session").json()["editor_available"] is False
        assert (
            owner.get("/", params={"workspace": "default", "artifact": app.artifact_id}).headers[
                "location"
            ]
            == f"/?artifact={app.artifact_id}"
        )

        token = grant(owner, "alice", app.artifact_id, "view")
        accept(guest, token)
        catalog = guest.get("/api/workspaces/default/catalog").json()
        assert [item["id"] for item in catalog["artifacts"]] == [app.artifact_id]
        hub = guest.get("/api/hub").json()
        assert [item["id"] for item in hub["artifacts"]] == [app.artifact_id]
        assert [item["title"] for item in hub["projects"]] == ["Shared with me"]
        assert guest.get("/api/decks").json() == []
        detail = guest.get(f"/api/artifacts/{app.artifact_id}").json()
        assert detail["access_role"] == "view"
        assert "source_path" not in detail and "storage_project_id" not in detail
        assert guest.get(f"/api/artifacts/{app.sibling_id}").status_code == 404
        assert guest.get(f"/api/decks/{app.deck_id}").status_code == 404
        assert guest.get("/api/instructions").status_code == 404
        assert guest.get("/web/hub.html").status_code == 404
        assert not guest.post("/api/hub/projects", json={"title": "Not allowed"}).is_success
        assert (
            owner.post(
                "/api/hub/projects", json={"title": "Wrong origin"}, headers={"origin": PREVIEW}
            ).status_code
            == 403
        )
        # A full editor write may change what a guest inherits.
        with (tmp_path / "app/sharing/access.lock").open("r+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            blocked = owner.post("/api/hub/projects", json={"title": "Must not be created"})
            assert blocked.status_code == 429
            assert blocked.headers["retry-after"]
        # An ordinary editor write must be visible in the scoped collaboration catalog.
        assert owner.post("/api/hub/projects", json={"title": "Created in the editor"}).is_success
        owner_catalog = owner.get("/api/workspaces/default/catalog").json()
        titles = {item["title"] for item in owner_catalog["projects"]}
        assert "Created in the editor" in titles
        assert "Must not be created" not in titles

        scoped = agent(owner, app.artifact_id)
        headers = {"host": "studio.example.test", "authorization": "Bearer " + scoped}
        assert anonymous.get("/api/hub", headers=headers).status_code == 401
        assert anonymous.post("/api/review/hooks", headers=headers, json={}).status_code == 422
        assert anonymous.post("/api/review/hooks", json={}).status_code == 401


def test_guest_deck_notes_require_current_edit_grant(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as editor,
        human(app, "bob") as viewer,
    ):
        deck = f"/api/decks/{app.deck_id}"
        path = deck + "/slides/cover/notes"
        accept(editor, grant(owner, "alice", app.deck_id, "edit"))
        accept(viewer, grant(owner, "bob", app.deck_id, "view"))
        snapshot = editor.get(deck).json()
        assert snapshot["access_role"] == "edit"
        assert not snapshot["feedback"] and not snapshot["authoring_instructions"]
        assert editor.get(f"/decks/{app.deck_id}/theme.css").is_success
        assert editor.get(f"/decks/{app.deck_id}/index.html").status_code == 404
        before = snapshot["slides"][0]
        edit = {
            "notes": "Shared edit",
            "expected_notes": before["notes"],
            "expected_revision": before["notes_revision"],
        }
        assert viewer.get(deck).json()["access_role"] == "view"
        assert viewer.post(path, json=edit).status_code == 404
        assert editor.post(path, json=edit, headers={"origin": PREVIEW}).status_code == 403
        assert editor.post(path, json=edit).is_success
        assert owner.get(deck).json()["slides"][0]["notes"] == "Shared edit"
        next_slide = snapshot["slides"][1]
        next_path = deck + "/slides/" + next_slide["id"] + "/notes"
        assert owner.post(
            next_path,
            json={
                "notes": "Parallel owner edit",
                "expected_notes": next_slide["notes"],
                "expected_revision": next_slide["notes_revision"],
            },
        ).is_success
        assert editor.get(deck).json()["slides"][1]["notes"] == "Parallel owner edit"
        assert editor.post(path, json={**edit, "notes": "Stale overwrite"}).status_code == 409
        order = deck + "/slide-order"
        move = {"slide_ids": ["next", "cover"], "expected_slide_ids": ["cover", "next"]}
        assert viewer.post(order, json=move).status_code == 404
        assert editor.post(order, json=move).is_success
        assert [slide["id"] for slide in owner.get(deck).json()["slides"]] == ["next", "cover"]
        assert (
            editor.post(
                order,
                json={"slide_ids": ["cover", "next"], "expected_slide_ids": ["cover", "next"]},
            ).status_code
            == 409
        )
        audience = f"/decks/{app.deck_id}/presenter?audience=1"
        assert editor.get(audience).is_success
        assert "Shared edit" not in editor.get(audience).text
        with (
            closing((owner_peer := Peer(app, agent(owner, app.deck_id))).client),
            closing((guest_peer := Peer(app, agent(editor, app.deck_id))).client),
        ):
            cover = owner_peer.value("get_slide", deck_id=app.deck_id, slide_id="cover")
            next_for_agent = guest_peer.value("get_slide", deck_id=app.deck_id, slide_id="next")
            edit_next = {
                "deck_id": app.deck_id,
                "slide_id": "next",
                "expected_hash": next_for_agent["expected_hash"],
                "request_id": "native-guest-agent-edit",
                "patch_json": json.dumps({"title": "Guest changed next"}),
            }
            guest_peer.value("revise_slide", **edit_next)
            owner_peer.value(
                "revise_slide",
                deck_id=app.deck_id,
                slide_id="cover",
                expected_hash=cover["expected_hash"],
                request_id="native-owner-agent-edit",
                patch_json=json.dumps({"title": "Owner changed cover"}),
            )
            assert guest_peer.call(
                "revise_slide",
                **{
                    **edit_next,
                    "request_id": "native-guest-stale-edit",
                    "patch_json": json.dumps({"title": "Stale overwrite"}),
                },
            ).is_error
            assert [slide["title"] for slide in viewer.get(deck).json()["slides"]] == [
                "Guest changed next",
                "Owner changed cover",
            ]
        timeline = deck + "/slides/next/history"
        saves = viewer.get(timeline).json()
        assert saves["items"][0]["author"] == "Fictional agent · agent"
        access = owner.get(
            "/api/workspaces/default/access",
            params={"kind": "artifact", "resource": app.deck_id},
        ).json()
        alice = next(row for row in access["grants"] if row["email"] == "alice@example.test")
        revoke = "/api/workspaces/default/access/" + alice["id"] + "/revoke"
        assert owner.post(revoke, json={"kind": "artifact", "resource": app.deck_id}).is_success
        assert editor.get(deck).status_code == 404
        assert editor.get(audience).status_code == 404
        assert editor.post(path, json=edit).status_code == 404


def test_both_preview_types_stay_on_the_isolated_host(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
    ):
        for path, signed in (
            (f"/api/artifacts/{app.artifact_id}/preview", True),
            (f"/api/workspaces/default/artifacts/{app.artifact_id}/preview", False),
        ):
            document = owner.get(path, headers={"sec-fetch-dest": "iframe"})
            assert document.is_success, document.text
            match = re.search(r"https://preview\.example\.test/_preview/([^/\"\s]+)", document.text)
            assert match, document.text
            assert ("." in match[1]) is signed
            source = (
                urlsplit(match[0]).path
                + f"/api/artifacts/{app.artifact_id}/revisions/1/files/source.md"
            )
            assert owner.get(source, headers={"host": "preview.example.test"}).is_success
            assert owner.get(source, headers={"origin": WORKSPACE}).status_code == 404


def seed_isolated_presentation(root: Path, app: MultiplayerApp, workspace: str) -> None:
    """Give an isolated workspace a different deck with the same ID as the default deck."""
    shared = Sharing(root / "sharing", app.store, "owner@example.test")
    with shared.locked():
        isolated = shared.store(workspace)
        make_design(isolated.root / TEMPLATE_ID)
        isolated.create(app.deck_id, "Isolated presentation", "test-brand")


def test_native_workspace_routes_never_use_the_default_collection(tmp_path: Path) -> None:
    """The same presentation ID must resolve, render and save in just one workspace."""
    root = tmp_path / "app"
    with (
        running_multiplayer_app(root, tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as guest,
        human(app, "bob") as outsider,
    ):
        workspace = owner.post("/api/workspaces", json={"title": "Isolated collection"}).json()[
            "id"
        ]
        prefix = f"/workspaces/{workspace}"
        seed_isolated_presentation(root, app, workspace)
        assert owner.get(f"{prefix}/").is_success
        assert owner.get(f"{prefix}/api/session").json()["workspace_owner"]
        assert owner.post(
            f"{prefix}/api/hub/projects", json={"title": "Isolated project"}
        ).is_success
        assert outsider.get(f"{prefix}/api/hub").json()["projects"] == []
        assert outsider.get(f"{prefix}/api/decks/{app.deck_id}").status_code == 404
        assert owner.get(f"{prefix}/api/workspaces/default/catalog").status_code == 404
        assert (
            owner.get(f"{prefix}/api/hub", headers={"host": "preview.example.test"}).status_code
            == 404
        )
        invite = owner.post(
            f"/api/workspaces/{workspace}/invitations",
            json={
                "kind": "artifact",
                "resource": app.deck_id,
                "email": "alice@example.test",
                "role": "edit",
            },
        ).json()["url"]
        assert invite.startswith(f"{WORKSPACE}{prefix}/#invite=")
        accept(guest, invite.split("#invite=")[1])
        assert not guest.get(f"{prefix}/api/session").json()["workspace_owner"]
        assert guest.post(f"{prefix}/api/hub/projects", json={"title": "Denied"}).status_code == 404
        assert guest.get(f"/api/decks/{app.deck_id}").status_code == 404
        assert (
            guest.get(
                "/together",
                params={"workspace": workspace, "artifact": app.deck_id, "slide": "cover"},
            ).headers["location"]
            == f"{prefix}/?artifact={app.deck_id}#cover"
        )
        deck = f"{prefix}/api/decks/{app.deck_id}"
        source = guest.get(deck).json()
        assert source["title"] == "Isolated presentation"
        before = source["slides"][0]
        assert guest.post(
            deck + "/slides/cover/notes",
            json={
                "notes": "Only in the isolated workspace",
                "expected_notes": before["notes"],
                "expected_revision": before["notes_revision"],
            },
        ).is_success
        assert owner.get(deck).json()["slides"][0]["notes"] == "Only in the isolated workspace"
        assert owner.get(f"/api/decks/{app.deck_id}").json()["slides"][0]["notes"] != (
            "Only in the isolated workspace"
        )
        presenter = guest.get(f"{prefix}/decks/{app.deck_id}/presenter")
        assert presenter.is_success and "Only in the isolated workspace" in presenter.text
        assert f"{WORKSPACE}{prefix}/api/decks/" in presenter.headers["content-security-policy"]
        assert guest.get(f"{prefix}/decks/{app.deck_id}/theme.css").is_success
        access = owner.get(
            f"/api/workspaces/{workspace}/access",
            params={"kind": "artifact", "resource": app.deck_id},
        ).json()
        grant_id = access["grants"][0]["id"]
        assert owner.post(
            f"/api/workspaces/{workspace}/access/{grant_id}/revoke",
            json={"kind": "artifact", "resource": app.deck_id},
        ).is_success
        assert guest.get(deck).status_code == 404
        assert guest.get(f"{prefix}/decks/{app.deck_id}/theme.css").status_code == 404


def test_configured_personal_owner_is_separate_from_primary_owner(tmp_path: Path) -> None:
    """A verified personal owner can share a project without gaining primary access."""
    with (
        running_multiplayer_app(
            tmp_path / "app",
            tmp_path / "server.log",
            unified=True,
            personal_workspace_emails=("alice@example.test",),
        ) as app,
        human(app, "owner") as primary,
        human(app, "alice") as personal,
        human(app, "bob") as guest,
        httpx.Client(base_url=app.url, headers={"host": "studio.example.test"}) as anonymous,
    ):
        first = personal.get("/")
        home = first.headers["location"]
        assert first.status_code == 307 and re.fullmatch(r"/workspaces/[0-9a-f]{24}/", home)
        assert personal.get("/").headers["location"] == home
        assert guest.get("/").status_code == 200
        assert anonymous.get(home).status_code == 401
        assert personal.get(home + "api/session").json()["workspace_owner"]
        assert not primary.get(home + "api/session").json()["workspace_owner"]
        assert not personal.get("/api/session").json()["workspace_owner"]
        assert personal.get(f"/api/artifacts/{app.artifact_id}").status_code == 404
        assert (
            personal.post("/api/workspaces", json={"title": "Extra workspace"}).status_code == 404
        )

        projects = home + "api/hub/projects"
        assert primary.post(projects, json={"title": "Forbidden"}).status_code == 404
        project = personal.post(projects, json={"title": "Personal project"})
        assert project.is_success
        assert primary.get(home + "api/hub").json()["projects"] == []
        assert guest.get(home + "api/hub").json()["projects"] == []
        assert personal.get(home + "api/hub").json()["projects"][0]["title"] == "Personal project"

        workspace = home.split("/")[2]
        invite = personal.post(
            f"/api/workspaces/{workspace}/invitations",
            json={
                "kind": "project",
                "resource": project.json()["id"],
                "email": "bob@example.test",
                "role": "edit",
            },
        )
        assert invite.is_success
        accept(guest, invite.json()["url"].split("#invite=")[1])
        assert guest.get(home + "api/hub").json()["projects"][0]["title"] == "Personal project"
        assert not guest.get(home + "api/session").json()["workspace_owner"]
        assert guest.post(projects, json={"title": "Forbidden"}).status_code == 404

        accept(personal, grant(primary, "alice", app.artifact_id, "edit"))
        assert personal.get("/").status_code == 200
        assert personal.get(f"/api/artifacts/{app.artifact_id}").is_success
        assert personal.get(f"/api/artifacts/{app.sibling_id}").status_code == 404
        workspaces = personal.get("/api/together/session").json()["workspaces"]
        assert {item["id"]: item["owner"] for item in workspaces} == {
            workspace: True,
            "default": False,
        }


@pytest.mark.parametrize("owner_name", ("owner", "alice"))
def test_organizing_hosted_projects_keeps_inherited_access_explicit(
    tmp_path: Path, owner_name: str
) -> None:
    """Folder changes are private and moving content requires current access confirmation."""
    with (
        running_multiplayer_app(
            tmp_path / "app",
            tmp_path / "server.log",
            unified=True,
            personal_workspace_emails=("alice@example.test",),
        ) as app,
        human(app, owner_name) as owner,
        human(app, "bob") as guest,
    ):
        home = owner.get("/").headers["location"] if owner_name == "alice" else "/"
        workspace = home.split("/")[2] if owner_name == "alice" else "default"
        api = home + "api/"
        projects = api + "hub/projects"
        source = owner.post(projects, json={"title": "Research"}).raise_for_status().json()["id"]
        destination = (
            owner.post(projects, json={"title": "Delivery"}).raise_for_status().json()["id"]
        )
        artifact = (
            owner.post(
                api + "artifacts/import",
                json={
                    "project_id": source,
                    "title": "Fictional notes",
                    "files": [{"name": "notes.md", "data_base64": "IyBOb3Rlcwo="}],
                },
            )
            .raise_for_status()
            .json()["id"]
        )
        path = f"{api}artifacts/{artifact}"
        original = owner.get(path).raise_for_status().json()
        invites = f"/api/workspaces/{workspace}/invitations"
        invite = (
            owner.post(
                invites,
                json={
                    "kind": "project",
                    "resource": source,
                    "email": "bob@example.test",
                    "role": "edit",
                },
            )
            .raise_for_status()
            .json()["url"]
        )
        accept(guest, invite.split("#invite=")[1])
        document = guest.get(
            f"/api/workspaces/{workspace}/artifacts/{artifact}/preview",
            headers={"sec-fetch-dest": "iframe"},
        )
        assert document.is_success
        ticket = re.search(r"https://preview\.example\.test/_preview/[^/\"\s]+", document.text)
        assert ticket
        preview = urlsplit(ticket[0]).path + f"/api/artifacts/{artifact}/revisions/1/files/notes.md"
        preview_host = {"host": "preview.example.test"}
        assert guest.get(preview, headers=preview_host).is_success
        tree_path = api + "hub/project-tree"
        tree_hash = owner.get(api + "hub").json()["project_tree"]["content_hash"]
        create = {"action": "create", "title": "Private folder", "expected_hash": tree_hash}
        assert guest.post(tree_path, json=create).status_code == 404
        tree = owner.post(tree_path, json=create).raise_for_status().json()
        folder = next(node for node in tree["nodes"] if node["kind"] == "folder")
        owner.post(
            tree_path,
            json={
                "action": "move",
                "id": source,
                "parent_id": folder["id"],
                "expected_hash": tree["content_hash"],
            },
        ).raise_for_status()
        assert "project_tree" not in guest.get(api + "hub").raise_for_status().json()
        assert guest.get(path).is_success
        move = {"project_id": destination}
        assert guest.post(path + "/project", json=move).status_code == 404
        first = owner.post(path + "/project", json=move)
        assert first.status_code == 409 and first.json()["code"] == "project-access-changed"
        assert guest.get(path).is_success
        owner.post(
            invites,
            json={
                "kind": "project",
                "resource": destination,
                "email": "alice@example.test" if owner_name == "owner" else "owner@example.test",
                "role": "view",
            },
        ).raise_for_status()
        second = owner.post(
            path + "/project", json={**move, "access_hash": first.json()["access_hash"]}
        )
        assert (
            second.status_code == 409
            and second.json()["access_hash"] != first.json()["access_hash"]
        )
        owner.post(
            path + "/project", json={**move, "access_hash": second.json()["access_hash"]}
        ).raise_for_status()
        assert guest.get(path).status_code == 404
        assert guest.get(preview, headers=preview_host).status_code == 404
        current = owner.get(path).raise_for_status().json()
        assert current["project_id"] == destination
        assert current["content_hash"] == original["content_hash"]
        assert current["revision"] == original["revision"]


def test_hosted_favorites_follow_reader_and_grant(tmp_path: Path) -> None:
    """Readers of one deck keep separate bookmarks and lose previews on revocation."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        human(app, "bob") as bob,
    ):
        for name, reader in (("alice", alice), ("bob", bob)):
            accept(reader, grant(owner, name, app.deck_id, "view"))
        index = "/api/favorites"
        star = {"kind": "slide", "artifact_id": app.deck_id, "slide_id": "cover", "starred": True}
        assert alice.post(index, json={**star, "artifact_id": app.artifact_id}).status_code == 404
        assert alice.post(index, json=star).raise_for_status().json()["slides"] == [
            app.deck_id + "/cover"
        ]
        path = f"{index}/slides/{app.deck_id}/cover/description"
        body = {"description": "Alice’s bookmark", "expected_description": ""}
        assert alice.post(path, json=body).is_success
        assert bob.post(path, json=body).status_code == 404
        assert alice.get(index + "/slides").json()[0]["description"] == body["description"]
        assert alice.post(path.replace("/description", "/reference"), json={}).status_code == 404
        assert bob.get(index).json()["slides"] == []
        assert owner.get(index).json()["slides"] == []
        assert bob.post(index, json=star).is_success
        assert bob.get(index + "/slides").json()[0]["description"] == ""

        preview = f"/api/workspaces/default/artifacts/{app.deck_id}/preview?slide_id=cover"
        frame = {"sec-fetch-dest": "iframe"}
        assert alice.get(preview, headers=frame).is_success
        access = "/api/workspaces/default/access"
        scope = {"kind": "artifact", "resource": app.deck_id}
        grants = owner.get(access, params=scope).json()["grants"]
        alice_grant = next(item for item in grants if item["email"] == "alice@example.test")
        assert owner.post(f"{access}/{alice_grant['id']}/revoke", json=scope).is_success
        assert alice.get(index).json()["slides"] == []
        assert alice.get(index + "/slides").json() == []
        assert alice.post(path, json=body).status_code == 404
        assert alice.get(preview, headers=frame).status_code == 404
        assert bob.get(index).json()["slides"] == [app.deck_id + "/cover"]
        unknown = "/workspaces/" + "0" * 24 + index
        assert alice.get(unknown).json() == alice.get(index).json()
        assert (
            alice.post(unknown, json=star).status_code == alice.post(index, json=star).status_code
        )
