"""Playback of provisional element alternatives stays separate from decisions."""

import os
import re

import pytest
from playwright.sync_api import Page, expect

from artifact_studio.models import SlidePatch
from artifact_studio.storage import digest
from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_element_alternatives import (
    element_markup,
    headline,
    open_group,
    option,
    options,
)
from tests.integration.test_slide_designs import history_chrome_thumbnail

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_alternative_motion_replays_without_deciding(page: Page, live_app: RunningApp) -> None:
    """Play candidate-only motion, switch options and cancel without changing the slide."""
    theme = live_app.store.folder(DECK_ID) / "theme.css"
    _ = theme.write_text(
        theme.read_text() + "\n@keyframes fixture-motion {from{opacity:.1}to{opacity:1}}\n"
    )
    current = live_app.store.locate(live_app.store.read(DECK_ID), "cover")
    source = element_markup()
    _ = live_app.store.revise(
        DECK_ID,
        "cover",
        digest(current.model_dump_json()),
        "motion-fixture",
        SlidePatch(html=source),
    )
    current = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    group = live_app.call(
        "publish_element_alternatives",
        deck_id=DECK_ID,
        slide_id="cover",
        target_id="headline",
        kind="headline",
        label="Headline",
        expected_content_hash=current["content_hash"],
        request_id="motion-alternatives-fixture",
        options=[
            {
                "name": "Animated",
                "html": headline(1).replace(
                    'style="', 'style="animation:fixture-motion 4s linear;'
                ),
            },
            {"name": "Built", "html": headline(2).replace("<h2 ", '<h2 data-build="1" ')},
        ],
    )
    posts: list[str] = []
    page.on(
        "request",
        lambda request: (
            posts.append(request.url)
            if request.method == "POST" and request.url.endswith("/decide")
            else None
        ),
    )
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    open_group(page, group)
    option(page, group, 1).click()
    play = page.get_by_role("button", name="Play animations", exact=True)
    expect(play).to_be_enabled()
    play.click()
    expect(page.locator(".animation-preview")).to_be_visible()
    expect(page.frame_locator(".animation-preview iframe").locator("#headline")).to_have_text(
        "A stronger story, every review."
    )
    animated = page.frame_locator(".animation-preview iframe").locator("#headline")
    first = animated.evaluate("""node => {
        const animation = node.getAnimations().find(item => item.playState === 'running');
        return animation && {time: animation.currentTime,
            opacity: Number(getComputedStyle(node).opacity)};
    }""")
    assert first is not None
    page.wait_for_timeout(200)
    second = animated.evaluate("""node => {
        const animation = node.getAnimations().find(item => item.playState === 'running');
        return animation && {time: animation.currentTime,
            opacity: Number(getComputedStyle(node).opacity)};
    }""")
    assert second is not None
    assert second["time"] > first["time"]
    assert second["opacity"] > first["opacity"]
    expect(page.locator("#element-alternative-panel")).to_be_visible()
    assert not posts
    page.keyboard.press("Escape")
    expect(page.locator(".animation-preview")).to_have_count(0)
    expect(page.locator("#element-alternative-panel")).to_be_visible()
    play.click()
    expect(page.locator(".animation-preview")).to_be_visible()
    option(page, group, 2).click()
    expect(page.locator(".animation-preview")).to_have_count(0)
    expect(play).to_be_enabled()
    play.click()
    expect(page.frame_locator(".animation-preview iframe").locator("#headline")).to_have_text(
        "From first thought to a clear story."
    )
    built = page.frame_locator(".animation-preview iframe").locator("#headline")
    expect(built).to_have_class(re.compile(r"\bunrevealed\b"))
    expect(built).not_to_have_class(re.compile(r"\bunrevealed\b"), timeout=5000)
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    expect(page.locator("#element-alternative-panel")).to_be_hidden()
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == source
    assert not posts
    open_group(page, group)
    expect(page.get_by_role("button", name="No animations on this slide")).to_be_disabled()
    page.keyboard.press("Escape")

    current_slide = live_app.store.locate(live_app.store.read(DECK_ID), "cover")
    changed = source.replace(headline(0), '<h2 id="headline">A newer slide</h2>')
    _ = live_app.store.revise(
        DECK_ID,
        "cover",
        digest(current_slide.model_dump_json()),
        "motion-stale-fixture",
        SlidePatch(html=changed),
    )
    page.reload()
    history_chrome_thumbnail(page)
    page.get_by_role("button", name="Version history", exact=True).click()
    history = page.locator(".ea-history")
    history.locator(":scope > summary").click()
    history.locator(f'.ea-saved-group[data-set-id="{group["id"]}"] > summary').click()
    history.locator(f'[data-ea-saved="{options(group)[1]["id"]}"]').click()
    expect(page.locator(".ea-saved-overlay")).to_be_visible()
    expect(page.get_by_role("button", name="No animations on this slide")).to_be_disabled()
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == changed
    assert not posts
