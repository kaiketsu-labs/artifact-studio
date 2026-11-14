"""Keep visible control names and their supporting descriptions available to assistive tools."""

import os

import pytest
from playwright.sync_api import Page, expect

from artifact_studio.design_library import DesignCreate, DesignLibrary
from artifact_studio.review import ReviewHookInput, Reviews
from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_browser import open_editor

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_design_cards_keep_names_concise_and_expose_supporting_context(
    page: Page, live_app: RunningApp
) -> None:
    """Read the default status and description, then open either kind of card by keyboard."""
    live_app.store.set_default_design("test-brand")
    reference = DesignLibrary(live_app.store).create(
        DesignCreate(name="Editorial system", description="Quiet typography and generous space.")
    )
    _ = page.goto(live_app.url + "/?view=designs", wait_until="networkidle")
    default = page.get_by_role("button", name="Open test-brand design", exact=True)
    expect(default).to_have_accessible_description("Default Fictional test design")
    default.focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", name="test-brand", exact=True)).to_be_visible()
    page.get_by_role("navigation", name="Breadcrumb").get_by_role(
        "button", name="Designs", exact=True
    ).click()
    context = page.get_by_role("button", name="Open Editorial system design", exact=True)
    expect(context).to_have_accessible_description("Quiet typography and generous space.")
    context.focus()
    page.keyboard.press("Space")
    expect(page.get_by_role("heading", name=reference.name, exact=True)).to_be_visible()


def test_agent_availability_and_copy_have_accessible_names(
    page: Page, live_app: RunningApp
) -> None:
    """Expose the real lease status and the copy control without a delivery switch."""
    open_editor(page, live_app)
    status = page.get_by_role("status", name="Agent connection status")
    expect(status).to_have_text("Agent unavailable")
    live_app.store.mark_agent_check(DECK_ID, wait_seconds=120)
    _ = page.reload(wait_until="networkidle")
    expect(status).to_have_text("Agent connected")
    reviews = Reviews(live_app.store)
    for harness in ("codex", "claude-code"):
        reviews.disconnect(DECK_ID)
        _ = reviews.bind(
            ReviewHookInput(
                harness=harness,
                session_id="accessible-label-check",
                event="PostToolUse",
                deck_id=DECK_ID,
                start_review=True,
            )
        )
        _ = page.reload(wait_until="networkidle")
        expect(status).to_have_text("Agent connected")
    copy = page.get_by_role("button", name="Copy conversation", exact=True)
    expect(copy).to_be_visible()
    copy.focus()
    expect(copy).to_be_focused()
    expect(page.get_by_role("button", name="Batch", exact=True)).to_have_count(0)
