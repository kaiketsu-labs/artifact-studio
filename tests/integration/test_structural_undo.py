"""Chronological undo across section, movement and guarded deletion actions."""

import os

import pytest
from playwright.sync_api import Browser, Page, Route, expect

from artifact_studio.models import Slide
from tests.integration.support import DECK_ID, PRIVATE_NOTE, RunningApp, markup
from tests.integration.test_browser import open_editor
from tests.integration.test_sections import add_section

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_undo_deletion_then_earlier_move_and_section(page: Page, live_app: RunningApp) -> None:
    """Restoration preserves original notes and permits undo of prior layout actions."""
    _ = live_app.store.add_slide(
        DECK_ID, Slide(id="closing", title="Closing", html=markup("Closing"))
    )
    open_editor(page, live_app)
    add_section(page, "Overview")
    page.locator('.slide-thumb[data-id="next"]').click()
    page.keyboard.press("Control+ArrowDown")
    expect(page.locator(".slide-thumb").nth(2)).to_have_attribute("data-id", "next")
    page.locator('.slide-thumb[data-id="cover"]').click()
    page.keyboard.press("Delete")
    expect(page.locator('.slide-thumb[data-id="cover"]')).to_have_count(0)
    page.keyboard.press("Control+z")
    expect(page.locator('.slide-thumb[data-id="cover"]')).to_have_count(1)
    assert live_app.store.read(DECK_ID).slides[0].notes == PRIVATE_NOTE
    page.keyboard.press("Control+z")
    expect(page.locator(".slide-thumb").nth(1)).to_have_attribute("data-id", "next")
    page.keyboard.press("Control+z")
    expect(page.locator(".slide-section")).to_have_count(0)
    assert [slide.id for slide in live_app.store.read(DECK_ID).slides] == [
        "cover",
        "next",
        "closing",
    ]


def test_undo_two_deletions_in_reverse_order(page: Page, live_app: RunningApp) -> None:
    """Each deletion is restored by its own stable operation ID."""
    _ = live_app.store.add_slide(
        DECK_ID, Slide(id="closing", title="Closing", html=markup("Closing"))
    )
    open_editor(page, live_app)
    page.locator('.slide-thumb[data-id="cover"]').click()
    page.keyboard.press("Delete")
    expect(page.locator('.slide-thumb[data-id="cover"]')).to_have_count(0)
    page.locator('.slide-thumb[data-id="next"]').click()
    page.keyboard.press("Delete")
    expect(page.locator('.slide-thumb[data-id="next"]')).to_have_count(0)
    page.keyboard.press("Control+z")
    expect(page.locator('.slide-thumb[data-id="next"]')).to_have_count(1)
    page.keyboard.press("Control+z")
    expect(page.locator('.slide-thumb[data-id="cover"]')).to_have_count(1)
    assert [slide.id for slide in live_app.store.read(DECK_ID).slides] == [
        "cover",
        "next",
        "closing",
    ]
    assert live_app.store.read(DECK_ID).slides[0].notes == PRIVATE_NOTE


def test_restore_retry_reuses_operation_after_lost_response(
    browser: Browser, live_app: RunningApp
) -> None:
    """A committed restore with a lost response is retried without duplicating slides."""
    context = browser.new_context()
    page = context.new_page()
    lost: list[object] = []

    def lose_response(route: Route) -> None:
        if not lost:
            response = route.fetch()
            assert response.ok
            lost.append(route.request.post_data_json)
            route.abort("connectionfailed")
        else:
            assert route.request.post_data_json == lost[0]
            route.continue_()

    try:
        open_editor(page, live_app)
        page.locator('.slide-thumb[data-id="next"]').click()
        page.keyboard.press("Control+ArrowUp")
        expect(page.locator(".slide-thumb").first).to_have_attribute("data-id", "next")
        page.keyboard.press("Delete")
        expect(page.locator('.slide-thumb[data-id="next"]')).to_have_count(0)
        page.route("**/restore-slides", lose_response)
        page.keyboard.press("Control+z")
        expect(page.get_by_role("button", name="Retry", exact=True)).to_be_visible()
        page.get_by_role("button", name="Retry", exact=True).click()
        expect(page.locator('.slide-thumb[data-id="next"]')).to_have_count(1)
        page.keyboard.press("Control+z")
        expect(page.locator(".slide-thumb").first).to_have_attribute("data-id", "cover")
        assert [slide.id for slide in live_app.store.read(DECK_ID).slides] == ["cover", "next"]
    finally:
        context.close()


def test_switching_presentations_fences_keyboard_and_stale_toast(
    page: Page, live_app: RunningApp
) -> None:
    """Undo controls from one presentation never restore slides in an inactive one."""
    other_id = "2026-11-01-other-workflow"
    _ = live_app.store.create(other_id, "Other workflow", "test-brand")
    other_before = live_app.store.read(other_id).model_dump()
    open_editor(page, live_app)
    page.locator('.slide-thumb[data-id="cover"]').click()
    page.keyboard.press("Delete")
    expect(page.locator('.slide-thumb[data-id="cover"]')).to_have_count(0)
    expect(page.get_by_role("button", name="Undo", exact=True)).to_be_visible()
    page.locator("#deck-switcher").click()
    page.get_by_role("menuitemradio", name="Other workflow").click()
    expect(page.locator("#deck-picker")).to_have_value(other_id)
    expect(page.locator("#slide-title")).to_have_text("Other workflow")
    page.get_by_role("button", name="Undo", exact=True).click()
    expect(page.locator("#toast")).to_contain_text("presentation changed")
    page.locator(".slide-thumb").first.focus()
    page.keyboard.press("Control+z")
    assert [slide.id for slide in live_app.store.read(DECK_ID).slides] == ["next"]
    assert live_app.store.read(other_id).model_dump() == other_before


def test_failed_action_retry_waits_for_original_presentation(
    browser: Browser, live_app: RunningApp
) -> None:
    """An uncommitted action keeps its token but cannot retry in another presentation."""
    other_id = "2026-11-01-retry-other"
    _ = live_app.store.create(other_id, "Retry other", "test-brand")
    other_before = live_app.store.read(other_id).model_dump()
    context = browser.new_context()
    page = context.new_page()
    requests: list[object] = []

    def fail_first(route: Route) -> None:
        requests.append(route.request.post_data_json)
        if len(requests) == 1:
            route.abort("connectionfailed")
        else:
            route.continue_()

    try:
        page.route("**/slide-actions", fail_first)
        open_editor(page, live_app)
        page.locator('.slide-thumb[data-id="cover"]').click()
        page.keyboard.press("Delete")
        expect(page.get_by_role("button", name="Retry", exact=True)).to_be_visible()
        assert len(requests) == 1
        page.locator("#deck-switcher").click()
        page.get_by_role("menuitemradio", name="Retry other").click()
        expect(page.locator("#deck-picker")).to_have_value(other_id)
        expect(page.locator("#slide-title")).to_have_text("Retry other")
        page.get_by_role("button", name="Retry", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("presentation changed")
        assert len(requests) == 1
        assert [slide.id for slide in live_app.store.read(DECK_ID).slides] == ["cover", "next"]
        assert live_app.store.read(other_id).model_dump() == other_before
        page.locator("#deck-switcher").click()
        page.get_by_role("menuitemradio", name="CI workflow").click()
        expect(page.locator("#deck-picker")).to_have_value(DECK_ID)
        expect(page.locator("#slide-title")).to_have_text("CI workflow")
        page.get_by_role("button", name="Retry", exact=True).click()
        expect(page.locator('.slide-thumb[data-id="cover"]')).to_have_count(0)
        assert len(requests) == 2 and requests[0] == requests[1]
        assert [slide.id for slide in live_app.store.read(DECK_ID).slides] == ["next"]
        assert live_app.store.read(other_id).model_dump() == other_before
    finally:
        context.close()
