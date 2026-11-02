"""Simulated speech retains real artifact targets and retries through the actual HTTP API."""

import base64
import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, Route, expect

from artifact_studio.artifacts import ArtifactStore
from tests.integration.support import RunningApp
from tests.integration.voice_support import install_voice, voice_end, voice_result

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def open_voice_artifact(page: Page, live_app: RunningApp, kind: str = "html") -> str:
    """Import an owned bundle using the real hub API, then open its review workspace."""
    project = live_app.post("/api/hub/projects", {"title": "Voice review"})
    source = (
        "<html><body><h1 id='title'>Original title</h1><p id='body'>Original body</p></body></html>"
        if kind == "html"
        else "# Original title\n\nOriginal body\n"
    )
    item = live_app.post(
        "/api/artifacts/import",
        {
            "project_id": project["id"],
            "title": "Voice page",
            "entrypoint": "index.html" if kind == "html" else "guide.md",
            "files": [
                {
                    "name": "index.html" if kind == "html" else "guide.md",
                    "data_base64": base64.b64encode(source.encode()).decode(),
                }
            ],
        },
    )
    _ = live_app.post(f"/api/artifacts/{item['id']}/mode", {"mode": "batch"})
    page.goto(live_app.url + "/?artifact=" + str(item["id"]), wait_until="networkidle")
    expect(page.locator("#hub-feedback-text")).to_be_visible()
    page.get_by_role("button", name="Select", exact=True).click()
    return str(item["id"])


def selected_nodes(page: Page, kind: str):
    """Use the authored frame for HTML and the owned Markdown rendering otherwise."""
    surface = (
        page.frame_locator("#hub-html-preview") if kind == "html" else page.locator("#hub-markdown")
    )
    return surface.get_by_role("heading", name="Original title"), surface.get_by_text(
        "Original body", exact=True
    )


@pytest.mark.parametrize("kind", ["html", "markdown"])
def test_hub_dictation_pins_late_finals_groups_and_batch_delivery(
    page: Page, live_app: RunningApp, kind: str
) -> None:
    """Selection changes create editable, versioned voice drafts without automatic sends."""
    install_voice(page)
    artifact_id = open_voice_artifact(page, live_app, kind)
    heading, body = selected_nodes(page, kind)
    heading.click()
    expect(page.locator("#hub-targets")).to_contain_text("Original title")
    mic = page.locator(".voice-input-button[data-voice-for='hub-feedback-text']")
    mic.click()
    voice_result(page, "Change the title", session=0)
    expect(page.locator("#hub-send")).to_be_disabled()
    body.click()
    expect(page.locator("#hub-targets")).to_contain_text("Original body")
    voice_result(page, "and keep the brand", session=0)
    voice_end(page, session=0)
    page.wait_for_function("window.__speech.sessions.length === 2")
    voice_result(page, "Move the body", session=1)
    heading.click(modifiers=["Shift"])
    expect(page.locator("#hub-targets")).to_contain_text("2 selected")
    voice_result(page, "to the bottom", session=1)
    voice_end(page, session=1)
    page.wait_for_function("window.__speech.sessions.length === 3")
    voice_result(page, "Align both objects", session=2)
    page.get_by_role("button", name="Stop recording", exact=True).click()
    voice_result(page, "with consistent spacing", session=2)
    voice_end(page, session=2)
    drafts = page.locator("textarea[data-hub-voice-segment]")
    expect(drafts).to_have_count(3)
    expect(drafts.nth(0)).to_have_value("Change the title and keep the brand")
    expect(drafts.nth(1)).to_have_value("Move the body to the bottom")
    expect(drafts.nth(2)).to_have_value("Align both objects with consistent spacing")
    expect(page.locator("#hub-feedback-text")).to_have_value("")
    assert live_app.get(f"/api/artifacts/{artifact_id}")["feedback"] == []
    page.reload(wait_until="networkidle")
    expect(drafts).to_have_count(3)
    drafts.nth(0).fill("Use a shorter title")
    page.locator("#hub-send").click()
    expect(drafts).to_have_count(0)
    store = ArtifactStore(live_app.store)
    assert store.feedback_feed(artifact_id).events == []
    page.get_by_role("button", name="Send batch", exact=False).click()
    expect(page.locator(".hub-message-state")).to_have_count(0)
    events = store.feedback_feed(artifact_id).events
    assert [event.message for event in events] == [
        "Use a shorter title",
        "Move the body to the bottom",
        "Align both objects with consistent spacing",
    ]
    assert all(event.revision == 1 for event in events)
    first, second, group = (event.target for event in events)
    assert first is not None and second is not None and group is not None
    assert first.quote == "Original title" and second.quote == "Original body"
    assert group.kind == "group"
    assert [target.quote for target in group.elements] == ["Original body", "Original title"]


def test_hub_lost_second_response_retains_original_retry_id_without_resending_prefix(
    page: Page, live_app: RunningApp
) -> None:
    """A committed response lost in transit leaves only the unacknowledged draft for exact retry."""
    install_voice(page)
    artifact_id = open_voice_artifact(page, live_app)
    heading, body = selected_nodes(page, "html")
    heading.click()
    mic = page.locator(".voice-input-button[data-voice-for='hub-feedback-text']")
    mic.click()
    voice_result(page, "First request", session=0)
    body.click()
    expect(page.locator("#hub-targets")).to_contain_text("Original body")
    voice_end(page, session=0)
    page.wait_for_function("window.__speech.sessions.length === 2")
    voice_result(page, "Second request", session=1)
    page.get_by_role("button", name="Stop recording", exact=True).click()
    voice_end(page, session=1)
    original_ids = page.evaluate(
        "id => JSON.parse(localStorage.getItem('artifact-studio:artifact-drafts:v1'))[id]"
        ".voice_segments.map(segment => segment.request_id)",
        artifact_id,
    )
    page.evaluate("""() => {
      const original = window.fetch; let responses = 0;
      window.fetch = async (...arguments_) => {
        const response = await original(...arguments_);
        const feedback = /\\/api\\/artifacts\\/[^/]+\\/feedback$/.test(String(arguments_[0]));
        if (feedback && ++responses === 2)
          throw new TypeError('Simulated response lost after commit');
        return response;
      };
    }""")
    page.locator("#hub-send").click()
    expect(page.locator("#hub-feedback-error")).to_contain_text("response lost")
    expect(page.locator("textarea[data-hub-voice-segment]")).to_have_count(1)
    retained = page.evaluate(
        "id => JSON.parse(localStorage.getItem('artifact-studio:artifact-drafts:v1'))[id]"
        ".voice_segments.map(segment => segment.request_id)",
        artifact_id,
    )
    assert retained == original_ids[1:]
    assert len(ArtifactStore(live_app.store).feedback(artifact_id, include_pending=True)) == 2
    page.reload(wait_until="networkidle")
    expect(page.locator("textarea[data-hub-voice-segment]")).to_have_count(1)
    page.locator("#hub-send").click()
    expect(page.locator("textarea[data-hub-voice-segment]")).to_have_count(0)
    events = ArtifactStore(live_app.store).feedback(artifact_id, include_pending=True)
    assert [event.message for event in events] == ["First request", "Second request"]


def test_hub_navigation_drains_to_original_artifact_and_dialog_fields_accept_dictation(
    page: Page, live_app: RunningApp
) -> None:
    """Late speech stays in its original draft while newly opened human fields remain dictatable."""
    install_voice(page)

    def preview(route: Route) -> None:
        """Keep unrelated thumbnail rendering outside the microphone/navigation simulation."""
        route.fulfill(
            status=200,
            content_type="image/svg+xml",
            body='<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"/>',
        )

    page.route("**/api/hub/previews/**", preview)
    artifact_id = open_voice_artifact(page, live_app)
    heading, _ = selected_nodes(page, "html")
    heading.click()
    expect(page.locator("#hub-targets")).to_contain_text("Original title")
    page.locator(".voice-input-button[data-voice-for='hub-feedback-text']").click()
    voice_result(page, "Original artifact request", session=0)
    page.get_by_role("button", name="Back to library", exact=True).click()
    voice_result(page, "with delayed words", session=0)
    voice_end(page, session=0)
    page.get_by_role("button", name="New project", exact=True).first.click()
    title = page.locator("#hub-project-form input[name=title]")
    identifier = title.get_attribute("id")
    assert identifier
    page.locator(f".voice-input-button[data-voice-for='{identifier}']").click()
    voice_result(page, "Spoken project name", session=1)
    voice_end(page, session=1)
    expect(title).to_have_value("Spoken project name")
    page.get_by_role("button", name="Cancel", exact=True).click()
    page.locator(f"[data-artifact='{artifact_id}']").first.click()
    expect(page.locator("textarea[data-hub-voice-segment]")).to_have_value(
        "Original artifact request with delayed words"
    )
    expect(page.locator("#hub-feedback-text")).to_have_value("")
    assert live_app.get(f"/api/artifacts/{artifact_id}")["feedback"] == []


@pytest.mark.parametrize("selected", [False, True])
def test_hub_voice_capture_uses_visible_revision_except_for_retained_selected_targets(
    page: Page, live_app: RunningApp, selected: bool
) -> None:
    """A preserved typed draft must not relabel new whole-artifact speech as an older version."""
    install_voice(page)
    artifact_id = open_voice_artifact(page, live_app, "markdown")
    original = live_app.get(f"/api/artifacts/{artifact_id}")
    if selected:
        page.locator("#hub-markdown").get_by_role("heading", name="Original title").click()
        expect(page.locator("#hub-targets")).to_contain_text("Original title")
    feedback = page.locator("#hub-feedback-text")
    feedback.fill("Typed feedback for version one")
    changed = live_app.call(
        "revise_artifact",
        artifact_id=artifact_id,
        files=[{"path": "guide.md", "text": "# Updated title\n\nUpdated body\n"}],
        expected_revision=1,
        request_id="voice-visible-revision",
    )
    # Toggling the real preference on starts its ordinary refresh immediately.
    page.locator("#hub-auto-update").click()
    page.locator("#hub-auto-update").click()
    expect(page.locator(".hub-version")).to_contain_text("Version 2")
    expect(feedback).to_have_value("Typed feedback for version one")
    page.locator(".voice-input-button[data-voice-for='hub-feedback-text']").click()
    voice_result(page, "New spoken feedback")
    voice_end(page)
    expected_revision = 1 if selected else 2
    expected_hash = original["content_hash"] if selected else changed["content_hash"]
    expect(page.locator(".voice-segment-context")).to_contain_text(f"Version {expected_revision}")
    page.locator("#hub-send").click()
    expect(page.locator("textarea[data-hub-voice-segment]")).to_have_count(0)
    expect(feedback).to_have_value("")
    messages = ArtifactStore(live_app.store).feedback(artifact_id, include_pending=True)
    assert [message.message for message in messages] == [
        "New spoken feedback",
        "Typed feedback for version one",
    ]
    assert messages[0].revision == expected_revision
    assert messages[0].content_hash == expected_hash
    assert messages[1].revision == 1 and messages[1].content_hash == original["content_hash"]
    if selected:
        assert messages[0].target is not None and messages[0].target.quote == "Original title"
    else:
        assert messages[0].target is None


def test_hub_cancel_recording_keeps_prior_voice_and_typed_drafts(
    page: Page, live_app: RunningApp
) -> None:
    """The recording's X discards only that recording, including its later target segments."""
    install_voice(page)
    artifact_id = open_voice_artifact(page, live_app, "markdown")
    heading, body = selected_nodes(page, "markdown")
    heading.click()
    mic = page.locator(".voice-input-button[data-voice-for='hub-feedback-text']")
    mic.click()
    voice_result(page, "Prior voice draft", session=0)
    voice_end(page, session=0)
    feedback = page.locator("#hub-feedback-text")
    feedback.fill("Typed draft stays")
    mic.click()
    expect(page.locator("#hub-feedback-recording")).to_be_visible()
    expect(feedback).to_be_hidden()
    expect(page.locator("#hub-feedback-recording .voice-listening-dots span")).to_have_count(28)
    voice_result(page, "Discard this heading feedback", session=1)
    body.click()
    voice_end(page, session=1)
    page.wait_for_function("window.__speech.sessions.length === 3")
    voice_result(page, "Discard this body feedback too", session=2)
    page.get_by_role("button", name="Cancel recording", exact=True).click()
    expect(page.locator("#hub-feedback-recording")).to_be_hidden()
    expect(feedback).to_have_value("Typed draft stays")
    drafts = page.locator("textarea[data-hub-voice-segment]")
    expect(drafts).to_have_count(1)
    expect(drafts).to_have_value("Prior voice draft")
    voice_result(page, "Late cancelled words", session=2)
    expect(drafts).to_have_count(1)
    assert live_app.get(f"/api/artifacts/{artifact_id}")["feedback"] == []


def test_hub_recording_send_drains_final_words_before_adding_batch(
    page: Page, live_app: RunningApp, artifacts: Path
) -> None:
    """The arrow is an explicit send; it waits for the final result and respects Batch privacy."""
    install_voice(page)
    artifact_id = open_voice_artifact(page, live_app, "markdown")
    heading, _ = selected_nodes(page, "markdown")
    heading.click()
    page.locator("#hub-feedback-text").fill("Typed feedback alongside")
    page.locator(".voice-input-button[data-voice-for='hub-feedback-text']").click()
    voice_result(page, "Spoken feedback", session=0)
    bar = page.locator("#hub-feedback-recording")
    expect(bar).to_be_visible()
    controls = [
        page.locator(selector).bounding_box()
        for selector in (
            "#hub-cancel-voice",
            "#hub-feedback-recording .voice-listening-dots",
            "#hub-stop-voice",
            "#hub-send-voice",
        )
    ]
    assert all(controls)
    assert [control["x"] for control in controls if control] == sorted(
        control["x"] for control in controls if control
    )
    _ = page.screenshot(path=str(artifacts / "hub-voice-recording.png"), full_page=True)
    page.get_by_role("button", name="Add dictated feedback to batch", exact=True).click()
    assert live_app.get(f"/api/artifacts/{artifact_id}")["feedback"] == []
    voice_result(page, "with the final words", session=0)
    voice_end(page, session=0)
    expect(page.locator("#hub-feedback-recording")).to_be_hidden()
    expect(page.locator("textarea[data-hub-voice-segment]")).to_have_count(0)
    expect(page.locator("#hub-feedback-text")).to_have_value("")
    store = ArtifactStore(live_app.store)
    messages = store.feedback(artifact_id, include_pending=True)
    assert [message.message for message in messages] == [
        "Spoken feedback with the final words",
        "Typed feedback alongside",
    ]
    assert all(not message.released for message in messages)
    assert store.feedback_feed(artifact_id).events == []


@pytest.mark.parametrize("ending", ["cancel", "error", "timeout", "unfinished"])
def test_hub_interrupted_recording_send_keeps_drafts_unsent(
    page: Page, live_app: RunningApp, ending: str
) -> None:
    """Cancel, failed recognition and incomplete drains cannot silently submit prior drafts."""
    install_voice(page)
    artifact_id = open_voice_artifact(page, live_app, "markdown")
    page.locator("#hub-feedback-text").fill("Typed draft stays")
    page.locator(".voice-input-button[data-voice-for='hub-feedback-text']").click()
    voice_result(page, "Confirmed voice draft.")
    voice_result(page, "Unfinished words", final=False)
    page.evaluate(
        """ending => {
          document.querySelector('#hub-send-voice').click();
          if (ending === 'cancel') document.querySelector('#hub-cancel-voice').click();
          if (ending === 'error') window.__speech.sessions.at(-1).fail('network');
          if (ending === 'unfinished') window.__speech.sessions.at(-1).end();
        }""",
        ending,
    )
    expect(page.locator("#hub-feedback-recording")).to_be_hidden()
    expect(page.locator("#hub-toast")).to_contain_text("without sending")
    expect(page.locator("#hub-feedback-text")).to_have_value("Typed draft stays")
    expect(page.locator("textarea[data-hub-voice-segment]")).to_have_count(
        0 if ending == "cancel" else 1
    )
    store = ArtifactStore(live_app.store)
    assert store.feedback(artifact_id, include_pending=True) == []
    voice_result(page, "Late retired words")
    assert store.feedback(artifact_id, include_pending=True) == []
    page.locator("#hub-send").click()
    expect(page.locator("#hub-feedback-text")).to_have_value("")
    expected = [] if ending == "cancel" else ["Confirmed voice draft."]
    assert [item.message for item in store.feedback(artifact_id, include_pending=True)] == [
        *expected,
        "Typed draft stays",
    ]
    assert store.feedback_feed(artifact_id).events == []
