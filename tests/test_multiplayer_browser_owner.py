"""Owner journeys against the fictional multiplayer server and real browser."""

import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from tests.integration.multiplayer_fixture import (
    routed_multiplayer_context,
    running_multiplayer_app,
)
from tests.test_multiplayer_simulation import Peer

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def invite(page: Page, email: str) -> str:
    page.get_by_label("Invite by email").fill(email)
    page.get_by_role("button", name="Create invite link").click()
    expect(page.locator("#invite-link")).to_be_visible()
    link = page.locator("#invite-link").input_value()
    page.get_by_role("button", name="Close access").click()
    return link


def test_owner_sharing_invites_and_inherited_access(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log") as app,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app) as owner:
                page = owner.new_page()
                page.goto("https://studio.example.test/")
                page.locator(f'[data-artifact="{app.artifact_id}"]').click()
                expect(page.locator("#artifact-title")).to_have_text("Shared candidate")
                page.locator("#artifact-access").click()
                artifact_link = invite(page, "alice@example.test")
                with routed_multiplayer_context(browser, app, "bob") as bob:
                    wrong = bob.new_page()
                    wrong.goto(artifact_link)
                    wrong.get_by_role("button", name="Join").click()
                    expect(wrong.locator("#notice")).to_contain_text("unavailable for this account")
                with routed_multiplayer_context(browser, app, "alice") as alice:
                    guest = alice.new_page()
                    guest.goto(artifact_link)
                    guest.get_by_role("button", name="Join").click()
                    expect(guest.locator("#artifact-title")).to_have_text("Shared candidate")
                    expect(guest.locator("#catalog")).not_to_contain_text("Private sibling")
                page.reload()
                expect(page.locator("#catalog")).to_contain_text("Contains shared items")
                page.get_by_role("button", name="Share project Fictional project").click()
                project_link = invite(page, "bob@example.test")
                with routed_multiplayer_context(browser, app, "bob") as bob:
                    guest = bob.new_page()
                    guest.goto(project_link)
                    guest.get_by_role("button", name="Join").click()
                    expect(guest.locator("#catalog")).to_contain_text("Private sibling")
                page.reload()
                expect(page.locator("#artifact-title")).to_have_text("Shared candidate")
                page.locator("#artifact-access").click()
                project_grant = page.locator(".access-row").filter(
                    has_text="Inherited from project"
                )
                expect(project_grant).to_contain_text("bob@example.test")
                project_grant.get_by_role("button", name="Manage parent").click()
                expect(page.locator("#access-title")).to_have_text("Project access")
                page.get_by_role("button", name="Close access").click()
                page.get_by_role("button", name="Manage access").click()
                workspace_link = invite(page, "alice@example.test")
                with routed_multiplayer_context(browser, app, "alice") as alice:
                    guest = alice.new_page()
                    guest.goto(workspace_link)
                    guest.get_by_role("button", name="Join").click()
                    expect(guest.locator("#catalog")).to_contain_text("Private sibling")
                page.reload()
                expect(page.locator("#workspace-sharing")).to_have_text("Shared")
                expect(page.locator("#artifact-title")).to_have_text("Shared candidate")
                page.locator("#artifact-access").click()
                workspace_grant = page.locator(".access-row").filter(
                    has_text="Inherited from workspace"
                )
                expect(workspace_grant).to_contain_text("alice@example.test")
                workspace_grant.get_by_role("button", name="Manage parent").click()
                expect(page.locator("#access-title")).to_have_text("Workspace access")
        finally:
            browser.close()


def test_owner_workspace_isolation_and_agent_connection(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log") as app,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app) as owner:
                page = owner.new_page()
                page.goto("https://studio.example.test/")
                page.locator(f'[data-artifact="{app.artifact_id}"]').click()
                expect(page.locator("#artifact-title")).to_have_text("Shared candidate")
                original = page.locator("#workspace").input_value()
                for title in ("Fictional workspace A", "Fictional workspace B"):
                    page.get_by_role("button", name="New workspace").click()
                    page.get_by_label("Name", exact=True).fill(title)
                    page.get_by_role("button", name="Create", exact=True).click()
                    expect(page.locator("#workspace option:checked")).to_have_text(title)
                    expect(page.locator("#catalog")).not_to_contain_text("Shared candidate")
                page.locator("#workspace").select_option(original)
                expect(page.locator("#artifact-title")).to_have_text("Shared candidate")
                page.get_by_role("button", name="Connect agent", exact=True).click()
                page.get_by_label("Agent name").fill("Review agent")
                page.get_by_role("button", name="Create connection").click()
                expect(page.locator("#agent-result")).to_be_visible()
                assert page.locator("#agent-token").input_value()
                page.get_by_role("button", name="Close agent setup").click()
                page.get_by_role("button", name="Connect agent", exact=True).click()
                expect(page.locator("#my-agents")).to_contain_text("Review agent")
                page.get_by_role("button", name="Disconnect").click()
                expect(page.locator("#my-agents")).not_to_contain_text("Review agent")
        finally:
            browser.close()


def test_empty_workspace_agent_creates_artifact_and_can_disconnect(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log") as app,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app) as owner:
                page = owner.new_page()
                page.goto("https://studio.example.test/")
                expect(page.locator("#workspace option")).to_have_count(1)
                original = page.locator("#workspace").input_value()
                page.get_by_role("button", name="New workspace").click()
                page.get_by_label("Name", exact=True).fill("Agent workspace")
                page.get_by_role("button", name="Create", exact=True).click()
                expect(page.locator("#workspace option:checked")).to_have_text("Agent workspace")
                workspace = page.locator("#workspace").input_value()
                page.get_by_role("button", name="New project").click()
                page.get_by_label("Name", exact=True).fill("Agent project")
                page.get_by_role("button", name="Create", exact=True).click()
                expect(page.locator("#catalog")).to_contain_text("Agent project")
                page.get_by_role("button", name="Connect an agent").click()
                project_option = page.locator("#agent-scope option").filter(
                    has_text="Project · Agent project"
                )
                project_id = json.loads(project_option.get_attribute("value") or "{}")["resource"]
                page.locator("#agent-scope").select_option(label="Entire workspace")
                page.get_by_label("Agent name").fill("Workspace agent")
                page.get_by_role("button", name="Create connection").click()
                expect(page.locator("#agent-result")).to_be_visible()
                token = page.locator("#agent-token").input_value()
                page.get_by_role("button", name="Close agent setup").click()
                page.locator("#workspace").select_option(original)
                expect(page.locator("#catalog")).to_contain_text("Fictional project")
                with page.expect_response(f"**/api/workspaces/{original}/agents"):
                    page.get_by_role("button", name="Connect agent", exact=True).click()
                expect(page.locator("#my-agents")).not_to_contain_text("Workspace agent")
                page.get_by_role("button", name="Close agent setup").click()
                page.locator("#workspace").select_option(workspace)
                expect(page.locator("#catalog")).to_contain_text("Agent project")
                page.get_by_role("button", name="Connect an agent").click()
                expect(page.locator("#my-agents")).to_contain_text("Workspace agent")
                page.get_by_role("button", name="Close agent setup").click()
                peer = Peer(app, token)
                try:
                    peer.value(
                        "create_artifact",
                        project_id=project_id,
                        title="Created by agent",
                        entrypoint="index.html",
                        files_json=json.dumps(
                            [{"path": "index.html", "text": "<h1>Fictional</h1>"}]
                        ),
                        request_id="fictional-create",
                    )
                    expect(page.locator("#artifact-title")).to_have_text(
                        "Created by agent", timeout=15000
                    )
                    page.get_by_role("button", name="Connect agent", exact=True).click()
                    expect(page.locator("#my-agents")).to_contain_text("Workspace agent")
                    page.get_by_role("button", name="Disconnect").click()
                    expect(page.locator("#my-agents")).not_to_contain_text("Workspace agent")
                    response = peer.client.post(
                        "/mcp",
                        json={
                            "jsonrpc": "2.0",
                            "id": 99,
                            "method": "tools/call",
                            "params": {"name": "get_working_context", "arguments": {}},
                        },
                    )
                    assert response.status_code == 401
                finally:
                    peer.client.close()
        finally:
            browser.close()
