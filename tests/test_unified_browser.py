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
                expect(page.locator("#artifact-title")).to_have_text("Fictional team deck")
                expect(page.get_by_role("tab", name="Shared")).to_be_visible()
                expect(page.get_by_role("button", name="2 Next")).to_have_attribute(
                    "aria-current", "true"
                )
                page.get_by_role("link", name="Back to Studio").click()
                expect(page).to_have_url(re.compile(r"[?&]deck=" + app.deck_id + "#next"))
                expect(page.locator("#slide-title")).to_have_text("Next")
                page.get_by_role("button", name="Menu", exact=True).click()
                page.get_by_role("menuitem", name="Agent connection").click()
                expect(page.get_by_role("dialog", name="Connect your agent")).to_be_visible()
        finally:
            browser.close()
