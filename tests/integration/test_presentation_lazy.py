"""Presentation mounts expensive slide content only when its view is opened."""

import os
from pathlib import Path
from typing import Literal

import pytest
from playwright.sync_api import Page, expect

from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_presentation_authoring import import_image

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


@pytest.mark.parametrize("appearance,width", [("light", 1600), ("dark", 650)])
def test_lazy_views_keep_navigation_and_interactive_state(
    page: Page,
    live_app: RunningApp,
    artifacts: Path,
    appearance: Literal["light", "dark"],
    width: int,
) -> None:
    """Open the overview and presenter after starting with just one interactive copy."""
    cover = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    _ = live_app.call(
        "revise_slide",
        deck_id=DECK_ID,
        slide_id="cover",
        expected_hash=cover["expected_hash"],
        request_id="lazy-interactive",
        patch={
            "interactive_html": (
                "<style>body{display:grid;place-content:center;height:100vh;margin:0}</style>"
                '<h1 id="value">Initial</h1><button onclick="setValue()">Change state</button>'
                '<script>function setValue(){document.querySelector("h1").textContent="Changed";'
                'window.artifactStudio.emitState({value:"Changed"})}'
                'window.artifactStudioSetState=s=>document.querySelector("h1").textContent=s.value;</script>'
            )
        },
    )
    _ = import_image(live_app)
    second = live_app.call("get_slide", deck_id=DECK_ID, slide_id="next")
    _ = live_app.call(
        "revise_slide",
        deck_id=DECK_ID,
        slide_id="next",
        expected_hash=second["expected_hash"],
        request_id="lazy-thumbnail",
        patch={
            "html": (
                '<article class="slide"><h1>Second slide</h1>'
                '<img src="assets/authoring/background.png"><p data-build>Reveal me</p></article>'
            )
        },
    )
    requests: list[str] = []
    page.route(
        "**/assets/authoring/background.png",
        lambda route: (
            requests.append(route.request.url),
            route.fulfill(
                status=200,
                content_type="image/svg+xml",
                body='<svg xmlns="http://www.w3.org/2000/svg"/>',
            ),
        ),
    )
    page.emulate_media(color_scheme=appearance)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(live_app.url + f"/decks/{DECK_ID}/index.html?present=1#cover")
    stage = page.frame_locator("#stage .runtime-slide-canvas > iframe").frame_locator("iframe")
    expect(stage.get_by_role("button", name="Change state")).to_be_visible()
    expect(page.locator("#presenter-current iframe, #presenter-next iframe")).to_have_count(0)
    expect(page.locator("#all-slides-grid button, #presenter-filmstrip button")).to_have_count(0)
    assert not requests
    stage.get_by_role("button", name="Change state").focus()
    page.keyboard.press("Enter")
    expect(stage.locator("h1")).to_have_text("Changed")
    page.locator("#all-slides-button").click()
    expect(page.locator("#all-slides-grid button")).to_have_count(2)
    page.locator('#all-slides-grid [data-index="1"]').click()
    expect(page.locator("#stage h1")).to_have_text("Second slide")
    expect(page.locator("#stage [data-build]")).to_have_class("unrevealed")
    page.locator("#stage").evaluate("element => { element.tabIndex = -1; element.focus(); }")
    page.keyboard.press("Enter")
    expect(page.locator("#stage [data-build]")).not_to_have_class("unrevealed")
    page.locator("#all-slides-button").click()
    page.locator('#all-slides-grid [data-index="0"]').click()
    expect(stage.locator("h1")).to_have_text("Changed")
    with page.expect_popup() as popup:
        page.locator("#show-presenter").click()
    audience = popup.value
    presenter = page.frame_locator(
        "#presenter-current .runtime-slide-canvas > iframe"
    ).frame_locator("iframe")
    expect(presenter.locator("h1")).to_have_text("Changed")
    expect(page.locator("#stage iframe")).to_have_count(0)
    expect(page.locator("#presenter-filmstrip button")).to_have_count(2)
    page.locator("#audience-view-button").click()
    expect(stage.locator("h1")).to_have_text("Changed")
    expect(page.locator("#presenter-current iframe")).to_have_count(0)
    page.locator("#show-presenter").click()
    expect(presenter.locator("h1")).to_have_text("Changed")
    page.screenshot(path=str(artifacts / f"lazy-presenter-{appearance}.png"))
    audience.close()
