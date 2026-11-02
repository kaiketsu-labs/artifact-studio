"""Exercise multi-object context selection against the real slide editor and feedback API."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from artifact_studio.models import ElementGroupTarget, ElementTarget
from tests.integration.support import DECK_ID, RunningApp, markup
from tests.integration.test_browser import open_editor

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_multiple_objects_toggle_restore_and_deliver_as_original_context(
    page: Page, live_app: RunningApp, artifacts: Path
) -> None:
    """Keep direct edits single-target while group context survives navigation and reload."""
    open_editor(page, live_app)
    preview = page.frame_locator("#preview")
    heading = preview.get_by_role("heading", level=1)
    body = preview.get_by_text("Create, review and present.", exact=True)
    heading.click()
    expect(page.locator("#element-tools")).to_be_visible()
    expect(page.locator("#selection-box")).to_be_visible()
    body.click(modifiers=["ControlOrMeta"])
    expect(page.locator("#target-label")).to_have_text("2 objects")
    expect(page.locator("#target-members .target-member")).to_have_count(2)
    expect(page.locator("#group-selection-boxes .element-box")).to_have_count(2)
    expect(page.locator("#element-tools")).to_be_hidden()
    expect(page.locator("#selection-box")).to_be_hidden()
    expect(page.locator("#hover-hint")).to_have_count(0)
    expect(page.locator("#objects-button")).to_have_attribute(
        "title", "Select slide objects · Cmd/Ctrl/Shift-click to add or remove"
    )
    body.click(modifiers=["ControlOrMeta"])
    expect(page.locator("#target-members")).to_be_hidden()
    expect(page.locator("#element-tools")).to_be_visible()
    body.click(modifiers=["Shift"])
    expect(page.locator("#target-label")).to_have_text("2 objects")

    page.locator("#objects-button").click()
    rows = page.locator("#object-panel [role=menuitemcheckbox]")
    expect(rows.filter(has_text="Heading")).to_have_attribute("aria-checked", "true")
    rows.filter(has_text="FICTIONAL CI FIXTURE").click(modifiers=["Shift"])
    expect(page.locator("#target-label")).to_have_text("3 objects")
    expect(page.locator("#object-panel")).to_be_visible()
    page.keyboard.press("Escape")
    page.locator("#target-members [data-remove-target='2']").click()
    expect(page.locator("#target-label")).to_have_text("2 objects")
    feedback = page.get_by_label("Your feedback", exact=True)
    feedback.fill("Align these two objects.")
    _ = page.screenshot(path=str(artifacts / "multi-object-context.png"), full_page=True)

    page.locator(".slide-thumb").nth(1).click()
    expect(page.locator("#target-members")).to_be_hidden()
    page.locator(".slide-thumb").nth(0).click()
    expect(page.locator("#target-label")).to_have_text("2 objects")
    _ = page.reload(wait_until="networkidle")
    expect(feedback).to_have_value("Align these two objects.")
    expect(page.locator("#group-selection-boxes .element-box")).to_have_count(2)
    feedback.press("Enter")
    expect(feedback).to_have_value("")
    expect(page.locator("#target-members")).to_be_hidden()
    sent = live_app.store.feedback_feed(DECK_ID).events[0]
    assert isinstance(sent.target, ElementGroupTarget)
    assert [item.quote for item in sent.target.elements] == [
        "Review together",
        "Create, review and present.",
    ]
    page.locator("#feedback-list .comment-target").click()
    expect(page.locator("#group-selection-boxes .element-box")).to_have_count(2)
    expect(page.locator("#element-tools")).to_be_hidden()
    body.click()
    expect(page.locator("#element-tools")).to_be_visible()
    expect(page.locator("#group-selection-boxes .element-box")).to_have_count(0)


def test_revision_keeps_group_draft_and_chip_removal_on_original_version(
    page: Page, live_app: RunningApp
) -> None:
    """Avoid highlighting stale anchors or mixing selections from different revisions."""
    open_editor(page, live_app)
    preview = page.frame_locator("#preview")
    preview.get_by_role("heading", level=1).click()
    preview.get_by_text("Create, review and present.", exact=True).click(modifiers=["Shift"])
    feedback = page.get_by_label("Your feedback", exact=True)
    feedback.fill("Keep this original context.")
    current = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    original_hash = current["content_hash"]
    _ = live_app.call(
        "revise_slide",
        deck_id=DECK_ID,
        slide_id="cover",
        expected_hash=current["expected_hash"],
        request_id="multi-object-new-revision",
        patch={"html": markup("New heading")},
    )
    _ = page.reload(wait_until="networkidle")
    expect(page.locator("#target-label")).to_have_text("2 objects · earlier version")
    expect(page.locator("#group-selection-boxes .element-box")).to_have_count(0)
    expect(page.locator("#element-tools")).to_be_hidden()
    preview.get_by_role("heading", level=1).click(modifiers=["Shift"])
    expect(page.locator("#toast")).to_contain_text("earlier version")
    expect(page.locator("#target-label")).to_have_text("2 objects · earlier version")
    page.locator("#target-members [data-remove-target='1']").click()
    expect(page.locator("#target-label")).to_contain_text("Review together · earlier version")
    expect(page.locator("#element-tools")).to_be_hidden()
    feedback.press("Enter")
    expect(feedback).to_have_value("")
    sent = live_app.store.feedback_feed(DECK_ID).events[0]
    assert isinstance(sent.target, ElementTarget)
    assert sent.target.quote == "Review together"
    assert sent.content_hash == original_hash
