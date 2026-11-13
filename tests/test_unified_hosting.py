"""Exercise editor and collaboration boundaries through one real HTTP listener."""

import fcntl
import json
import os
import re
from contextlib import closing
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZipFile

import httpx
import pytest

from artifact_studio.artifact_models import ArtifactFileInput
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.deployment import DeploymentConfig
from artifact_studio.element_alternatives import ElementSet
from artifact_studio.models import SlidePatch, Source
from artifact_studio.multiplayer_access import Sharing
from artifact_studio.slide_designs import DesignPage, DesignSet
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


def test_design_transfers_require_workspace_ownership(tmp_path: Path) -> None:
    """Shared edit access does not expose private design ZIPs or install designs."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
    ):
        accept(alice, grant(owner, "alice", app.project_id, "edit", "project"))
        path = "/api/design-library/test-brand/export"
        response = owner.get(path).raise_for_status()
        assert response.headers["content-disposition"].endswith('test-brand.design.zip"')
        assert alice.get(path).status_code == 404
        headers = {"content-type": "application/zip"}
        upload = "/api/design-library/import"
        assert alice.post(upload, content=response.content, headers=headers).status_code == 404
        assert owner.post(upload, content=response.content, headers=headers).status_code == 409
        workspace = owner.post("/api/workspaces", json={"title": "Design destination"})
        prefix = "/workspaces/" + workspace.raise_for_status().json()["id"]
        imported = owner.post(prefix + upload, content=response.content, headers=headers)
        assert imported.raise_for_status().json()["id"] == "test-brand"
        assert "local_path" not in imported.text
        assert owner.get(prefix + path).status_code == 200


def test_scoped_exports_follow_the_reader_and_current_project_access(tmp_path: Path) -> None:
    """Bind prepared downloads to their reader and recheck every archived artifact."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        human(app, "bob") as bob,
    ):
        accept(alice, grant(owner, "alice", app.project_id, "view", "project"))
        accept(bob, grant(owner, "bob", app.artifact_id, "view"))
        artifact = {"scope": "artifact", "id": app.artifact_id}
        project = {"scope": "project", "id": app.project_id}
        assert bob.post("/api/shares", json=project).status_code == 404
        file = alice.post("/api/shares", json=artifact).raise_for_status().json()["download_url"]
        assert bob.get(file).status_code == 404
        own = bob.post("/api/shares", json=artifact).raise_for_status().json()["download_url"]
        assert bob.get(own).content == b"Fictional shared artifact"
        archive = alice.post("/api/shares", json=project).raise_for_status().json()["download_url"]
        response = alice.get(archive).raise_for_status()
        assert response.headers["content-disposition"].startswith("attachment;")
        with ZipFile(BytesIO(response.content)) as exported:
            assert len(exported.namelist()) == 2
        assert not alice.get("/api/shares/capabilities").json()["outlook"]

        destination = (
            owner.post("/api/hub/projects", json={"title": "Private workspace project"})
            .raise_for_status()
            .json()["id"]
        )
        move = f"/api/artifacts/{app.sibling_id}/project"
        body = {"project_id": destination}
        pending = owner.post(move, json=body)
        assert pending.status_code == 409
        owner.post(
            move, json={**body, "access_hash": pending.json()["access_hash"]}
        ).raise_for_status()
        assert alice.get(archive).status_code == 404
        assert alice.get(file).is_success


def test_scoped_presentation_export_uses_audience_only_content(tmp_path: Path) -> None:
    """Include the deck and builds while leaving notes and private source labels out."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
    ):
        slide = app.store.view(app.deck_id).slides[0]
        _ = app.store.revise_view(
            app.deck_id,
            slide.id,
            slide.expected_hash,
            "export-fixture-sources",
            SlidePatch(sources=[Source(label="Fictional private source label")]),
        )
        _ = app.store.save_notes(
            app.deck_id, slide.id, "Fictional private speaker note", slide.notes
        )
        accept(alice, grant(owner, "alice", app.deck_id, "view"))
        body = {"scope": "artifact", "id": app.deck_id}
        assert alice.post("/api/shares", json=body).status_code == 404
        receipt = (
            alice.post("/api/shares", json={**body, "format": "html"}).raise_for_status().json()
        )
        html = alice.get(receipt["download_url"]).raise_for_status().text
        assert 'data-export="audience"' in html and "Fictional team deck" in html
        assert "Fictional private" not in html


def test_shared_deck_validators_follow_content_and_current_access(tmp_path: Path) -> None:
    """An unchanged deck sends no body, and a validator never bypasses a current grant."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        human(app, "bob") as bob,
    ):
        for reader, name in ((alice, "alice"), (bob, "bob")):
            accept(reader, grant(owner, name, app.deck_id, role="view"))
        path = f"/api/decks/{app.deck_id}"
        initial = alice.get(path).raise_for_status()
        headers = {"if-none-match": initial.headers["etag"]}
        unchanged = alice.get(path, headers=headers)
        assert unchanged.status_code == 304 and not unchanged.content
        assert unchanged.headers["cache-control"] == "no-store"
        assert bob.get(path, headers=headers).status_code == 200

        slide = app.store.view(app.deck_id).slides[0]
        _ = app.store.save_notes(app.deck_id, slide.id, "Fictional speaker note", slide.notes)
        revised = alice.get(path, headers=headers).raise_for_status()
        assert revised.headers["etag"] != initial.headers["etag"]
        assert revised.json()["slides"][0]["notes"] == "Fictional speaker note"
        headers["if-none-match"] = revised.headers["etag"]

        app.store.approve(app.deck_id, slide.id, slide.content_hash, True)
        approved = alice.get(path, headers=headers).raise_for_status()
        assert approved.json()["slides"][0]["approved"]
        seen_path = f"{path}/slides/{slide.id}/seen"
        assert alice.post(seen_path, json={"content_hash": slide.content_hash}).json() == {
            "seen": True
        }
        recorded = alice.get(path, headers={"if-none-match": approved.headers["etag"]})
        headers["if-none-match"] = recorded.raise_for_status().headers["etag"]
        theme = app.store.folder(app.deck_id) / "theme.css"
        _ = theme.write_text(theme.read_text() + "\n.slide { color: navy; }\n")
        themed = alice.get(path, headers=headers).raise_for_status()
        assert themed.json()["slides"][0]["content_hash"] != slide.content_hash
        assert themed.json()["slides"][0]["has_unseen_changes"]
        headers["if-none-match"] = themed.headers["etag"]

        seen = alice.post(
            seen_path,
            json={"content_hash": themed.json()["slides"][0]["content_hash"]},
        )
        assert seen.json() == {"seen": True}
        opened = alice.get(path, headers=headers).raise_for_status()
        assert not opened.json()["slides"][0]["has_unseen_changes"]
        headers["if-none-match"] = opened.headers["etag"]

        old_stat = theme.stat()
        _ = theme.write_text(theme.read_text().replace("color: navy", "color: gold"))
        os.utime(theme, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        same_size = theme.stat()
        assert same_size.st_size == old_stat.st_size
        assert same_size.st_mtime_ns == old_stat.st_mtime_ns
        changed = alice.get(path, headers=headers).raise_for_status()
        assert changed.headers["etag"] != opened.headers["etag"]
        assert changed.json()["design_hash"] != opened.json()["design_hash"]
        headers["if-none-match"] = changed.headers["etag"]

        scope = {"kind": "artifact", "resource": app.deck_id}
        grants = owner.get("/api/workspaces/default/access", params=scope).json()["grants"]
        alice_grant = next(item["id"] for item in grants if item["email"] == "alice@example.test")
        owner.post(
            f"/api/workspaces/default/access/{alice_grant}/revoke", json=scope
        ).raise_for_status()
        denied = alice.get(path, headers=headers)
        assert denied.status_code == 404 and "etag" not in denied.headers


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


def seed_artifact_history(root: Path, app: MultiplayerApp, workspace: str) -> str:
    """Retain a previous fictional page and stylesheet in the selected workspace."""
    source = root / "history-fixture"
    source.mkdir()
    _ = (source / "index.html").write_text(
        '<link rel="stylesheet" href="retired.css"><h1>Original draft</h1>'
    )
    _ = (source / "retired.css").write_text("h1 { color: rgb(93, 24, 117) }")
    shared = Sharing(root / "sharing", app.store, "owner@example.test")
    with shared.locked():
        service = ArtifactStore(shared.store(workspace))
        project = service.create_project("History fixture")
        initial = service.register_artifact(project.id, "History sample", source, "index.html")
        _ = service.revise_artifact(
            initial.id,
            [
                ArtifactFileInput(path="index.html", text="<h1>Current draft</h1>"),
                ArtifactFileInput(path="retired.css", delete=True),
            ],
            initial.content_hash,
            "fixture-current-draft",
        )
    return initial.id


def test_artifact_history_is_private_until_the_owner_restores_it(tmp_path: Path) -> None:
    """A current editor cannot see retired files, while the owner can restore atomically."""
    root = tmp_path / "app"
    with (
        running_multiplayer_app(
            root,
            tmp_path / "server.log",
            unified=True,
            personal_workspace_emails=("alice@example.test",),
        ) as app,
        human(app, "alice") as owner,
        human(app, "bob") as editor,
    ):
        home = owner.get("/").headers["location"]
        workspace = home.split("/")[2]
        artifact = seed_artifact_history(root, app, workspace)
        api = f"{home}api/artifacts/{artifact}"
        timeline = api + "/history"
        preview = f"/api/workspaces/{workspace}/artifacts/{artifact}/preview"
        invite = (
            owner.post(
                f"/api/workspaces/{workspace}/invitations",
                json={
                    "kind": "artifact",
                    "resource": artifact,
                    "role": "edit",
                    "email": "bob@example.test",
                },
            )
            .raise_for_status()
            .json()["url"]
        )
        accept(editor, invite.split("#invite=")[1])
        assert editor.get(api).is_success
        assert editor.get(timeline).status_code == 404
        assert (
            editor.get(timeline + "/thumbnail", params={"version_id": "artifact:1"}).status_code
            == 404
        )
        current = owner.get(timeline).raise_for_status().json()
        body = {
            "version_id": "artifact:1",
            "expected_hash": current["expected_hash"],
            "request_id": "fixture-restore-original",
        }
        assert editor.post(timeline + "/restore", json=body).status_code == 404
        assert (
            owner.post(timeline + "/restore", json={**body, "expected_hash": "0" * 64}).status_code
            == 409
        )
        frame = {"sec-fetch-dest": "iframe"}
        params = {"history_revision": 1}
        assert editor.get(api + "/preview", headers=frame, params=params).status_code == 404
        assert editor.get(preview, headers=frame, params=params).status_code == 404
        document = owner.get(preview, headers=frame, params=params).raise_for_status().text
        assert "Original draft" in document
        match = re.search(r"https://preview\.example\.test/_preview/[^/\"\s]+", document)
        assert match
        asset = urlsplit(match[0]).path + f"/api/artifacts/{artifact}/revisions/1/files/retired.css"
        response = owner.get(
            asset, headers={"host": "preview.example.test", "sec-fetch-dest": "style"}
        )
        assert response.is_success and "rgb(93, 24, 117)" in response.text
        assert editor.get(api + "/files/retired.css").status_code == 404
        saved = owner.post(timeline + "/restore", json=body).raise_for_status()
        assert saved.json() == {"saved": True}
        assert editor.get(api).json()["revision"] == 3
        assert "rgb(93, 24, 117)" in editor.get(api + "/files/retired.css").text
        assert editor.get(timeline).status_code == 404


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

        preview = (
            f"/api/workspaces/default/artifacts/{app.deck_id}/preview?slide_id=cover&thumbnail=true"
        )
        frame = {"sec-fetch-dest": "iframe"}
        document = alice.get(preview, headers=frame).raise_for_status()
        assert "sandbox; script-src 'none'" in document.headers["content-security-policy"]
        assert document.headers["cache-control"] == "no-store"
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


def test_hosted_agents_author_native_decks_with_their_existing_grants(tmp_path: Path) -> None:
    """Run native authoring, proposal review and reversible removal over authenticated MCP."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as editor,
        human(app, "bob") as viewer,
    ):
        token = (
            owner.post(
                "/api/workspaces/default/agents",
                json={"kind": "project", "resource": app.project_id, "name": "Project author"},
            )
            .raise_for_status()
            .json()["token"]
        )
        owner_peer = Peer(app, token)
        with closing(owner_peer.client):
            creation = {
                "deck_id": "2026-11-11-fictional-parity",
                "title": "Fictional parity deck",
                "brief": "A short presentation about shared authoring.",
                "request_id": "create-native-deck",
            }
            created = owner_peer.value("create_presentation", **creation)
            assert created == owner_peer.value("create_presentation", **creation)
            assert owner_peer.call(
                "create_presentation", **(creation | {"title": "Changed"})
            ).is_error
            deck_id = str(created["id"])
            assert owner.get(urlsplit(str(created["audience_url"])).path + "?audience=1").is_success
            assert ArtifactStore(app.store).get_artifact(deck_id).project_id == app.project_id
        accept(editor, grant(owner, "alice", deck_id))
        accept(viewer, grant(owner, "bob", deck_id, "view"))
        peer, reader = Peer(app, agent(editor, deck_id)), Peer(app, agent(viewer, deck_id))
        with closing(peer.client), closing(reader.client):
            assert peer.call("create_presentation", **creation).is_error
            outline = peer.value("get_presentation_outline", deck_id=deck_id)
            assert "shared authoring" in str(outline["context_markdown"])
            cover_id = app.store.read(deck_id).slides[0].id
            ids = [cover_id]
            added = peer.value(
                "add_slide",
                deck_id=deck_id,
                after_slide_id=cover_id,
                expected_slide_ids=ids,
                expected_sections_hash=outline["sections_hash"],
                request_id="add-second-slide",
                slide_json=json.dumps(
                    {
                        "id": "detail",
                        "title": "Detail",
                        "html": '<article class="slide"><h1 id="headline">Detail</h1></article>',
                    }
                ),
            )
            assert added["id"] == "detail"
            ordered = peer.value(
                "reorder_slides",
                deck_id=deck_id,
                slide_ids=["detail", cover_id],
                expected_slide_ids=[cover_id, "detail"],
            )
            assert ordered["slide_ids"] == ["detail", cover_id]
            grouped = peer.value(
                "set_presentation_sections",
                deck_id=deck_id,
                sections=[
                    {"id": "overview", "title": "Overview", "slide_ids": [cover_id, "detail"]}
                ],
                expected_slide_ids=["detail", cover_id],
                expected_sections_hash=outline["sections_hash"],
            )
            assert grouped["slide_ids"] == [cover_id, "detail"]
            assert not peer.call(
                "append_presentation_context",
                deck_id=deck_id,
                markdown="Updated continuation.",
                expected_hash=outline["context_hash"],
            ).is_error
            theme = peer.value("get_presentation_theme", deck_id=deck_id)
            assert reader.call(
                "update_presentation_theme",
                deck_id=deck_id,
                css="h1 { color: navy; }",
                expected_hash=theme["expected_hash"],
            ).is_error
            assert (
                peer.value(
                    "update_presentation_theme",
                    deck_id=deck_id,
                    css="h1 { color: navy; }",
                    expected_hash=theme["expected_hash"],
                )["expected_hash"]
                != theme["expected_hash"]
            )
            upload = {
                "deck_id": deck_id,
                "asset_path": "assets/widget.js",
                "data_base64": "Y29uc29sZS5sb2coJ2ZpeHR1cmUnKQ==",
            }
            assert peer.value("upload_presentation_asset", **upload)["size"] == 22
            assets = peer.value("list_presentation_assets", deck_id=deck_id)
            assert "import_directory" not in assets
            assert "assets/widget.js" in str(assets)
            assert "console.log" in str(
                peer.call(
                    "prepare_interactive_html",
                    deck_id=deck_id,
                    include_theme=False,
                    html='<script src="assets/widget.js"></script>',
                ).content
            )
            assert peer.call(
                "prepare_interactive_html",
                deck_id=deck_id,
                html='<script src="README.md"></script>',
            ).is_error
            slide = peer.value("get_slide", deck_id=deck_id, slide_id="detail")
            publish = {
                "deck_id": deck_id,
                "slide_id": "detail",
                "expected_content_hash": slide["content_hash"],
                "request_id": "publish-designs",
                "options": [
                    {
                        "name": "Editorial",
                        "html": '<article class="slide"><h1>Editorial</h1></article>',
                    }
                ],
            }
            assert reader.call("publish_slide_designs", **publish).is_error
            proposal = peer.value("publish_slide_designs", **publish)
            assert peer.value("publish_slide_designs", **publish) == proposal
            option = DesignSet.model_validate(proposal).options[1]
            page = DesignPage.model_validate(
                reader.value("get_slide_designs", deck_id=deck_id, slide_id="detail")
            )
            assert page.sets[0].id == proposal["id"]
            assert "Editorial" in str(
                reader.value(
                    "get_slide_design",
                    deck_id=deck_id,
                    slide_id="detail",
                    design_id=option.id,
                )["html"]
            )
            document = viewer.get(option.preview_url).raise_for_status()
            assert (
                "Editorial" in document.text
                and "sandbox" in document.headers["content-security-policy"]
            )
            assert "no-store" in document.headers["cache-control"]
            assert "Detail" in app.store.read(deck_id).slides[1].html

            element = peer.value(
                "publish_element_alternatives",
                deck_id=deck_id,
                slide_id="detail",
                target_id="headline",
                kind="headline",
                label="Heading",
                expected_content_hash=slide["content_hash"],
                request_id="publish-element",
                options=[{"name": "Short", "html": '<h1 id="headline">Brief</h1>'}],
            )
            assert viewer.get(ElementSet.model_validate(element).options[1].preview_url).is_success
            path = f"/api/decks/{deck_id}/slides/detail/designs"
            decision = {
                "set_id": proposal["id"],
                "option_id": option.id,
                "expected_content_hash": slide["content_hash"],
                "request_id": "choose-design",
            }
            assert viewer.post(path + "/decide", json=decision).status_code == 404
            chosen = editor.post(path + "/decide", json=decision).raise_for_status().json()
            assert chosen["action"] == "choose"
            restored = (
                editor.post(
                    path + "/undo",
                    json={
                        "decision_id": chosen["id"],
                        "expected_content_hash": chosen["content_hash"],
                        "request_id": "undo-design",
                    },
                )
                .raise_for_status()
                .json()
            )
            assert restored["action"] == "undo"

            removal = {
                "deck_id": deck_id,
                "slide_id": "detail",
                "expected_hash": slide["expected_hash"],
                "request_id": "remove-detail",
            }
            assert reader.call("remove_slide", **removal).is_error
            assert peer.call("remove_slide", **removal).is_error
            current = peer.value("get_slide", deck_id=deck_id, slide_id="detail")
            removal["expected_hash"] = current["expected_hash"]
            removed = peer.value("remove_slide", **removal)
            assert peer.value("remove_slide", **removal) == removed
            recovery = f"/api/decks/{deck_id}/deleted-slides"
            assert (
                editor.get(recovery).raise_for_status().json()[0]["operation_id"]
                == removed["operation_id"]
            )
            assert peer.value(
                "restore_slides",
                deck_id=deck_id,
                deletion_id=removed["operation_id"],
                request_id="restore-detail",
            )["added_ids"] == ["detail"]
            assert editor.get(recovery).raise_for_status().json() == []
