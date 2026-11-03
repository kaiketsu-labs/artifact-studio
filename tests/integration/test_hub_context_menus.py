"""Library menus use captured targets while preserving private review state."""

import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from artifact_studio.project_tree import ProjectTree
from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_artifact_workspace import register

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_artifact_cards_keyboard_history_share_copy_and_move(
    live_app: RunningApp, page: Page, tmp_path: Path, artifacts: Path
) -> None:
    """Card commands open the chosen artifact and use the shared accessible menu."""
    store, item, notes, destination, source = register(live_app, tmp_path)
    original = source.read_bytes()
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=live_app.url)
    page.goto(live_app.url, wait_until="networkidle")
    card = page.locator(f'[data-artifact="{notes.id}"]')
    card.click(button="right")
    menu = page.get_by_role("menu", name="Artifact actions", exact=True)
    expect(menu).to_be_visible()
    expect(page.get_by_role("menuitem", name="Open", exact=True)).to_be_focused()
    page.keyboard.press("ArrowDown")
    expect(page.get_by_role("menuitem", name="View", exact=True)).to_be_focused()
    page.keyboard.press("End")
    expect(page.get_by_role("menuitem", name="Agent context", exact=True)).to_be_focused()
    _ = page.screenshot(path=str(artifacts / "artifact-menu-light.png"))
    page.keyboard.press("Escape")
    expect(card).to_be_focused()
    card.press("Shift+F10")
    page.get_by_role("menuitem", name="Copy link", exact=True).click()
    assert page.evaluate("navigator.clipboard.readText()") == (
        f"{live_app.url}/?artifact={notes.id}"
    )
    card.press("ContextMenu")
    page.get_by_role("menuitem", name="Share", exact=True).click()
    expect(page.locator(".studio-share-item strong")).to_have_text(notes.title)
    page.get_by_role("button", name="Close share panel").click()
    card.click(button="right")
    page.get_by_role("menuitem", name="Version history", exact=True).click()
    expect(page.locator(".version-history .version-subtitle")).to_have_text(notes.title)
    expect(page.locator(".version-history iframe")).to_have_attribute(
        "src", re.compile(re.escape(f"/api/artifacts/{notes.id}/preview"))
    )
    page.get_by_role("button", name="Close version history").click()
    card.click(button="right")
    page.get_by_role("menuitem", name="Move to project", exact=True).click()
    expect(page.locator("#hub-move-form")).to_have_attribute("data-artifact-id", notes.id)
    page.locator("#hub-move-form select").select_option(destination.id)
    page.get_by_role("button", name="Move artifact", exact=True).click()
    expect(page.get_by_role("dialog", name="Move to project")).not_to_be_visible()
    assert store.get_artifact(notes.id).project_id == destination.id
    assert store.get_artifact(item.id).project_id == item.project_id
    page.locator(f'[data-artifact="{notes.id}"]').click(button="right")
    page.get_by_role("menuitem", name="Comment", exact=True).click()
    expect(page.locator("#hub-feedback-text")).to_be_focused()
    page.locator("#hub-feedback-text").fill("Private notes draft")
    page.get_by_role("button", name="Back to library", exact=True).click()
    page.locator(f'[data-artifact="{notes.id}"]').press("Shift+F10")
    page.get_by_role("menuitem", name="View", exact=True).click()
    expect(page.locator("body")).to_have_class(re.compile("workspace-clean-view"))
    page.keyboard.press("Escape")
    expect(page.locator("#hub-feedback-text")).to_have_value("Private notes draft")
    assert store.feedback(notes.id, include_pending=True) == []
    assert source.read_bytes() == original


def test_related_artifact_commands_preserve_active_edit_and_private_draft(
    live_app: RunningApp, page: Page, tmp_path: Path, artifacts: Path
) -> None:
    """A related artifact's dialogs and move never relabel or discard the current draft."""
    store, item, notes, destination, source = register(live_app, tmp_path)
    original = source.read_bytes()
    page.goto(f"{live_app.url}/?artifact={item.id}", wait_until="networkidle")
    page.locator("#hub-feedback-text").fill("Private feedback for the page")
    page.get_by_role("button", name="Select", exact=True).click()
    heading = page.frame_locator("#hub-html-preview").locator("#title")
    heading.click()
    inspector = page.locator("#hub-element-properties")
    inspector.get_by_role("button", name="Edit text", exact=True).click()
    inspector.get_by_label("Text", exact=True).fill("Provisional heading")
    related = page.locator(f'[data-artifact="{notes.id}"]')
    related.click(button="right")
    expect(heading).to_have_text("Provisional heading")
    with page.expect_response(
        lambda response: response.url.endswith(f"/api/hub/context?artifact_id={notes.id}")
    ):
        page.get_by_role("menuitem", name="Agent context", exact=True).click()
    expect(page.locator(".hub-context-receipt")).to_contain_text(notes.title)
    expect(page.locator(".hub-context-receipt")).not_to_contain_text("Private feedback")
    page.get_by_role("button", name="Close dialog", exact=True).click()
    related.click(button="right")
    page.get_by_role("menuitem", name="Share", exact=True).click()
    expect(page.locator(".studio-share-item strong")).to_have_text(notes.title)
    page.get_by_role("button", name="Close share panel").click()
    related.press("ContextMenu")
    page.get_by_role("menuitem", name="Move to project", exact=True).click()
    page.locator("#hub-move-form select").select_option(destination.id)
    page.get_by_role("button", name="Move artifact", exact=True).click()
    expect(related).to_have_count(0)
    expect(heading).to_have_text("Provisional heading")
    expect(inspector.get_by_label("Text", exact=True)).to_have_value("Provisional heading")
    expect(page.locator("#hub-feedback-text")).to_have_value("Private feedback for the page")
    expect(page).to_have_url(f"{live_app.url}/?artifact={item.id}")
    _ = page.screenshot(path=str(artifacts / "active-edit-after-related-move.png"))
    assert store.get_artifact(notes.id).project_id == destination.id
    assert store.feedback(item.id, include_pending=True) == []
    assert store.feedback(notes.id, include_pending=True) == []
    assert source.read_bytes() == original


def test_project_folder_and_background_commands_keep_their_context(
    live_app: RunningApp, page: Page, tmp_path: Path, artifacts: Path
) -> None:
    """Create, inspect, import and organize through the row or background actually invoked."""
    store, item, _, destination, source = register(live_app, tmp_path)
    original = source.read_bytes()
    page.goto(live_app.url, wait_until="networkidle")
    library = page.get_by_role("main", name="Artifact library")
    library.click(button="right", position={"x": 30, "y": 600})
    expect(page.get_by_role("menu", name="Library actions")).to_be_visible()
    page.keyboard.press("Escape")
    expect(library).to_be_focused()
    library.press("Shift+F10")
    expect(page.get_by_role("menu", name="Library actions")).to_be_visible()
    _ = page.screenshot(path=str(artifacts / "library-menu.png"))
    page.get_by_role("menuitem", name="New folder", exact=True).click()
    page.get_by_role("dialog").get_by_label("Name", exact=True).fill("Fictional cluster")
    page.get_by_role("button", name="Create folder", exact=True).click()
    expect(page.get_by_role("button", name="Fictional cluster", exact=True)).to_be_visible()
    folder = next(
        node
        for node in ProjectTree(live_app.store).view().nodes
        if node.title == "Fictional cluster"
    )
    page.locator(f'[data-project="folder:{folder.id}"]').click(button="right")
    page.get_by_role("menuitem", name="Import artifact", exact=True).click()
    expect(page.locator("#hub-project-form")).to_have_attribute("data-parent", folder.id)
    expect(page.locator("#hub-project-form")).to_have_attribute("data-then-import", "true")
    page.get_by_role("button", name="Cancel", exact=True).click()
    project = page.locator(f'[data-tree-node="{item.project_id}"] .project-tree-open')
    project.click(button="right")
    expect(page.get_by_role("menuitem", name="Rename", exact=True)).to_have_count(0)
    with page.expect_response(
        lambda response: response.url.endswith(f"/api/hub/context?project_id={item.project_id}")
    ):
        page.get_by_role("menuitem", name="Project brief", exact=True).click()
    expect(page.get_by_role("dialog", name="Project brief")).to_be_visible()
    expect(page.locator(".hub-context-receipt")).to_contain_text("Fictional source")
    page.get_by_role("button", name="Close dialog", exact=True).click()
    project.press("ContextMenu")
    page.get_by_role("menuitem", name="Import artifact", exact=True).click()
    expect(page.locator("#hub-import-form select[name=project_id]")).to_have_value(item.project_id)
    page.get_by_role("button", name="Cancel", exact=True).click()
    project.click(button="right")
    page.get_by_role("menuitem", name="Share project", exact=True).click()
    expect(page.locator(".studio-share-item strong")).to_have_text("Fictional source")
    page.get_by_role("button", name="Close share panel").click()
    project.click(button="right")
    page.get_by_role("menuitem", name="Move to…", exact=True).click()
    page.locator("#hub-tree-move-form select").select_option(folder.id)
    page.get_by_role("dialog").get_by_role("button", name="Move", exact=True).click()
    expect(
        page.locator(f'[data-folder-children="{folder.id}"] [data-project="{item.project_id}"]')
    ).to_be_visible()
    folder_button = page.locator(f'[data-project="folder:{folder.id}"]')
    folder_button.press("Shift+F10")
    menu = page.get_by_role("menu", name="Folder actions")
    page.evaluate("document.documentElement.dataset.theme='dark'")
    _ = page.screenshot(path=str(artifacts / "folder-menu-dark.png"))
    page.get_by_role("menuitem", name="Import artifact", exact=True).click()
    options = page.locator("#hub-import-form select[name=project_id] option")
    expect(options).to_have_count(1)
    expect(options).to_have_attribute("value", item.project_id)
    page.get_by_role("button", name="Cancel", exact=True).click()
    folder_button.click(button="right")
    page.get_by_role("menuitem", name="New project", exact=True).click()
    expect(page.locator("#hub-project-form")).to_have_attribute("data-parent", folder.id)
    page.get_by_role("button", name="Cancel", exact=True).click()
    folder_button.click(button="right")
    page.get_by_role("menuitem", name="Rename", exact=True).click()
    page.get_by_role("dialog").get_by_label("Name", exact=True).fill("Fictional archive")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_role("button", name="Fictional archive", exact=True)).to_be_visible()
    folder_button.click(button="right")
    page.get_by_role("menuitem", name="Open", exact=True).click()
    expect(page.locator(f'[data-artifact="{item.id}"]')).to_be_visible()
    expect(page.get_by_role("heading", name="Fictional archive", exact=True)).to_be_visible()
    folder_button.click(button="right")
    page.get_by_role("menuitem", name="Remove folder", exact=True).click()
    expect(menu).not_to_be_visible()
    expect(folder_button).to_have_count(0)
    assert store.get_artifact(item.id).project_id == item.project_id
    assert any(node.id == destination.id for node in ProjectTree(live_app.store).view().nodes)
    assert source.read_bytes() == original


def test_native_menus_selection_groups_and_legacy_commands(
    live_app: RunningApp, page: Page, tmp_path: Path, artifacts: Path
) -> None:
    """Native fields and authored controls coexist with selected-target and library menus."""
    store, item, notes, _, source = register(live_app, tmp_path)
    original = source.read_bytes()
    page.goto(live_app.url, wait_until="networkidle")
    search = page.get_by_role("searchbox", name="Search artifacts")
    search.evaluate(
        "node => node.addEventListener('contextmenu', event => "
        "setTimeout(() => node.dataset.prevented=String(event.defaultPrevented)))"
    )
    search.click(button="right")
    expect(search).to_have_attribute("data-prevented", "false")
    expect(page.locator("#hub-context-menu")).not_to_be_visible()
    search.press("Shift+F10")
    expect(page.locator("#hub-context-menu")).not_to_be_visible()
    legacy = page.locator(f'[data-artifact="{DECK_ID}"]')
    legacy.click(button="right")
    expect(page.get_by_role("menuitem", name="Present", exact=True)).to_be_visible()
    expect(page.get_by_role("menuitem", name="Version history", exact=True)).to_have_count(0)
    _ = page.screenshot(path=str(artifacts / "presentation-card-menu.png"))
    page.keyboard.press("Escape")
    page.locator(f'[data-artifact="{item.id}"]').click()
    control = page.frame_locator("#hub-html-preview").locator("#counter")
    control.click()
    expect(control).to_have_text("1")
    control.click(button="right")
    expect(page.locator("#hub-context-menu")).not_to_be_visible()
    expect(page.get_by_role("menu", name="Element actions")).not_to_be_visible()
    page.locator(".hub-files summary").click()
    link = page.locator(".hub-file-list a").first
    link.evaluate(
        "node => node.addEventListener('contextmenu', event => "
        "setTimeout(() => node.dataset.prevented=String(event.defaultPrevented)))"
    )
    link.click(button="right")
    expect(link).to_have_attribute("data-prevented", "false")
    page.locator(f'[data-artifact="{notes.id}"]').click()
    page.get_by_role("button", name="Select", exact=True).click()
    heading = page.locator("#hub-markdown h1")
    heading.click()
    paragraph = page.locator("#hub-markdown p")
    paragraph.click(modifiers=["Shift"])
    expect(page.locator("#hub-targets")).to_contain_text("2 selected")
    heading.click(button="right")
    expect(page.get_by_role("menu", name="Element actions")).to_be_visible()
    expect(page.locator("#hub-context-menu")).not_to_be_visible()
    expect(page.locator("#hub-targets")).to_contain_text("2 selected")
    assert store.feedback(item.id, include_pending=True) == []
    assert source.read_bytes() == original
    page.keyboard.press("Escape")
    page.get_by_role("button", name="Back to library", exact=True).click()
    page.get_by_role("main", name="Artifact library").press("Shift+F10")
    page.get_by_role("menuitem", name="New presentation", exact=True).click()
    expect(page).to_have_url(f"{live_app.url}/?editor=1&new=1")
    expect(page.locator("#new-dialog")).to_be_visible()
