"""End-to-end checks for the isolated artifact workspace."""

import json
import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from artifact_studio.artifact_models import ArtifactView, ProjectView
from artifact_studio.artifacts import ArtifactStore
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


def test_direct_edit_and_clean_view(live_app: RunningApp, page: Page, tmp_path: Path) -> None:
    """Send one version-bound proposal and retain authored source and preview state."""
    store, item, _, _, source = register(live_app, tmp_path)
    original = source.read_text()
    page.goto(f"{live_app.url}/?artifact={item.id}")
    frame = page.frame_locator("#hub-html-preview")
    expect(frame.locator("#title")).to_have_text("Original heading")
    page.get_by_role("button", name="Select", exact=True).click()
    frame.locator("#title").click()
    inspector = page.locator("#hub-element-properties")
    expect(inspector).to_be_visible()
    _ = page.screenshot(path=str(tmp_path / "workspace-review.png"))
    inspector.get_by_label("Text", exact=True).fill("Revised heading")
    inspector.get_by_label("Font size", exact=True).fill("30")
    expect(frame.locator("#title")).to_have_css("font-size", "30px")
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
        "font_size": 30,
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
    heading = page.frame_locator("#preview").get_by_role("heading", level=1)
    heading.click()
    properties = page.locator("#element-properties")
    expect(properties).to_be_visible()
    expect(properties.get_by_label("Font size unit")).to_have_value("cqw")
    properties.get_by_label("Text", exact=True).fill("Draft heading")
    properties.get_by_label("Font size", exact=True).fill("5")
    expect(heading).to_have_text("Draft heading")
    properties.get_by_role("button", name="Cancel edit").click()
    expect(heading).to_have_text("Review together")
    expect(properties.get_by_label("Font size", exact=True)).to_have_value("4.625")
    properties.get_by_label("Text", exact=True).fill("New slide heading")
    properties.get_by_label("Font size", exact=True).fill("5")
    with page.expect_response(
        lambda response: "/feedback" in response.url and response.request.method == "POST"
    ) as response:
        properties.get_by_role("button", name="Send edit").click()
    assert response.value.ok
    feedback = live_app.store.feedback_feed(DECK_ID).events[0]
    assert feedback.content_hash == original.content_hash
    assert feedback.edit_intent is not None
    assert feedback.edit_intent.action == "update"
    assert feedback.edit_intent.values == {
        "text": "New slide heading",
        "font_size": 5,
        "font_size_unit": "cqw",
    }
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
