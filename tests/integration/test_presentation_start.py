"""Keyboard navigation after starting a presentation from its visible controls."""

import os

import pytest
from playwright.sync_api import Page, Route, expect

from artifact_studio.models import Slide
from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_browser import open_editor

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


@pytest.mark.parametrize("host", ["standalone", "editor"])
def test_enter_after_present_advances_builds_and_skips_hidden(
    page: Page, live_app: RunningApp, host: str
) -> None:
    deck = live_app.store.read(DECK_ID)
    deck.slides[0].html = (
        '<article class="slide"><h1>Review together</h1><p data-build="1">Build one</p>'
        '<p data-build="2">Build two</p></article>'
    )
    deck.slides.insert(
        1,
        Slide(
            id="hidden",
            title="Hidden",
            hidden=True,
            html='<article class="slide"><h1>Hidden</h1></article>',
        ),
    )
    deck.slides[2].interactive_html = (
        '<button id="action" onclick="this.textContent=\'Activated\'">Activate</button>'
        '<input id="entry" aria-label="Entry">'
    )
    live_app.store.write(DECK_ID, deck)
    if host == "editor":
        open_editor(page, live_app)
        page.get_by_role("link", name="Present", exact=True).click()
        viewer = page.frame_locator('iframe[title="Presentation"]')
    else:
        page.goto(f"{live_app.url}/decks/{DECK_ID}/index.html#cover")
        page.get_by_role("button", name="Present", exact=True).click()
        viewer = page
    stage = viewer.locator("#stage")
    first = stage.locator('[data-build="1"]')
    second = stage.locator('[data-build="2"]')
    expect(first).to_have_class("unrevealed")
    page.keyboard.press("Enter")
    expect(first).not_to_have_class("unrevealed")
    expect(second).to_have_class("unrevealed")
    page.keyboard.press("Enter")
    expect(second).not_to_have_class("unrevealed")
    page.keyboard.press("Enter")
    expect(viewer.locator("#single-counter")).to_have_text("3 / 3")
    interactive = viewer.frame_locator("#stage iframe").frame_locator("iframe")
    interactive.locator("#action").focus()
    page.keyboard.press("Enter")
    expect(interactive.locator("#action")).to_have_text("Activated")
    expect(viewer.locator("#single-counter")).to_have_text("3 / 3")
    interactive.locator("#entry").focus()
    page.keyboard.press("Enter")
    expect(viewer.locator("#single-counter")).to_have_text("3 / 3")
    page.keyboard.press("Escape")
    expect(page.locator('iframe[title="Presentation"]')).to_have_count(0)
    expect(page.get_by_role("link", name="Present", exact=True)).to_be_visible()


def test_loading_can_be_cancelled_and_restarted(page: Page, live_app: RunningApp) -> None:
    """A stalled document shows a usable exit and a subsequent launch still works."""
    held: list[Route] = []
    page.route("**/decks/*/index.html?*", lambda route: held.append(route))
    open_editor(page, live_app)
    present = page.get_by_role("link", name="Present", exact=True)
    present.click()
    expect(page.get_by_text("Loading slides…", exact=True)).to_be_visible()
    cancel = page.get_by_role("button", name="Cancel", exact=True)
    expect(cancel).to_be_focused()
    cancel.click()
    expect(page.locator('iframe[title="Presentation"]')).to_have_count(0)
    expect(present).to_be_focused()
    assert held
    held[0].abort()
    page.unroute_all()
    present.click()
    viewer = page.frame_locator('iframe[title="Presentation"]')
    expect(viewer.locator("#stage h1")).to_have_text("Review together")
    page.keyboard.press("Enter")
    expect(viewer.locator("#stage h1")).to_have_text("Present with confidence")
    page.keyboard.press("Escape")
    expect(page.locator('iframe[title="Presentation"]')).to_have_count(0)
    expect(present).to_be_focused()
    expect(page.frame_locator("#preview").locator("h1")).to_have_text("Present with confidence")


def test_ready_presentation_accepts_input_before_image_load(
    page: Page, live_app: RunningApp
) -> None:
    """A slow image must not keep an already initialized slideshow behind the loading screen."""
    held: list[Route] = []
    page.route("**/artificial-slow.svg", lambda route: held.append(route))
    page.add_init_script("""if (location.pathname.includes('/decks/')) {
      document.addEventListener('DOMContentLoaded', () => {
        const image = document.createElement('img'); image.src = '/artificial-slow.svg';
        document.body.append(image);
      });
    }""")
    open_editor(page, live_app)
    page.get_by_role("link", name="Present", exact=True).click()
    viewer = page.frame_locator('iframe[title="Presentation"]')
    expect(viewer.locator("#stage h1")).to_have_text("Review together")
    assert held
    assert viewer.locator("body").evaluate("() => document.readyState") == "interactive"
    page.keyboard.press("Enter")
    expect(viewer.locator("#stage h1")).to_have_text("Present with confidence")
    held[0].fulfill(
        status=200, content_type="image/svg+xml", body='<svg xmlns="http://www.w3.org/2000/svg"/>'
    )
    page.keyboard.press("Escape")
    expect(page.locator('iframe[title="Presentation"]')).to_have_count(0)
