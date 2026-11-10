"""Exercise regular card commands inside a fictional shared workspace."""

import os
import re
from pathlib import Path
from typing import cast

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.multiplayer_access import Sharing
from tests.integration.multiplayer_fixture import (
    WORKSPACE,
    routed_multiplayer_context,
    running_multiplayer_app,
)
from tests.test_multiplayer_server import accept, human
from tests.test_unified_hosting import seed_isolated_presentation

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def invite_artifact(
    owner: httpx.Client,
    recipient: httpx.Client,
    workspace: str,
    identifier: str,
    email: str,
    role: str,
) -> None:
    """Grant a fictional identity the selected workspace artifact role."""
    invitation = owner.post(
        f"/api/workspaces/{workspace}/invitations",
        json={"kind": "artifact", "resource": identifier, "email": email, "role": role},
    )
    invitation.raise_for_status()
    accept(recipient, cast(str, invitation.json()["url"]).split("#invite=")[1])


def test_shared_card_commands_keep_the_workspace_and_role(tmp_path: Path) -> None:
    """View, Comment, Present and Copy link use the native shell and existing grants."""
    root = tmp_path / "app"
    with (
        running_multiplayer_app(root, tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as commenter,
        human(app, "bob") as viewer,
        sync_playwright() as playwright,
    ):
        created = owner.post("/api/workspaces", json={"title": "Fictional Studio"})
        created.raise_for_status()
        workspace = cast(str, created.json()["id"])
        prefix = f"/workspaces/{workspace}"
        home = f"{WORKSPACE}{prefix}/"
        seed_isolated_presentation(root, app, workspace)
        hub = Sharing(root / "sharing", app.store, "owner@example.test")
        with hub.locked():
            service = ArtifactStore(hub.store(workspace))
            project = service.create_project("Fictional library")
            source = tmp_path / "shortcuts.md"
            _ = source.write_text("# Fictional handout")
            item = service.register_artifact(project.id, "Shared handout", source, source.name)
        invite_artifact(owner, commenter, workspace, item.id, "alice@example.test", "comment")
        invite_artifact(owner, commenter, workspace, app.deck_id, "alice@example.test", "view")
        invite_artifact(owner, viewer, workspace, item.id, "bob@example.test", "view")

        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, "bob") as context:
                context.grant_permissions(["clipboard-read", "clipboard-write"], origin=WORKSPACE)
                page = context.new_page()
                page.goto(home)
                card = page.get_by_role("button", name=re.compile("^Open Shared handout,"))
                card.click(button="right")
                expect(page.get_by_role("menuitem", name="Comment", exact=True)).to_have_count(0)
                expect(page.get_by_role("menuitem", name="Share link…")).to_have_count(0)
                page.get_by_role("menuitem", name="Copy link", exact=True).click()
                expect(page.get_by_text("Artifact link copied.")).to_be_visible()
                assert (
                    page.evaluate("navigator.clipboard.readText()") == f"{home}?artifact={item.id}"
                )
                card.click(button="right")
                page.get_by_role("menuitem", name="View", exact=True).click()
                expect(page.locator("body")).to_have_class(re.compile("workspace-clean-view"))
                expect(page.get_by_role("heading", name="Fictional handout")).to_be_visible()
                page.keyboard.press("Escape")
                expect(page.locator("body")).not_to_have_class(re.compile("workspace-clean-view"))

            with routed_multiplayer_context(browser, app, "alice") as context:
                page = context.new_page()
                page.add_init_script(
                    'localStorage.setItem("artifact-studio:workspace-panels:v1",'
                    "JSON.stringify({right: true}))"
                )
                page.goto(home)
                page.get_by_role("button", name=re.compile("^Open Shared handout,")).click(
                    button="right"
                )
                page.get_by_role("menuitem", name="Comment", exact=True).click()
                expect(page.locator("#hub-feedback-text")).to_be_focused()
                page.goto(home)
                page.get_by_role("button", name=re.compile("^Open Isolated presentation,")).click(
                    button="right"
                )
                page.get_by_role("menuitem", name="Present", exact=True).click()
                expect(page.locator("#stage .slide")).to_be_visible()
                expect(page).to_have_url(
                    f"{WORKSPACE}{prefix}/decks/{app.deck_id}/presenter?present=1#cover"
                )
                page.keyboard.press("Escape")
                expect(page).to_have_url(re.compile(re.escape(home) + r"\?deck="))
                scope = {"kind": "artifact", "resource": app.deck_id}
                access = f"/api/workspaces/{workspace}/access"
                grants = owner.get(access, params=scope).json()["grants"]
                grant_id = next(row["id"] for row in grants if row["email"] == "alice@example.test")
                owner.post(f"{access}/{grant_id}/revoke", json=scope).raise_for_status()
                page.goto(f"{home}?deck={app.deck_id}")
                expect(page).to_have_url(home)
                expect(
                    page.get_by_role("button", name=re.compile("^Open Shared handout,"))
                ).to_be_visible()
                expect(
                    page.get_by_role("button", name=re.compile("^Open Isolated presentation,"))
                ).to_have_count(0)
        finally:
            browser.close()
