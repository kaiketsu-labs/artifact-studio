"""Normal sends leave previously saved feedback private until explicitly released."""

import os
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
def test_new_send_preserves_saved_feedback_until_explicit_release(
    page: Page,
    live_app: RunningApp,
    artifacts: Path,
    width: int,
    theme: Literal["light", "dark"],
) -> None:
    """Keep legacy feedback private while sending new text and its attachment immediately."""
    slide = live_app.store.view(DECK_ID).slides[0]
    _ = live_app.store.set_mode(DECK_ID, "batch")
    _ = live_app.store.add_feedback(
        DECK_ID, slide.id, "Previously saved feedback", slide.content_hash, "fixture-saved-feedback"
    )
    page.add_init_script("""Object.defineProperty(navigator, 'clipboard', {value: {
      writeText: async text => { window.fixtureCopiedConversation = text; }
    }});""")
    page.set_viewport_size({"width": width, "height": 800})
    page.emulate_media(color_scheme=theme)
    open_editor(page, live_app)
    open_chat = page.get_by_role("button", name="Open chat", exact=True)
    if open_chat.is_visible():
        open_chat.click()
    else:
        page.get_by_role("separator", name="Resize chat panel").press("Home")
    expect(page.locator("html")).to_have_attribute("data-theme", theme)
    expect(page.get_by_role("button", name="Batch", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Live", exact=True)).to_have_count(0)
    feedback = page.get_by_label("Your feedback", exact=True)
    feedback.fill("Clarify the title.")
    feedback.press("Shift+Enter")
    page.keyboard.insert_text("Keep the source.")
    message = "Clarify the title.\nKeep the source."
    page.locator("#feedback-file-input").set_input_files(
        {"name": "context.txt", "mimeType": "text/plain", "buffer": b"Title context"}
    )
    feedback.press("Enter")
    expect(feedback).to_have_value("")
    expect(page.locator("#feedback-files")).to_be_empty()
    expect(page.locator("#feedback-list")).to_contain_text("Clarify the title.")
    expect(page.locator("#feedback-list")).to_contain_text("Keep the source.")
    expect(page.locator("#feedback-list")).to_contain_text("Previously saved feedback")
    feed = FeedbackFeed.model_validate(live_app.call("read_feedback", deck_id=DECK_ID))
    assert [event.message for event in feed.events] == [message]
    assert [file.name for file in feed.events[0].files] == ["context.txt"]
    assert live_app.store.workflow(DECK_ID).pending == 1
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.locator(".discussion").evaluate(
        "element => element.scrollWidth <= element.clientWidth"
    )
    page.get_by_role("button", name="Copy conversation", exact=True).click()
    copied = page.evaluate("window.fixtureCopiedConversation")
    assert message in copied and "Previously saved feedback" in copied
    assert live_app.store.workflow(DECK_ID).pending == 1
    assert [event.message for event in live_app.store.feedback_feed(DECK_ID).events] == [message]
    _ = page.screenshot(path=str(artifacts / "saved-feedback.png"), full_page=True)
    page.get_by_role(
        "button", name="Send saved messages for this presentation (1)", exact=True
    ).click()
    expect(page.locator("#send-batch")).to_be_hidden()
    feed = live_app.store.feedback_feed(DECK_ID)
    assert [event.message for event in feed.events] == [message, "Previously saved feedback"]
    assert live_app.store.workflow(DECK_ID).pending == 0


def test_artifact_send_and_copy_keep_saved_feedback_private(
    page: Page, live_app: RunningApp, tmp_path: Path
) -> None:
    """An artifact's copied conversation and new send do not release an older draft."""
    from artifact_studio.artifacts import ArtifactStore

    registry = ArtifactStore(live_app.store)
    project = registry.create_project("Fictional review")
    source = tmp_path / "fictional.md"
    source.write_text("# Fictional artifact")
    artifact = registry.register_artifact(project.id, "Fictional artifact", source, "fictional.md")
    _ = registry.set_mode(artifact.id, "batch")
    _ = live_app.post(
        f"/api/artifacts/{artifact.id}/feedback",
        {
            "message": "Earlier saved request",
            "content_hash": artifact.content_hash,
            "request_id": "fixture-saved-artifact",
            "expected_mode": "batch",
        },
    )
    page.add_init_script("""Object.defineProperty(navigator, 'clipboard', {value: {
      writeText: async text => { window.fixtureCopiedConversation = text; }
    }});""")
    page.goto(f"{live_app.url}/?artifact={artifact.id}")
    field = page.locator("#hub-feedback-text")
    field.fill("New artifact request")
    page.locator("#hub-send").click()
    expect(field).to_have_value("")
    expect(page.locator("#hub-thread")).to_contain_text("New artifact request")
    assert [event.message for event in registry.feedback_feed(artifact.id).events] == [
        "New artifact request"
    ]
    assert registry.workflow(artifact.id).pending == 1
    page.get_by_role("button", name="Copy conversation", exact=True).click()
    copied = page.evaluate("window.fixtureCopiedConversation")
    assert "Earlier saved request" in copied and "New artifact request" in copied
    assert registry.workflow(artifact.id).pending == 1
    page.get_by_role("button", name="Send saved messages for this artifact · 1", exact=True).click()
    assert [event.message for event in registry.feedback_feed(artifact.id).events] == [
        "New artifact request",
        "Earlier saved request",
    ]
    assert registry.workflow(artifact.id).pending == 0


def test_cross_tab_release_moves_presentation_focus_to_copy(
    page: Page, live_app: RunningApp
) -> None:
    """A disappearing saved-message action does not leave keyboard focus on the page body."""
    slide = live_app.store.view(DECK_ID).slides[0]
    _ = live_app.store.set_mode(DECK_ID, "batch")
    _ = live_app.store.add_feedback(
        DECK_ID, slide.id, "Fictional saved feedback", slide.content_hash, "fixture-focus-release"
    )
    open_editor(page, live_app)
    release = page.locator("#send-batch")
    expect(release).to_be_visible()
    release.focus()
    expect(release).to_be_focused()
    _ = live_app.post(f"/api/decks/{DECK_ID}/send-batch", {})
    expect(release).to_be_hidden(timeout=15000)
    expect(page.get_by_role("button", name="Copy conversation", exact=True)).to_be_focused()
    assert [event.message for event in live_app.store.feedback_feed(DECK_ID).events] == [
        "Fictional saved feedback"
    ]
