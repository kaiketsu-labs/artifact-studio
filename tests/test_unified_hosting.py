"""Exercise editor and collaboration boundaries through one real HTTP listener."""

import fcntl
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from artifact_studio.deployment import DeploymentConfig
from artifact_studio.unified import create_app
from tests.integration.multiplayer_fixture import PREVIEW, WORKSPACE, running_multiplayer_app
from tests.test_multiplayer_server import accept, agent, grant, human


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
        assert guest.get("/").headers["location"] == "/together"
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
        for path in (
            "/api/hub",
            "/api/decks",
            f"/api/artifacts/{app.artifact_id}",
            "/web/hub.html",
        ):
            assert guest.get(path).status_code == 403
        assert guest.post("/api/hub/projects", json={"title": "Not allowed"}).status_code == 403
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
