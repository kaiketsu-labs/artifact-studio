"""Keyboard and small-screen layout for provisional text edits."""

import os
import re

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


def open_text(page: Page, live_app: RunningApp) -> None:
    open_editor(page, live_app)
    page.frame_locator("#preview").get_by_role("heading", level=1).click()
    page.get_by_role("button", name="Edit text", exact=True).click()


def test_text_actions_follow_editor_and_restore_focus(page: Page, live_app: RunningApp) -> None:
    open_text(page, live_app)
    text = page.get_by_role("textbox", name="Text", exact=True)
    text.fill("A revised heading")
    text.evaluate("el => el.setSelectionRange(3, 7)")
    page.locator("#element-properties").evaluate("el => window.dispatchEvent(new Event('resize'))")
    expect(text).to_be_focused()
    assert text.evaluate("el => [el.selectionStart, el.selectionEnd]") == [3, 7]
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Send edit", exact=True)).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Cancel edit", exact=True)).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.get_by_role("button", name="Edit text", exact=True)).to_be_focused()
    expect(text).to_have_value("Review together")
    text.fill("Another heading")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    expect(page.get_by_role("button", name="Edit text", exact=True)).to_be_focused()
    expect(page.get_by_role("button", name="Send edit", exact=True)).to_be_hidden()


def test_mobile_text_controls_fit_below_slide(page: Page, live_app: RunningApp) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    open_text(page, live_app)
    text = page.get_by_role("textbox", name="Text", exact=True)
    text.fill("A revised heading")
    slide = page.locator("#preview").bounding_box()
    controls = page.locator("#element-properties").bounding_box()
    footer = page.locator(".view-navigation").bounding_box()
    assert slide is not None and controls is not None and footer is not None
    assert controls["y"] >= slide["y"] + slide["height"]
    assert controls["y"] + controls["height"] <= footer["y"]
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Send edit", exact=True)).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Cancel edit", exact=True)).to_be_focused()


def test_mobile_visible_viewport_repositions_without_losing_caret(
    page: Page, live_app: RunningApp
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    open_text(page, live_app)
    text = page.get_by_role("textbox", name="Text", exact=True)
    text.fill("A revised heading")
    text.evaluate("el => el.setSelectionRange(2, 5)")
    page.evaluate("""() => {
        Object.defineProperty(window.visualViewport, 'height', {value: 540, configurable: true});
        window.visualViewport.dispatchEvent(new Event('resize'));
    }""")
    expect(page.locator("#element-properties")).to_have_class(
        re.compile("ep-mobile-text")
    )
    page.wait_for_function(
        "document.querySelector('#element-properties').getBoundingClientRect().bottom <= 492"
    )
    expect(text).to_be_focused()
    assert text.evaluate("el => [el.selectionStart, el.selectionEnd]") == [2, 5]
