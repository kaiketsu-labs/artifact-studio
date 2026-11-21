"""Record human design decisions on the presentation the reviewer actually sees."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Locator, Page, expect

from artifact_studio.models import SlidePatch
from tests.integration.multiplayer_fixture import (
    WORKSPACE,
    routed_multiplayer_context,
    running_multiplayer_app,
)
from tests.integration.support import DECK_ID, RunningApp, markup
from tests.test_multiplayer_server import accept, grant, human

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def open_review(page: Page) -> Locator:
    page.get_by_role("button", name="Menu", exact=True).click()
    page.get_by_role("menuitem", name="Review visual direction").click()
    panel = page.get_by_role("dialog", name="Review visual direction")
    expect(panel.locator("details")).to_be_visible()
    return panel


def test_first_delivery_and_factual_followup(
    page: Page, live_app: RunningApp, artifacts: Path
) -> None:
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    panel = open_review(page)
    submit = panel.get_by_role("button", name="Record review")
    expect(submit).to_be_disabled()
    panel.get_by_role("radio", name="First draft").check()
    panel.get_by_role("radio", name="Change direction").check()
    expect(panel.get_by_text("Use slide feedback to describe")).to_be_visible()
    panel.get_by_role("checkbox", name="Layout or legibility").check()
    panel.screenshot(path=str(artifacts / "review-first-draft.png"))
    submit.click()
    expect(panel.get_by_role("status")).to_contain_text("Your first-draft review")
    expect(panel.locator('[data-review-value="visual_redirect_rounds"]')).to_have_text("1")
    page.keyboard.press("Escape")
    expect(page.get_by_role("button", name="Menu", exact=True)).to_be_focused()

    slide = live_app.store.view(DECK_ID).slides[0]
    _ = live_app.store.revise_view(
        DECK_ID,
        slide.id,
        slide.expected_hash,
        "fictional-revision",
        SlidePatch(html=markup("Next")),
    )
    page.reload()
    panel = open_review(page)
    expect(panel.get_by_role("radio", name="First draft")).to_be_hidden()
    panel.get_by_role("checkbox", name="Factual corrections").check()
    submit.click()
    expect(panel.get_by_role("status")).to_contain_text("Your review of this revision")
    for name, value in (
        ("first_delivery", "Change requested"),
        ("visual_redirect_rounds", "1"),
        ("factual_correction_rounds", "1"),
        ("legibility_correction_rounds", "1"),
        ("agent_self_revisions", "0"),
    ):
        expect(panel.locator(f'[data-review-value="{name}"]')).to_have_text(value)


def test_hosted_commenter_reviews_and_viewer_reads_history(
    browser: Browser, tmp_path: Path, artifacts: Path
) -> None:
    with (
        running_multiplayer_app(tmp_path / "hosted", artifacts / "hosted.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "bob") as bob,
        human(app, "alice") as alice,
        routed_multiplayer_context(browser, app, "bob") as commenter,
        routed_multiplayer_context(browser, app, "alice") as viewer,
        routed_multiplayer_context(browser, app, "owner") as owner_context,
    ):
        accept(bob, grant(owner, "bob", app.deck_id, role="comment"))
        accept(alice, grant(owner, "alice", app.deck_id, role="view"))
        reader = viewer.new_page()
        reader.goto(f"{WORKSPACE}/?deck={app.deck_id}")
        panel = open_review(reader)
        expect(panel.get_by_role("button", name="Record review")).to_be_hidden()
        expect(panel.locator('[data-review-value="first_delivery"]')).to_have_text("Unrated")
        reader.keyboard.press("Escape")

        writer = commenter.new_page()
        writer.goto(f"{WORKSPACE}/?deck={app.deck_id}#next")
        editor_panel = open_review(writer)
        editor_panel.get_by_role("radio", name="First draft").check()
        editor_panel.get_by_role("radio", name="Keep this direction").check()
        editor_panel.get_by_role("checkbox", name="Layout or legibility").check()
        editor_panel.get_by_role("button", name="Record review").click()
        expect(editor_panel.get_by_role("status")).to_contain_text("Your first-draft review")

        owner_page = owner_context.new_page()
        owner_page.goto(f"{WORKSPACE}/?deck={app.deck_id}")
        owner_panel = open_review(owner_page)
        expect(owner_panel.get_by_role("status")).to_have_text("Reviewing the first draft.")
        expect(owner_panel.get_by_role("radio", name="First draft")).to_be_hidden()
        owner_panel.get_by_role("radio", name="Change direction").check()
        owner_panel.get_by_role("checkbox", name="Layout or legibility").check()
        owner_panel.get_by_role("checkbox", name="Factual corrections").check()
        owner_panel.get_by_role("button", name="Record review").click()
        expect(owner_panel.get_by_role("status")).to_contain_text("change requested")
        expect(owner_panel.get_by_role("button", name="Record review")).to_be_hidden()

        writer.keyboard.press("Escape")
        editor_panel = open_review(writer)
        expect(editor_panel.get_by_role("status")).to_contain_text(
            "Your first-draft review · accepted"
        )
        expect(editor_panel.locator('[data-review-value="first_delivery"]')).to_have_text(
            "Change requested"
        )
        expect(
            editor_panel.locator('[data-review-value="legibility_correction_rounds"]')
        ).to_have_text("1")

        editor_panel.screenshot(path=str(artifacts / "personal-and-team-review.png"))
        writer.screenshot(path=str(artifacts / "team-review-editor.png"))

        reader.set_viewport_size({"width": 390, "height": 800})
        reader.emulate_media(color_scheme="dark")
        panel = open_review(reader)
        expect(panel.get_by_role("status")).to_contain_text(
            "Team review of this delivery · change requested"
        )
        expect(panel.locator('[data-review-value="first_delivery"]')).to_have_text(
            "Change requested"
        )
        expect(panel.locator('[data-review-value="visual_redirect_rounds"]')).to_have_text("1")
        expect(panel.locator('[data-review-value="factual_correction_rounds"]')).to_have_text("1")
        expect(panel.get_by_role("button", name="Record review")).to_be_hidden()
        expect(panel.locator("details")).to_be_in_viewport()
        panel.screenshot(path=str(artifacts / "review-history-dark.png"))
