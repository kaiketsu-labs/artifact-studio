"""Sort and group fictional artifacts through the visible library controls."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from artifact_studio.artifact_models import ArtifactFileInput, ArtifactView, ProjectView
from artifact_studio.artifacts import ArtifactStore
from tests.integration.support import RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def sorting_project(app: RunningApp, root: Path) -> ProjectView:
    """Seed different formats with creation and modification in different orders."""
    store = ArtifactStore(app.store)
    project = store.create_project("Mission control")
    sources = (
        ("Orbit 10", "index.html", "<h1>Orbit 10</h1>"),
        ("Orbit 2", "guide.md", "# Orbit 2"),
        ("Beacon", "beacon.svg", '<svg xmlns="http://www.w3.org/2000/svg"/>'),
    )
    items: list[ArtifactView] = []
    for title, name, content in sources:
        folder = root / title
        folder.mkdir()
        _ = (folder / name).write_text(content)
        items.append(store.register_artifact(project.id, title, folder, name))
    first = items[0]
    _ = store.revise_artifact(
        first.id,
        [ArtifactFileInput(path="index.html", text="<h1>Orbit 10 updated</h1>")],
        first.content_hash,
        "sorting-fixture-revision",
    )
    return project


def titles(page: Page) -> list[str]:
    """Read displayed card titles in visual source order."""
    return page.locator("#hub-grid .hub-card-title>span").all_text_contents()


def test_sorting_dates_names_groups_and_persistence(
    live_app: RunningApp, page: Page, tmp_path: Path
) -> None:
    """Keep one sort choice while filtering, reloading and switching overviews."""
    project = sorting_project(live_app, tmp_path)
    page.goto(f"{live_app.url}/?project={project.id}")
    control = page.get_by_role("combobox", name="Sort artifacts")
    expect(control).to_have_value("updated")
    expect(page.locator("#hub-grid .hub-card")).to_have_count(3)
    assert titles(page) == ["Orbit 10", "Beacon", "Orbit 2"]
    control.select_option("created")
    assert titles(page) == ["Beacon", "Orbit 2", "Orbit 10"]
    control.select_option("name")
    assert titles(page) == ["Beacon", "Orbit 2", "Orbit 10"]
    page.get_by_role("button", name="Z to A", exact=True).click()
    assert titles(page) == ["Orbit 10", "Orbit 2", "Beacon"]
    expect(page.get_by_role("button", name="A to Z", exact=True)).to_be_focused()
    control.select_option("type")
    groups = page.locator(".hub-type-group")
    expect(groups).to_have_count(3)
    assert groups.locator("h2").all_text_contents() == ["HTML1", "Markdown1", "SVG1"]
    page.get_by_role("searchbox", name="Search artifacts").fill("Orbit")
    expect(groups).to_have_count(2)
    expect(page.locator("#hub-result-count")).to_have_text("2 artifacts")
    page.get_by_role("searchbox", name="Search artifacts").fill("missing")
    expect(page.get_by_role("heading", name="No matching artifacts")).to_be_visible()
    page.get_by_role("searchbox", name="Search artifacts").fill("")
    page.locator('[data-project="all"]').click()
    expect(control).to_have_value("type")
    expect(page.locator(".hub-type-group[data-file-type=presentation]")).to_be_visible()
    page.reload()
    expect(control).to_have_value("type")
    page.locator(f'[data-project="{project.id}"]').click()
    expect(control).to_have_value("type")
    expect(page.locator("#hub-grid .hub-card")).to_have_count(3)


def test_sort_keyboard_focus_and_narrow_dark_layout(
    live_app: RunningApp, page: Page, tmp_path: Path
) -> None:
    """Keep sorting reachable without overflow or losing keyboard focus."""
    project = sorting_project(live_app, tmp_path)
    page.goto(f"{live_app.url}/?project={project.id}")
    control = page.get_by_role("combobox", name="Sort artifacts")
    control.focus()
    control.select_option("type")
    expect(control).to_be_focused()
    page.get_by_role("button", name="Appearance", exact=True).click()
    page.get_by_role("menuitemradio", name="Dark", exact=True).click()
    page.set_viewport_size({"width": 390, "height": 844})
    expect(control).to_be_visible()
    control.select_option("name")
    expect(page.get_by_role("button", name="Z to A", exact=True)).to_be_visible()
    bounds = page.locator(".hub-sort").bounding_box()
    assert bounds is not None and 0 <= bounds["x"] < bounds["x"] + bounds["width"] <= 390
    assert page.locator("body").evaluate("node => node.scrollWidth <= innerWidth")
