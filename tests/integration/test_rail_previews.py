"""Thumbnail documents follow the visible slide rail without changing its controls."""

import os

import pytest
from playwright.sync_api import Page, expect

from artifact_studio.models import Slide
from tests.integration.support import DECK_ID, RunningApp, markup
from tests.integration.test_browser import open_editor

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_thumbnails_follow_scroll_and_collapsed_sections(page: Page, live_app: RunningApp) -> None:
    """Release remote frames, restore exact slide content and keep the selected slide usable."""
    for number in range(2, 40):
        _ = live_app.store.add_slide(
            DECK_ID,
            Slide(
                id=f"slide-{number}", title=f"Slide {number}", html=markup(f"Unique slide {number}")
            ),
        )
    snapshot = live_app.store.view(DECK_ID)
    ids = [item.id for item in snapshot.slides]
    _ = live_app.post(
        f"/api/decks/{DECK_ID}/sections",
        {
            "slide_ids": ids,
            "expected_slide_ids": ids,
            "expected_sections_hash": snapshot.sections_hash,
            "sections": [{"id": "all", "title": "All slides", "slide_ids": ids}],
        },
    )
    open_editor(page, live_app)
    rail = page.locator("#slides")
    expect(page.frame_locator('iframe[title="Thumbnail 1"]').locator(".slide")).to_be_visible()
    assert rail.locator("iframe").count() < 15
    last = page.locator('.slide-thumb[data-id="slide-39"]')
    last.scroll_into_view_if_needed()
    expect(
        page.frame_locator('iframe[title="Thumbnail 40"]').get_by_role(
            "heading", name="Unique slide 39"
        )
    ).to_be_visible()
    expect(rail.locator('iframe[title="Thumbnail 1"]')).to_have_count(0)
    assert rail.locator("iframe").count() < 15
    last.click()
    expect(
        page.frame_locator("#preview").get_by_role("heading", name="Unique slide 39")
    ).to_be_visible()
    rail.locator(".section-toggle").click()
    expect(rail.locator("iframe")).to_have_count(0)
    rail.locator(".section-toggle").click()
    page.locator('.slide-thumb[data-id="slide-39"]').scroll_into_view_if_needed()
    expect(
        page.frame_locator('iframe[title="Thumbnail 40"]').get_by_role(
            "heading", name="Unique slide 39"
        )
    ).to_be_visible()
