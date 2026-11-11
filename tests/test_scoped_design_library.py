"""Exercise the native design library within its owner's fictional workspace."""

import base64
import os
from pathlib import Path
from typing import cast

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from tests.design_factory import make_design
from tests.integration.multiplayer_fixture import (
    WORKSPACE,
    routed_multiplayer_context,
    running_multiplayer_app,
)
from tests.test_multiplayer_server import accept, human


def test_design_library_and_agent_use_only_the_selected_owned_workspace(tmp_path: Path) -> None:
    """Keep original files and design operations outside collaborator grants."""
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
        human(app, "bob") as viewer,
    ):
        home = owner.get("/").headers["location"]
        workspace = home.split("/")[2]
        invite = owner.post(
            f"/api/workspaces/{workspace}/invitations",
            json={"kind": "workspace", "resource": "", "email": "bob@example.test", "role": "view"},
        )
        invite.raise_for_status()
        accept(viewer, cast(str, invite.json()["url"]).split("#invite=")[1])
        source = b"fictional" * 200_000
        encoded = base64.b64encode(source).decode()
        assert len(encoded) > 2 * 1024 * 1024
        body = {
            "name": "Private design sources",
            "files": [{"name": "notes.txt", "data_base64": encoded}],
        }
        route = home + "api/design-library"
        assert viewer.post(route, json=body).status_code == 404
        assert primary.post(route, json=body).status_code == 404
        invalid = owner.post(route, json={**body, "name": ""})
        assert invalid.status_code == 422 and len(invalid.content) < 250
        saved = owner.post(route, json=body)
        saved.raise_for_status()
        design = saved.json()
        identifier, file = design["id"], design["files"][0]
        assert "local_path" not in file
        assert viewer.get(route + "/" + identifier).status_code == 404
        url = home.rstrip("/") + file["url"]
        download = owner.get(url)
        assert download.content == source
        assert download.headers["content-disposition"].startswith("attachment")
        assert viewer.get(url).status_code == 404
        assert primary.get(route + "/" + identifier).status_code == 404
        assert viewer.get(home + "api/hub").json()["designs"] == []
        denied_link = viewer.get(home, params={"view": "designs", "design": identifier})
        assert denied_link.status_code == 307 and denied_link.headers["location"] == home
        assert primary.get("/api/design-library/" + identifier).status_code == 404

        connection = owner.post(
            f"/api/workspaces/{workspace}/agents",
            json={"kind": "workspace", "resource": "", "name": "Fictional design agent"},
        )
        connection.raise_for_status()
        guest_connection = viewer.post(
            f"/api/workspaces/{workspace}/agents",
            json={"kind": "workspace", "resource": "", "name": "Fictional viewer agent"},
        )
        guest_connection.raise_for_status()
        with httpx.Client(
            base_url=app.url,
            headers={
                "host": "studio.example.test",
                "authorization": "Bearer " + cast(str, connection.json()["token"]),
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

            context = invoke("get_design_context", {"design_id": identifier})
            assert not context.get("isError") and "local_path" not in str(context)
            assert invoke("get_design_context", {"design_id": "test-brand"}).get("isError")
            read = invoke(
                "read_design_reference",
                {"design_id": identifier, "path": file["path"], "offset": 9, "max_bytes": 9},
            )
            assert not read.get("isError"), read
            assert cast(dict[str, object], read["structuredContent"])["text"] == "fictional"
            created = invoke(
                "create_design_pack", {"design_id": "private-pack", "name": "Private pack"}
            )
            assert not created.get("isError") and "import_directory" not in str(created)
            result = cast(dict[str, object], created["structuredContent"])
            example = cast(str, result["folder_id"])
            mcp.headers["authorization"] = "Bearer " + cast(str, guest_connection.json()["token"])
            assert invoke("get_design_context", {"design_id": identifier}).get("isError")
            assert invoke("create_design_pack", {"design_id": "other", "name": "Other"}).get(
                "isError"
            )
            assert invoke("get_presentation_outline", {"deck_id": example}).get("isError")

        assert owner.post(route + "/private-pack/default", json={}).is_success
        assert owner.get(home + "api/preferences").json()["default_design_id"] == "private-pack"
        assert owner.get(home + f"decks/{example}/presenter?audience=1").is_success
        assert viewer.get(home + f"decks/{example}/presenter?audience=1").status_code == 404
        assert viewer.get(home + f"api/decks/{example}").status_code == 404
        assert owner.get(home + "api/decks").json() == []
        refs_url = home + "api/designs/private-pack/references"
        assert viewer.get(refs_url).status_code == 404
        attached = owner.post(refs_url, json={"files": body["files"]})
        attached.raise_for_status()
        reference = attached.json()[0]
        assert "local_path" not in reference
        assert owner.get(home.rstrip("/") + reference["url"]).content == source
        assert viewer.get(home.rstrip("/") + reference["url"]).status_code == 404

        edit_url = route + "/private-pack/files/edit"
        detail = owner.get(route + "/private-pack").json()
        current = next(row for row in detail["files"] if row["name"] == "PREFERENCES.md")
        edited = {"path": current["path"], "expected_hash": current["sha256"], "text": "# Revised"}
        assert owner.post(edit_url, json=edited).is_success
        assert owner.post(edit_url, json=edited).status_code == 409


@pytest.mark.browser
@pytest.mark.skipif(
    os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
)
def test_personal_owner_uses_regular_design_library(tmp_path: Path) -> None:
    """Open designs, set a default, preview context, and save a reference in the native UI."""
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
        workspace = home.split("/")[2]
        collection = root / "sharing/workspaces" / workspace / "collection"
        make_design(collection / "blue-pack", "blue-brand")
        make_design(collection / "green-pack", "green-brand")
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, "alice") as context:
                page = context.new_page()
                page.goto(WORKSPACE + home)
                page.locator('button[data-project="designs"]').click()
                page.get_by_role("button", name="Set green-brand as default").click()
                expect(
                    page.get_by_role("button", name="green-brand is the default design")
                ).to_be_visible()
                page.get_by_role("button", name="Open green-brand design").click()
                expect(page.get_by_role("heading", name="green-brand", exact=True)).to_be_visible()
                page.get_by_role("button", name="Open PREFERENCES.md", exact=True).click()
                preview = page.get_by_role("dialog", name="PREFERENCES.md")
                expect(
                    preview.get_by_text("Concise text; compose freely.", exact=True)
                ).to_be_visible()
                preview.get_by_role("button", name="Close dialog").click()
                page.get_by_role("button", name="Add files", exact=True).click()
                dialog = page.get_by_role("dialog", name="Add reference files")
                dialog.locator("input[type=file]").set_input_files(
                    {"name": "references.txt", "mimeType": "text/plain", "buffer": b"Fictional"}
                )
                dialog.get_by_role("button", name="Add files", exact=True).click()
                expect(
                    page.get_by_role("button", name="Open references.txt", exact=True)
                ).to_be_visible()
                expect(page.get_by_role("link", name="Open example slides")).to_have_attribute(
                    "href", home + "decks/green-pack/presenter?present=1&audience=1"
                )
                page.get_by_role("button", name="Menu", exact=True).click()
                page.get_by_role("menuitem", name="New presentation", exact=True).click()
                creation = page.get_by_role("dialog", name="New presentation")
                creation.get_by_role("button", name="Manage selected design references").click()
                references = page.get_by_role("dialog", name="Design references")
                file_link = references.get_by_role("link", name="references.txt")
                expect(file_link).to_be_visible()
                href = file_link.get_attribute("href")
                assert href and href.startswith(home + "api/design-library/green-brand/files/")
        finally:
            browser.close()
