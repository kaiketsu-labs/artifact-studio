"""Simulate browser dictation while real selection, drafts and feedback stay authoritative."""

import os
import re
from pathlib import Path
from zipfile import ZipFile

import pytest
from playwright.sync_api import Page, Route, expect

from artifact_studio.agent_client import object_value
from artifact_studio.models import ElementGroupTarget, ElementTarget
from artifact_studio.projects import ProjectBundle
from tests.integration.support import DECK_ID, PRIVATE_NOTE, RunningApp
from tests.integration.test_browser import open_editor, open_notes
from tests.integration.test_questions import ask
from tests.integration.voice_support import (
    install_voice,
    voice_end,
    voice_error,
    voice_result,
    voice_sessions,
)

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]

FEEDBACK_MIC = ".voice-input-button[data-voice-for='feedback-text']"
FEEDBACK_STOP = "#stop-feedback-voice"
SEGMENTS = "#voice-feedback-segments [data-voice-segment]"


def assert_composer_controls_fit(page: Page, selectors: list[str]) -> None:
    """Transcript growth must not push recording or send controls outside their composer."""
    form = page.locator("#feedback-form").bounding_box()
    assert form is not None
    for selector in selectors:
        control = page.locator(selector)
        expect(control).to_be_in_viewport()
        bounds = control.bounding_box()
        assert bounds is not None
        assert bounds["y"] >= form["y"]
        assert bounds["y"] + bounds["height"] <= form["y"] + form["height"] - 2
    expect(page.locator(".discussion-top")).to_be_in_viewport()


def test_dictation_pins_late_results_and_multiple_objects_before_batch_release(
    page: Page, live_app: RunningApp, artifacts: Path
) -> None:
    """Changing selection creates explicit contexts without sending or reassigning prior words."""
    install_voice(page)
    page.set_viewport_size({"width": 1280, "height": 800})
    open_editor(page, live_app)
    page.get_by_role("button", name="Batch", exact=True).click()
    original = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    preview = page.frame_locator("#preview")
    heading = preview.get_by_role("heading", level=1)
    body = preview.get_by_text("Create, review and present.", exact=True)
    heading.click()
    page.locator(FEEDBACK_MIC).click()
    expect(page.locator("#feedback-recording")).to_be_visible()
    expect(page.locator("#feedback-text")).to_be_hidden()
    expect(page.locator(FEEDBACK_MIC)).to_be_hidden()
    expect(page.locator("#send-feedback-voice")).to_have_attribute(
        "aria-label", "Add dictated feedback to batch"
    )
    voice_result(page, "Unconfirmed heading words", final=False)
    expect(page.locator(SEGMENTS)).to_have_count(0)
    expect(page.get_by_label("Your feedback", exact=True)).to_have_value("")
    assert live_app.store.comments(DECK_ID) == []
    voice_result(page, "Make the heading shorter.")
    expect(page.locator(SEGMENTS)).to_have_count(1)
    expect(page.locator("#send-feedback")).to_be_disabled()

    body.click()
    voice_result(page, "Keep its emphasis.", session=0)
    voice_end(page, session=0)
    page.wait_for_function("window.__speech.sessions.length === 2")
    voice_result(page, "Increase the body text.", session=1)
    heading.click(modifiers=["Shift"])
    expect(page.locator("#target-label")).to_have_text("2 objects")
    voice_end(page, session=1)
    page.wait_for_function("window.__speech.sessions.length === 3")
    voice_result(page, "Align both objects.", session=2)
    expect(page.locator("#voice-feedback-segments")).to_be_hidden()
    assert_composer_controls_fit(
        page, ["#cancel-feedback-voice", FEEDBACK_STOP, "#send-feedback-voice"]
    )
    _ = page.screenshot(path=str(artifacts / "voice-listening-light.png"), full_page=True)
    page.evaluate("document.documentElement.dataset.theme = 'dark'")
    _ = page.screenshot(path=str(artifacts / "voice-listening-dark.png"), full_page=True)
    page.evaluate("document.documentElement.dataset.theme = 'light'")
    voice_result(page, "Unconfirmed group fragment", final=False, session=2)
    page.locator(FEEDBACK_STOP).click()
    voice_end(page, session=2)
    expect(page.locator(SEGMENTS)).to_have_count(3)
    expect(page.locator("#send-feedback")).to_be_enabled()
    assert_composer_controls_fit(page, ["#attach-feedback", FEEDBACK_MIC, "#send-feedback"])
    assert live_app.store.comments(DECK_ID) == []
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    _ = page.screenshot(path=str(artifacts / "voice-context-light.png"), full_page=True)
    page.evaluate("document.documentElement.dataset.theme = 'dark'")
    _ = page.screenshot(path=str(artifacts / "voice-context-dark.png"), full_page=True)
    page.locator("#send-feedback").click()
    expect(page.locator(SEGMENTS)).to_have_count(0)
    comments = live_app.store.comments(DECK_ID)
    assert [item.message for item in comments] == [
        "Make the heading shorter. Keep its emphasis.",
        "Increase the body text.",
        "Align both objects.",
    ]
    first, second, third = comments
    assert isinstance(first.target, ElementTarget) and first.target.quote == "Review together"
    assert isinstance(second.target, ElementTarget)
    assert second.target.quote == "Create, review and present."
    assert isinstance(third.target, ElementGroupTarget)
    assert [item.quote for item in third.target.elements] == [
        "Create, review and present.",
        "Review together",
    ]
    assert all(item.content_hash == original["content_hash"] for item in comments)
    assert all(not item.released for item in comments)
    assert live_app.store.feedback_feed(DECK_ID).events == []
    assert live_app.store.read(DECK_ID).slides[0].notes == PRIVATE_NOTE
    page.get_by_role("button", name="Send batch (3)", exact=True).click()
    expect(page.locator("#send-batch")).to_be_hidden()
    assert len(live_app.store.feedback_feed(DECK_ID).events) == 3


def test_navigation_and_reload_keep_dictated_feedback_on_its_original_slide(
    page: Page, live_app: RunningApp
) -> None:
    """A stopped recognizer can finish the old draft but cannot write into the next slide."""
    install_voice(page)
    open_editor(page, live_app)
    page.frame_locator("#preview").get_by_role("heading", level=1).click()
    page.locator(FEEDBACK_MIC).click()
    voice_result(page, "Original slide instruction.")
    page.locator('.slide-thumb[data-id="next"]').click()
    voice_result(page, "Delayed original words.", session=0)
    voice_end(page, session=0)
    expect(page.locator("#chat-slide-title")).to_have_text("Next steps")
    voice_result(page, "Retired callback must be ignored.", session=0)
    expect(page.locator(SEGMENTS)).to_have_count(0)
    expect(page.get_by_label("Your feedback", exact=True)).to_have_value("")
    assert voice_sessions(page) == 1
    page.locator('.slide-thumb[data-id="cover"]').click()
    expect(page.locator(SEGMENTS)).to_have_count(1)
    expect(page.locator(f"{SEGMENTS} textarea")).to_have_value(
        "Original slide instruction. Delayed original words."
    )
    _ = page.reload(wait_until="networkidle")
    expect(page.locator(f"{SEGMENTS} textarea")).to_have_value(
        "Original slide instruction. Delayed original words."
    )
    assert voice_sessions(page) == 0
    page.locator("#send-feedback").click()
    expect(page.locator(SEGMENTS)).to_have_count(0)
    comments = live_app.store.comments(DECK_ID)
    assert len(comments) == 1 and comments[0].slide_id == "cover"
    assert isinstance(comments[0].target, ElementTarget)
    assert comments[0].target.quote == "Review together"


def test_partial_dictation_delivery_retains_retry_ids_without_duplicate_comments(
    page: Page, live_app: RunningApp
) -> None:
    """An already accepted response loss must retry the same comment ID, then finish the queue."""
    install_voice(page)
    open_editor(page, live_app)
    preview = page.frame_locator("#preview")
    preview.get_by_role("heading", level=1).click()
    page.locator(FEEDBACK_MIC).click()
    voice_result(page, "First target.")
    preview.get_by_text("Create, review and present.", exact=True).click()
    voice_end(page, session=0)
    page.wait_for_function("window.__speech.sessions.length === 2")
    voice_result(page, "Second target.")
    page.locator(FEEDBACK_STOP).click()
    voice_end(page)
    accepted_ids: list[str] = []

    def lose_once(route: Route) -> None:
        body = object_value(route.request.post_data_json)
        accepted_ids.append(str(body["request_id"]))
        response = route.fetch()
        if len(accepted_ids) == 1:
            # A valid HTTP response with invalid JSON loses acknowledgement after server commit.
            route.fulfill(status=200, content_type="application/json", body="response lost")
        else:
            route.fulfill(response=response)

    page.route("**/api/decks/*/slides/*/feedback", lose_once)
    page.locator("#send-feedback").click()
    expect(page.locator("#feedback-error")).not_to_be_empty()
    assert len(live_app.store.comments(DECK_ID)) == 1
    expect(page.locator(SEGMENTS)).to_have_count(2)
    page.locator("#send-feedback").click()
    expect(page.locator(SEGMENTS)).to_have_count(0)
    assert accepted_ids[0] == accepted_ids[1]
    assert [item.message for item in live_app.store.comments(DECK_ID)] == [
        "First target.",
        "Second target.",
    ]


def test_saving_project_waits_for_the_current_recognizers_final_words(
    page: Page, live_app: RunningApp
) -> None:
    """A portable handoff must include confirmed words received during the save's stop drain."""
    install_voice(page)
    open_editor(page, live_app)
    original = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    page.frame_locator("#preview").get_by_role("heading", level=1).click()
    page.locator(FEEDBACK_MIC).click()
    voice_result(page, "First confirmed instruction.")
    with page.expect_download() as downloaded:
        page.get_by_role("button", name="Menu", exact=True).click()
        page.get_by_role("menuitem", name="Save presentation", exact=True).click()
        voice_result(page, "Final words during save.")
        voice_end(page)
    with ZipFile(downloaded.value.path()) as archive:
        project = ProjectBundle.model_validate_json(archive.read("project.json"))
    segments = project.client.feedback["cover"].voice_segments
    assert len(segments) == 1
    assert segments[0].message == "First confirmed instruction. Final words during save."
    assert segments[0].content_hash == original["content_hash"]
    assert isinstance(segments[0].target, ElementTarget)
    assert segments[0].target.quote == "Review together"
    assert live_app.store.comments(DECK_ID) == []


def test_dictating_into_a_queued_section_keeps_listening_and_its_original_target(
    page: Page, live_app: RunningApp
) -> None:
    """Editing a previous voice section cannot stop its own microphone or retarget its words."""
    install_voice(page)
    open_editor(page, live_app)
    preview = page.frame_locator("#preview")
    preview.get_by_role("heading", level=1).click()
    page.locator(FEEDBACK_MIC).click()
    voice_result(page, "Recorded heading instruction.")
    page.locator(FEEDBACK_STOP).click()
    voice_end(page)
    preview.get_by_text("Create, review and present.", exact=True).click()
    section = page.locator(SEGMENTS)
    section.hover()
    mic = section.locator(".voice-input-button")
    mic.click()
    voice_result(page, "First amendment.")
    expect(mic).to_have_attribute("aria-pressed", "true")
    voice_result(page, "Second amendment.")
    expect(mic).to_have_attribute("aria-pressed", "true")
    mic.click()
    voice_end(page)
    expect(section.locator("textarea")).to_have_value(
        "Recorded heading instruction. First amendment. Second amendment."
    )
    expect(page.get_by_label("Your feedback", exact=True)).to_have_value("")
    page.locator("#send-feedback").click()
    expect(page.locator(SEGMENTS)).to_have_count(0)
    sent = live_app.store.comments(DECK_ID)
    assert len(sent) == 1
    assert sent[0].message == "Recorded heading instruction. First amendment. Second amendment."
    assert isinstance(sent[0].target, ElementTarget) and sent[0].target.quote == "Review together"


def test_cancel_recording_preserves_typed_text_and_earlier_dictation(
    page: Page, live_app: RunningApp
) -> None:
    """Cancel discards just the current recording, including its changed-selection sections."""
    install_voice(page)
    open_editor(page, live_app)
    preview = page.frame_locator("#preview")
    heading = preview.get_by_role("heading", level=1)
    heading.click()
    field = page.get_by_label("Your feedback", exact=True)
    field.fill("Keep the typed draft.")
    page.locator(FEEDBACK_MIC).click()
    voice_result(page, "Keep the earlier dictation.")
    open_notes(page)
    notes = page.get_by_label("Speaker notes", exact=True)
    notes.evaluate(
        "element => element.setSelectionRange(element.value.length,element.value.length)"
    )
    note_mic = page.locator(".voice-input-button[data-voice-for='notes']")
    note_mic.click()
    voice_end(page, session=0)
    page.wait_for_function("window.__speech.sessions.length === 2")
    voice_result(page, "Keep the dictated speaker note.")
    note_mic.click()
    voice_end(page, session=1)
    preview.get_by_text("Create, review and present.", exact=True).click()
    page.locator(FEEDBACK_MIC).click()
    voice_result(page, "Discard this body instruction.")
    heading.click(modifiers=["Shift"])
    voice_end(page, session=2)
    page.wait_for_function("window.__speech.sessions.length === 4")
    voice_result(page, "Discard this group instruction.")
    expect(page.locator("#feedback-recording")).to_be_visible()
    page.locator("#cancel-feedback-voice").click()
    voice_result(page, "Retired after cancel.", session=3)
    voice_end(page, session=3)
    expect(page.locator("#feedback-recording")).to_be_hidden()
    expect(field).to_have_value("Keep the typed draft.")
    expect(field).to_be_visible()
    expect(page.locator(SEGMENTS)).to_have_count(1)
    expect(page.locator(f"{SEGMENTS} textarea")).to_have_value("Keep the earlier dictation.")
    assert live_app.store.comments(DECK_ID) == []


def test_recording_send_button_waits_for_final_words_then_sends_explicitly(
    page: Page, live_app: RunningApp
) -> None:
    """The recording bar sends only after a deliberate click and a completed stop drain."""
    install_voice(page)
    open_editor(page, live_app)
    original = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    page.frame_locator("#preview").get_by_role("heading", level=1).click()
    page.locator(FEEDBACK_MIC).click()
    voice_result(page, "Confirmed instruction.")
    assert live_app.store.comments(DECK_ID) == []
    page.locator("#send-feedback-voice").click()
    expect(page.locator("#feedback-recording")).to_be_visible()
    voice_result(page, "Final words on send.")
    assert live_app.store.comments(DECK_ID) == []
    voice_end(page)
    expect(page.locator("#feedback-recording")).to_be_hidden()
    expect(page.locator(SEGMENTS)).to_have_count(0)
    expect(page.locator("#send-feedback")).to_be_disabled()
    comments = live_app.store.comments(DECK_ID)
    assert len(comments) == 1
    assert comments[0].message == "Confirmed instruction. Final words on send."
    assert comments[0].content_hash == original["content_hash"]
    assert isinstance(comments[0].target, ElementTarget)
    assert comments[0].target.quote == "Review together"


def test_navigation_during_recording_send_cannot_submit_the_next_slides_draft(
    page: Page, live_app: RunningApp
) -> None:
    """Navigation and Send share a drain, but no newly selected draft may be submitted."""
    install_voice(page)
    open_editor(page, live_app)
    page.locator('.slide-thumb[data-id="next"]').click()
    page.locator("#feedback-text").fill("Unsent next-slide draft")
    page.locator('.slide-thumb[data-id="cover"]').click()
    page.locator("#feedback-text").fill("Unsent cover draft")
    page.locator(FEEDBACK_MIC).click()
    voice_result(page, "Cover voice draft.")
    page.evaluate(
        """async () => {
          document.querySelector('#send-feedback-voice').click();
          document.querySelector('.slide-thumb[data-id="next"]').click();
          await Promise.resolve();
          window.__speech.sessions.at(-1).end();
        }"""
    )
    expect(page.locator("#chat-slide-title")).to_have_text("Next steps")
    expect(page.locator("#feedback-text")).to_have_value("Unsent next-slide draft")
    assert live_app.store.comments(DECK_ID) == []
    page.locator('.slide-thumb[data-id="cover"]').click()
    expect(page.locator("#feedback-text")).to_have_value("Unsent cover draft")
    expect(page.locator(f"{SEGMENTS} textarea")).to_have_value("Cover voice draft.")


@pytest.mark.parametrize("ending", ["cancel", "error", "timeout", "unfinished"])
def test_interrupted_recording_send_keeps_drafts_unsent(
    page: Page, live_app: RunningApp, ending: str
) -> None:
    """An interrupted Send drain cannot send typed or previously confirmed feedback."""
    install_voice(page)
    open_editor(page, live_app)
    page.locator("#feedback-text").fill("Typed draft stays")
    page.locator(FEEDBACK_MIC).click()
    voice_result(page, "Confirmed voice draft.")
    voice_result(page, "Unfinished words", final=False)
    # Keep interruption and Send in one browser turn, before the bounded drain timer.
    page.evaluate(
        """ending => {
          document.querySelector('#send-feedback-voice').click();
          if (ending === 'cancel') document.querySelector('#cancel-feedback-voice').click();
          if (ending === 'error') window.__speech.sessions.at(-1).fail('network');
          if (ending === 'unfinished') window.__speech.sessions.at(-1).end();
        }""",
        ending,
    )
    expect(page.locator("#feedback-recording")).to_be_hidden()
    expect(page.locator("#toast")).to_contain_text("without sending")
    expect(page.locator("#feedback-text")).to_have_value("Typed draft stays")
    expect(page.locator(SEGMENTS)).to_have_count(0 if ending == "cancel" else 1)
    assert live_app.store.comments(DECK_ID) == []
    voice_result(page, "Late retired words")
    assert live_app.store.comments(DECK_ID) == []
    # A later, explicit ordinary send remains available after review.
    page.locator("#send-feedback").click()
    expect(page.locator("#feedback-text")).to_have_value("")
    expected = [] if ending == "cancel" else ["Confirmed voice draft."]
    assert [item.message for item in live_app.store.comments(DECK_ID)] == [
        *expected,
        "Typed draft stays",
    ]


def test_generic_dictation_preserves_caret_and_dynamic_question_fields(
    page: Page, live_app: RunningApp
) -> None:
    """Ordinary text fields receive final text through their normal input/save paths."""
    install_voice(page)
    open_editor(page, live_app)
    page.get_by_role("button", name="Menu", exact=True).click()
    page.get_by_role("menuitem", name="New presentation", exact=True).click()
    title = page.locator("#new-title")
    title.fill("Before after")
    title.evaluate("element => element.setSelectionRange(7,7)")
    page.locator(".voice-input-button[data-voice-for='new-title']").click()
    voice_result(page, "spoken")
    page.locator(".voice-input-button[data-voice-for='new-title']").click()
    voice_end(page)
    expect(title).to_have_value("Before spoken after")
    brief = page.locator("#new-brief")
    page.locator(".voice-input-button[data-voice-for='new-brief']").click()
    voice_result(page, "Brief from dictation.")
    page.locator(".voice-input-button[data-voice-for='new-brief']").click()
    voice_end(page)
    expect(brief).to_have_value("Brief from dictation.")
    page.locator("#close-new").click()
    open_notes(page)
    notes = page.get_by_label("Speaker notes", exact=True)
    notes.evaluate(
        "element => element.setSelectionRange(element.value.length,element.value.length)"
    )
    note_mic = page.locator(".voice-input-button[data-voice-for='notes']")
    note_mic.click()
    voice_result(page, "Speaker follow-up.")
    note_mic.click()
    voice_end(page)
    expect(notes).to_have_value(PRIVATE_NOTE + " Speaker follow-up.")
    expect(page.locator("#notes-state")).to_have_text("Saved")
    assert live_app.store.read(DECK_ID).slides[0].notes == PRIVATE_NOTE + " Speaker follow-up."
    event_id = ask(live_app, request_id="voice-question-dynamic")
    card = page.locator(f'[data-question-id="{event_id}"]')
    answer = card.get_by_role("textbox", name="Write your own answer")
    expect(answer).to_be_visible()
    mic = card.locator(".voice-input-button")
    mic.click()
    voice_result(page, "Customer outcomes.")
    answer.press("Enter")
    expect(card).to_have_attribute("data-question-state", "open")
    assert live_app.store.comments(DECK_ID)[-1].author == "agent"
    mic.click()
    voice_end(page)
    expect(answer).to_have_value("Customer outcomes.")
    expect(card).to_have_attribute("data-question-state", "open")
    card.get_by_role("button", name="Send answer", exact=True).click()
    expect(card).to_have_attribute("data-question-state", "answered")
    assert live_app.store.comments(DECK_ID)[-1].message == "Customer outcomes."


def test_permission_failure_stops_without_restart_or_submitting_text(
    page: Page, live_app: RunningApp
) -> None:
    """Permission denial must be recoverable and cannot create a recognition restart loop."""
    install_voice(page)
    open_editor(page, live_app)
    page.locator(FEEDBACK_MIC).click()
    voice_result(page, "Never confirmed", final=False)
    voice_error(page, "not-allowed")
    voice_end(page)
    expect(page.locator(FEEDBACK_MIC)).to_have_attribute("aria-pressed", "false")
    expect(page.locator(SEGMENTS)).to_have_count(0)
    assert voice_sessions(page) == 1
    assert live_app.store.comments(DECK_ID) == []


def test_unsupported_browser_explains_dictation_and_keeps_typing_available(
    page: Page, live_app: RunningApp
) -> None:
    """Unsupported recognition leaves normal authoring usable without pretending to record."""
    install_voice(page, supported=False)
    open_editor(page, live_app)
    mic = page.locator(FEEDBACK_MIC)
    expect(mic).to_be_disabled()
    expect(mic).to_have_attribute(
        "title", re.compile("unavailable|unsupported|not supported", re.I)
    )
    mic.focus()
    expect(mic).to_be_focused()
    expect(mic.locator("..").get_by_role("status")).to_contain_text("keyboard dictation")
    field = page.get_by_label("Your feedback", exact=True)
    field.fill("Typed feedback still works.")
    field.press("Enter")
    expect(field).to_have_value("")
    assert live_app.store.comments(DECK_ID)[0].message == "Typed feedback still works."
