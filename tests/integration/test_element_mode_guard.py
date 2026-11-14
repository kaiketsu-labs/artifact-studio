"""Element sends retain the delivery choice visible before asynchronous finalization."""

import os

import pytest
from playwright.sync_api import Browser, Page, Route, expect

from artifact_studio.artifacts import ArtifactStore
from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_browser import open_editor
from tests.integration.test_voice_hub import open_voice_property
from tests.integration.voice_support import install_voice, voice_end, voice_result

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def capture_review_poll(page: Page) -> None:
    """Invoke the registered real poll while speech is draining, without waiting 12 seconds."""
    page.add_init_script(
        """(() => {
          const repeat=window.setInterval;
          window.__reviewPolls=[];
          window.__heldInspector=[];
          window.__holdInspector=false;
          window.addEventListener('message',event=>{
            if(window.__holdInspector && event.data?.type==='artifact:inspector'){
              event.stopImmediatePropagation();window.__heldInspector.push(event);
            }
          },true);
          window.setInterval=(callback,delay,...args)=>{
            if(delay===3500 || delay===12000)window.__reviewPolls.push(callback);
            return repeat(callback,delay,...args);
          };
        })();"""
    )


@pytest.mark.parametrize("kind", ["artifact", "slides"])
def test_element_voice_send_requires_resubmit_after_mode_changes(
    browser: Browser, live_app: RunningApp, kind: str
) -> None:
    """A cross-tab mode change during voice finalization cannot publish an edit silently."""
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    capture_review_poll(page)
    artifact_id = ""
    if kind == "artifact":
        artifact_id = open_voice_property(page, live_app)
        mode_path = f"/api/artifacts/{artifact_id}/mode"
        panel = page.locator("#hub-element-properties")
        toast = page.locator("#hub-toast")
    else:
        install_voice(page)
        open_editor(page, live_app)
        page.frame_locator("#preview").get_by_role("heading", level=1).click()
        panel = page.locator("#element-properties")
        panel.get_by_role("button", name="Edit text", exact=True).click()
        mode_path = f"/api/decks/{DECK_ID}/feedback-mode"
        toast = page.locator("#toast")
    try:
        text = panel.get_by_label("Text", exact=True)
        text.fill("Keep this edit private")
        panel.locator(".voice-input-button").click()
        voice_result(page, "with a final phrase")
        requests: list[str] = []
        feedback_path = (
            f"/api/artifacts/{artifact_id}/feedback"
            if kind == "artifact"
            else f"/api/decks/{DECK_ID}/slides/cover/feedback"
        )

        def conflict_once(route: Route) -> None:
            requests.append(route.request.url)
            if len(requests) == 1:
                _ = live_app.post(mode_path, {"mode": "batch"})
            route.continue_()

        page.route("**" + feedback_path, conflict_once)
        panel.get_by_role("button", name="Send edit", exact=True).click()
        voice_end(page)
        expect(toast).to_contain_text("Feedback mode changed")
        expect(text).to_have_value("Keep this edit private with a final phrase")
        if kind == "artifact":
            assert ArtifactStore(live_app.store).feedback(artifact_id, include_pending=True) == []
        else:
            assert live_app.store.comments(DECK_ID) == []
        panel.get_by_role("button", name="Send edit", exact=True).click()
        expect(panel.get_by_role("button", name="Send edit", exact=True)).to_be_hidden()
        events = (
            ArtifactStore(live_app.store).feedback(artifact_id, include_pending=True)
            if kind == "artifact"
            else live_app.store.comments(DECK_ID)
        )
        assert len(events) == 1 and events[0].released
        assert events[0].edit_intent is not None
        assert events[0].edit_intent.values["text"] == "Keep this edit private with a final phrase"
    finally:
        context.close()


def test_element_send_keeps_clicked_mode_while_preview_acknowledgement_is_pending(
    browser: Browser, live_app: RunningApp
) -> None:
    """A mode conflict after the sandbox acknowledgement keeps the edit for retry."""
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    capture_review_poll(page)
    artifact_id = open_voice_property(page, live_app)
    panel = page.locator("#hub-element-properties")
    try:
        # Delay only delivery of the real sandbox's inspector reply to its host controller.
        page.evaluate("window.__holdInspector=true")
        panel.get_by_label("Text", exact=True).fill("A pending private edit")
        page.wait_for_function("window.__heldInspector.length > 0")
        requests: list[str] = []

        def conflict_once(route: Route) -> None:
            requests.append(route.request.url)
            if len(requests) == 1:
                _ = live_app.post(f"/api/artifacts/{artifact_id}/mode", {"mode": "batch"})
            route.continue_()

        page.route(f"**/api/artifacts/{artifact_id}/feedback", conflict_once)
        panel.get_by_role("button", name="Send edit", exact=True).click()
        page.evaluate(
            """() => {
              window.__holdInspector=false;
              for(const event of window.__heldInspector)window.dispatchEvent(new MessageEvent(
                'message',{data:event.data,origin:event.origin,source:event.source}));
            }"""
        )
        expect(page.locator("#hub-toast")).to_contain_text("Feedback mode changed")
        assert ArtifactStore(live_app.store).feedback(artifact_id, include_pending=True) == []
        expect(panel.get_by_label("Text", exact=True)).to_have_value("A pending private edit")
        panel.get_by_role("button", name="Send edit", exact=True).click()
        expect(panel.get_by_role("button", name="Send edit", exact=True)).to_be_hidden()
        events = ArtifactStore(live_app.store).feedback(artifact_id, include_pending=True)
        assert len(events) == 1 and events[0].released
    finally:
        context.close()
