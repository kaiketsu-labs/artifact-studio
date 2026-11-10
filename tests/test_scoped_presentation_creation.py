"""Cover native presentation creation inside an isolated personal workspace."""

import base64
import json
import os
import re
import shutil
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from artifact_studio.storage import Store
from tests.design_factory import make_design
from tests.integration.multiplayer_fixture import (
    WORKSPACE,
    routed_multiplayer_context,
    running_multiplayer_app,
)
from tests.test_multiplayer_server import accept, human

DECK_ID = "2026-11-10-personal-presentation"
PERSONAL_DESIGN = "personal-design"


def test_design_copy_preserves_reserved_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject creation cleanly when only the workspace reserve fits on disk."""
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    make_design(store.root / "personal-design-pack", PERSONAL_DESIGN)
    available = shutil.disk_usage(store.root)._replace(free=1024)
    monkeypatch.setattr(shutil, "disk_usage", Mock(return_value=available))

    with pytest.raises(OSError, match="Not enough workspace storage"):
        _ = store.create(DECK_ID, "Personal presentation", scoped=True, disk_reserve_bytes=1024)
    assert not store.folder(DECK_ID).exists()


def test_owner_creates_deck_for_a_project_scoped_agent(tmp_path: Path) -> None:
    """Keep the creation, private brief and agent edits within the selected workspace."""
    root = tmp_path / "server"
    with (
        running_multiplayer_app(
            root,
            tmp_path / "server.log",
            unified=True,
            personal_workspace_emails=("alice@example.test",),
        ) as app,
        human(app, "owner") as primary,
        human(app, "alice") as owner,
        human(app, "bob") as guest,
    ):
        home = owner.get("/").headers["location"]
        workspace = home.split("/")[2]
        project = owner.post(home + "api/hub/projects", json={"title": "Fictional project"})
        project.raise_for_status()
        project_id = cast(str, project.json()["id"])
        invite = owner.post(
            f"/api/workspaces/{workspace}/invitations",
            json={
                "kind": "project",
                "resource": project_id,
                "email": "bob@example.test",
                "role": "edit",
            },
        )
        invite.raise_for_status()
        accept(guest, cast(str, invite.json()["url"]).split("#invite=")[1])

        data = b"fictional" * 200_000
        payload = {
            "project_id": project_id,
            "deck_id": DECK_ID,
            "title": "Personal presentation",
            "brief": "Fictional brief for a project agent.",
            "files": [{"name": "reference.txt", "data_base64": base64.b64encode(data).decode()}],
        }
        route = home + "api/decks"
        assert primary.post(route, json=payload).status_code == 404
        assert guest.post(route, json=payload).status_code == 404
        assert owner.post(route, json={**payload, "project_id": app.project_id}).status_code == 404
        assert owner.post(route, json={**payload, "design_id": "test-brand"}).status_code == 404
        invalid = owner.post(route, json={**payload, "title": ""})
        assert invalid.status_code == 422 and len(invalid.content) < 300
        assert owner.get(home + "api/designs").json() == []
        assert owner.get(home + "api/designs/test-brand/preview").status_code == 404
        assert guest.get(home + "api/designs").status_code == 404
        assert owner.get("/api/designs/test-brand/preview").status_code == 404

        created = owner.post(route, json=payload)
        created.raise_for_status()
        assert created.json() == {"id": DECK_ID, "title": payload["title"]}
        deck = f"{route}/{DECK_ID}"
        owner_view = owner.get(deck).json()
        assert owner_view["design_id"] == "" and owner_view["project_id"] == project_id
        assert owner_view["brief_files"] == [{"name": "reference.txt", "size": len(data)}]
        assert owner.get(deck + "/brief-files/0").content == data
        assert guest.get(deck).json()["brief_files"] == []
        assert guest.get(deck + "/brief-files/0").status_code == 404
        shared = guest.get(home + f"api/artifacts/{DECK_ID}")
        assert shared.is_success
        assert all(not file["path"].startswith("brief-") for file in shared.json()["files"])
        assert primary.get(deck).status_code == 404
        assert primary.get(f"/api/decks/{DECK_ID}").status_code == 404

        agent = owner.post(
            f"/api/workspaces/{workspace}/agents",
            json={"kind": "project", "resource": project_id, "name": "Fictional scoped agent"},
        )
        agent.raise_for_status()
        token = cast(str, agent.json()["token"])
        guest_agent = guest.post(
            f"/api/workspaces/{workspace}/agents",
            json={"kind": "project", "resource": project_id, "name": "Fictional guest agent"},
        )
        guest_agent.raise_for_status()
        with httpx.Client(
            base_url=app.url,
            headers={
                "host": "studio.example.test",
                "authorization": "Bearer " + token,
                "accept": "application/json, text/event-stream",
            },
        ) as mcp:

            def invoke(name: str, arguments: dict[str, object]) -> dict[str, object]:
                response = mcp.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments},
                    },
                )
                assert response.status_code == 200
                return cast(dict[str, object], response.json()["result"])

            context = invoke("get_presentation_outline", {"deck_id": DECK_ID})
            assert not context.get("isError"), context
            assert "Fictional brief for a project agent." in json.dumps(context)
            context_data = cast(dict[str, object], context["structuredContent"])
            assert context_data["brief_files"] == [
                {"file_index": 0, "name": "reference.txt", "size": len(data)}
            ]
            read = invoke(
                "read_presentation_brief_file",
                {"deck_id": DECK_ID, "file_index": 0, "offset": 9, "max_bytes": 9},
            )
            assert not read.get("isError"), read
            assert cast(dict[str, object], read["structuredContent"])["text"] == "fictional"
            assert invoke("get_presentation_outline", {"deck_id": app.deck_id}).get("isError")
            slide = invoke("get_slide", {"deck_id": DECK_ID, "slide_id": "cover"})
            assert not slide.get("isError"), slide
            source = cast(dict[str, object], slide["structuredContent"])
            edit = invoke(
                "revise_slide",
                {
                    "deck_id": DECK_ID,
                    "slide_id": "cover",
                    "expected_hash": source["expected_hash"],
                    "request_id": "fictional-personal-edit",
                    "patch_json": json.dumps(
                        {"html": '<article class="slide"><h1>Revised</h1></article>'}
                    ),
                },
            )
            assert not edit.get("isError"), edit
            assert "Revised" in owner.get(deck).json()["slides"][0]["html"]
            mcp.headers["authorization"] = "Bearer " + cast(str, guest_agent.json()["token"])
            guest_context = invoke("get_presentation_outline", {"deck_id": DECK_ID})
            assert not guest_context.get("isError"), guest_context
            assert "Fictional brief for a project agent." in json.dumps(guest_context)
            assert "reference.txt" not in json.dumps(guest_context)
            assert invoke(
                "read_presentation_brief_file", {"deck_id": DECK_ID, "file_index": 0}
            ).get("isError")

        design_dir = root / "sharing/workspaces" / workspace / "collection" / "personal-design-pack"
        make_design(design_dir, PERSONAL_DESIGN)
        assert owner.get(home + "api/preferences").json()["default_design_id"] == PERSONAL_DESIGN
        preview = owner.get(home + f"api/designs/{PERSONAL_DESIGN}/preview")
        assert preview.is_success and "data:image/svg+xml;base64," in preview.text
        assert "sandbox;" in preview.headers["content-security-policy"]
        assert primary.get(home + f"api/designs/{PERSONAL_DESIGN}/preview").status_code == 404
        assert guest.get(home + f"api/designs/{PERSONAL_DESIGN}/files/theme.css").status_code == 404
        branded = owner.post(
            route, json={"deck_id": DECK_ID + "-design", "title": "Designed presentation"}
        )
        branded.raise_for_status()
        view = owner.get(route + "/" + DECK_ID + "-design").json()
        assert view["design_id"] == PERSONAL_DESIGN
        assert view["slides"][0]["notes"] == "" and view["slides"][0]["sources"] == []


@pytest.mark.browser
@pytest.mark.skipif(
    os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
)
def test_new_presentation_uses_native_dialog_and_editor(tmp_path: Path) -> None:
    """Use the workspace Tools menu and verify the styled cover and attached brief."""
    root = tmp_path / "server"
    with (
        running_multiplayer_app(
            root,
            tmp_path / "server.log",
            unified=True,
            personal_workspace_emails=("alice@example.test",),
        ) as app,
        human(app, "alice") as owner,
        sync_playwright() as playwright,
    ):
        home = owner.get("/").headers["location"]
        project = owner.post(home + "api/hub/projects", json={"title": "Demo project"})
        project.raise_for_status()
        project_id = cast(str, project.json()["id"])
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, "alice") as context:
                page = context.new_page()
                project_url = WORKSPACE + home + "?project=" + project_id
                page.goto(project_url)
                expect(page.get_by_role("heading", name="Demo project", exact=True)).to_be_visible()
                page.get_by_role("button", name="Menu", exact=True).click()
                page.get_by_role("menuitem", name="New presentation", exact=True).click()
                dialog = page.get_by_role("dialog", name="New presentation")
                expect(dialog.get_by_role("textbox", name="Title")).to_be_focused()
                expect(dialog.locator(".design-fieldset")).to_be_hidden()
                dialog.get_by_role("button", name="Close").click()
                expect(page).to_have_url(project_url)
                page.get_by_role("button", name="Menu", exact=True).click()
                page.get_by_role("menuitem", name="New presentation", exact=True).click()
                dialog.get_by_role("textbox", name="Title").fill("Fictional deck")
                dialog.get_by_role("textbox", name="Brief").fill("A brief for the project agent.")
                dialog.locator("#brief-file-input").set_input_files(
                    {"name": "reference.txt", "mimeType": "text/plain", "buffer": b"fictional"}
                )
                dialog.locator("#create-button").click()
                expect(dialog).to_be_hidden()
                expect(page).to_have_url(re.compile(home + r"\?deck=.*fictional-deck#cover$"))
                preview = page.frame_locator("#preview").get_by_role(
                    "heading", name="Fictional deck"
                )
                expect(preview).to_be_visible()
                assert (
                    float(
                        preview.evaluate(
                            "element => parseFloat(getComputedStyle(element).fontSize)"
                        )
                    )
                    > 20
                )
                expect(page.locator("#brief-attachments a")).to_have_attribute(
                    "href", re.compile(home + r"api/decks/.*/brief-files/0$")
                )
                assert any(
                    item["title"] == "Fictional deck" and item["project_id"] == project_id
                    for item in owner.get(home + "api/hub").json()["artifacts"]
                )
        finally:
            browser.close()
