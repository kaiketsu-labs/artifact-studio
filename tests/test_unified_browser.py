"""Check the owner can collaborate from the full editor and return to the same deck."""

import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.integration.multiplayer_fixture import (
    WORKSPACE,
    routed_multiplayer_context,
    running_multiplayer_app,
)
from tests.test_multiplayer_server import accept, grant, human

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_full_editor_round_trip_and_scoped_agent_entry(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app) as owner:
                page = owner.new_page()
                page.goto(WORKSPACE)
                expect(page.get_by_role("heading", name="All artifacts")).to_be_visible()
                page.get_by_role("button", name=re.compile("Open Fictional team deck")).click()
                expect(page.locator("#slide-title")).to_be_visible()
                page.locator("#slides button").filter(has_text="Next").first.click()
                page.get_by_role("button", name="Collaborate").click()
                expect(page.locator("#slide-title")).to_have_text("Next")
                expect(page.locator("#shared-channel")).to_have_value("shared")
                expect(page).to_have_url(re.compile(r"[?&]chat=shared#next"))
                page.get_by_role("button", name="Local review").click()
                expect(page).to_have_url(re.compile(r"[?&]deck=" + app.deck_id + "#next"))
                expect(page.locator("#slide-title")).to_have_text("Next")
                page.get_by_role("button", name="Menu", exact=True).click()
                page.get_by_role("menuitem", name="Agent connection").click()
                expect(page.get_by_role("dialog", name="Agent connection")).to_be_visible()
        finally:
            browser.close()


def test_invited_guest_opens_normal_editor_with_only_shared_items(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        sync_playwright() as playwright,
    ):
        invite = grant(owner, "alice", app.deck_id, "view")
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, "alice") as guest:
                page = guest.new_page()
                page.goto(f"{WORKSPACE}/#invite={invite}")
                page.get_by_role("button", name="Join", exact=True).click()
                expect(page.locator("#slide-title")).to_be_visible()
                preview = page.frame_locator("#preview")
                expect(preview.locator(".slide")).to_be_visible()
                expect(page.get_by_text("Can view", exact=True)).to_be_visible()
                expect(page.locator("#notes")).to_have_js_property("readOnly", True)
                page.locator("#slides button").filter(has_text="Next").first.click()
                expect(page.locator("#slide-title")).to_have_text("Next")
                expect(preview.get_by_text("Next", exact=True)).to_be_visible()

                expect(page.locator("#shared-channel")).to_have_value("shared")
                expect(page.locator("#feedback-text")).to_be_disabled()
                with human(app, "alice") as guest_api:
                    accept(guest_api, grant(owner, "alice", app.deck_id, "edit"))
                expect(page.locator("#feedback-text")).to_be_enabled(timeout=12000)
                expect(page.get_by_text("Can edit", exact=True)).to_be_visible()
                page.get_by_role("button", name="Menu", exact=True).click()
                page.get_by_role("menuitem", name="Agent connection").click()
                connection = page.get_by_role("dialog", name="Agent connection")
                connection.get_by_label("Agent name").fill("Fictional teammate agent")
                connection.get_by_role("button", name="Connect agent").click()
                expect(connection.get_by_role("button", name="Copy token")).to_be_visible()
                connection.get_by_role("button", name="Close agent connection").click()
                expect(page.locator("#slide-title")).to_have_text("Next")
                page.locator("#feedback-text").fill("Fictional teammate comment")
                page.locator("#feedback-text").press("Enter")
                expect(page.locator("#feedback-list")).to_contain_text("Fictional teammate comment")
                expect(page.locator("#feedback-list").get_by_text("Earlier version")).to_have_count(
                    0
                )
                with routed_multiplayer_context(browser, app) as author:
                    same_deck = author.new_page()
                    same_deck.goto(f"{WORKSPACE}/?deck={app.deck_id}#next")
                    expect(same_deck.locator("#feedback-list")).to_contain_text(
                        "Fictional teammate comment"
                    )
                    expect(same_deck.locator("#feedback-list")).to_contain_text(
                        "alice@example.test"
                    )

                page.goto(WORKSPACE)
                expect(page.get_by_role("heading", name="All artifacts")).to_be_visible()
                expect(
                    page.get_by_role("button", name=re.compile("Open Fictional team deck"))
                ).to_be_visible()
                expect(
                    page.get_by_role("button", name=re.compile("Open Private sibling"))
                ).to_have_count(0)
                expect(page.get_by_role("button", name="Import", exact=True)).to_be_hidden()
        finally:
            browser.close()


def test_owner_creates_link_from_native_context_menu(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "alice") as invitee,
        human(app, "bob") as other,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app) as owner:
                page = owner.new_page()
                page.goto(WORKSPACE)
                page.wait_for_function("window.StudioInvitation?.available === true")
                scopes = (
                    (
                        page.get_by_role("button", name=re.compile("Open Shared candidate")),
                        "Shared candidate",
                    ),
                    (
                        page.get_by_role("button", name=re.compile("Open Fictional team deck")),
                        "Fictional team deck",
                    ),
                    (
                        page.locator(f'[data-tree-node="{app.project_id}"] .project-tree-open'),
                        "Fictional project",
                    ),
                )
                for anchor, title in scopes:
                    anchor.click(button="right")
                    page.get_by_role("menuitem", name="Share link…").click()
                    dialog = page.get_by_role("dialog", name="Share link")
                    expect(dialog.get_by_text(title, exact=True)).to_be_visible()
                    expect(dialog.get_by_label("Access")).to_have_value("view")
                    if title == "Shared candidate":
                        dialog.get_by_label("Email address").fill("alice@example.test")
                        dialog.get_by_role("button", name="Create link").click()
                        field = dialog.get_by_label("Invitation link", exact=True)
                        expect(field).to_have_value(
                            re.compile(r"https://studio\.example\.test/#invite=")
                        )
                        expect(dialog.get_by_role("button", name="Copy link")).to_be_focused()
                        token = field.input_value().split("#invite=")[1]
                        wrong = other.post("/api/invitations/accept", json={"token": token})
                        assert wrong.status_code in {403, 404}
                        accept(invitee, token)
                        assert invitee.get(f"/api/artifacts/{app.artifact_id}").is_success
                        assert invitee.get(f"/api/artifacts/{app.sibling_id}").status_code == 404
                    page.keyboard.press("Escape")
                    expect(anchor).to_be_focused()
        finally:
            browser.close()
