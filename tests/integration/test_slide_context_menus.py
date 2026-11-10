"""Slide menus stay contextual, usable on small screens and separate from native typing."""

import io
import os
import re
from pathlib import Path
from typing import Literal

import pytest
from PIL import Image
from playwright.sync_api import Locator, Page, expect

from artifact_studio.models import SlidePatch
from artifact_studio.storage import digest
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
    menu.get_by_role("menuitem", name="Organize", exact=True).click()
    menu.get_by_role("menuitem", name="Select all slides", exact=True).click()
    expect(page.locator('#slides [data-id][aria-pressed="true"]')).to_have_count(2)
    assert live_app.store.comments(DECK_ID) == []


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_thumbnail_copies_the_invoked_slide_as_a_real_png(
    page: Page, live_app: RunningApp, artifacts: Path, theme: Literal["light", "dark"]
) -> None:
    """Read actual clipboard pixels after right-clicking a slide other than the active one."""
    before = live_app.store.read(DECK_ID)
    item = live_app.store.locate(before, "next")
    _ = live_app.store.revise(
        DECK_ID,
        item.id,
        digest(item.model_dump_json()),
        "clipboard-image-fixture",
        SlidePatch(
            html='<article class="slide" style="background:#315bea;color:white">'
            "<h1>Fictional launch plan</h1></article>"
        ),
    )
    expected = live_app.store.read(DECK_ID)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=live_app.url)
    page.emulate_media(color_scheme=theme)
    open_editor(page, live_app)
    expect(page.locator("html")).to_have_attribute("data-theme", theme)
    feedback = page.get_by_role("textbox", name="Your feedback", exact=True)
    feedback.fill("Keep this unsent draft on the first slide.")
    page.locator('#slides [data-id="next"]').click(button="right")
    menu = page.get_by_role("menu", name="Slide options", exact=True)
    expect(menu).to_be_visible()
    _ = page.screenshot(path=str(artifacts / f"copy-slide-image-{theme}.png"))
    page.evaluate("""() => {
        const original = window.fetch;
        const gate = new Promise(resolve => {window.releaseSlideImage = resolve;});
        window.fetch = async (...args) => {
            const response = await original(...args);
            if (String(args[0]).endsWith('/image')) await gate;
            return response;
        };
    }""")
    menu.get_by_role("menuitem", name="Copy", exact=True).click()
    menu.get_by_role("menuitem", name="Copy as image", exact=True).click()
    page.locator('#slides [data-id="cover"]').click(button="right")
    menu.get_by_role("menuitem", name="Copy", exact=True).click()
    expect(menu.get_by_role("menuitem", name="Copy as image", exact=True)).to_be_disabled()
    page.evaluate("window.releaseSlideImage()")
    expect(page.locator("#toast")).to_have_text("Slide image copied.", timeout=30_000)
    expect(menu.get_by_role("menuitem", name="Copy as image", exact=True)).to_be_enabled()
    data = page.evaluate("""async () => {
        const items = await navigator.clipboard.read();
        const item = items.find(item => item.types.includes('image/png'));
        const image = await item.getType('image/png');
        return Array.from(new Uint8Array(await image.arrayBuffer()));
    }""")
    with Image.open(io.BytesIO(bytes(data))) as image:
        assert image.size == (1600, 900)
        assert image.convert("RGB").getpixel((1500, 800)) == (49, 91, 234)
    page.locator('#slides [data-id="cover"]').click()
    expect(feedback).to_have_value("Keep this unsent draft on the first slide.")
    page.locator('#slides [data-id="cover"]').click(button="right")
    menu.get_by_role("menuitem", name="Organize", exact=True).click()
    menu.get_by_role("menuitem", name="Select all slides", exact=True).click()
    page.locator('#slides [data-id="cover"]').click(button="right")
    menu.get_by_role("menuitem", name="Copy", exact=True).click()
    expect(menu.get_by_role("menuitem", name="Copy as image", exact=True)).to_be_disabled()
    assert live_app.store.read(DECK_ID) == expected
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
    last = menu.get_by_role("menuitem", name="Delete slide", exact=True)
    expect(last).to_be_focused()
    last_bounds = last.bounding_box()
    assert last_bounds is not None
    assert bounds["y"] <= last_bounds["y"]
    assert last_bounds["y"] + last_bounds["height"] <= bounds["y"] + bounds["height"]
    _ = page.screenshot(path=str(artifacts / "slide-menu-in-viewport.png"))
    page.keyboard.press("Escape")
    expect(page.locator('#slides [data-id="cover"]')).to_be_focused()


def test_slide_submenus_keep_keyboard_navigation_in_the_current_level(
    page: Page, live_app: RunningApp
) -> None:
    """Open children with Right, return with Left/Escape and reach the separate Delete action."""
    open_editor(page, live_app)
    thumbnail = page.locator('#slides [data-id="cover"]')
    thumbnail.focus()
    page.keyboard.press("Shift+F10")
    menu = page.get_by_role("menu", name="Slide options", exact=True)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    copy = menu.get_by_role("menuitem", name="Copy", exact=True)
    expect(copy).to_be_focused()
    page.keyboard.press("ArrowRight")
    expect(menu.get_by_role("menuitem", name="Copy as image", exact=True)).to_be_focused()
    page.keyboard.press("End")
    expect(menu.get_by_role("menuitem", name="Copy slide link", exact=True)).to_be_focused()
    page.keyboard.press("ArrowLeft")
    expect(copy).to_be_focused()
    expect(copy).to_have_attribute("aria-expanded", "false")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowRight")
    expect(menu.get_by_role("menuitem", name="Move down", exact=True)).to_be_focused()
    page.keyboard.press("Escape")
    expect(menu.get_by_role("menuitem", name="Organize", exact=True)).to_be_focused()
    page.keyboard.press("End")
    expect(menu.get_by_role("menuitem", name="Delete slide", exact=True)).to_be_focused()
    page.keyboard.press("Escape")
    expect(menu).not_to_be_visible()
    expect(thumbnail).to_be_focused()


def test_slide_submenu_hover_and_copy_link_close_every_panel(
    page: Page, live_app: RunningApp
) -> None:
    """Follow the actual pointer into a flyout and copy the invoked slide's link."""
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=live_app.url)
    open_editor(page, live_app)
    page.locator('#slides [data-id="next"]').click(button="right")
    menu = page.get_by_role("menu", name="Slide options", exact=True)
    copy = menu.get_by_role("menuitem", name="Copy", exact=True)
    copy.hover()
    child = menu.get_by_role("menu", name="Copy slide", exact=True)
    expect(child).to_be_visible()
    copy.click()
    expect(child).to_be_visible()
    child.get_by_role("menuitem", name="Copy slide link", exact=True).hover()
    expect(child).to_be_visible()
    child.get_by_role("menuitem", name="Copy slide link", exact=True).click()
    expect(page.locator("#toast")).to_have_text("Slide link copied.")
    assert page.evaluate("navigator.clipboard.readText()") == f"{live_app.url}/?deck={DECK_ID}#next"
    expect(menu).not_to_be_visible()
    expect(child).not_to_be_visible()
    page.locator('#slides [data-id="next"]').click(button="right")
    copy.hover()
    expect(child).to_be_visible()
    menu.get_by_role("menuitem", name="New slide", exact=True).hover()
    expect(child).not_to_be_visible()
    page.mouse.click(1100, 100)
    expect(menu).not_to_be_visible()
    expect(child).not_to_be_visible()


def test_organize_move_uses_the_invoked_slide_and_preserves_drafts(
    page: Page, live_app: RunningApp
) -> None:
    """A command in the flyout reaches the existing reorder controller with its original context."""
    open_editor(page, live_app)
    feedback = page.get_by_role("textbox", name="Your feedback", exact=True)
    feedback.fill("Keep the first slide's unsent draft.")
    page.locator('#slides [data-id="next"]').click(button="right")
    menu = page.get_by_role("menu", name="Slide options", exact=True)
    menu.get_by_role("menuitem", name="Organize", exact=True).click()
    menu.get_by_role("menuitem", name="Move to beginning", exact=True).click()
    expect(page.locator("#slides [data-id]").first).to_have_attribute("data-id", "next")
    assert live_app.store.read(DECK_ID).slides[0].id == "next"
    page.locator('#slides [data-id="cover"]').click()
    expect(feedback).to_have_value("Keep the first slide's unsent draft.")
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").notes == PRIVATE_NOTE
    assert live_app.store.comments(DECK_ID) == []


@pytest.mark.parametrize("width,height", [(390, 720), (800, 400)])
def test_organize_submenu_fits_short_and_narrow_sorter_windows(
    page: Page, live_app: RunningApp, artifacts: Path, width: int, height: int
) -> None:
    """Expand inline when necessary and scroll focused submenu actions into the menu."""
    page.set_viewport_size({"width": width, "height": height})
    open_editor(page, live_app)
    page.get_by_role("button", name="Slide sorter", exact=True).click()
    thumbnail = page.locator('#slides [data-id="cover"]')
    bounds = thumbnail.bounding_box()
    assert bounds is not None
    page.mouse.click(
        bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2, button="right"
    )
    menu = page.get_by_role("menu", name="Slide options", exact=True)
    menu.get_by_role("menuitem", name="Organize", exact=True).click()
    child = menu.get_by_role("menu", name="Organize slides", exact=True)
    expect(child).to_be_visible()
    if width == 390:
        expect(child).to_have_class(re.compile(r"\bis-inline\b"))
    bounds = child.bounding_box()
    assert bounds is not None
    assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
    page.keyboard.press("End")
    last = child.get_by_role("menuitem", name="Select all slides", exact=True)
    expect(last).to_be_focused()
    expect(last).to_be_in_viewport()
    _ = page.screenshot(path=str(artifacts / "organized-slide-submenu.png"))
    last.click()
    expect(page.locator('#slides [data-id][aria-pressed="true"]')).to_have_count(2)
    expect(menu).not_to_be_visible()
    expect(child).not_to_be_visible()


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
