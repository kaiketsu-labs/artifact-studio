"""Optional runtime warmup yields to actual note editing and presentation launch."""

import os

import pytest
from playwright.sync_api import Page, Route, expect

from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_browser import open_editor

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_idle_runtime_fetch_yields_to_editing_and_present(page: Page, live_app: RunningApp) -> None:
    held: list[Route] = []

    def hold_warmup(route: Route) -> None:
        if route.request.resource_type == "fetch":
            held.append(route)
            page.evaluate("window.warmupHeld = true")
        else:
            route.continue_()

    page.add_init_script("""(() => {
      const fetch = window.fetch;
      window.warmupAborts = 0;
      window.fetch = function(url, options) {
        if (String(url).includes('/decks/') && String(url).endsWith('/presentation.js'))
          options?.signal?.addEventListener('abort', () => window.warmupAborts++);
        return fetch.apply(this, arguments);
      };
    })();""")
    page.route(f"**/decks/{DECK_ID}/presentation.js", hold_warmup)
    open_editor(page, live_app)
    page.locator("#slide-title").click()
    page.wait_for_function("window.warmupHeld === true", timeout=20_000)
    assert held
    expect(page.locator('iframe[title="Presentation"]')).to_have_count(0)
    page.locator("#notes-panel summary").click()
    page.locator("#notes").fill("Fictional note entered while runtime warmup was pending.")
    page.wait_for_function("window.warmupAborts === 1")
    held[0].abort()
    page.get_by_role("link", name="Present", exact=True).click()
    viewer = page.frame_locator('iframe[title="Presentation"]')
    expect(viewer.locator("#stage h1")).to_have_text("Review together")
    assert live_app.store.read(DECK_ID).slides[0].notes == (
        "Fictional note entered while runtime warmup was pending."
    )
    page.keyboard.press("Enter")
    expect(viewer.locator("#stage h1")).to_have_text("Present with confidence")
