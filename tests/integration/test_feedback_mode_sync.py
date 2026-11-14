"""Keep displayed delivery mode and first-publication intent consistent across tabs."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, Route, expect
from pydantic import TypeAdapter

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.models import FeedbackFeed
from tests.integration.support import DECK_ID, RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def review_artifact(app: RunningApp, root: Path) -> str:
    store = ArtifactStore(app.store)
    project = store.create_project("Delivery review")
    source = root / "review-source"
    source.mkdir()
    _ = (source / "guide.md").write_text("# Review this\n\nA fictional paragraph.")
    item = store.register_artifact(project.id, "Delivery example", source, "guide.md")
    _ = app.post(f"/api/artifacts/{item.id}/mode", {"mode": "batch"})
    return item.id


def test_hub_poll_updates_review_controls_without_replacing_drafts(
    page: Page, live_app: RunningApp, tmp_path: Path
) -> None:
    artifact_id = review_artifact(live_app, tmp_path)
    page.clock.install()
    _ = page.goto(f"{live_app.url}/?artifact={artifact_id}")
    field = page.get_by_role("textbox", name="Feedback for Delivery example", exact=True)
    field.fill("Keep this draft while another tab changes delivery.")
    page.locator("#hub-feedback-files").set_input_files(
        {"name": "context.txt", "mimeType": "text/plain", "buffer": b"Fictional context"}
    )
    field.focus()
    _ = field.evaluate("field => field.setSelectionRange(2, 8)")
    original = field.element_handle()
    _ = live_app.post(f"/api/artifacts/{artifact_id}/mode", {"mode": "live"})
    page.clock.fast_forward(12_001)
    expect(page.locator("#hub-send")).to_have_attribute("aria-label", "Send feedback")
    expect(field).to_be_focused()
    assert field.evaluate("(field, original) => field === original", original)
    assert field.evaluate("field => [field.selectionStart, field.selectionEnd]") == [2, 8]
    expect(page.locator("#hub-feedback-attachments")).to_contain_text("context.txt")
    _ = live_app.post(f"/api/artifacts/{artifact_id}/mode", {"mode": "batch"})
    page.clock.fast_forward(12_001)
    expect(page.locator("#hub-send")).to_have_attribute("aria-label", "Send feedback")
    expect(field).to_have_value("Keep this draft while another tab changes delivery.")
    expect(field).to_be_focused()
    current = ArtifactStore(live_app.store).get_artifact(artifact_id)
    _ = live_app.post(
        f"/api/artifacts/{artifact_id}/approve", {"content_hash": current.content_hash}
    )
    page.clock.fast_forward(12_001)
    expect(page.get_by_role("button", name="Approved", exact=True)).to_be_disabled()
    _ = live_app.post(
        f"/api/artifacts/{artifact_id}/feedback",
        {
            "message": "A queued review from another tab",
            "content_hash": current.content_hash,
            "request_id": "mode-sync-release-focus",
            "expected_mode": "batch",
        },
    )
    page.clock.fast_forward(12_001)
    release = page.locator('[data-action="release"]')
    expect(release).to_be_enabled()
    release.focus()
    _ = live_app.post(f"/api/artifacts/{artifact_id}/release", {})
    page.clock.fast_forward(12_001)
    expect(release).to_have_count(0)
    expect(page.get_by_role("button", name="Copy conversation", exact=True)).to_be_focused()


def test_slide_feedback_waits_for_the_initial_slide(page: Page, live_app: RunningApp) -> None:
    """Prevent a draft from being typed before its slide identity is available."""
    pending: list[Route] = []
    page.route(f"**/api/decks/{DECK_ID}", lambda route: pending.append(route))
    with page.expect_request(f"**/api/decks/{DECK_ID}"):
        _ = page.goto(f"{live_app.url}/?deck={DECK_ID}")
    field = page.locator("#feedback-text")
    expect(field).to_be_disabled()
    assert len(pending) == 1
    pending[0].continue_()
    expect(field).to_be_enabled()
    field.fill("Draft after the slide is ready")
    expect(field).to_have_value("Draft after the slide is ready")


@pytest.mark.parametrize("kind", ["artifact", "slides"])
def test_feedback_mode_race_preserves_draft_and_requires_explicit_resubmit(
    browser: Browser, live_app: RunningApp, tmp_path: Path, kind: str
) -> None:
    """Change server mode after the user's click but before its real POST reaches storage."""
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    errors: list[str] = []
    statuses: list[int] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "response",
        lambda response: statuses.append(response.status) if response.status >= 400 else None,
    )
    artifact_id = ""
    if kind == "artifact":
        artifact_id = review_artifact(live_app, tmp_path)
        route_path = f"/api/artifacts/{artifact_id}/feedback"
        mode_path = f"/api/artifacts/{artifact_id}/mode"
        url = f"/?artifact={artifact_id}"
        field_id, send_id, error_id = "hub-feedback-text", "hub-send", "hub-feedback-error"
        file_id, files_id = "hub-feedback-files", "hub-feedback-attachments"
    else:
        _ = live_app.post(f"/api/decks/{DECK_ID}/feedback-mode", {"mode": "batch"})
        route_path = f"/api/decks/{DECK_ID}/slides/cover/feedback"
        mode_path = f"/api/decks/{DECK_ID}/feedback-mode"
        url = f"/?deck={DECK_ID}"
        field_id, send_id, error_id = "feedback-text", "send-feedback", "feedback-error"
        file_id, files_id = "feedback-file-input", "feedback-files"
    requests: list[dict[str, object]] = []

    def change_mode_before_publish(route: Route) -> None:
        requests.append(
            TypeAdapter(dict[str, object]).validate_python(route.request.post_data_json)
        )
        if len(requests) == 1:
            _ = live_app.post(mode_path, {"mode": "batch"})
        route.continue_()

    page.route("**" + route_path, change_mode_before_publish)
    try:
        _ = page.goto(live_app.url + url)
        field = page.locator("#" + field_id)
        field.fill("A deliberate cross-tab delivery review")
        page.locator("#" + file_id).set_input_files(
            {"name": "context.txt", "mimeType": "text/plain", "buffer": b"Fictional context"}
        )
        page.locator("#" + send_id).click()
        expect(page.locator("#" + error_id)).to_contain_text("Feedback mode changed")
        expect(field).to_have_value("A deliberate cross-tab delivery review")
        expect(page.locator("#" + files_id)).to_contain_text("context.txt")
        assert requests[0]["expected_mode"] == "live"
        if kind == "artifact":
            assert not ArtifactStore(live_app.store).feedback(artifact_id, include_pending=True)
        else:
            assert not FeedbackFeed.model_validate(
                live_app.call("read_feedback", deck_id=DECK_ID)
            ).events
        expect(page.locator("#" + send_id)).to_have_attribute("aria-label", "Send feedback")
        page.locator("#" + send_id).click()
        expect(field).to_have_value("")
        assert requests[1]["expected_mode"] == "live"
        if kind == "artifact":
            events = ArtifactStore(live_app.store).feedback(artifact_id, include_pending=True)
            assert len(events) == 1 and events[0].released
        else:
            published = FeedbackFeed.model_validate(live_app.call("read_feedback", deck_id=DECK_ID))
            assert len(published.events) == 1 and published.events[0].released
        assert statuses == [409] and not errors
    finally:
        context.close()
