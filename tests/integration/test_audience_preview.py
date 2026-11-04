"""Browser agents have an inline audience surface that excludes private review context."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from artifact_studio.models import SlidePatch
from artifact_studio.projects import audience_html
from artifact_studio.storage import digest
from tests.integration.support import DECK_ID, PRIVATE_NOTE, RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_advertised_audience_url_is_inline_and_excludes_review_state(
    page: Page, live_app: RunningApp
) -> None:
    """Inspect the entire returned HTML, then render actual slide pixels without a download."""
    _ = live_app.post(f"/api/decks/{DECK_ID}/feedback-mode", {"mode": "batch"})
    _ = live_app.post(
        f"/api/decks/{DECK_ID}/feedback",
        {
            "message": "WITHHELD_AUDIENCE_SENTINEL",
            "request_id": "private-audience-test",
            "context_slide_id": "cover",
        },
    )
    context = live_app.call("get_working_context", deck_id=DECK_ID)
    url = str(context["audience_url"])
    response = page.request.get(url)
    assert response.ok and "content-disposition" not in response.headers
    source = response.text()
    assert PRIVATE_NOTE not in source and "WITHHELD_AUDIENCE_SENTINEL" not in source
    assert "Disposable integration fixture." not in source
    _ = page.goto(url, wait_until="networkidle")
    expect(page.locator("#stage .slide h1")).to_have_text("Review together")
    expect(page.locator("#stage .slide h1")).to_be_visible()
    assert page.evaluate("document.documentElement.dataset.export") == "audience"
    assert PRIVATE_NOTE not in str(
        page.evaluate("document.getElementById('deck-data').textContent")
    )


def test_offline_overview_preserves_slide_typography(
    page: Page, live_app: RunningApp, tmp_path: Path, artifacts: Path
) -> None:
    """Scale actual exported slide layouts rather than clipping their fixed-size typography."""
    store = live_app.store
    before = store.locate(store.read(DECK_ID), "cover")
    _ = store.revise(
        DECK_ID,
        "cover",
        digest(before.model_dump_json()),
        "export-overview-typography",
        SlidePatch(
            html='<article class="slide" style="font-family:Arial,sans-serif;background:#e9efff">'
            '<div style="position:absolute;left:96px;top:80px;width:1350px">'
            '<h1 style="font-size:72px;line-height:1.1">Same slide. Smaller preview.</h1>'
            '<p style="font-size:3cqw">Keep the typography and layout together.</p>'
            '<svg width="320" height="120"><text x="0" y="80" font-size="64">SVG text</text>'
            "</svg></div></article>"
        ),
    )
    exported = tmp_path / "presentation.html"
    _ = exported.write_text(audience_html(store, DECK_ID))
    page.set_viewport_size({"width": 1600, "height": 900})
    page.goto(exported.as_uri())
    stage = page.locator("#stage .slide")
    expect(stage.locator("h1")).to_be_visible()
    canvas = stage.bounding_box()
    assert canvas
    ratios: dict[str, float] = {}
    for selector in ("h1", "p", "svg text"):
        text = stage.locator(selector).bounding_box()
        assert text
        ratios[selector] = text["height"] / canvas["width"]
    page.locator("#all-slides-button").click()
    overview = page.locator("#all-slides-dialog")
    expect(overview).to_be_visible()

    def check_thumbnail(selector: str) -> None:
        """Wait for actual scaled text geometry, including after hidden panels become visible."""
        page.wait_for_function(
            """({selector, ratios}) => {
                const thumb = document.querySelector(selector);
                const bounds = thumb?.getBoundingClientRect();
                return bounds?.width > 0 && Math.abs(bounds.width / bounds.height - 16/9) < .01
                    && Object.entries(ratios).every(([text, ratio]) =>
                        Math.abs(thumb.querySelector(text).getBoundingClientRect().height
                            / bounds.width - ratio) < .001);
            }""",
            arg={"selector": selector, "ratios": ratios},
        )

    check_thumbnail("#all-slides-grid .film-slide")
    _ = page.screenshot(path=str(artifacts / "export-overview.png"))
    page.set_viewport_size({"width": 700, "height": 800})
    check_thumbnail("#all-slides-grid .film-slide")
    check_thumbnail("#stage")
    _ = page.screenshot(path=str(artifacts / "export-overview-narrow.png"))
    overview.get_by_role("button", name="Slide 2: Next steps").click()
    expect(overview).not_to_be_visible()
    expect(page.locator("#stage h1")).to_have_text("Present with confidence")
    page.keyboard.press("ArrowLeft")
    with page.expect_popup():
        page.locator("#show-presenter").click()
    page.bring_to_front()
    expect(page.locator("#presenter-view")).to_be_visible()
    check_thumbnail("#presenter-filmstrip .film-slide")
    check_thumbnail("#presenter-current")
    _ = page.screenshot(path=str(artifacts / "export-presenter-filmstrip.png"))
