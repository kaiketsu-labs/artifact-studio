"""Keep the real favorites picker responsive without retaining hidden slide documents."""

import os
from collections import Counter

import pytest
from playwright.sync_api import Browser, Page, Route, expect

from artifact_studio.favorites import Favorites, StarInput
from artifact_studio.models import Slide
from tests.integration.support import DECK_ID, RunningApp, markup
from tests.integration.test_library_previews import fixture_image

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def seed_favorites(app: RunningApp, count: int = 24) -> None:
    """Use authored fictional slides and real favorite/preview API data."""
    deck = app.store.read(DECK_ID)
    deck.slides = [
        Slide(id=f"favorite-{index:02}", title=f"Favorite {index:02}", html=markup(str(index)))
        for index in range(count)
    ]
    app.store.write(DECK_ID, deck)
    favorites = Favorites(app.store)
    for slide in deck.slides:
        _ = favorites.set(
            StarInput(kind="slide", artifact_id=DECK_ID, slide_id=slide.id, starred=True)
        )


def library(page: Page, app: RunningApp) -> None:
    """The cover image is unrelated to the real favorite slide documents under test."""
    image = fixture_image()
    page.route(
        "**/api/hub/previews/**",
        lambda route: route.fulfill(status=200, content_type="image/png", body=image),
    )
    page.goto(app.url)


def test_search_scroll_and_close_keep_only_visible_favorite_documents(
    live_app: RunningApp, browser: Browser
) -> None:
    seed_favorites(live_app)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    requests: Counter[str] = Counter()
    page.on(
        "request",
        lambda request: (
            requests.update([request.url])
            if "/slides/" in request.url and "/preview" in request.url
            else None
        ),
    )
    try:
        library(page, live_app)
        page.get_by_role("button", name="Favorite slides", exact=True).click()
        dialog = page.get_by_role("dialog", name="Favorite slides", exact=True)
        expect(dialog.locator(".favorite-card")).to_have_count(24)
        expect(page.frame_locator(".favorite-card iframe").first.locator(".slide")).to_be_visible()
        first = dialog.locator(".favorite-card").first
        first_url = first.locator("iframe").get_attribute("src")
        assert first_url
        title = first.locator(".favorite-caption strong").inner_text()
        _ = first.locator("iframe").evaluate("frame => frame.dataset.retained = 'yes'")
        search = page.get_by_role("searchbox", name="Search favorite slides")
        search.fill(title)
        expect(dialog.locator(".favorite-card")).to_have_count(1)
        expect(first.locator("iframe")).to_have_attribute("data-retained", "yes")
        search.fill("")
        expect(dialog.locator(".favorite-card")).to_have_count(24)
        expect(first.locator("iframe")).to_have_attribute("data-retained", "yes")
        assert requests[live_app.url + first_url] == 1
        assert dialog.locator("iframe").count() <= 8
        last = dialog.locator(".favorite-card").last
        last.scroll_into_view_if_needed()
        expect(last.locator("iframe")).to_have_count(1)
        expect(first.locator("iframe")).to_have_count(0)
        assert dialog.locator("iframe").count() <= 8
        page.get_by_role("button", name="Close favorite slides", exact=True).click()
        expect(page.locator(".favorites-dialog iframe")).to_have_count(0)
        expect(page.locator(".favorites-dialog .favorite-card")).to_have_count(0)
        expect(page.get_by_role("button", name="Favorite slides", exact=True)).to_be_focused()
        page.get_by_role("button", name="Favorite slides", exact=True).click()
        expect(page.frame_locator(".favorite-card iframe").first.locator(".slide")).to_be_visible()
    finally:
        context.close()


def test_closing_slow_favorites_aborts_two_inflight_documents_and_reopens(
    live_app: RunningApp, browser: Browser
) -> None:
    seed_favorites(live_app)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    pending: list[Route] = []
    failures: list[str] = []
    page.on("requestfailed", lambda request: failures.append(request.url))

    def hold(route: Route) -> None:
        pending.append(route)

    page.route("**/api/decks/**/slides/**/preview?*", hold)
    try:
        library(page, live_app)
        page.get_by_role("button", name="Favorite slides", exact=True).click()
        expect(page.locator(".favorite-card")).to_have_count(24)
        for _ in range(40):
            if len(pending) == 2:
                break
            page.wait_for_timeout(25)
        page.wait_for_timeout(150)
        assert len(pending) == 2
        page.get_by_role("button", name="Close favorite slides", exact=True).click()
        expect(page.locator(".favorites-dialog iframe")).to_have_count(0)
        page.wait_for_timeout(150)
        assert any("/slides/" in url and "/preview" in url for url in failures)
        page.unroute("**/api/decks/**/slides/**/preview?*", hold)
        page.get_by_role("button", name="Favorite slides", exact=True).click()
        expect(page.frame_locator(".favorite-card iframe").first.locator(".slide")).to_be_visible()
    finally:
        context.close()
