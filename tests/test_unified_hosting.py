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
from artifact_studio.unified import create_app
from tests.integration.multiplayer_fixture import PREVIEW, WORKSPACE, running_multiplayer_app
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
            == f"/together?workspace=default&artifact={app.artifact_id}"
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
