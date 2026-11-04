"""Presentation context remains visible in the slide sorter."""

import os
from typing import Literal

import pytest
from playwright.sync_api import Page, expect

from artifact_studio.artifacts import ArtifactStore
from scripts.screenshot_fixture import seed
from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_element_alternatives import seed_elements

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


@pytest.mark.parametrize(("width", "theme"), [(1280, "light"), (1280, "dark"), (480, "dark")])
def test_project_presentation_context_in_sorter(
    page: Page, live_app: RunningApp, width: int, theme: Literal["light", "dark"]
) -> None:
    """Keep the selected presentation and slide controls visible and usable."""
    page.set_viewport_size({"width": width, "height": 800})
    deck = seed(live_app)
    deck.title = "A clearer way to work — a fictional presentation with a long title"
    live_app.store.write(DECK_ID, deck)
    seed_elements(live_app, slide_id="workflow")
    store = ArtifactStore(live_app.store)
    project = store.create_project("Fictional project")
    store.set_artifact_project(DECK_ID, project.id)
    page.emulate_media(color_scheme=theme)
    page.goto(f"{live_app.url}/?deck={DECK_ID}#workflow", wait_until="networkidle")
    expect(page.locator("#artifact-rail-items [aria-current=true]")).to_have_text(deck.title)
    expect(page.locator(".ea-badge").first).to_be_visible()
    page.get_by_role("button", name="Slide sorter", exact=True).click()
    title = page.locator("#sorter-presentation-title")
    expect(title).to_be_visible()
    expect(title).to_have_text(deck.title)
    expect(title).to_have_attribute("title", deck.title)
    expect(page.locator(".ea-badges")).to_be_hidden()
    expect(page.get_by_role("button", name="New slide", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=f"/tmp/sorter-{theme}-{width}.png")
    page.locator("#slides .slide-thumb").nth(1).click()
    page.get_by_role("button", name="Edit view", exact=True).click()
    expect(page.locator("#slide-title")).to_have_text("Good slides happen in the loop")
    expect(title).to_be_hidden()
    expect(page.locator(".ea-badge").first).to_be_visible()
    expect(page.locator("#artifact-rail-items [aria-current=true]")).to_have_text(deck.title)


def test_legacy_presentation_picker_still_works(page: Page, live_app: RunningApp) -> None:
    """Presentations without a hub project retain their existing picker."""
    page.goto(f"{live_app.url}/?deck={DECK_ID}", wait_until="networkidle")
    page.get_by_role("button", name="Slide sorter", exact=True).click()
    expect(page.locator("#sorter-presentation-title")).to_be_hidden()
    page.locator("#deck-switcher").click()
    expect(page.get_by_role("menu", name="Projects")).to_be_visible()
