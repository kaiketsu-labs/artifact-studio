"""Browser agents have an inline audience surface that excludes private review context."""

import os

import pytest
from playwright.sync_api import Page, expect

from tests.integration.support import DECK_ID, PRIVATE_NOTE, RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("SLIDE_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
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
