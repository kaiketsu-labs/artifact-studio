"""Graphic alternative thumbnails stay inside their interactive option cards."""

import os
from typing import Literal, cast

import pytest
from playwright.sync_api import Page, expect

from artifact_studio.models import SlidePatch
from artifact_studio.storage import digest
from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_element_alternatives import element_markup, process_graphic

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def graphic(index: int) -> str:
    """Small, freely authored pixel-style graphics in the same target position."""
    colours = ("#18122d", "#d64e32", "#4d329a", "#286d5b")
    return (
        '<svg id="process-graphic" viewBox="0 0 16 16" '
        'style="width:6cqw;height:6cqw;image-rendering:pixelated" '
        'xmlns="http://www.w3.org/2000/svg">'
        f'<path d="M2 2h12v12H2z" fill="{colours[index]}"/>'
        '<path d="M4 6h8v6H4z" fill="#f2bb88"/>'
        '<path d="M5 7h1v1H5zm5 0h1v1h-1zm-3 3h2v1H7z" fill="#17122e"/>'
        "</svg>"
    )


def seed_graphics(app: RunningApp) -> dict[str, object]:
    """Publish three visual options through the actual MCP authoring interface."""
    current = app.store.locate(app.store.read(DECK_ID), "cover")
    _ = app.store.revise(
        DECK_ID,
        "cover",
        digest(current.model_dump_json()),
        "graphic-layout-fixture",
        SlidePatch(html=element_markup().replace(process_graphic(), graphic(0))),
    )
    slide = app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    return app.call(
        "publish_element_alternatives",
        deck_id=DECK_ID,
        slide_id="cover",
        target_id="process-graphic",
        kind="graphic",
        label="Reviewer head",
        expected_content_hash=slide["content_hash"],
        request_id="graphic-layout-alternatives",
        options=[{"name": f"Option {i}", "html": graphic(i)} for i in range(1, 4)],
    )


def assert_cards_fit(page: Page, selector: str) -> None:
    """Check actual transformed frames and their card content area in browser pixels."""
    cards = page.locator(f"{selector} [data-ea-option]")
    expect(cards).to_have_count(4)
    measurements = cast(
        list[dict[str, float]],
        cards.evaluate_all(
            """cards => cards.map(card => {
              const image = card.querySelector('.ea-frame');
              const rect = card.getBoundingClientRect();
              const preview = image.getBoundingClientRect();
              const rendered = image.querySelector('iframe').getBoundingClientRect();
              const padding = getComputedStyle(card);
              return {left:rect.left + parseFloat(padding.paddingLeft),
                right:rect.right - parseFloat(padding.paddingRight),
                imageLeft:preview.left, imageRight:preview.right,
                imageWidth:preview.width, imageHeight:preview.height,
                renderedWidth:rendered.width, renderedHeight:rendered.height};
            })"""
        ),
    )
    for card in measurements:
        assert card["imageWidth"] > 0 and card["imageHeight"] > 0
        assert card["imageLeft"] >= card["left"] - 1, card
        assert card["imageRight"] <= card["right"] + 1, card
        assert abs(card["imageWidth"] - card["renderedWidth"]) <= 3, card
        assert abs(card["imageHeight"] - card["renderedHeight"]) <= 3, card
    for index in range(4):
        cards.nth(index).scroll_into_view_if_needed()
        frame = page.frame_locator(f"{selector} iframe").nth(index)
        expect(frame.locator("html")).to_have_attribute("data-element-preview-ready", "true")
        fits = frame.locator("svg#process-graphic").evaluate(
            """node => {
              const rect = node.getBoundingClientRect();
              return rect.left >= -1 && rect.top >= -1 &&
                rect.right <= innerWidth + 1 && rect.bottom <= innerHeight + 1;
            }"""
        )
        assert fits, f"Graphic {index} is cropped inside its preview iframe"


@pytest.mark.parametrize(
    ("width", "theme"), [(1600, "light"), (1365, "dark"), (390, "dark"), (320, "light")]
)
def test_four_graphics_fit_their_cards_when_browsed(
    live_app: RunningApp, page: Page, width: int, theme: Literal["light", "dark"]
) -> None:
    _ = seed_graphics(live_app)
    page.set_viewport_size({"width": width, "height": 900})
    page.emulate_media(color_scheme=theme)
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    toggle = page.locator("#element-alternative-toggle")
    expect(toggle).to_have_attribute("aria-label", "Review alternatives for 1 element")
    expect(toggle.locator(".ea-pending-count")).to_have_text("1")
    bounds = toggle.bounding_box()
    assert bounds is not None and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    toggle.click()
    panel = page.locator("#element-alternative-panel")
    expect(panel.locator("iframe")).to_have_count(4)
    assert_cards_fit(page, "#element-alternative-panel")
    panel.locator("[data-ea-option]").nth(1).click()
    panel.locator("[data-ea-option]").nth(2).click()
    panel.locator("[data-ea-all]").click()
    expect(page.locator("#element-alternative-tray")).to_be_visible()
    assert_cards_fit(page, "#element-alternative-tray")
