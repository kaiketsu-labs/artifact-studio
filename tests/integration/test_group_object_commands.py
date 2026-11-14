"""Group edit requests remain provisional and anchored to the reviewed slide."""

import json
import os

import pytest
from playwright.sync_api import Page, expect

from artifact_studio.models import ElementGroupTarget
from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_browser import open_editor

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def select_objects(page: Page) -> None:
    """Select two distinct slide objects with the normal additive gesture."""
    frame = page.frame_locator("#preview")
    frame.get_by_role("heading", level=1).click()
    frame.get_by_text("Create, review and present.").click(modifiers=["Shift"])
    expect(page.locator("#target-members")).to_be_visible()


def group_menu(page: Page) -> None:
    """Open the selected-object menu without changing the selection."""
    page.frame_locator("#preview").get_by_role("heading", level=1).click(button="right")
    expect(page.get_by_role("menu", name="Selected objects")).to_be_visible()


def test_group_delete_undo_cancel_and_send(page: Page, live_app: RunningApp) -> None:
    """Only an explicit send delivers one request with the original targets."""
    original_hash = live_app.store.content_hash(DECK_ID, live_app.store.read(DECK_ID).slides[0])
    open_editor(page, live_app)
    select_objects(page)
    frame = page.frame_locator("#preview")
    heading = frame.get_by_role("heading", level=1)
    paragraph = frame.get_by_text("Create, review and present.")
    heading.press("Shift+F10")
    expect(page.get_by_role("menu", name="Selected objects")).to_be_visible()
    page.keyboard.press("Escape")
    page.keyboard.press("Delete")
    expect(heading).to_be_hidden()
    toolbar = page.get_by_role("toolbar", name="Selection edit")
    toolbar.get_by_role("button", name="Undo").focus()
    page.keyboard.press("Escape")
    expect(heading).to_be_visible()
    group_menu(page)
    page.get_by_role("menuitem", name="Delete selection").click()
    expect(heading).to_be_hidden()
    expect(paragraph).to_be_hidden()
    toolbar.get_by_role("button", name="Undo").click()
    expect(heading).to_be_visible()
    expect(paragraph).to_be_visible()
    toolbar.get_by_role("button", name="Redo").click()
    expect(heading).to_be_hidden()
    toolbar.get_by_role("button", name="Cancel edit").click()
    expect(heading).to_be_visible()
    assert live_app.store.comments(DECK_ID) == []
    page.keyboard.press("Delete")
    expect(heading).to_be_hidden()
    toolbar.get_by_role("button", name="Send edit").click()
    expect(toolbar).to_contain_text("Edit saved")
    comments = live_app.store.comments(DECK_ID)
    assert len(comments) == 1
    comment = comments[0]
    assert json.loads(comment.message) == {
        "action": "edit_selection",
        "operations": [{"action": "delete"}],
    }
    assert isinstance(comment.target, ElementGroupTarget)
    assert len(comment.target.elements) == 2
    assert all("visibility" not in element.html for element in comment.target.elements)
    assert comment.content_hash == original_hash
    assert comment.edit_intent is None and comment.released
    assert [event.event_id for event in live_app.store.feedback_feed(DECK_ID).events] == [
        comment.event_id
    ]
    toolbar.get_by_role("button", name="Dismiss").click()
    expect(heading).to_be_visible()
    expect(paragraph).to_be_visible()


def test_group_alignment_is_labeled_request_only(page: Page, live_app: RunningApp) -> None:
    """Alignment is presented as an agent request rather than a simulated preview."""
    open_editor(page, live_app)
    select_objects(page)
    group_menu(page)
    page.get_by_role("menuitem", name="Ask agent to align…").click()
    page.get_by_role("menuitem", name="Align left").click()
    toolbar = page.get_by_role("toolbar", name="Selection edit")
    expect(toolbar).to_contain_text("alignment is not previewed")
    toolbar.get_by_role("button", name="Cancel edit").click()
    assert live_app.store.comments(DECK_ID) == []


def test_group_arrange_in_normal_flow_is_request_only(page: Page, live_app: RunningApp) -> None:
    """Normal-flow objects receive an honest arrangement request without moving."""
    open_editor(page, live_app)
    select_objects(page)
    frame = page.frame_locator("#preview")
    heading = frame.get_by_role("heading", level=1)
    original = heading.bounding_box()
    group_menu(page)
    page.get_by_role("menuitem", name="Arrange…").click()
    page.get_by_role("menuitem", name="Bring to front").click()
    toolbar = page.get_by_role("toolbar", name="Selection edit")
    expect(toolbar).to_contain_text("arrangement is not previewed")
    assert heading.bounding_box() == original
    toolbar.get_by_role("button", name="Undo").click()
    toolbar.get_by_role("button", name="Redo").click()
    toolbar.get_by_role("button", name="Send edit").click()
    expect(toolbar).to_contain_text("Edit saved")
    comments = live_app.store.comments(DECK_ID)
    assert len(comments) == 1
    assert json.loads(comments[0].message)["operations"] == [
        {"action": "front", "request_only": True}
    ]
