"""Real MCP questions, per-slide indicators and human answers in the browser."""

import json
import os
import re
import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_browser import open_editor

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("SLIDE_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def ask(
    live_app: RunningApp,
    request_id: str = "browser-question-001",
    question: str = "What should this slide focus on?",
    options: list[str] | None = None,
) -> int:
    """Create the actual agent event through the stateless MCP interface."""
    source = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    result = live_app.call(
        "ask_slide_question",
        deck_id=DECK_ID,
        slide_id="cover",
        question=question,
        options=["Cost savings", "Customer outcomes", "Team capacity"]
        if options is None
        else options,
        expected_hash=source["expected_hash"],
        request_id=request_id,
    )
    return int(str(result["event_id"]))


@pytest.mark.parametrize("width,height", [(1280, 800), (1600, 1000)])
def test_question_choices_and_slide_markers(
    page: Page,
    live_app: RunningApp,
    artifacts: Path,
    width: int,
    height: int,
) -> None:
    """Make pending questions clear and resolve them without changing slide approval."""
    page.set_viewport_size({"width": width, "height": height})
    open_editor(page, live_app)
    page.get_by_role("button", name="Approve", exact=True).click()
    before = live_app.store.view(DECK_ID).slides[0]
    event_id = ask(live_app)
    card = page.locator(f'[data-question-id="{event_id}"]')
    expect(card).to_have_attribute("data-question-state", "open")
    expect(card.get_by_role("radio", name="Cost savings", exact=True)).to_be_visible()
    expect(card.get_by_role("textbox", name="Write your own answer")).to_be_visible()
    expect(card.get_by_role("button", name="Send answer", exact=True)).to_be_disabled()
    thumb = page.locator('.slide-thumb[data-id="cover"]')
    expect(thumb).to_have_attribute("aria-label", re.compile(".*1 question awaiting your answer.*"))
    expect(thumb.locator(".thumb-question")).to_be_visible()
    expect(page.locator('.slide-thumb[data-id="next"] .thumb-question')).to_be_hidden()
    page.locator("#chat-question-status").click()
    expect(card.locator(".question-prompt")).to_be_focused()
    _ = page.screenshot(path=str(artifacts / "pending-question.png"), full_page=True)
    before_theme = card.evaluate("element => getComputedStyle(element).backgroundColor")
    page.get_by_role("button", name="Menu", exact=True).click()
    page.get_by_role("menuitem", name="Appearance", exact=True).click()
    page.get_by_role("menuitemradio", name="Dark", exact=True).click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    assert card.evaluate("element => getComputedStyle(element).backgroundColor") != before_theme
    expect(card.get_by_role("button", name="Send answer", exact=True)).to_be_in_viewport()
    page.evaluate(
        """async () => {
          await document.fonts.ready;
          await new Promise(requestAnimationFrame);
          await new Promise(requestAnimationFrame);
        }"""
    )
    _ = page.screenshot(path=str(artifacts / "pending-question-dark.png"), full_page=True)
    choice = card.get_by_role("radio", name="Customer outcomes", exact=True)
    choice.check()
    choice.focus()
    page.keyboard.press("Tab")
    expect(card.get_by_role("textbox", name="Write your own answer")).to_be_focused()
    expect(choice).to_be_checked()
    page.keyboard.press("Tab")
    expect(card.get_by_role("button", name="Discard unsent answer", exact=True)).to_be_focused()
    page.keyboard.press("Tab")
    expect(card.get_by_role("button", name="Send answer", exact=True)).to_be_focused()
    page.keyboard.press("Enter")
    expect(card).to_have_attribute("data-question-state", "answered")
    expect(card.locator(".question-response")).to_be_hidden()
    expect(thumb.locator(".thumb-question")).to_be_hidden()
    expect(page.locator("#chat-question-status")).to_be_hidden()
    expect(page.locator(".comment.user .chat-markdown")).to_contain_text("Customer outcomes")
    feed = live_app.call("read_feedback", deck_id=DECK_ID)
    assert "Customer outcomes" in str(feed["events"])
    assert any(item.reply_to == event_id for item in live_app.store.feedback_feed(DECK_ID).events)
    after = live_app.store.view(DECK_ID).slides[0]
    assert after.approved and after.expected_hash == before.expected_hash
    _ = page.screenshot(path=str(artifacts / "answered-question.png"), full_page=True)


def test_free_answer_draft_survives_updates_slide_switch_reload_and_project(
    page: Page,
    live_app: RunningApp,
    artifacts: Path,
) -> None:
    """Keep an in-progress answer through live messages and a portable project round trip."""
    open_editor(page, live_app)
    event_id = ask(live_app)
    card = page.locator(f'[data-question-id="{event_id}"]')
    text = card.get_by_role("textbox", name="Write your own answer")
    text.fill("A mix of customer outcomes and practical next steps.")
    card.get_by_role("radio", name="Cost savings", exact=True).check()
    expect(text).to_have_value("A mix of customer outcomes and practical next steps.")
    expect(card.get_by_role("radio", name="Cost savings", exact=True)).to_be_checked()
    text.click()
    expect(card.get_by_role("radio", name="Write your own answer", exact=True)).to_be_checked()
    second = ask(live_app, request_id="browser-question-002", question="Who will present it?")
    expect(page.locator(f'[data-question-id="{second}"]')).to_be_visible()
    expect(text).to_be_focused()
    expect(text).to_have_value("A mix of customer outcomes and practical next steps.")
    page.locator('.slide-thumb[data-id="next"]').click()
    page.locator('.slide-thumb[data-id="cover"]').click()
    expect(text).to_have_value("A mix of customer outcomes and practical next steps.")
    _ = page.reload(wait_until="networkidle")
    expect(text).to_have_value("A mix of customer outcomes and practical next steps.")
    current = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    _ = live_app.call(
        "revise_slide",
        deck_id=DECK_ID,
        slide_id="cover",
        expected_hash=current["expected_hash"],
        request_id="question-slide-change",
        patch={"title": "An updated opening"},
    )
    expect(card.locator(".question-version")).to_have_text("Earlier version")
    expect(text).to_have_value("A mix of customer outcomes and practical next steps.")
    with page.expect_download() as downloaded:
        page.get_by_role("button", name="Menu", exact=True).click()
        page.get_by_role("menuitem", name="Save project", exact=True).click()
    project = downloaded.value.path()
    with zipfile.ZipFile(project) as archive:
        payload = json.loads(archive.read("project.json"))
        assert payload["client"]["question_answers"][str(event_id)]["text"].startswith("A mix")
    page.get_by_role("button", name="Menu", exact=True).click()
    page.get_by_role("menuitem", name="Open project", exact=True).click()
    page.locator("#project-file").set_input_files(project)
    expect(page.locator("#project-dialog")).not_to_be_visible()
    imported_id = page.locator("#deck-picker").input_value()
    assert imported_id != DECK_ID
    imported = next(
        item for item in live_app.store.view(imported_id).slides[0].feedback if item.question
    )
    imported_card = page.locator(f'[data-question-id="{imported.event_id}"]')
    expect(imported_card.get_by_role("textbox", name="Write your own answer")).to_have_value(
        "A mix of customer outcomes and practical next steps."
    )
    imported_card.get_by_role("button", name="Send answer", exact=True).click()
    expect(imported_card).to_have_attribute("data-question-state", "answered")
    assert any(
        item.message.startswith("A mix") and item.reply_to == imported.event_id
        for item in live_app.store.feedback_feed(imported_id).events
    )
    assert not live_app.store.feedback_feed(DECK_ID).events


def test_batch_answer_has_a_visible_queued_state_until_release(
    page: Page, live_app: RunningApp
) -> None:
    """A submitted local answer is not presented as delivered while Batch holds it."""
    open_editor(page, live_app)
    event_id = ask(live_app)
    page.locator("#mode-batch").click()
    card = page.locator(f'[data-question-id="{event_id}"]')
    card.get_by_role("textbox", name="Write your own answer").fill("PRIVATE_BATCH_BROWSER_ANSWER")
    card.get_by_role("button", name="Add answer to batch", exact=True).click()
    expect(card).to_have_attribute("data-question-state", "queued")
    expect(card.get_by_role("img", name="Answer queued — not sent to the agent")).to_be_visible()
    expect(page.locator(".comment.user .comment-pending")).to_have_text("Not sent")
    assert not live_app.call("read_feedback", deck_id=DECK_ID)["events"]
    assert "PRIVATE_BATCH_BROWSER_ANSWER" not in str(
        live_app.call("get_presentation", deck_id=DECK_ID)
    )
    page.locator("#send-batch").click()
    expect(card).to_have_attribute("data-question-state", "answered")
    assert "PRIVATE_BATCH_BROWSER_ANSWER" in str(
        live_app.call("read_feedback", deck_id=DECK_ID)["events"]
    )


def test_cancelled_question_preserves_unsent_text_and_can_discard_it(
    page: Page, live_app: RunningApp
) -> None:
    """Cancel obsolete questions without losing the human's in-progress answer."""
    open_editor(page, live_app)
    event_id = ask(live_app)
    card = page.locator(f'[data-question-id="{event_id}"]')
    card.get_by_role("textbox", name="Write your own answer").fill("My answer in progress")
    _ = live_app.call(
        "cancel_slide_question",
        deck_id=DECK_ID,
        slide_id="cover",
        event_id=event_id,
        request_id="cancel-browser-question",
    )
    expect(card).to_have_attribute("data-question-state", "cancelled")
    expect(card.get_by_role("textbox", name="Unsent answer")).to_have_value("My answer in progress")
    expect(card.get_by_role("button", name="Send answer", exact=True)).to_have_count(0)
    assert not page.evaluate("dispatchEvent(new Event('beforeunload', {cancelable: true}))")
    card.get_by_role("button", name="Discard unsent answer", exact=True).click()
    assert page.evaluate("dispatchEvent(new Event('beforeunload', {cancelable: true}))")
    assert not live_app.store.feedback_feed(DECK_ID).events


def test_free_only_markdown_question_stays_inert(page: Page, live_app: RunningApp) -> None:
    """A question can request only free text and renders Markdown through the safe chat renderer."""
    open_editor(page, live_app)
    event_id = ask(
        live_app,
        options=[],
        question=(
            "**What should change?**\n\n"
            "<script>window.questionExecuted=true</script>\n\n"
            "[Unsafe](javascript:alert(1))"
        ),
    )
    card = page.locator(f'[data-question-id="{event_id}"]')
    expect(card.locator("strong")).to_have_text("What should change?")
    expect(card.locator("script")).to_have_count(0)
    expect(card.locator('a[href^="javascript:"]')).to_have_count(0)
    assert page.evaluate("window.questionExecuted") is None
    card.get_by_role("textbox", name="Write your own answer").fill("Use a chart.")
    card.get_by_role("button", name="Send answer", exact=True).click()
    expect(card).to_have_attribute("data-question-state", "answered")
