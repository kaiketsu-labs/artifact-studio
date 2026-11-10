"""Exercise attachment visibility and the familiar composer over real HTTP and MCP."""

import base64
import os
import sqlite3
from contextlib import closing
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from PIL import Image
from playwright.sync_api import expect, sync_playwright

from tests.integration.multiplayer_fixture import (
    PREVIEW,
    WORKSPACE,
    routed_multiplayer_context,
    running_multiplayer_app,
)
from tests.test_multiplayer_server import accept, agent, grant, human
from tests.test_multiplayer_simulation import Peer


def raster_fixture() -> bytes:
    """Create a disposable image with no external dependencies."""
    buffer = BytesIO()
    Image.new("RGB", (24, 16), "#4169e1").save(buffer, format="PNG")
    return buffer.getvalue()


def test_attachments_follow_message_visibility_and_current_grants(tmp_path: Path) -> None:
    """Check retries, release, safe downloads, agent reads, and revoked access."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as collaborator,
        human(app, "bob") as viewer,
        httpx.Client(base_url=app.url, headers={"host": urlsplit(PREVIEW).netloc}) as preview,
    ):
        accept(collaborator, grant(owner, "alice", app.artifact_id, "comment"))
        accept(viewer, grant(owner, "bob", app.artifact_id, "view"))
        root = f"/api/workspaces/default/artifacts/{app.artifact_id}"
        png = raster_fixture()
        text = b"Fictional notes about the figure."
        files = [
            {"name": "figure.png", "data_base64": base64.b64encode(png).decode()},
            {"name": "notes.txt", "data_base64": base64.b64encode(text).decode()},
        ]
        payload = {
            "message": "",
            "request_id": "fixture-attachment",
            "released": False,
            "files": files,
        }
        assert viewer.post(root + "/chat", json=payload).status_code == 404
        posted = collaborator.post(root + "/chat", json=payload)
        assert posted.is_success, posted.text
        receipt = posted.json()
        event = receipt["id"]
        figure, notes = receipt["files"]
        assert figure["size"] == len(png)
        assert "data_base64" not in figure
        assert collaborator.post(root + "/chat", json=payload).json() == receipt
        assert (
            collaborator.post(root + "/chat", json={**payload, "files": files[:1]}).status_code
            == 409
        )
        assert viewer.get(root + "/chat").json() == []
        assert viewer.get(figure["download_url"]).status_code == 404
        assert collaborator.get(figure["download_url"]).content == png
        with closing((peer := Peer(app, agent(collaborator, app.artifact_id))).client):
            args = {"artifact_id": app.artifact_id, "event_id": event, "file_index": 1}
            assert peer.call("read_chat_attachment", **args).is_error
            assert collaborator.post(root + "/release", json={}).is_success
            assert viewer.get(root + "/chat").json()[0]["files"] == receipt["files"]
            download = viewer.get(figure["download_url"])
            assert download.content == png
            assert download.headers["content-type"] == "application/octet-stream"
            assert download.headers["content-disposition"].startswith("attachment")
            rendered = viewer.get(figure["download_url"], params={"preview": True})
            assert rendered.headers["content-type"] == "image/png"
            assert viewer.get(notes["download_url"], params={"preview": True}).status_code == 204
            assert preview.get(figure["download_url"]).status_code == 404
            assert (
                owner.get(
                    figure["download_url"].replace(app.artifact_id, app.sibling_id)
                ).status_code
                == 404
            )
            part = peer.value("read_chat_attachment", **args, max_bytes=9)
            assert part["text"] == text[:9].decode()
            assert part["next_offset"] == 9
            private = collaborator.post(
                root + "/chat",
                json={
                    **payload,
                    "request_id": "fixture-private",
                    "channel": "private",
                    "released": True,
                },
            ).json()
            assert owner.get(private["files"][0]["download_url"]).status_code == 404
            assert not peer.call(
                "read_chat_attachment", **{**args, "event_id": private["id"]}
            ).is_error
            access = owner.get(
                "/api/workspaces/default/access",
                params={"kind": "artifact", "resource": app.artifact_id},
            ).json()
            grant_id = next(
                row["id"] for row in access["grants"] if row["email"] == "alice@example.test"
            )
            assert owner.post(
                f"/api/workspaces/default/access/{grant_id}/revoke",
                json={"kind": "artifact", "resource": app.artifact_id},
            ).is_success
            assert collaborator.get(figure["download_url"]).status_code == 404
            assert peer.call("read_chat_attachment", **args).is_error

        large = base64.b64encode(b"x" * 1_600_000).decode()
        assert owner.post(
            root + "/chat",
            json={
                "message": "",
                "request_id": "fixture-large",
                "files": [{"name": "data.txt", "data_base64": large}],
            },
        ).is_success
        invalid = owner.post(
            root + "/chat",
            json={
                "message": "",
                "request_id": "fixture-invalid",
                "files": [{"name": 42, "data_base64": large}],
            },
        )
        assert invalid.status_code == 422
        assert len(invalid.content) < 1000
        with closing(sqlite3.connect(tmp_path / "app/sharing/sharing.sqlite3")) as db, db:
            _ = db.execute(
                "UPDATE chat_attachment_usage SET bytes=? WHERE workspace='default'", (1024**3,)
            )
        exceeded = owner.post(root + "/chat", json={**payload, "request_id": "fixture-quota"})
        assert exceeded.status_code == 409
        assert owner.post(
            root + "/chat", json={"message": "Text still works", "request_id": "fixture-text-only"}
        ).is_success


@pytest.mark.browser
@pytest.mark.skipif(
    os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Browser checks are opt in."
)
def test_normal_editor_sends_and_previews_shared_images(tmp_path: Path) -> None:
    """Use the same attachment picker in the shared slide and artifact editors."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as collaborator,
        sync_playwright() as playwright,
    ):
        accept(collaborator, grant(owner, "alice", app.deck_id, "comment"))
        accept(collaborator, grant(owner, "alice", app.artifact_id, "comment"))
        browser = playwright.chromium.launch()
        try:
            with (
                routed_multiplayer_context(browser, app, "alice") as guest,
                routed_multiplayer_context(browser, app) as author,
            ):
                page, receiving = guest.new_page(), author.new_page()
                for url in (f"/?deck={app.deck_id}", f"/?artifact={app.artifact_id}"):
                    receiving.goto(WORKSPACE + url)
                    page.goto(WORKSPACE + url)
                    with page.expect_file_chooser() as chooser:
                        page.get_by_role("button", name="Attach files", exact=True).click()
                    chooser.value.set_files(
                        {"name": "fixture.png", "mimeType": "image/png", "buffer": raster_fixture()}
                    )
                    expect(page.get_by_role("button", name="Remove fixture.png")).to_be_visible()
                    draft_thumbnail = page.get_by_role("button", name="Preview fixture.png")
                    draft_thumbnail.focus()
                    page.keyboard.press("Enter")
                    image = page.get_by_role("dialog", name="Image preview: fixture.png")
                    expect(image).to_be_visible()
                    page.keyboard.press("Tab")
                    page.keyboard.press("Escape")
                    expect(image).to_be_hidden()
                    expect(draft_thumbnail).to_be_focused()
                    thumbnail = receiving.get_by_role("button", name="Preview fixture.png")
                    if url.startswith("/?artifact="):
                        page.get_by_role("button", name="Batch", exact=True).click()
                        page.get_by_role("button", name="Add feedback to batch").click()
                        expect(page.get_by_text("Private batch · not released")).to_be_visible()
                        expect(thumbnail).to_have_count(0)
                        page.get_by_role("button", name="Send batch · 1").click()
                        expect(page.get_by_text("Private batch · not released")).to_have_count(0)
                    else:
                        page.get_by_role("button", name="Send feedback", exact=True).click()
                    expect(thumbnail).to_be_visible(timeout=16000)
                    thumbnail.click()
                    expect(
                        receiving.get_by_role("dialog", name="Image preview: fixture.png")
                    ).to_be_visible()
                    receiving.keyboard.press("Escape")
        finally:
            browser.close()
