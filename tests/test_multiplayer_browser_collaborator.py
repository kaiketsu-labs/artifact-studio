"""Opt-in real-browser journeys for collaborator drafts and chat privacy."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from tests.integration.multiplayer_fixture import (
    WORKSPACE,
    routed_multiplayer_context,
    running_multiplayer_app,
)

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def connect_agent(page: Page, name: str) -> None:
    page.locator("#connect-agent").click()
    page.locator("#agent-name").fill(name)
    page.get_by_role("button", name="Create connection").click()
    expect(page.locator("#agent-result")).to_be_visible()
    page.get_by_label("Close agent setup").click()


def test_collaborator_slide_drafts_and_explicit_agent_selection(tmp_path: Path) -> None:
    """Slide drafts retain their anchors and each channel has its own recipient."""
    with (
        running_multiplayer_app(tmp_path / "server", tmp_path / "server.log") as app,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(headless=True)
        try:
            with (
                routed_multiplayer_context(browser, app, "owner") as owner_context,
                routed_multiplayer_context(browser, app, "alice") as alice_context,
            ):
                owner = owner_context.new_page()
                alice = alice_context.new_page()
                owner.goto(WORKSPACE)
                owner.get_by_role("button", name="Fictional team deck").click()
                expect(owner.locator("#slides button")).to_have_count(2)
                owner.locator("#artifact-access").click()
                owner.locator("#invite-email").fill("alice@example.test")
                owner.get_by_role("button", name="Create invite link").click()
                expect(owner.locator("#invite-result")).to_be_visible()
                invitation = owner.locator("#invite-link").input_value()
                owner.get_by_label("Close access").click()
                alice.goto(invitation)
                alice.locator("#join").click()
                expect(alice.locator("#artifact-title")).to_have_text("Fictional team deck")
                connect_agent(alice, "First bot")
                connect_agent(alice, "Second bot")
                alice.locator("#private-tab").click()
                expect(alice.locator("#recipient")).to_have_value("")
                alice.locator("#message").fill("Private question")
                alice.locator("#send").click()
                expect(alice.locator("#notice")).to_have_text("Connect or choose your agent first.")
                expect(alice.locator("#message")).to_have_value("Private question")
                alice.locator("#recipient").select_option(label="Second bot (offline)")
                alice.locator("#send").click()
                expect(alice.locator("#messages")).to_contain_text("Private question")
                owner.locator("#private-tab").click()
                expect(owner.locator("#messages")).not_to_contain_text("Private question")
                alice.locator("#shared-tab").click()
                expect(alice.locator("#recipient")).to_have_value("")
                alice.locator("#message").fill("First slide draft")
                alice.locator("#slides button").nth(1).click()
                expect(alice.locator("#message")).to_have_value("")
                alice.locator("#message").fill("Second slide draft")
                alice.locator("#slides button").first.click()
                expect(alice.locator("#message")).to_have_value("First slide draft")
                expect(alice.locator("#target-label")).to_have_text("Fictional team deck")
                with alice.expect_request(
                    lambda request: request.method == "POST" and request.url.endswith("/chat")
                ) as sent:
                    alice.locator("#send").click()
                payload = sent.value.post_data_json
                assert payload is not None
                assert payload["assigned"] == ""
                assert payload["target"]["label"] == "Fictional team deck"
                expect(alice.locator("#messages")).to_contain_text("First slide draft")
                alice.locator("#slides button").nth(1).click()
                expect(alice.locator("#message")).to_have_value("Second slide draft")
                alice.locator("#private-tab").click()
                expect(alice.locator("#recipient").locator("option:checked")).to_have_text(
                    "Second bot (offline)"
                )
        finally:
            browser.close()
