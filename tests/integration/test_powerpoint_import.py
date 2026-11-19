"""A fictional owner imports and presents a PowerPoint through the real UI."""

import io
import os
from pathlib import Path
from zipfile import ZipFile

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
    image = slide.locator("img")
    expect(image).to_be_visible()
    assert image.evaluate("e => [e.naturalWidth, e.naturalHeight]") == [200, 120]
    with ZipFile(io.BytesIO(fictional_layers_powerpoint())) as source:
        original = source.read("ppt/media/image1.png")
    response = page.request.get(str(image.evaluate("e => e.src")))
    assert response.ok and response.body() == original
    crop = image.evaluate("""e => {
        const image = e.getBoundingClientRect(), frame = e.parentElement.getBoundingClientRect();
        return [image.width / frame.width, image.height / frame.height,
                (image.left - frame.left) / frame.width, (image.top - frame.top) / frame.height];
    }""")
    assert crop == pytest.approx([1 / 0.7, 1 / 0.7, -0.2 / 0.7, -0.1 / 0.7], abs=0.002)
    translucent = slide.locator('[data-pptx-object="Rectangle 3"]')
    expect(translucent).to_have_css("background-color", "rgba(219, 154, 76, 0.6)")
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
    assert stage.locator("img").evaluate("e => [e.naturalWidth, e.naturalHeight]") == [200, 120]
    stage_crop = stage.locator("img").evaluate("""e => {
        const image = e.getBoundingClientRect(), frame = e.parentElement.getBoundingClientRect();
        return [image.width / frame.width, image.height / frame.height,
                (image.left - frame.left) / frame.width, (image.top - frame.top) / frame.height];
    }""")
    assert stage_crop == pytest.approx(crop, abs=0.002)
    expect(stage.locator('[data-pptx-object="Rectangle 3"]')).to_have_css(
        "background-color", "rgba(219, 154, 76, 0.6)"
    )
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


def test_placeholder_geometry_follows_master_layout_slide_precedence() -> None:
    """Use the master-only fixture and confirm each more local transform takes precedence."""
    from copy import deepcopy
    from xml.etree import ElementTree as ET
    from zipfile import ZIP_DEFLATED

    from artifact_studio.powerpoint.importer import compile_slides
    from artifact_studio.powerpoint.package import Package

    drawing = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    presentation = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
    slide_name = "ppt/slides/slide1.xml"
    layout_name = "ppt/slideLayouts/slideLayout1.xml"
    master_name = "ppt/slideMasters/slideMaster1.xml"

    def title(root: ET.Element, kind: str) -> ET.Element:
        return next(
            shape
            for shape in root.iter(presentation + "sp")
            if (ph := shape.find(f"./{presentation}nvSpPr/{presentation}nvPr/{presentation}ph"))
            is not None
            and ph.get("type") == kind
        )

    with ZipFile(io.BytesIO(fictional_powerpoint())) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    slide = ET.fromstring(files[slide_name])
    layout = ET.fromstring(files[layout_name])
    master = ET.fromstring(files[master_name])
    master_transform = title(master, "title").find(f"./{presentation}spPr/{drawing}xfrm")
    assert master_transform is not None
    assert title(slide, "ctrTitle").find(f"./{presentation}spPr/{drawing}xfrm") is None
    assert title(layout, "ctrTitle").find(f"./{presentation}spPr/{drawing}xfrm") is None

    def transformed(root: ET.Element, x: int) -> None:
        shape_props = title(root, "ctrTitle").find(presentation + "spPr")
        assert shape_props is not None
        transform = deepcopy(master_transform)
        offset = transform.find(drawing + "off")
        assert offset is not None
        offset.set("x", str(x))
        shape_props.insert(0, transform)

    def render() -> str:
        output = io.BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            for name, data in files.items():
                if name == slide_name:
                    data = ET.tostring(slide)
                elif name == layout_name:
                    data = ET.tostring(layout)
                archive.writestr(name, data)
        package = Package(output.getvalue())
        with package.archive:
            slides, _ = compile_slides(package)
        return slides[0].html.split('data-pptx-object="Title 1"', 1)[1].split(">", 1)[0]

    assert "left:5.62500046%" in render()  # 685800 EMUs from the master
    transformed(layout, 1219200)
    assert "left:10.0000008%" in render()
    transformed(slide, 2438400)
    assert "left:20.0000016%" in render()
