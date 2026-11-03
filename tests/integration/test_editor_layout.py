"""Browser regressions for responsive slide editing."""

import base64
import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_artifact_workspace import register
from tests.integration.test_browser import open_editor

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def panel_positions(page: Page) -> tuple[float, float]:
    """Keep panel controls at their corresponding edges without obscuring other actions."""
    left = page.get_by_role("button", name=re.compile(r"^(Open|Close) sidebar$")).bounding_box()
    right = page.get_by_role("button", name=re.compile(r"^(Open|Close) chat$")).bounding_box()
    brand = page.get_by_role("link", name="Artifact Studio", exact=True).bounding_box()
    present = page.get_by_role("link", name="Present", exact=True).bounding_box()
    assert left is not None and right is not None and brand is not None and present is not None
    assert left["x"] + left["width"] <= brand["x"]
    assert present["x"] + present["width"] <= right["x"]
    viewport = page.viewport_size
    assert viewport is not None and right["x"] + right["width"] <= viewport["width"]
    minimum = 44 if viewport["width"] <= 600 else 34
    assert left["width"] >= minimum and left["height"] >= minimum
    assert right["width"] >= minimum and right["height"] >= minimum
    return left["x"], right["x"]


@pytest.mark.parametrize("width", [320, 375, 390, 850, 901, 1000, 1280])
def test_chat_remains_reachable_and_preserves_editor(
    live_app: RunningApp, page: Page, artifacts: Path, width: int
) -> None:
    """Keep the slide and chat usable without losing the iframe or a draft."""
    page.set_viewport_size({"width": width, "height": 844})
    open_editor(page, live_app)
    positions = panel_positions(page)
    preview = page.locator("#preview")
    expect(preview).to_be_visible()
    expect(page.get_by_label("Speaker notes", exact=True)).to_be_hidden()
    navigation = page.locator(".view-navigation").bounding_box()
    tools = page.locator(".view-controls").bounding_box()
    assert navigation is not None and tools is not None
    assert navigation["x"] + navigation["width"] <= tools["x"]
    for selector in ("#fit-view", "#fullscreen-button"):
        control = page.locator(selector).bounding_box()
        assert control is not None
        assert control["x"] >= 0 and control["x"] + control["width"] <= width
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
    assert panel_positions(page) == positions
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
def test_element_toolbar_does_not_move_slide(live_app: RunningApp, page: Page, width: int) -> None:
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


@pytest.mark.parametrize("width", [390, 850])
def test_compact_chat_skips_covered_canvas_and_restores_on_close_and_resize(
    live_app: RunningApp, page: Page, artifacts: Path, width: int
) -> None:
    """Keyboard navigation must not reach controls covered by the chat drawer."""
    page.set_viewport_size({"width": width, "height": 844})
    open_editor(page, live_app)
    canvas = page.locator(".canvas-area")
    preview = page.locator("#preview")
    preview.evaluate("element => { window.__drawerPreview = element; }")
    page.get_by_role("button", name="Open chat", exact=True).click()
    composer = page.get_by_label("Your feedback", exact=True)
    composer.fill("Retain the draft while changing drawers.")
    expect(canvas).to_have_js_property("inert", True)
    page.locator("#mode-live").focus()
    for _ in range(10):
        page.keyboard.press("Shift+Tab")
        assert page.evaluate("!document.activeElement.closest('.canvas-area')")
    page.screenshot(path=str(artifacts / "drawer-keyboard-focus.png"))
    composer.focus()
    page.keyboard.press("Escape")
    expect(page.get_by_role("button", name="Open chat", exact=True)).to_be_focused()
    expect(canvas).to_have_js_property("inert", False)
    page.get_by_role("button", name="Open chat", exact=True).click()
    page.set_viewport_size({"width": 1280, "height": 844})
    expect(canvas).to_have_js_property("inert", False)
    expect(composer).to_have_value("Retain the draft while changing drawers.")
    assert preview.evaluate("element => element === window.__drawerPreview")
    composer.focus()
    page.set_viewport_size({"width": width, "height": 844})
    expect(canvas).to_have_js_property("inert", True)
    page.keyboard.press("Escape")
    # A desktop chat can become hidden at the breakpoint without a user toggle.
    page.set_viewport_size({"width": 1280, "height": 844})
    composer.focus()
    page.set_viewport_size({"width": width, "height": 844})
    expect(page.get_by_role("button", name="Open chat", exact=True)).to_be_focused()
    expect(canvas).to_have_js_property("inert", False)


def test_phone_sidebar_restores_canvas_without_reloading(live_app: RunningApp, page: Page) -> None:
    """Sidebar drawers isolate their covered canvas and become normal desktop rails."""
    page.set_viewport_size({"width": 390, "height": 844})
    open_editor(page, live_app)
    canvas = page.locator(".canvas-area")
    page.get_by_role("button", name="Open sidebar", exact=True).click()
    expect(canvas).to_have_js_property("inert", True)
    expect(page.locator('.slide-thumb[aria-current="true"]')).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.get_by_role("button", name="Open sidebar", exact=True)).to_be_focused()
    expect(canvas).to_have_js_property("inert", False)
    page.get_by_role("button", name="Open sidebar", exact=True).click()
    page.set_viewport_size({"width": 850, "height": 844})
    expect(canvas).to_have_js_property("inert", False)
    expect(page.locator('.slide-thumb[aria-current="true"]')).to_be_focused()


@pytest.mark.parametrize("width", [850, 1280])
def test_sidebar_separator_exposes_current_width(
    live_app: RunningApp, page: Page, width: int
) -> None:
    """Announce the actual adjustable sidebar range as keyboard and mouse resize it."""
    page.set_viewport_size({"width": width, "height": 844})
    open_editor(page, live_app)
    handle = page.get_by_role("separator", name="Resize sidebar", exact=True)
    rail = page.locator(".rail")
    expect(handle).to_have_attribute("aria-valuemin", "120")
    expect(handle).to_have_attribute("aria-valuemax", str(int(width * 0.26)))

    def actual_width() -> int:
        bounds = rail.bounding_box()
        assert bounds is not None
        return round(bounds["width"])

    expect(handle).to_have_attribute("aria-valuenow", str(actual_width()))
    handle.press("End")
    expect(handle).to_have_attribute("aria-valuenow", str(actual_width()))
    assert actual_width() == int(width * 0.26)
    handle.press("Home")
    expect(handle).to_have_attribute("aria-valuenow", "120")
    handle.press("ArrowRight")
    expect(handle).to_have_attribute("aria-valuenow", "140")
    bounds = handle.bounding_box()
    assert bounds is not None
    page.mouse.move(bounds["x"] + bounds["width"] / 2, bounds["y"] + 30)
    page.mouse.down()
    page.mouse.move(bounds["x"] + bounds["width"] / 2 + 30, bounds["y"] + 30)
    page.mouse.up()
    expect(handle).to_have_attribute("aria-valuenow", str(actual_width()))
    assert actual_width() == 170
    handle.press("Home")
    handle.press("ArrowLeft")
    reopen = page.get_by_role("button", name="Open sidebar", exact=True)
    expect(reopen).to_be_focused()
    reopen.press("Enter")
    expect(handle).to_have_attribute("aria-valuenow", "120")
    page.set_viewport_size({"width": 1000, "height": 844})
    expect(handle).to_have_attribute("aria-valuemax", "260")


@pytest.mark.parametrize("kind", ["html", "markdown"])
def test_artifact_toolbar_reflows_at_320_pixels(
    live_app: RunningApp, page: Page, tmp_path: Path, artifacts: Path, kind: str
) -> None:
    """Keep the complete artifact toolbar reachable when its controls wrap."""
    _, html, markdown, _, _ = register(live_app, tmp_path)
    item = markdown if kind == "markdown" else html
    page.set_viewport_size({"width": 320, "height": 844})
    page.goto(f"{live_app.url}/?artifact={item.id}", wait_until="networkidle")
    labels = ["View", "Version history", "Select"]
    if kind == "markdown":
        labels.append("Source")
    for label in labels:
        control = page.get_by_role("button", name=label, exact=True).bounding_box()
        assert control is not None
        assert control["x"] >= 0 and control["x"] + control["width"] <= 320
    assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
    page.screenshot(path=str(artifacts / "artifact-toolbar-320.png"), full_page=True)
    if kind == "markdown":
        page.get_by_role("button", name="Source", exact=True).focus()
        page.keyboard.press("Enter")
        expect(page.locator(".hub-source")).to_contain_text("# Fictional notes")


@pytest.mark.parametrize("width,height", [(320, 844), (320, 568), (390, 844)])
def test_phone_history_preview_and_restore_are_not_clipped(
    live_app: RunningApp, page: Page, artifacts: Path, width: int, height: int
) -> None:
    """Stack the history list above a full-width preview and usable restore controls."""
    # Exercise actual history/preview/layout; thumbnail capture has its own renderer checks.
    page.route(
        "**/history/thumbnail?*",
        lambda route: route.fulfill(
            status=200,
            content_type="image/png",
            body=base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1Z0AAAAASUVORK5CYII="
            ),
        ),
    )
    page.set_viewport_size({"width": width, "height": height})
    open_editor(page, live_app)
    page.get_by_role("button", name="Version history", exact=True).click()
    timeline = page.locator("dialog.version-history")
    timeline.locator(".version-list summary").filter(has_text="Earlier save").click()
    timeline.locator(".version-list [data-version]").last.click()
    restore = timeline.get_by_role("button", name="Restore version", exact=True)
    expect(restore).to_be_enabled()
    detail = timeline.locator(".version-detail").bounding_box()
    preview = timeline.locator(".version-preview").bounding_box()
    assert detail is not None and preview is not None and preview["width"] >= 240
    restore.scroll_into_view_if_needed()
    bounds = restore.bounding_box()
    assert bounds is not None
    assert bounds["x"] >= detail["x"]
    assert bounds["x"] + bounds["width"] <= detail["x"] + detail["width"]
    assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= height
    page.screenshot(path=str(artifacts / "history-restore-phone.png"))
    restore.click()
    confirm = timeline.get_by_role("button", name="Restore", exact=True)
    expect(confirm).to_be_focused()
    bounds = confirm.bounding_box()
    assert bounds is not None
    assert bounds["x"] >= detail["x"]
    assert bounds["x"] + bounds["width"] <= detail["x"] + detail["width"]
    assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= height
    page.screenshot(path=str(artifacts / "history-confirm-phone.png"))
    page.keyboard.press("Escape")
    expect(timeline.locator(".version-confirm")).to_be_hidden()
    expect(restore).to_be_focused()
    restore.press("Enter")
    timeline.get_by_role("button", name="Cancel", exact=True).click()
    expect(restore).to_be_focused()
    page.keyboard.press("Escape")
    expect(timeline).not_to_be_visible()
    expect(page.get_by_role("button", name="Version history", exact=True)).to_be_focused()
    if width == 320 and height == 844:
        before = live_app.store.read(DECK_ID).slides[0].revision
        page.get_by_role("button", name="Version history", exact=True).click()
        timeline.locator(".version-list summary").filter(has_text="Earlier save").click()
        timeline.locator(".version-list [data-version]").last.click()
        timeline.get_by_role("button", name="Restore version", exact=True).click()
        timeline.get_by_role("button", name="Restore", exact=True).click()
        expect(timeline).not_to_be_visible()
        assert live_app.store.read(DECK_ID).slides[0].revision == before + 1
