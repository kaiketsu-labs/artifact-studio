"""Slide menus stay contextual, usable on small screens and separate from native typing."""

import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.integration.support import DECK_ID, PRIVATE_NOTE, RunningApp
from tests.integration.test_browser import open_editor, open_notes

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def open_background_menu(page: Page, background: Locator) -> Locator:
    """Right-click the actual empty corner of a canvas or slide rail."""
    bounds = background.bounding_box()
    assert bounds is not None
    page.mouse.click(
        bounds["x"] + bounds["width"] - 12,
        bounds["y"] + bounds["height"] - 12,
        button="right",
    )
    menu = page.get_by_role("menu", name="Slide workspace", exact=True)
    expect(menu).to_be_visible()
    return menu


def test_blank_slide_corner_opens_its_workspace_history(page: Page, live_app: RunningApp) -> None:
    """The slide article's empty area opens workspace actions instead of an object editor."""
    open_editor(page, live_app)
    slide = page.frame_locator("#preview").locator(".slide")
    menu = open_background_menu(page, slide)
    expect(page.get_by_role("menu", name="Object actions", exact=True)).not_to_be_visible()
    menu.get_by_role("menuitem", name="Version history", exact=True).click()
    history = page.get_by_role("dialog", name="Version history", exact=True)
    expect(history).to_be_visible()
    expect(history.locator(".version-subtitle")).to_have_text("CI workflow")
    expect(history.locator("iframe")).to_have_attribute(
        "src", re.compile(re.escape(f"/api/decks/{DECK_ID}/slides/cover/history/preview"))
    )
    page.get_by_role("button", name="Close version history", exact=True).click()
    assert live_app.store.comments(DECK_ID) == []
    assert live_app.store.read(DECK_ID).slides[0].notes == PRIVATE_NOTE


def test_blank_rail_menu_selects_all_and_creates_a_slide(page: Page, live_app: RunningApp) -> None:
    """Empty rail space exposes working selection and insertion commands."""
    open_editor(page, live_app)
    rail = page.locator("#slides")
    menu = open_background_menu(page, rail)
    menu.get_by_role("menuitem", name="Select all slides", exact=True).click()
    expect(rail.locator('[data-id][aria-pressed="true"]')).to_have_count(2)
    menu = open_background_menu(page, rail)
    menu.get_by_role("menuitem", name="New slide", exact=True).click()
    expect(rail.locator("[data-id]")).to_have_count(3)
    deck = live_app.store.read(DECK_ID)
    assert len(deck.slides) == 3
    assert deck.slides[0].notes == PRIVATE_NOTE
    assert live_app.store.comments(DECK_ID) == []


def test_thumbnail_comment_history_and_selection_target_the_clicked_slide(
    page: Page, live_app: RunningApp
) -> None:
    """Thumbnail commands use the right-clicked slide while preserving its unsent draft."""
    open_editor(page, live_app)
    thumbnail = page.locator('#slides [data-id="next"]')
    thumbnail.click(button="right")
    menu = page.get_by_role("menu", name="Slide options", exact=True)
    expect(menu).to_be_visible()
    menu.get_by_role("menuitem", name="New comment", exact=True).click()
    feedback = page.get_by_role("textbox", name="Your feedback", exact=True)
    expect(feedback).to_be_focused()
    feedback.fill("Private draft about the second slide")
    thumbnail.click(button="right")
    menu.get_by_role("menuitem", name="Version history", exact=True).click()
    history = page.get_by_role("dialog", name="Version history", exact=True)
    expect(history.locator(".version-subtitle")).to_have_text("Next steps")
    expect(history.locator("iframe")).to_have_attribute(
        "src", re.compile(re.escape(f"/api/decks/{DECK_ID}/slides/next/history/preview"))
    )
    page.get_by_role("button", name="Close version history", exact=True).click()
    expect(feedback).to_have_value("Private draft about the second slide")
    thumbnail.click(button="right")
    menu.get_by_role("menuitem", name="Select all slides", exact=True).click()
    expect(page.locator('#slides [data-id][aria-pressed="true"]')).to_have_count(2)
    assert live_app.store.comments(DECK_ID) == []


@pytest.mark.parametrize("width,height", [(800, 600), (1280, 800)])
def test_thumbnail_menu_fits_the_viewport_and_reaches_its_last_action(
    page: Page, live_app: RunningApp, artifacts: Path, width: int, height: int
) -> None:
    """Long menus stay inside the viewport and scroll to keyboard-focused commands."""
    page.set_viewport_size({"width": width, "height": height})
    open_editor(page, live_app)
    page.locator('#slides [data-id="cover"]').click(button="right")
    menu = page.get_by_role("menu", name="Slide options", exact=True)
    expect(menu).to_be_visible()
    bounds = menu.bounding_box()
    assert bounds is not None
    assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
    assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= height
    page.keyboard.press("End")
    last = menu.get_by_role("menuitem", name="Move to end", exact=True)
    expect(last).to_be_focused()
    last_bounds = last.bounding_box()
    assert last_bounds is not None
    assert bounds["y"] <= last_bounds["y"]
    assert last_bounds["y"] + last_bounds["height"] <= bounds["y"] + bounds["height"]
    _ = page.screenshot(path=str(artifacts / "slide-menu-in-viewport.png"))
    page.keyboard.press("Escape")
    expect(page.locator('#slides [data-id="cover"]')).to_be_focused()


def test_user_text_fields_keep_native_context_menus(page: Page, live_app: RunningApp) -> None:
    """Feedback and speaker notes keep native menus and do not open workspace actions."""
    open_editor(page, live_app)
    open_notes(page)
    for field in [
        page.get_by_role("textbox", name="Your feedback", exact=True),
        page.get_by_role("textbox", name="Speaker notes", exact=True),
    ]:
        field.evaluate(
            "node => node.addEventListener('contextmenu', event => "
            "setTimeout(() => node.dataset.prevented=String(event.defaultPrevented)))"
        )
        field.click(button="right")
        expect(field).to_have_attribute("data-prevented", "false")
        expect(page.get_by_role("menu", name="Slide workspace", exact=True)).not_to_be_visible()
        expect(page.get_by_role("menu", name="Slide options", exact=True)).not_to_be_visible()
    assert live_app.store.comments(DECK_ID) == []
    assert live_app.store.read(DECK_ID).slides[0].notes == PRIVATE_NOTE


def test_object_menu_scrolls_in_a_short_window(page: Page, live_app: RunningApp) -> None:
    """Keep the last object action reachable in short landscape windows."""
    page.set_viewport_size({"width": 800, "height": 400})
    open_editor(page, live_app)
    page.frame_locator("#preview").get_by_role("heading", level=1).click(button="right")
    menu = page.get_by_role("menu", name="Object actions", exact=True)
    expect(menu).to_be_visible()
    bounds = menu.bounding_box()
    assert bounds is not None and bounds["y"] >= 0
    assert bounds["y"] + bounds["height"] <= 400
    page.keyboard.press("End")
    last = menu.get_by_role("menuitem", name="Clear selection", exact=True)
    expect(last).to_be_focused()
    expect(last).to_be_in_viewport()
