"""End-to-end checks for the isolated artifact workspace."""

import json
import os
import re
from pathlib import Path
from typing import Literal

import pytest
from playwright.sync_api import Page, expect

from artifact_studio.artifact_models import ArtifactView, ProjectView
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.review import ReviewHookInput, Reviews
from tests.integration.support import RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def register(
    live_app: RunningApp, tmp_path: Path
) -> tuple[ArtifactStore, ArtifactView, ArtifactView, ProjectView, Path]:
    """Register fictional HTML and Markdown in a disposable project."""
    store = ArtifactStore(live_app.store)
    first = store.create_project("Fictional source", "")
    second = store.create_project("Fictional destination", "")
    html = tmp_path / "fictional-html"
    html.mkdir()
    source = html / "index.html"
    _ = source.write_text(
        '<h1 id="title" style="font-size:24px">Original heading</h1>'
        '<button id="counter" onclick="this.textContent='
        'String(Number(this.textContent)+1)">0</button>'
    )
    artifact = store.register_artifact(first.id, "Fictional page", html, "index.html")
    markdown = tmp_path / "fictional-markdown"
    markdown.mkdir()
    _ = (markdown / "index.md").write_text("# Fictional notes\n\nA useful paragraph.")
    notes = store.register_artifact(first.id, "Fictional notes", markdown, "index.md")
    return store, artifact, notes, second, source


@pytest.mark.parametrize(("width", "theme"), [(1280, "light"), (390, "dark")])
def test_artifact_live_connection_preserves_draft_when_review_ends(
    live_app: RunningApp,
    page: Page,
    tmp_path: Path,
    artifacts: Path,
    width: int,
    theme: Literal["light", "dark"],
) -> None:
    """Show real lease availability and end listening without discarding a typed comment."""
    registry, item, _, _, _ = register(live_app, tmp_path)
    _ = registry.set_mode(item.id, "batch")
    _ = live_app.post(
        f"/api/artifacts/{item.id}/feedback",
        {
            "message": "A saved request",
            "content_hash": item.content_hash,
            "request_id": "fixture-saved-request",
            "expected_mode": "batch",
        },
    )
    reviews = Reviews(live_app.store)
    body = ReviewHookInput(
        harness="codex",
        session_id="browser-review",
        event="PostToolUse",
        artifact_id=item.id,
        auto_start=True,
    )
    assert reviews.bind(body).listening
    page.set_viewport_size({"width": width, "height": 900})
    page.emulate_media(color_scheme=theme)
    page.goto(f"{live_app.url}/?artifact={item.id}")
    status = page.locator("#hub-agent-status")
    expect(status).to_have_text("Codex connected · session browser-")
    expect(status).to_have_accessible_name(
        "Agent connection status: Codex connected · session browser-"
    )
    expect(page.get_by_role("button", name="End review", exact=True)).to_be_visible()
    feedback = page.get_by_role("textbox", name="Feedback for Fictional page")
    feedback.fill("A new request")
    feedback.press("Enter")
    expect(feedback).to_have_value("")
    assert [event.message for event in registry.feedback_feed(item.id).events] == ["A new request"]
    assert registry.workflow(item.id).pending == 1
    release = page.get_by_role(
        "button", name="Send saved messages for this artifact · 1", exact=True
    )
    expect(release).to_be_enabled()
    assert page.locator("html").evaluate("element => element.scrollWidth <= innerWidth")
    bounds = release.bounding_box()
    assert bounds is not None and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
    _ = page.screenshot(path=str(artifacts / "artifact-live-connected.png"), full_page=True)
    feedback.fill("Keep my unsent draft")
    page.get_by_role("button", name="End review", exact=True).click()
    expect(status).to_have_text("Agent unavailable")
    expect(feedback).to_have_value("Keep my unsent draft")
    assert not reviews.pending(body).listening
    assert not reviews.bind(body).listening
    assert reviews.bind(body.model_copy(update={"start_review": True})).listening
    page.reload()
    expect(status).to_have_text("Codex connected · session browser-")
    expect(status).to_have_accessible_name(
        "Agent connection status: Codex connected · session browser-"
    )


@pytest.mark.parametrize("markdown", [False, True], ids=["html", "markdown"])
def test_narrow_artifact_chat_reveals_composer_actions(
    live_app: RunningApp, page: Page, tmp_path: Path, markdown: bool
) -> None:
    """Keep the entire action row clear of the report launcher after opening and typing."""
    _, html, notes, _, _ = register(live_app, tmp_path)
    item = notes if markdown else html
    page.set_viewport_size({"width": 390, "height": 844})
    page.emulate_media(color_scheme="dark")
    page.goto(f"{live_app.url}/?artifact={item.id}")
    close = page.get_by_role("button", name="Close chat", exact=True)
    open_chat = page.get_by_role("button", name="Open chat", exact=True)
    textarea = page.get_by_role("textbox", name=f"Feedback for {item.title}")
    report = page.get_by_role("button", name="Report an issue", exact=True)
    close.click()
    open_chat.press("Enter")
    expect(textarea).to_be_focused()
    textarea.fill("A fictional draft")
    page.locator("#hub-feedback-files").set_input_files(
        {"name": "review.txt", "mimeType": "text/plain", "buffer": b"Fictional attachment"}
    )
    expect(page.locator("#hub-feedback-attachments")).to_contain_text("review.txt")
    page.set_viewport_size({"width": 390, "height": 550})
    expect(page.locator("#hub-send")).to_be_in_viewport(ratio=1)
    assert page.locator("#hub-send").evaluate(
        "button => button.getBoundingClientRect().bottom <= "
        "document.querySelector('.studio-report-trigger').getBoundingClientRect().top"
    )
    expect(report).to_be_in_viewport(ratio=1)
    close.click()
    open_chat.click()
    expect(textarea).to_have_value("A fictional draft")
    expect(page.locator("#hub-feedback-attachments")).to_contain_text("review.txt")
    expect(page.locator("#hub-send")).to_be_in_viewport(ratio=1)


def test_direct_edit_and_clean_view(live_app: RunningApp, page: Page, tmp_path: Path) -> None:
    """Send one version-bound proposal and retain authored source and preview state."""
    store, item, _, _, source = register(live_app, tmp_path)
    original = source.read_text()
    page.goto(f"{live_app.url}/?artifact={item.id}")
    frame = page.frame_locator("#hub-html-preview")
    expect(frame.locator("#title")).to_have_text("Original heading")
    original_frame = page.locator("#hub-html-preview").bounding_box()
    page.get_by_role("button", name="Select", exact=True).click()
    frame.locator("#title").click()
    inspector = page.locator("#hub-element-properties")
    expect(inspector).to_be_visible()
    assert page.locator("#hub-html-preview").bounding_box() == original_frame
    toolbar_bounds = inspector.bounding_box()
    assert toolbar_bounds is not None and toolbar_bounds["height"] <= 48
    expect(inspector.get_by_label("Text", exact=True)).to_be_hidden()
    inspector.get_by_role("button", name="Edit text", exact=True).click()
    assert page.locator("#hub-html-preview").bounding_box() == original_frame
    _ = page.screenshot(path=str(tmp_path / "workspace-review.png"))
    inspector.get_by_label("Text", exact=True).fill("Revised heading")
    inspector.get_by_label("Font size", exact=True).fill("30")
    expect(frame.locator("#title")).to_have_css("font-size", "40px")
    expect(frame.locator("#title")).to_have_text("Revised heading")
    with page.expect_response(
        lambda response: (
            response.url.endswith(f"/api/artifacts/{item.id}/feedback")
            and response.request.method == "POST"
        )
    ) as response:
        inspector.get_by_role("button", name="Send edit").click()
    assert response.value.ok
    feedback = store.feedback(item.id, include_pending=True)
    assert len(feedback) == 1
    assert feedback[0].content_hash == item.content_hash
    assert feedback[0].edit_intent is not None
    assert feedback[0].edit_intent.action == "update"
    assert feedback[0].edit_intent.values == {
        "text": "Revised heading",
        "font_size": 40,
        "font_size_unit": "px",
    }
    assert feedback[0].target is not None and feedback[0].target.selector == "#title"
    assert json.loads(feedback[0].target.state)["typography"]["font_size"] == 24
    inspector.get_by_label("Text", exact=True).fill("Cancelled")
    inspector.get_by_role("button", name="Cancel edit").click()
    expect(frame.locator("#title")).to_have_text("Revised heading")
    inspector.get_by_label("Text", exact=True).fill("Revised heading")
    expect(inspector.get_by_role("button", name="Send edit")).to_be_hidden()
    assert len(store.feedback(item.id, include_pending=True)) == 1
    page.get_by_role("button", name="Selecting", exact=True).click()
    frame.locator("#counter").click()
    expect(frame.locator("#counter")).to_have_text("1")
    page.get_by_role("button", name="View", exact=True).click()
    expect(page.locator("body")).to_have_class(re.compile("workspace-clean-view"))
    expect(frame.locator("#counter")).to_have_text("1")
    frame.locator("#counter").press("Escape")
    expect(page.locator("body")).not_to_have_class(re.compile("workspace-clean-view"))
    expect(frame.locator("#counter")).to_have_text("1")
    assert source.read_text() == original
    assert store.get_artifact(item.id).content_hash == item.content_hash


def test_markdown_view_and_sidebar_controls(
    live_app: RunningApp, page: Page, tmp_path: Path
) -> None:
    """Clean view and panel controls work with a document workspace."""
    _, _, notes, _, _ = register(live_app, tmp_path)
    page.goto(f"{live_app.url}/?artifact={notes.id}")
    expect(page.get_by_text("Fictional notes", exact=True).first).to_be_visible()
    page.get_by_role("button", name="Close sidebar").click()
    page.get_by_role("button", name="Open sidebar").click()
    page.get_by_role("button", name="Close chat").click()
    page.get_by_role("button", name="Open chat").click()
    page.get_by_role("button", name="View", exact=True).click()
    expect(page.locator("body")).to_have_class(re.compile("workspace-clean-view"))
    expect(page.locator("#hub-markdown").get_by_text("A useful paragraph.")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("body")).not_to_have_class(re.compile("workspace-clean-view"))


def test_drag_card_to_project(live_app: RunningApp, page: Page, tmp_path: Path) -> None:
    """Move a registered card by pointer drag without moving or changing source bytes."""
    store, item, _, destination, source = register(live_app, tmp_path)
    original = source.read_bytes()
    path = store.get_artifact(item.id).source_path
    page.goto(live_app.url)
    card = page.locator(f'[data-drag-artifact="{item.id}"]')
    folder = page.locator(f'[data-project-folder="{destination.id}"]')
    card.drag_to(folder)
    expect(page.get_by_text("Artifact moved.")).to_be_visible()
    moved = store.get_artifact(item.id)
    assert moved.project_id == destination.id
    assert moved.source_path == path
    assert source.read_bytes() == original


def test_slide_inspector_preserves_original_until_agent_applies_feedback(
    live_app: RunningApp, page: Page
) -> None:
    """Preview, cancel and send text and responsive sizing against the original slide."""
    from tests.integration.support import DECK_ID
    from tests.integration.test_browser import open_editor

    original = live_app.store.view_slide(DECK_ID, live_app.store.read(DECK_ID).slides[0])
    open_editor(page, live_app)
    original_frame = page.locator("#preview").bounding_box()
    original_notes = page.locator("#notes-panel").bounding_box()
    heading = page.frame_locator("#preview").get_by_role("heading", level=1)
    heading.click()
    properties = page.locator("#element-properties")
    expect(properties).to_be_visible()
    assert page.locator("#preview").bounding_box() == original_frame
    assert page.locator("#notes-panel").bounding_box() == original_notes
    expect(properties.locator(".ep-label")).to_have_text("Heading")
    expect(properties.locator("select")).to_have_count(0)
    expect(properties.get_by_label("Font size", exact=True)).to_have_value("38.3")
    page.get_by_role("button", name="Close sidebar").click()
    heading.click()
    expect(properties.get_by_label("Font size", exact=True)).to_have_value("38.3")
    page.get_by_role("button", name="Open sidebar").click()
    properties.get_by_role("button", name="Edit text", exact=True).click()
    properties.get_by_label("Text", exact=True).fill("Draft heading")
    properties.get_by_label("Font size", exact=True).fill("48")
    expect(heading).to_have_text("Draft heading")
    properties.get_by_role("button", name="Cancel edit").click()
    expect(heading).to_have_text("Review together")
    expect(properties.get_by_label("Font size", exact=True)).to_have_value("38.3")
    properties.get_by_label("Text", exact=True).fill("New slide heading")
    properties.get_by_label("Font size", exact=True).fill("48")
    with page.expect_response(
        lambda response: "/feedback" in response.url and response.request.method == "POST"
    ) as response:
        properties.get_by_role("button", name="Send edit").click()
    assert response.value.ok
    feedback = live_app.store.feedback_feed(DECK_ID).events[0]
    assert feedback.content_hash == original.content_hash
    assert feedback.edit_intent is not None
    assert feedback.edit_intent.action == "update"
    assert feedback.edit_intent.values["text"] == "New slide heading"
    assert feedback.edit_intent.values["font_size_unit"] == "cqw"
    assert feedback.edit_intent.values["font_size"] == pytest.approx(5.797101449, abs=0.001)
    assert live_app.store.read(DECK_ID).slides[0].html == original.html
    page.get_by_role("button", name="Close sidebar").click()
    page.get_by_role("button", name="Open sidebar").click()
    page.get_by_role("button", name="Close chat").click()
    page.get_by_role("button", name="Open chat").click()
    resizer = page.locator("#chat-resizer")
    expect(resizer).to_be_visible()
    box = resizer.bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] - 40, box["y"] + box["height"] / 2)
    page.mouse.up()


def test_slide_move_keeps_draft_controls_reachable(live_app: RunningApp, page: Page) -> None:
    """Reveal send and cancel after moving directly from a closed floating toolbar."""
    from tests.integration.support import DECK_ID
    from tests.integration.test_browser import open_editor

    original = live_app.store.view_slide(DECK_ID, live_app.store.read(DECK_ID).slides[0])
    open_editor(page, live_app)
    heading = page.frame_locator("#preview").get_by_role("heading", level=1)
    heading.click()
    properties = page.locator("#element-properties")
    options = properties.get_by_role("button", name="Object options", exact=True)
    expect(options).to_have_attribute("aria-expanded", "false")
    page.locator("#selection-box").press("ArrowRight")
    expect(options).to_have_attribute("aria-expanded", "true")
    expect(page.get_by_role("button", name="Send element change", exact=True)).to_be_visible()
    options.click()
    expect(page.get_by_role("button", name="Send element change", exact=True)).to_be_hidden()
    options.click()
    with page.expect_response(
        lambda response: "/feedback" in response.url and response.request.method == "POST"
    ) as response:
        page.get_by_role("button", name="Send element change", exact=True).click()
    assert response.value.ok
    feedback = live_app.store.feedback_feed(DECK_ID).events[0]
    assert feedback.content_hash == original.content_hash
    assert feedback.edit_intent is not None
    assert feedback.edit_intent.action == "move"
    assert live_app.store.read(DECK_ID).slides[0].html == original.html


@pytest.mark.parametrize("width", [390, 768])
def test_responsive_workspace_clean_view(
    live_app: RunningApp, page: Page, tmp_path: Path, width: int
) -> None:
    """Keep clean view and its exit control accessible on narrow screens."""
    _, item, _, _, _ = register(live_app, tmp_path)
    page.set_viewport_size({"width": width, "height": 844})
    page.goto(f"{live_app.url}/?artifact={item.id}")
    expect(page.frame_locator("#hub-html-preview").locator("#title")).to_be_visible()
    for label in ("Close sidebar", "Close chat"):
        control = page.get_by_role("button", name=label)
        if control.is_visible():
            box = control.bounding_box()
            assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= width
    page.get_by_role("button", name="View", exact=True).click()
    expect(page.get_by_role("button", name="Exit view")).to_be_visible()
    exit_box = page.get_by_role("button", name="Exit view").bounding_box()
    assert exit_box is not None and exit_box["x"] >= 0
    assert exit_box["x"] + exit_box["width"] <= width
    _ = page.screenshot(path=str(tmp_path / f"clean-view-{width}.png"))
    page.locator(".workspace-clean-exit-area").hover()
    page.get_by_role("button", name="Exit view").click()
    expect(page.locator("body")).not_to_have_class(re.compile("workspace-clean-view"))


def test_backup_deep_link_opens_shared_storage(live_app: RunningApp, page: Page) -> None:
    """Open the shared local storage dialog without a configured GitHub repository."""
    page.goto(f"{live_app.url}/?settings=backup")
    expect(page.get_by_role("dialog", name="Storage and backup")).to_be_visible()
    expect(
        page.get_by_text("Artifacts and notes save on this device.", exact=False)
    ).to_be_visible()


def test_clean_view_clears_selection_and_edge_drag_restores_width(
    live_app: RunningApp, page: Page, tmp_path: Path
) -> None:
    """Drop selection marks for viewing and restore a panel's width after dragging it closed."""
    from io import BytesIO

    from PIL import Image

    _, item, _, _, _ = register(live_app, tmp_path)
    page.goto(f"{live_app.url}/?artifact={item.id}")
    frame = page.frame_locator("#hub-html-preview")
    expect(frame.locator("#title")).to_be_visible()
    page.get_by_role("button", name="Select", exact=True).click()
    frame.locator("#title").click()
    expect(page.locator("#hub-element-properties")).to_be_visible()
    frame_locator = page.locator("#hub-html-preview")
    selected = Image.open(BytesIO(frame_locator.screenshot())).convert("RGB")
    assert (113, 100, 223) in {
        color for _, color in selected.getcolors(selected.width * selected.height) or []
    }
    page.get_by_role("button", name="View", exact=True).click()
    image = Image.open(BytesIO(frame_locator.screenshot())).convert("RGB")
    assert (113, 100, 223) not in {
        color for _, color in image.getcolors(image.width * image.height) or []
    }
    page.locator(".workspace-clean-exit-area").hover()
    page.get_by_role("button", name="Exit view").click()
    expect(page.get_by_role("button", name="Select", exact=True)).to_have_attribute(
        "aria-pressed", "false"
    )
    expect(page.locator("#hub-element-properties")).to_be_hidden()

    handle = page.get_by_role("separator", name="Resize sidebar")
    box = handle.bounding_box()
    assert box is not None
    y = box["y"] + box["height"] / 2
    page.mouse.move(box["x"] + box["width"] / 2, y)
    page.mouse.down()
    page.mouse.move(box["x"] + 65, y, steps=5)
    page.mouse.up()
    sidebar = page.get_by_label("Project artifacts")
    original = sidebar.bounding_box()
    assert original is not None
    box = handle.bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + box["width"] / 2, y)
    page.mouse.down()
    page.mouse.move(10, y, steps=5)
    page.mouse.up()
    expect(page.get_by_role("button", name="Open sidebar")).to_be_visible()
    page.get_by_role("button", name="Open sidebar").click()
    reopened = sidebar.bounding_box()
    assert reopened is not None and abs(reopened["width"] - original["width"]) < 2
