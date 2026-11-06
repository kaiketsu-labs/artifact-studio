"""Type @ paths through the real composer, including cursor previews and delivery."""

import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from artifact_studio.artifacts import ArtifactStore
from scripts.preview_favorites import prepare_covers, seed_favorites
from tests.integration.support import DECK_ID, RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


@pytest.fixture
def prepared(live_app: RunningApp, browser: Browser, tmp_path: Path) -> RunningApp:
    seed_favorites(live_app, tmp_path)
    prepare_covers(live_app, browser)
    return live_app


def test_keyboard_paths_and_reference_message_delivery(page: Page, prepared: RunningApp) -> None:
    page.goto(f"{prepared.url}/?deck={DECK_ID}#workflow")
    text = page.locator("#feedback-text")
    text.fill("Use @Fieldnotes")
    options = page.get_by_role("listbox", name="Workspace references")
    expect(options).to_be_visible()
    # Keyboard browse a project without attaching it; Enter chooses the resulting artifact.
    row = options.get_by_role("option", name="/Fieldnotes", exact=True)
    row.hover()
    text.press("Tab")
    expect(text).to_have_value("Use @/Fieldnotes/")
    text.press("Enter")
    expect(page.locator(".is-path-reference")).to_have_count(1)
    expect(text).to_be_focused()
    assert not prepared.store.comments(DECK_ID)
    text.press_sequentially("again @Brand direction")
    expect(options.get_by_role("option")).to_have_count(1)
    text.press("Enter")
    expect(page.locator(".is-path-reference")).to_have_count(1)
    text.press("End")
    text.press_sequentially("and @three-step")
    expect(options.get_by_role("option")).to_have_count(1)
    text.press("Enter")
    expect(page.locator(".is-path-reference")).to_have_count(2)
    text.press_sequentially("for layout")
    text.press("Enter")
    expect(page.locator(".is-path-reference")).to_have_count(0)
    event = prepared.store.comments(DECK_ID)[-1]
    assert len(event.files) == 2
    assert "for layout" in event.message
    assert "reference" in event.files[0].name and "artifact-" not in event.files[0].name
    assert "Clear three-step process." in Path(event.files[1].path).read_text()
    page.reload()
    expect(page.locator("#feedback-list")).to_contain_text(event.files[0].name)


def test_hover_preview_tracks_cursor_and_clamps_to_viewport(
    page: Page, prepared: RunningApp
) -> None:
    page.goto(f"{prepared.url}/?deck={DECK_ID}#workflow")
    text = page.locator("#feedback-text")
    text.fill("@three-step")
    row = page.get_by_role("option").first
    row.hover()
    preview = page.locator(".chat-reference-preview")
    expect(preview).to_be_visible()
    expect(preview.frame_locator("iframe").locator(".slide")).to_be_visible()
    box = preview.bounding_box()
    assert box is not None and box["x"] >= 0 and box["y"] >= 0
    assert box["x"] + box["width"] <= page.viewport_size["width"]  # type: ignore[index]
    assert preview.evaluate("el=>getComputedStyle(el).pointerEvents") == "none"
    bounds = row.bounding_box()
    assert bounds is not None
    page.mouse.move(bounds["x"] + 20, bounds["y"] + 10)
    page.wait_for_timeout(200)
    moved = preview.bounding_box()
    assert moved is not None and moved != box
    text.press("Escape")
    expect(preview).to_be_hidden()
    expect(page.get_by_role("listbox", name="Workspace references")).to_be_hidden()
    text.fill("Contact editor@example.test")
    expect(page.get_by_role("listbox", name="Workspace references")).to_be_hidden()


def test_touch_native_artifact_reference_keeps_focus_and_saved_context(
    browser: Browser, prepared: RunningApp
) -> None:
    item = next(
        item
        for item in ArtifactStore(prepared.store).list_artifacts()
        if item.title == "Launch brief"
    )
    context = browser.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, color_scheme="dark"
    )
    page = context.new_page()
    try:
        page.goto(f"{prepared.url}/?artifact={item.id}")
        text = page.locator("#hub-feedback-text")
        text.fill("Compare @Research notes")
        options = page.get_by_role("listbox", name="Workspace references")
        options.get_by_role("option").first.tap()
        expect(page.locator(".is-path-reference")).to_have_count(1)
        expect(text).to_be_focused()
        text.press_sequentially("with @Fieldnotes")
        expect(page.get_by_role("listbox", name="Workspace references")).to_be_visible()
        options.get_by_role("option", name="/Fieldnotes", exact=True).tap()
        expect(page.locator(".is-path-reference")).to_have_count(2)
        expect(text).to_be_focused()
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        page.locator("#hub-send").tap()
        expect(page.locator(".is-path-reference")).to_have_count(0)
        event = ArtifactStore(prepared.store).feedback(item.id, include_pending=True)[-1]
        assert len(event.files) == 2
        assert json.loads(Path(event.files[1].path).read_text())["scope"].startswith(
            "Project brief"
        )
        page.reload()
        expect(page.locator("#hub-thread")).to_contain_text(
            "Research notes - artifact reference.json"
        )
    finally:
        context.close()


def test_path_browsing_handles_slashes_quotes_and_at_signs(
    page: Page, prepared: RunningApp, tmp_path: Path
) -> None:
    artifacts = ArtifactStore(prepared.store)
    project = artifacts.create_project('Research / @home "2026"')
    source = tmp_path / "reference-source"
    source.mkdir()
    _ = (source / "index.html").write_text("<h1>Odd path names work</h1>")
    _ = artifacts.register_artifact(project.id, "Exact source", source, "index.html")
    page.goto(f"{prepared.url}/?deck={DECK_ID}#workflow")
    text = page.locator("#feedback-text")
    text.fill("Use @Research")
    options = page.get_by_role("listbox", name="Workspace references")
    options.get_by_role("option", name="/" + project.title, exact=True).hover()
    text.press("Tab")
    expect(options.get_by_role("option")).to_have_count(1)
    text.press("Enter")
    expect(page.locator(".is-path-reference")).to_contain_text("Exact source")
    assert json.loads(text.input_value().removeprefix("Use @").strip()).endswith("/Exact source")
