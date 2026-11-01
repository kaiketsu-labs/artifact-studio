"""Keep a visible slide coordinate still while zooming through each input surface."""

import os

import pytest
from playwright.sync_api import Page, expect

from tests.integration.support import RunningApp
from tests.integration.test_browser import open_editor

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("SLIDE_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


@pytest.mark.parametrize("source", ["preview", "canvas", "toolbar"])
def test_zoom_preserves_the_visible_slide_point(
    page: Page, live_app: RunningApp, source: str
) -> None:
    """Translate iframe coordinates, retain pointer anchors and centre toolbar zooms."""
    page.set_viewport_size({"width": 1280, "height": 800})
    open_editor(page, live_app)
    page.locator("#view-zoom").evaluate(
        "element => { element.value = '200'; element.dispatchEvent(new Event('input')); }"
    )
    expect(page.locator("#zoom-value")).to_have_text("200%")
    canvas = page.locator(".canvas-area")
    canvas.evaluate("element => { element.scrollLeft = 200; element.scrollTop = 100; }")
    viewport = canvas.evaluate(
        """element => {
            const bounds = element.getBoundingClientRect();
            return {x: bounds.left + element.clientLeft, y: bounds.top + element.clientTop,
                    width: element.clientWidth, height: element.clientHeight};
        }"""
    )
    before = page.locator("#preview").bounding_box()
    assert viewport and before
    left, right = (
        max(before["x"], viewport["x"]),
        min(before["x"] + before["width"], viewport["x"] + viewport["width"]),
    )
    top, bottom = (
        max(before["y"], viewport["y"]),
        min(before["y"] + before["height"], viewport["y"] + viewport["height"]),
    )
    x = (left + right) / 2 if source == "toolbar" else left + (right - left) * 0.7
    y = (top + bottom) / 2 if source == "toolbar" else top + (bottom - top) * 0.6
    u, v = (x - before["x"]) / before["width"], (y - before["y"]) / before["height"]

    for direction in (1, -1):
        if source == "preview":
            page.mouse.move(x, y)
            page.keyboard.down("Control")
            page.mouse.wheel(0, -120 * direction)
            page.keyboard.up("Control")
        elif source == "canvas":
            canvas.evaluate(
                """(element, point) => element.dispatchEvent(new WheelEvent('wheel', {
                    clientX: point.x, clientY: point.y, deltaY: -120 * point.direction,
                    ctrlKey: true, bubbles: true, cancelable: true
                }))""",
                {"x": x, "y": y, "direction": direction},
            )
        else:
            page.locator("#zoom-in" if direction == 1 else "#zoom-out").click()
        expect(page.locator("#zoom-value")).to_have_text(
            f"{200 + (10 if source == 'toolbar' else 5) if direction == 1 else 200}%"
        )
        after = page.locator("#preview").bounding_box()
        assert after
        assert abs(after["x"] + u * after["width"] - x) < 2
        assert abs(after["y"] + v * after["height"] - y) < 2

    page.locator("#fit-view").click()
    expect(page.locator("#zoom-value")).to_have_text("100%")
    assert canvas.evaluate("element => element.scrollLeft == 0 && element.scrollTop == 0")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
