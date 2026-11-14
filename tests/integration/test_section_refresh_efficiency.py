"""Changing the selected slide does not rebuild unchanged section headings."""

import os

import pytest
from playwright.sync_api import Page, expect

from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_browser import open_editor

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_slide_selection_preserves_unchanged_section_nodes(
    page: Page, live_app: RunningApp
) -> None:
    snapshot = live_app.store.view(DECK_ID)
    ids = [item.id for item in snapshot.slides]
    live_app.post(
        f"/api/decks/{DECK_ID}/sections",
        {
            "slide_ids": ids,
            "expected_slide_ids": ids,
            "expected_sections_hash": snapshot.sections_hash,
            "sections": [
                {"id": "first", "title": "First", "slide_ids": ids[:1]},
                {"id": "second", "title": "Second", "slide_ids": ids[1:]},
            ],
        },
    )
    open_editor(page, live_app)
    headings = page.locator(".section-title")
    expect(headings).to_have_count(2)
    page.evaluate("""() => {
      window.sectionMutations = 0;
      const observer = new MutationObserver(records => window.sectionMutations += records.length);
      for (const title of document.querySelectorAll('.section-title, .section-count'))
        observer.observe(title, {childList:true, characterData:true, subtree:true});
    }""")
    page.locator('.slide-thumb[data-id="next"]').click()
    expect(page.frame_locator("#preview").locator("h1")).to_have_text("Present with confidence")
    assert page.evaluate("window.sectionMutations") == 0
    expect(headings).to_have_text(["First", "Second"])
