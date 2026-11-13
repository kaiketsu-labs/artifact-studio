"""Browser checks for hosted hook downloads and expired sessions."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Browser, ViewportSize, expect

from tests.integration.hosted_fixture import WORKSPACE, routed_hosted_context, running_hosted_app

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


@pytest.mark.parametrize(
    "viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}]
)
def test_hook_download_disappears_after_expiry_and_stays_hidden_on_reopen(
    browser: Browser, tmp_path: Path, artifacts: Path, viewport: ViewportSize
) -> None:
    with (
        running_hosted_app(tmp_path / "hosted", artifacts / "server.log") as app,
        routed_hosted_context(browser, app) as context,
    ):
        page = context.new_page()
        page.set_viewport_size(viewport)
        page.goto(WORKSPACE)
        expect(page.locator("[data-server-version]")).to_be_visible()
        page.locator("#hub-tools-button").click()
        page.locator("[data-hub-connect]").click()
        dialog = page.get_by_role("dialog", name="Agent connection")
        link = dialog.get_by_role("link", name="Download agent hooks")
        expect(link).to_be_visible()
        expect(link).to_have_attribute("href", "/api/agent/hooks-client")
        expect(dialog.locator("#hub-mcp-url")).to_have_value(WORKSPACE + "/mcp")

        page.evaluate("window.ArtifactSession.observe(new Response('', {status: 401}))")
        expect(link).to_be_hidden()
        expect(dialog.locator("#hub-mcp-url")).to_have_value("")
        expect(dialog.locator("[data-copy-mcp]")).to_be_disabled()
        page.keyboard.press("Escape")
        page.locator("#hub-tools-button").click()
        page.locator("[data-hub-connect]").click()
        expect(link).to_be_hidden()
        expect(dialog.locator("#hub-mcp-url")).to_have_value("")
        expect(dialog.locator("[data-copy-mcp]")).to_be_disabled()
        expect(dialog.locator("[data-hub-connection-status]")).to_contain_text("expired")


def test_delayed_session_response_cannot_restore_expired_hook_download(
    browser: Browser, tmp_path: Path, artifacts: Path
) -> None:
    with (
        running_hosted_app(tmp_path / "hosted", artifacts / "server.log") as app,
        routed_hosted_context(browser, app) as context,
    ):
        page = context.new_page()
        page.add_init_script("""const originalFetch = window.fetch;
            window.fetch = async function(...args) {
                const response = await originalFetch.apply(this, args);
                if (String(args[0]).includes('/api/session')) {
                    await new Promise(resolve => { window.releaseSession = resolve; });
                }
                return response;
            };""")
        page.goto(WORKSPACE)
        page.wait_for_function("window.releaseSession !== undefined")
        page.evaluate("window.ArtifactSession.observe(new Response('', {status: 401}))")
        page.evaluate("window.releaseSession()")
        page.wait_for_timeout(300)
        page.locator("#hub-tools-button").click()
        page.locator("[data-hub-connect]").click()
        dialog = page.get_by_role("dialog", name="Agent connection")
        expect(dialog.locator("[data-hub-connection-status]")).to_contain_text("expired")
        expect(dialog.locator("#hub-mcp-url")).to_have_value("")
        expect(dialog.locator("[data-copy-mcp]")).to_be_disabled()
        expect(dialog.locator("[data-hosted-hooks-link]")).to_have_count(0)
