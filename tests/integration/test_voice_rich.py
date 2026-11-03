"""Dictation uses normal rich-editor operations and cannot escape its original document."""

import os

import pytest
from playwright.sync_api import Page, expect

from artifact_studio.models import ElementTarget
from artifact_studio.storage import digest
from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_browser import open_editor
from tests.integration.voice_support import install_voice, voice_end, voice_result

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def open_rules(page: Page, live_app: RunningApp) -> None:
    """Open the real Quill rules editor with a short, representable document."""
    _ = live_app.store.save_instructions(
        "Keep source facts exact.", digest(live_app.store.authoring_instructions())
    )
    install_voice(page)
    open_editor(page, live_app)
    page.get_by_role("button", name="Menu", exact=True).click()
    page.get_by_role("menuitem", name="Instructions", exact=True).click()
    expect(page.locator("#authoring-instructions .ql-editor")).to_be_visible()


def test_rich_rules_dictation_uses_quill_and_the_normal_save_path(
    page: Page, live_app: RunningApp
) -> None:
    """Final words update the rich document and save as Markdown only on explicit Save."""
    open_rules(page, live_app)
    editor = page.locator("#authoring-instructions .ql-editor")
    editor.click()
    page.keyboard.press("ControlOrMeta+End")
    page.get_by_role("button", name="Dictate into Workspace rules", exact=True).click()
    voice_result(page, "Add a diagram.")
    expect(editor).to_have_text("Keep source facts exact. Add a diagram.")
    assert live_app.store.authoring_instructions() == "Keep source facts exact."
    page.get_by_role("button", name="Stop dictation for Workspace rules", exact=True).click()
    voice_end(page)
    page.locator("#save-instructions").click()
    expect(page.locator("#instructions-dialog")).not_to_be_visible()
    assert live_app.store.authoring_instructions().strip() == (
        "Keep source facts exact. Add a diagram."
    )


def test_rules_reload_retires_late_results_and_readonly_editors_have_no_microphone(
    page: Page, live_app: RunningApp
) -> None:
    """Reloaded rules cannot receive an old recognizer's words; read-only text stays read-only."""
    open_rules(page, live_app)
    page.get_by_role("button", name="Dictate into Workspace rules", exact=True).click()
    voice_result(page, "Unfinished old words", final=False)
    _ = live_app.store.save_instructions(
        "Reloaded rules.", digest(live_app.store.authoring_instructions())
    )
    page.locator("#reload-instructions").click()
    expect(page.locator("#authoring-instructions .ql-editor")).to_have_text("Reloaded rules.")
    voice_result(page, "Late old result.", session=0)
    expect(page.locator("#authoring-instructions .ql-editor")).to_have_text("Reloaded rules.")
    page.evaluate(
        """async () => {
            const wrapper=document.createElement('div');wrapper.id='readonly-voice-check';
            const host=document.createElement('div');wrapper.append(host);
            document.querySelector('#instructions-form').append(wrapper);
            const editor=await SlideRulesEditor.create(host,{readOnly:true,label:'Read only'});
            editor.setMarkdown('Reference rules.');
        }"""
    )
    expect(page.locator("#readonly-voice-check .ql-editor")).to_have_attribute(
        "aria-readonly", "true"
    )
    expect(page.locator("#readonly-voice-check .voice-input-button")).to_have_count(0)


def test_rules_typing_during_dictation_does_not_reuse_a_stale_replacement_range(
    page: Page, live_app: RunningApp
) -> None:
    """A pending voice replacement cannot delete a newer typed edit at its old selection."""
    open_rules(page, live_app)
    editor = page.locator("#authoring-instructions .ql-editor")
    editor.click()
    is_mac = bool(page.evaluate("/Mac|iPhone|iPad/.test(navigator.platform)"))
    home = "Meta+ArrowUp" if is_mac else "Control+Home"
    page.keyboard.press(home)
    page.keyboard.press("Alt+Shift+ArrowRight" if is_mac else "Control+Shift+ArrowRight")
    page.get_by_role("button", name="Dictate into Workspace rules", exact=True).click()
    voice_result(page, "Unfinished replacement", final=False)
    editor.click()
    page.keyboard.press(home)
    page.keyboard.type("NEW ")
    expect(editor).to_have_text("NEW Keep source facts exact.")
    voice_result(page, "Stale replacement")
    expect(editor).to_have_text("NEW Keep source facts exact.")
    expect(
        page.locator("#instructions-dialog > form > .ql-toolbar .voice-input-status")
    ).to_contain_text("original field changed")
    assert live_app.store.authoring_instructions() == "Keep source facts exact."


def test_inline_dictation_preserves_markup_and_sends_only_an_explicit_text_proposal(
    page: Page, live_app: RunningApp
) -> None:
    """Insert plain text at the retained caret without adding microphone markup to the slide."""
    before = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    source = str(before["html"]).replace("Review together", "Review <em>together</em>")
    _ = live_app.call(
        "revise_slide",
        deck_id=DECK_ID,
        slide_id="cover",
        expected_hash=before["expected_hash"],
        request_id="rich-voice-fixture",
        patch={"html": source},
    )
    install_voice(page)
    open_editor(page, live_app)
    preview = page.frame_locator("#preview")
    heading = preview.get_by_role("heading", level=1)
    heading.click(position={"x": 2, "y": 2})
    page.locator("#element-properties [data-panel=objects]").click()
    page.locator("#edit-element-text").click()
    heading.evaluate(
        """element => {
            const range=element.ownerDocument.createRange();range.setStart(element.firstChild,6);
            range.collapse(true);const selection=element.ownerDocument.getSelection();
            selection.removeAllRanges();selection.addRange(range);
        }"""
    )
    page.get_by_role("button", name="Dictate into selected element text", exact=True).click()
    voice_result(page, "carefully")
    voice_result(page, "<b>literal</b>")
    expect(heading).to_have_text("Review carefully <b>literal</b> together")
    expect(heading.locator("em")).to_have_text("together")
    expect(heading.locator("b")).to_have_count(0)
    expect(preview.locator(".voice-input-controls")).to_have_count(0)
    assert heading.get_attribute("id") is None
    assert live_app.store.comments(DECK_ID) == []
    assert live_app.store.read(DECK_ID).slides[0].html == source
    page.get_by_role("button", name="Stop dictation for selected element text", exact=True).click()
    voice_end(page)
    page.get_by_role("button", name="Send text change", exact=True).click()
    expect(page.locator("#feedback-list")).to_contain_text("Replace text.")
    comments = live_app.store.comments(DECK_ID)
    assert len(comments) == 1 and comments[0].edit_intent is not None
    assert comments[0].edit_intent.values["text"] == "Review carefully <b>literal</b> together"
    target = comments[0].target
    assert isinstance(target, ElementTarget)
    assert "voice-input" not in target.html
    assert live_app.store.read(DECK_ID).slides[0].html == source


def test_cancelled_inline_dictation_never_changes_a_new_element(
    page: Page, live_app: RunningApp
) -> None:
    """Cancellation retires callbacks even after another element begins its own text edit."""
    install_voice(page)
    open_editor(page, live_app)
    preview = page.frame_locator("#preview")
    heading = preview.get_by_role("heading", level=1)
    heading.click()
    page.locator("#element-properties [data-panel=objects]").click()
    page.locator("#edit-element-text").click()
    page.get_by_role("button", name="Dictate into selected element text", exact=True).click()
    voice_result(page, "Unfinished heading words", final=False)
    page.locator("#cancel-element-edit").click()
    body = preview.locator("p").last
    body.click()
    page.locator("#element-properties [data-panel=objects]").click()
    page.locator("#edit-element-text").click()
    page.get_by_role("button", name="Dictate into selected element text", exact=True).click()
    voice_result(page, "Stale heading words", session=0)
    expect(heading).to_have_text("Review together")
    expect(body).to_have_text("Create, review and present.")
    voice_result(page, "Body addition", session=1)
    expect(body).to_contain_text("Body addition")
    assert live_app.store.comments(DECK_ID) == []
    page.locator("#cancel-element-edit").click()


def test_property_text_dictation_is_a_reviewable_element_edit(
    page: Page, live_app: RunningApp
) -> None:
    """Dictation in the current text panel stays provisional until the edit is sent."""
    install_voice(page)
    open_editor(page, live_app)
    page.frame_locator("#preview").get_by_role("heading", level=1).click()
    page.locator("#element-properties [data-panel=text]").click()
    text = page.locator("#element-properties textarea")
    text.evaluate(
        "element => element.setSelectionRange(element.value.length, element.value.length)"
    )
    mic = page.locator("#element-properties .ep-text-panel .voice-input-button")
    mic.click()
    voice_result(page, "with care")
    expect(text).to_have_value("Review together with care")
    assert live_app.store.comments(DECK_ID) == []
    mic.click()
    voice_end(page)
    page.locator("#element-properties").get_by_role("button", name="Send edit", exact=True).click()
    expect(page.locator("#feedback-list")).to_contain_text("Update element properties.")
    comments = live_app.store.comments(DECK_ID)
    assert len(comments) == 1 and comments[0].edit_intent is not None
    assert comments[0].edit_intent.values["text"] == "Review together with care"
    target = comments[0].target
    assert isinstance(target, ElementTarget)
    assert "voice-input" not in target.html
