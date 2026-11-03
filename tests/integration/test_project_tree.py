"""Real pointer and menu journeys for nested project organization."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from artifact_studio.project_tree import ProjectTree
from tests.integration.support import RunningApp
from tests.integration.test_artifact_workspace import register

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_nested_folders_drag_order_and_reload(
    live_app: RunningApp, page: Page, tmp_path: Path, artifacts: Path
) -> None:
    """Create nested folders, move real projects, filter their work and retain order on reload."""
    store, item, _, destination, source = register(live_app, tmp_path)
    original = source.read_bytes()
    page.goto(live_app.url)
    page.get_by_role("button", name="Add project or folder").click()
    page.get_by_role("menuitem", name="New folder").click()
    page.get_by_role("dialog").get_by_label("Name", exact=True).fill("Allianz")
    page.get_by_role("button", name="Create folder", exact=True).click()
    expect(page.get_by_role("button", name="Allianz", exact=True)).to_be_visible()
    outer = next(
        node for node in ProjectTree(live_app.store).view().nodes if node.title == "Allianz"
    )
    page.get_by_role("button", name="Options for Allianz").click()
    page.get_by_role("menuitem", name="New folder").click()
    page.get_by_role("dialog").get_by_label("Name", exact=True).fill("Research")
    page.get_by_role("button", name="Create folder", exact=True).click()
    expect(page.get_by_role("button", name="Research", exact=True)).to_be_visible()
    inner = next(
        node for node in ProjectTree(live_app.store).view().nodes if node.title == "Research"
    )
    source_row = page.locator(f'[data-tree-node="{item.project_id}"]')
    source_row.drag_to(page.locator(f'[data-tree-node="{inner.id}"]'))
    expect(
        page.locator(f'[data-folder-children="{inner.id}"] [data-project="{item.project_id}"]')
    ).to_be_visible()
    page.locator(f'[data-project="folder:{outer.id}"]').click()
    expect(page.locator(f'[data-artifact="{item.id}"]')).to_be_visible()
    page.reload()
    expect(page.locator(f'[data-project="folder:{outer.id}"]')).to_have_attribute(
        "aria-current", "true"
    )
    page.get_by_role("button", name="Collapse Research").click()
    expect(page.locator(f'[data-project="{item.project_id}"]')).to_be_hidden()
    page.reload()
    expect(page.get_by_role("button", name="Expand Research")).to_be_visible()
    page.get_by_role("button", name="Expand Research").click()
    with page.expect_response(
        lambda response: (
            response.url.endswith("/api/hub/project-tree") and response.request.method == "POST"
        )
    ) as response:
        page.locator(f'[data-tree-node="{destination.id}"]').drag_to(
            page.locator(f'[data-tree-node="{outer.id}"]'), target_position={"x": 60, "y": 2}
        )
    assert response.value.ok
    expect(page.locator(f'[data-tree-node="{destination.id}"]')).to_be_visible()
    roots = [node.id for node in ProjectTree(live_app.store).view().nodes if node.parent_id is None]
    assert roots.index(destination.id) < roots.index(outer.id)
    page.reload()
    expect(page.locator(f'[data-tree-node="{destination.id}"]')).to_be_visible()
    rows = page.locator(".project-tree > [data-tree-node]").evaluate_all(
        "nodes => nodes.map(node => node.dataset.treeNode)"
    )
    assert rows.index(destination.id) < rows.index(outer.id)
    assert store.get_artifact(item.id).project_id == item.project_id
    assert source.read_bytes() == original
    _ = page.screenshot(path=str(artifacts / "nested-projects-light.png"))
    page.evaluate("document.documentElement.dataset.theme='dark'")
    expect(page.locator(f'[data-project="folder:{outer.id}"]')).to_have_css(
        "background-color", "rgba(0, 0, 0, 0)"
    )
    _ = page.screenshot(path=str(artifacts / "nested-projects-dark.png"))
    page.get_by_role("button", name="Options for Allianz").click()
    _ = page.screenshot(path=str(artifacts / "project-menu-dark.png"))
    page.keyboard.press("Escape")
    page.set_viewport_size({"width": 390, "height": 844})
    _ = page.screenshot(path=str(artifacts / "nested-projects-mobile.png"))
    page.set_viewport_size({"width": 1600, "height": 1000})
    page.get_by_role("button", name="Options for Research").click()
    page.get_by_role("menuitem", name="Rename", exact=True).click()
    page.get_by_role("dialog").get_by_label("Name", exact=True).fill("Archive")
    page.get_by_role("dialog").get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_role("button", name="Archive", exact=True)).to_be_visible()
    page.locator(f'[data-tree-node="{item.project_id}"] .project-tree-open').press("Alt+ArrowLeft")
    expect(
        page.locator(f'[data-folder-children="{outer.id}"] > [data-tree-node="{item.project_id}"]')
    ).to_be_visible()
    page.get_by_role("button", name="Options for Allianz").click()
    page.get_by_role("menuitem", name="Remove folder", exact=True).click()
    expect(page.get_by_role("button", name="Allianz", exact=True)).to_have_count(0)
    assert store.get_artifact(item.id).project_id == item.project_id
    assert source.read_bytes() == original
