"""Keep visible control names and their supporting descriptions available to assistive tools."""

import os
from typing import Literal

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


def test_live_mode_name_tracks_visible_availability_and_keyboard_activation(
    page: Page, live_app: RunningApp
) -> None:
    """Expose each real lease state with the same words people can see on the control."""
    open_editor(page, live_app)
    live = page.get_by_role("button", name="Live Unavailable", exact=True)
    expect(live).to_be_visible()
    live_app.store.mark_agent_check(DECK_ID, wait_seconds=120)
    _ = page.reload(wait_until="networkidle")
    expect(page.get_by_role("button", name="Live Available", exact=True)).to_be_visible()
    harnesses: list[tuple[Literal["codex", "claude-code"], str]] = [
        ("codex", "Codex"),
        ("claude-code", "Claude Code"),
    ]
    reviews = Reviews(live_app.store)
    for harness, label in harnesses:
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
        live = page.get_by_role("button", name="Live " + label, exact=True)
        expect(live).to_be_visible()
        expect(live).to_contain_text(label)
    page.get_by_role("button", name="Batch", exact=True).focus()
    page.keyboard.press("Enter")
    expect(page.locator("#mode-batch")).to_have_attribute("aria-pressed", "true")
    live.focus()
    page.keyboard.press("Space")
    expect(live).to_have_attribute("aria-pressed", "true")
    assert live_app.store.workflow(DECK_ID).mode == "live"
