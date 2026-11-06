"""New-presentation background work follows the visibility and lifetime of its dialog."""

import os

import pytest
from playwright.sync_api import Browser, Route, expect

from tests.integration.support import DECK_ID, RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_hidden_design_dialog_defers_polling_and_close_releases_preview(
    live_app: RunningApp,
    browser: Browser,
) -> None:
    """Preserve draft/reopen behavior without hidden polling, overlapping work or late frames."""
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    page.clock.install()
    requests: list[str] = []
    pending: list[Route] = []
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append(request.url))
    try:
        page.goto(f"{live_app.url}/?deck={DECK_ID}")
        page.get_by_role("button", name="Menu", exact=True).click()
        page.get_by_role("menuitem", name="New presentation", exact=True).click()
        dialog = page.locator("#new-dialog")
        expect(dialog).to_be_visible()
        expect(dialog.locator("iframe")).to_have_count(1)
        page.locator("#new-title").fill("Preserved fictional draft")
        page.evaluate("""() => {
            Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
            document.dispatchEvent(new Event('visibilitychange'));
        }""")
        page.wait_for_timeout(150)
        before = len(requests)
        page.clock.fast_forward(10_000)
        page.wait_for_timeout(150)
        assert not any(
            endpoint in url
            for url in requests[before:]
            for endpoint in ("/api/design-requests", "/api/designs", "/api/preferences")
        )

        page.route("**/api/design-requests", lambda route: pending.append(route))
        page.evaluate("""() => {
            Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
            document.dispatchEvent(new Event('visibilitychange'));
        }""")
        for _ in range(50):
            if pending:
                break
            page.wait_for_timeout(20)
        assert len(pending) == 1
        page.clock.fast_forward(10_000)
        page.wait_for_timeout(150)
        assert len(pending) == 1, "A slow refresh must not accumulate overlapping polls"
        page.locator("#close-new").click()
        expect(dialog).to_be_hidden()
        expect(dialog.locator("iframe")).to_have_count(0)
        pending[0].fulfill(response=pending[0].fetch())
        page.wait_for_timeout(150)
        expect(dialog.locator("iframe")).to_have_count(0)
        page.unroute("**/api/design-requests")
        page.get_by_role("button", name="Menu", exact=True).click()
        page.get_by_role("menuitem", name="New presentation", exact=True).click()
        expect(dialog).to_be_visible()
        expect(dialog.locator("iframe")).to_have_count(1)
        expect(page.locator("#new-title")).to_have_value("Preserved fictional draft")
        assert not errors, errors
    finally:
        context.close()
