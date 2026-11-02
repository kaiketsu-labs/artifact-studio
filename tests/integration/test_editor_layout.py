"""Browser regressions for responsive slide editing."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.integration.support import RunningApp
from tests.integration.test_browser import open_editor

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


@pytest.mark.parametrize("width", [375, 390, 850, 901, 1000, 1280])
def test_chat_remains_reachable_and_preserves_editor(
    live_app: RunningApp, page: Page, artifacts: Path, width: int
) -> None:
    """Keep the slide and chat usable without losing the iframe or a draft."""
    page.set_viewport_size({"width": width, "height": 844})
    open_editor(page, live_app)
    preview = page.locator("#preview")
    expect(preview).to_be_visible()
    expect(page.get_by_label("Speaker notes", exact=True)).to_be_hidden()
    navigation = page.locator(".view-navigation").bounding_box()
    tools = page.locator(".view-controls").bounding_box()
    assert navigation is not None and tools is not None
    assert navigation["x"] + navigation["width"] <= tools["x"]
    page.get_by_role("button", name="Next slide", exact=True).click()
    expect(page.frame_locator("#preview").get_by_role("heading", level=1)).to_have_text(
        "Present with confidence"
    )
    page.get_by_role("button", name="Previous slide", exact=True).click()
    expect(page.frame_locator("#preview").get_by_role("heading", level=1)).to_have_text(
        "Review together"
    )
    preview.evaluate("element => { window.__layoutTestFrame = element; }")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(artifacts / f"editor-{width}.png"))

    open_chat = page.get_by_role("button", name="Open chat", exact=True)
    close_chat = page.get_by_role("button", name="Close chat", exact=True)
    if close_chat.is_visible():
        close_chat.click()
    expect(open_chat).to_be_visible()
    open_chat.click()
    composer = page.locator("#feedback-text")
    expect(composer).to_be_visible()
    box = composer.bounding_box()
    assert box is not None
    assert box["y"] >= 0 and box["y"] + box["height"] <= 844
    composer.fill("A draft that must survive panel changes")
    page.screenshot(path=str(artifacts / f"chat-{width}.png"))
    close_chat.focus()
    page.keyboard.press("Enter")
    expect(open_chat).to_be_visible()
    open_chat.focus()
    page.keyboard.press("Enter")
    expect(composer).to_have_value("A draft that must survive panel changes")
    assert preview.evaluate("element => element === window.__layoutTestFrame")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    if width <= 1050:
        page.keyboard.press("Escape")
        expect(open_chat).to_be_visible()
        page.set_viewport_size({"width": 1280, "height": 844})
        expect(close_chat).to_be_visible()
        expect(composer).to_have_value("A draft that must survive panel changes")
        assert preview.evaluate("element => element === window.__layoutTestFrame")


@pytest.mark.parametrize("width", [390, 850, 1000, 1280])
def test_element_toolbar_does_not_move_slide(
    live_app: RunningApp, page: Page, width: int
) -> None:
    """Opening element controls does not shift the slide being edited."""
    page.set_viewport_size({"width": width, "height": 844})
    open_editor(page, live_app)
    preview = page.locator("#preview")
    before = preview.bounding_box()
    page.frame_locator("#preview").get_by_role("heading", level=1).click()
    properties = page.locator("#element-properties")
    expect(properties).to_be_visible()
    assert preview.bounding_box() == before
    properties.get_by_role("button", name="Edit text", exact=True).click()
    assert preview.bounding_box() == before
