"""A fictional owner imports and presents a PowerPoint through the real UI."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from artifact_studio.artifacts import ArtifactStore
from tests.integration.support import RunningApp
from tests.pptx_fixture import fictional_powerpoint

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_owner_imports_and_presents_powerpoint(
    live_app: RunningApp, page: Page, artifacts: Path
) -> None:
    """Import one PPTX from the project, then open and present the resulting deck."""
    project = ArtifactStore(live_app.store).create_project("Fictional garden")
    page.goto(f"{live_app.url}/?project={project.id}")
    page.get_by_role("button", name="Import", exact=True).click()
    dialog = page.get_by_role("dialog", name="Import artifact")
    dialog.get_by_label("Title", exact=True).fill("Garden plan")
    page.locator("#hub-import-files").set_input_files(
        {
            "name": "fictional-garden.pptx",
            "mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "buffer": fictional_powerpoint(),
        }
    )
    dialog.get_by_role("button", name="Import presentation").click()
    expect(page.frame_locator("#preview").get_by_text("Harbor Garden")).to_be_visible()
    title = page.frame_locator("#preview").get_by_text("Harbor Garden", exact=True)
    title.click(button="right")
    page.get_by_role("menuitem", name="Edit text", exact=True).click()
    text = page.get_by_role("textbox", name="Text", exact=True)
    text.fill("Harbor Garden revised")
    expect(page.frame_locator("#preview").get_by_text("Harbor Garden revised")).to_be_visible()
    page.screenshot(path=str(artifacts / "imported-editor.png"))
    page.get_by_role("button", name="Save text change", exact=True).click()
    expect(page.get_by_text("Text saved.")).to_be_visible()
    page.reload()
    expect(page.frame_locator("#preview").get_by_text("Harbor Garden revised")).to_be_visible()
    page.get_by_text("Season outlook", exact=True).first.click()
    cell = page.frame_locator("#preview").get_by_text("24 baskets", exact=True)
    cell.click(button="right")
    page.get_by_role("menuitem", name="Edit text", exact=True).click()
    page.get_by_role("textbox", name="Text", exact=True).fill("30 baskets")
    page.get_by_role("button", name="Save text change", exact=True).click()
    expect(page.get_by_text("Text saved.")).to_be_visible()
    page.reload()
    expect(page.frame_locator("#preview").get_by_text("30 baskets")).to_be_visible()
    page.screenshot(path=str(artifacts / "imported-second-editor.png"))
    page.frame_locator("#preview").locator(".slide").screenshot(
        path=str(artifacts / "garden-2-editor.png")
    )
    page.get_by_text("Harbor Garden revised", exact=True).first.click()
    page.get_by_role("link", name="Present", exact=True).click()
    viewer = page.frame_locator('iframe[title="Presentation"]')
    expect(viewer.locator("#stage").get_by_text("Harbor Garden revised")).to_be_visible()
    page.screenshot(path=str(artifacts / "imported-presentation.png"))
    viewer.locator("#stage .slide").screenshot(path=str(artifacts / "garden-1-present.png"))
    page.keyboard.press("Enter")
    expect(viewer.locator("#stage").get_by_text("Season outlook")).to_be_visible()
    expect(viewer.locator("#stage").get_by_text("Herbs")).to_be_visible()
    page.screenshot(path=str(artifacts / "imported-table-chart.png"))
    viewer.locator("#stage .slide").screenshot(path=str(artifacts / "garden-2-present.png"))


def test_owner_imports_four_by_three_layers(
    live_app: RunningApp, page: Page, artifacts: Path
) -> None:
    """Keep grouped overlaps and a picture visible on a 4:3 imported slide."""
    from tests.pptx_fixture import fictional_layers_powerpoint

    project = ArtifactStore(live_app.store).create_project("Fictional layers")
    page.goto(f"{live_app.url}/?project={project.id}")
    page.get_by_role("button", name="Import", exact=True).click()
    dialog = page.get_by_role("dialog", name="Import artifact")
    dialog.get_by_label("Title", exact=True).fill("Layer study")
    page.locator("#hub-import-files").set_input_files(
        {
            "name": "layers.pptx",
            "mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "buffer": fictional_layers_powerpoint(),
        }
    )
    dialog.get_by_role("button", name="Import presentation").click()
    frame = page.frame_locator("#preview")
    slide = frame.locator(".slide")
    expect(slide.locator("img")).to_be_visible()
    wrapped = slide.get_by_text("A small garden grows across the neighborhood")
    expect(wrapped).to_be_visible()
    assert wrapped.evaluate("e => e.getClientRects().length") > 1
    box = slide.bounding_box()
    assert box is not None and abs(box["width"] / box["height"] - 4 / 3) < 0.02
    page.screenshot(path=str(artifacts / "layers-editor.png"))
    slide.screenshot(path=str(artifacts / "layers-editor-crop.png"))
    page.get_by_role("link", name="Present", exact=True).click()
    viewer = page.frame_locator('iframe[title="Presentation"]')
    stage = viewer.locator("#stage .slide")
    expect(stage.locator("img")).to_be_visible()
    box = stage.bounding_box()
    assert box is not None and abs(box["width"] / box["height"] - 4 / 3) < 0.02
    page.screenshot(path=str(artifacts / "layers-presentation.png"))
    stage.screenshot(path=str(artifacts / "layers-present-crop.png"))


@pytest.mark.parametrize("variant", ["corrupt", "unsupported"])
def test_owner_sees_import_failure_without_leaving_project(
    live_app: RunningApp, browser: Browser, variant: str
) -> None:
    """A rejected file reports the reason and leaves the owner in the import dialog."""
    from tests.pptx_fixture import corrupt_powerpoint, unsupported_powerpoint

    project = ArtifactStore(live_app.store).create_project("Fictional rejection")
    page = browser.new_page()
    page.goto(f"{live_app.url}/?project={project.id}")
    page.get_by_role("button", name="Import", exact=True).click()
    dialog = page.get_by_role("dialog", name="Import artifact")
    dialog.get_by_label("Title", exact=True).fill("Rejected presentation")
    page.locator("#hub-import-files").set_input_files(
        {
            "name": "rejected.pptx",
            "mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "buffer": corrupt_powerpoint() if variant == "corrupt" else unsupported_powerpoint(),
        }
    )
    with page.expect_response(
        lambda response: response.url.endswith("/api/artifacts/import")
    ) as result:
        dialog.get_by_role("button", name="Import presentation").click()
    assert result.value.status == 400
    expect(dialog.get_by_role("alert")).not_to_be_empty()
    expect(dialog).to_be_visible()
    assert f"project={project.id}" in page.url
    page.close()
