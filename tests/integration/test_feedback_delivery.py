"""Make Batch delivery understandable before sending the first comment."""

import os
import re
from pathlib import Path
from typing import Literal

import pytest
from playwright.sync_api import Page, expect

from artifact_studio.models import FeedbackFeed
from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_browser import open_editor

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


@pytest.mark.parametrize("width,theme", [(1280, "light"), (950, "dark")])
def test_initial_batch_explains_queueing_and_releases_only_on_send(
    page: Page,
    live_app: RunningApp,
    artifacts: Path,
    width: int,
    theme: Literal["light", "dark"],
) -> None:
    """Queue from the keyboard, retain files, release explicitly, then return to Live."""
    _ = live_app.post(f"/api/decks/{DECK_ID}/feedback-mode", {"mode": "batch"})
    page.set_viewport_size({"width": width, "height": 800})
    page.emulate_media(color_scheme=theme)
    open_editor(page, live_app)
    open_chat = page.get_by_role("button", name="Open chat", exact=True)
    if open_chat.is_visible():
        open_chat.click()
    else:
        page.get_by_role("separator", name="Resize chat panel").press("Home")
    expect(page.locator("html")).to_have_attribute("data-theme", theme)
    expect(page.locator("#mode-batch")).to_have_attribute("aria-pressed", "true")

    add = page.get_by_role("button", name="Add to batch", exact=True)
    helper = page.locator("#batch-delivery-help")
    feedback = page.get_by_label("Your feedback", exact=True)
    expect(add).to_be_in_viewport()
    expect(add).to_have_attribute("title", re.compile("^Add to batch"))
    expect(add.locator("svg")).to_be_visible()
    expect(helper).to_have_text("Comments stay private until you choose Send batch.")
    expect(helper).to_be_in_viewport()
    expect(feedback).to_have_attribute("aria-describedby", "batch-delivery-help")
    expect(page.locator("#send-batch")).to_be_hidden()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.locator(".discussion").evaluate(
        "element => element.scrollWidth <= element.clientWidth"
    )
    _ = page.screenshot(path=str(artifacts / "initial-batch.png"), full_page=True)

    feedback.fill("Clarify the title.")
    feedback.press("Shift+Enter")
    page.keyboard.insert_text("Keep the source.")
    message = "Clarify the title.\nKeep the source."
    expect(feedback).to_have_value(message)
    expect(page.locator("#feedback-list .comment.user")).to_have_count(0)
    page.locator("#feedback-file-input").set_input_files(
        {"name": "batch-context.txt", "mimeType": "text/plain", "buffer": b"Title context"}
    )
    expect(page.locator("#feedback-files")).to_contain_text("batch-context.txt")
    feedback.press("Enter")
    expect(page.locator("#feedback-list .comment.user .chat-markdown")).to_have_text(
        message, use_inner_text=True
    )
    expect(page.locator(".comment-pending")).to_have_text("Not sent")
    expect(page.locator("#feedback-list").get_by_role("link")).to_contain_text("batch-context.txt")
    expect(feedback).to_have_value("")
    expect(page.locator("#feedback-files")).to_be_empty()
    expect(page.get_by_role("button", name="Send batch (1)", exact=True)).to_be_in_viewport()
    assert not live_app.call("read_feedback", deck_id=DECK_ID)["events"]
    _ = page.screenshot(path=str(artifacts / "queued-batch.png"), full_page=True)

    page.get_by_role("button", name="Send batch (1)", exact=True).click()
    expect(page.locator(".comment-pending")).to_have_count(0)
    expect(page.locator("#send-batch")).to_be_hidden()
    feed = FeedbackFeed.model_validate(live_app.call("read_feedback", deck_id=DECK_ID))
    assert len(feed.events) == 1 and feed.events[0].message == message
    assert feed.events[0].released and feed.events[0].batch_id
    assert [file.name for file in feed.events[0].files] == ["batch-context.txt"]

    page.locator("#mode-live").click()
    expect(page.locator("#mode-live")).to_have_attribute("aria-pressed", "true")
    expect(helper).to_be_hidden()
    send = page.get_by_role("button", name="Send feedback", exact=True)
    expect(send).to_be_in_viewport()
    expect(send.locator("span")).to_have_count(0)
    expect(send).to_have_attribute("aria-keyshortcuts", "Enter Meta+Enter Control+Enter")
    feedback.fill("Send immediately in Live.")
    send.click()
    expect(feedback).to_have_value("")
    expect(page.locator(".comment-pending")).to_have_count(0)
    feed = FeedbackFeed.model_validate(live_app.call("read_feedback", deck_id=DECK_ID))
    assert [event.message for event in feed.events] == [message, "Send immediately in Live."]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.locator(".discussion").evaluate(
        "element => element.scrollWidth <= element.clientWidth"
    )
    _ = page.screenshot(path=str(artifacts / "live-feedback.png"), full_page=True)
