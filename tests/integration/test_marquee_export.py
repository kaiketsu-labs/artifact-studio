"""Real pointer selection and image downloads, using only fictional source bundles."""

import base64
import io
import os
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import Page, expect

from artifact_studio.artifact_models import ArtifactFileInput
from artifact_studio.models import SlidePatch
from artifact_studio.storage import digest
from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_artifact_workspace import register

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]

SHAPES = (
    '<div id="backdrop" data-element-id="backdrop" style="position:absolute;left:300px;'
    'top:100px;width:900px;height:500px;background:#e5e7eb"></div>'
    '<div id="alpha" data-element-id="alpha" aria-label="Alpha" style="position:absolute;'
    'left:100px;top:200px;width:200px;height:120px;background:#f04444"></div>'
    '<div id="beta" data-element-id="beta" aria-label="Beta" style="position:absolute;'
    'left:450px;top:200px;width:200px;height:120px;background:#315bea"></div>'
)


def drag(page: Page, selector: str, start: tuple[float, float], end: tuple[float, float]) -> None:
    """Draw in preview coordinates, respecting the actual iframe zoom in the editor."""
    bounds = page.locator(selector).bounding_box()
    assert bounds
    size = page.locator(selector).evaluate(
        "node => ({width:node.clientWidth,height:node.clientHeight})"
    )
    width, height = size["width"], size["height"]
    page.mouse.move(
        bounds["x"] + start[0] / width * bounds["width"],
        bounds["y"] + start[1] / height * bounds["height"],
    )
    page.mouse.down()
    page.mouse.move(
        bounds["x"] + end[0] / width * bounds["width"],
        bounds["y"] + end[1] / height * bounds["height"],
        steps=8,
    )
    page.mouse.up()


def verify_png(path: Path) -> None:
    """Require selected paint and transparent gaps without other objects."""
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        assert rgba.size == (554, 124)
        assert rgba.getpixel((20, 20)) == (240, 68, 68, 255)
        assert rgba.getpixel((500, 20)) == (49, 91, 234, 255)
        assert rgba.getchannel("A").getextrema() == (0, 255)
        assert rgba.getpixel((300, 60)) == (0, 0, 0, 0)


def test_slide_marquee_and_transparent_selection_download(
    live_app: RunningApp, page: Page, tmp_path: Path, artifacts: Path
) -> None:
    """Select and export displayed objects without saving feedback."""
    store = live_app.store
    before = store.locate(store.read(DECK_ID), "cover")
    updated = store.revise(
        DECK_ID,
        "cover",
        digest(before.model_dump_json()),
        "marquee-slide-fixture",
        SlidePatch(html='<article class="slide" style="background:#fff">' + SHAPES + "</article>"),
    )
    page.goto(f"{live_app.url}/?deck={DECK_ID}#cover")
    frame = page.frame_locator("#preview")
    expect(frame.locator("#alpha")).to_be_visible()
    drag(page, "#preview", (80, 180), (700, 380))
    expect(page.locator("#target-members .target-member")).to_have_count(2)
    expect(page.locator("#group-selection-boxes .element-box")).to_have_count(2)
    frame.locator("#alpha").click(button="right")
    _ = page.screenshot(path=str(artifacts / "slide-selection-menu.png"))
    with page.expect_download() as download:
        page.get_by_role("menuitem", name="Save as image…", exact=True).click()
    exported = tmp_path / "selection.png"
    download.value.save_as(exported)
    verify_png(exported)
    after = store.locate(store.read(DECK_ID), "cover")
    assert after.model_dump() == updated.model_dump()
    assert store.feedback_feed(DECK_ID).events == []
    expect(page.locator("#target-members .target-member")).to_have_count(2)


def test_html_marquee_and_png_preserve_bundle_and_context(
    live_app: RunningApp, page: Page, tmp_path: Path, artifacts: Path
) -> None:
    """Use the same marquee inside an opaque artifact and honor a user-initiated export handoff."""
    registry, item, _, _, _ = register(live_app, tmp_path)
    source = (
        "<!doctype html><html><head><style>body{margin:0;min-height:700px}</style></head><body>"
        + SHAPES
        + "</body></html>"
    )
    item = registry.revise_artifact(
        item.id,
        [ArtifactFileInput(path="index.html", text=source)],
        item.content_hash,
        "marquee-html-fixture",
    )
    page.goto(f"{live_app.url}/?artifact={item.id}")
    page.get_by_role("button", name="Select", exact=True).click()
    frame = page.frame_locator("#hub-html-preview")
    expect(frame.locator("#alpha")).to_be_visible()
    drag(page, "#hub-html-preview", (80, 180), (700, 380))
    expect(page.locator("#hub-targets [data-remove-target]")).to_have_count(2)
    frame.locator("#alpha").click(button="right")
    _ = page.screenshot(path=str(artifacts / "html-selection-menu.png"))
    with page.expect_download() as download:
        page.get_by_role("menuitem", name="Save as image…", exact=True).click()
    exported = tmp_path / "artifact-selection.png"
    download.value.save_as(exported)
    verify_png(exported)
    assert registry.get_artifact(item.id).content_hash == item.content_hash
    assert registry.feedback(item.id) == []


def test_reverse_additive_and_cancelled_marquees(
    live_app: RunningApp, page: Page, artifacts: Path
) -> None:
    """Keep additive selection, cancellation and menu placement predictable."""
    store = live_app.store
    before = store.locate(store.read(DECK_ID), "cover")
    _ = store.revise(
        DECK_ID,
        "cover",
        digest(before.model_dump_json()),
        "marquee-gestures",
        SlidePatch(html='<article class="slide">' + SHAPES + "</article>"),
    )
    page.goto(f"{live_app.url}/?deck={DECK_ID}#cover")
    frame = page.frame_locator("#preview")
    expect(frame.locator("#alpha")).to_be_visible()
    drag(page, "#preview", (80, 180), (320, 340))
    expect(page.locator("#selection-box")).to_be_visible()
    page.keyboard.down("Shift")
    drag(page, "#preview", (430, 340), (680, 180))
    page.keyboard.up("Shift")
    expect(page.locator("#target-members .target-member")).to_have_count(2)
    frame.locator("#alpha").click(button="right")
    expect(page.get_by_role("menuitem", name="Save as image…", exact=True)).to_be_visible()
    expect(page.get_by_role("menuitem", name="Save original image…", exact=True)).to_have_count(0)
    page.keyboard.press("Escape")
    bounds = page.locator("#preview").bounding_box()
    assert bounds
    page.mouse.move(bounds["x"] + 30, bounds["y"] + 30)
    page.mouse.down()
    page.mouse.move(bounds["x"] + 150, bounds["y"] + 110, steps=4)
    expect(frame.locator("[data-review-chrome]")).to_be_visible()
    _ = page.screenshot(path=str(artifacts / "marquee-active.png"))
    page.keyboard.press("Escape")
    page.mouse.up()
    expect(frame.locator("[data-review-chrome]")).to_have_count(0)
    expect(page.locator("#target-members .target-member")).to_have_count(2)
    page.evaluate("document.documentElement.dataset.theme='dark'")
    frame.locator("#alpha").click(button="right")
    _ = page.screenshot(path=str(artifacts / "selection-menu-dark.png"))
    page.keyboard.press("Escape")
    page.set_viewport_size({"width": 920, "height": 850})
    frame.locator("#alpha").click(button="right")
    menu = page.get_by_role("menu", name="Selected objects")
    expect(menu).to_be_visible()
    menu_bounds = menu.bounding_box()
    assert menu_bounds and menu_bounds["x"] >= 0
    assert menu_bounds["x"] + menu_bounds["width"] <= 920
    _ = page.screenshot(path=str(artifacts / "selection-menu-narrow.png"))
    assert store.feedback_feed(DECK_ID).events == []


def image_source() -> tuple[bytes, str]:
    """Create a tiny non-PNG original so byte fidelity and raster conversion differ."""
    output = io.BytesIO()
    Image.new("RGB", (32, 24), "#b045c8").save(output, "JPEG", quality=95)
    original = output.getvalue()
    return original, "data:image/jpeg;base64," + base64.b64encode(original).decode()


@pytest.mark.parametrize("kind", ["slide", "html", "image"])
def test_original_image_and_png_downloads(
    kind: str, live_app: RunningApp, page: Page, tmp_path: Path
) -> None:
    """Offer original bytes and a transparent PNG through every image context menu."""
    original, url = image_source()
    html = (
        f'<img id="photo" src="{url}" alt="Fictional photograph" '
        'width="160" height="120" style="margin:20px">'
    )
    if kind == "slide":
        store = live_app.store
        asset = store.folder(DECK_ID) / "assets/photo.jpg"
        asset.parent.mkdir(exist_ok=True)
        _ = asset.write_bytes(original)
        html = html.replace(url, "assets/photo.jpg")
        before = store.locate(store.read(DECK_ID), "cover")
        _ = store.revise(
            DECK_ID,
            "cover",
            digest(before.model_dump_json()),
            "image-export-fixture",
            SlidePatch(html='<article class="slide">' + html + "</article>"),
        )
        page.goto(f"{live_app.url}/?deck={DECK_ID}#cover")
        image = page.frame_locator("#preview").locator("#photo")
    else:
        registry, item, _, _, _ = register(live_app, tmp_path)
        if kind == "html":
            item = registry.revise_artifact(
                item.id,
                [ArtifactFileInput(path="index.html", text=html)],
                item.content_hash,
                "image-export-fixture",
            )
        else:
            folder = tmp_path / "picture"
            folder.mkdir()
            _ = (folder / "photo.jpg").write_bytes(original)
            item = registry.register_artifact(item.project_id, "Picture", folder, "photo.jpg")
        page.goto(f"{live_app.url}/?artifact={item.id}")
        if kind == "html":
            page.get_by_role("button", name="Select", exact=True).click()
            image = page.frame_locator("#hub-html-preview").locator("#photo")
        else:
            image = page.locator("#hub-image")
        if kind == "html":
            image.click()
            page.reload()
            page.get_by_role("button", name="Select", exact=True).click()
    image.click(button="right")
    with page.expect_download() as downloaded:
        page.get_by_role("menuitem", name="Save original image…", exact=True).click()
    target = tmp_path / f"{kind}-original.jpg"
    downloaded.value.save_as(target)
    assert target.read_bytes() == original
    image.click(button="right")
    with page.expect_download() as downloaded:
        page.get_by_role("menuitem", name="Save as image…", exact=True).click()
    target = tmp_path / f"{kind}.png"
    downloaded.value.save_as(target)
    with Image.open(target) as exported:
        assert exported.format == "PNG"
        assert exported.size == ((32, 24) if kind == "image" else (164, 124))


def test_html_export_includes_unsent_preview_without_saving_it(
    live_app: RunningApp, page: Page, tmp_path: Path
) -> None:
    """Rasterize the visible local draft while preserving source, notes and feedback."""
    registry, item, _, _, source = register(live_app, tmp_path)
    original = source.read_text()
    page.goto(f"{live_app.url}/?artifact={item.id}")
    page.get_by_role("button", name="Select", exact=True).click()
    frame = page.frame_locator("#hub-html-preview")
    frame.locator("#title").click()
    inspector = page.locator("#hub-element-properties")
    inspector.get_by_role("button", name="Edit text", exact=True).click()
    inspector.get_by_label("Text", exact=True).fill("Unsent preview")
    inspector.get_by_label("Font size", exact=True).fill("30")
    expect(frame.locator("#title")).to_have_text("Unsent preview")
    frame.locator("#title").click(button="right")
    with page.expect_download() as downloaded:
        page.get_by_role("menuitem", name="Save as image…", exact=True).click()
    target = tmp_path / "unsent.png"
    downloaded.value.save_as(target)
    with Image.open(target) as exported:
        assert exported.height >= 40
    assert registry.feedback(item.id, include_pending=True) == []
    assert source.read_text() == original
    assert registry.get_artifact(item.id).content_hash == item.content_hash
    expect(inspector.get_by_role("button", name="Send edit")).to_be_visible()


def test_canvas_and_svg_exports_use_visible_pixels(
    live_app: RunningApp, page: Page, tmp_path: Path
) -> None:
    """Freeze authored canvas drawing and retain vector paint without replaying source scripts."""
    registry, item, _, _, _ = register(live_app, tmp_path)
    html = (
        '<canvas id="chart" width="100" height="60"></canvas>'
        '<svg id="diagram" width="100" height="60" xmlns="http://www.w3.org/2000/svg">'
        '<rect width="100" height="60" fill="#20a565"/></svg>'
        '<script>const chart=document.querySelector("#chart");'
        'const pen=chart.getContext("2d");pen.fillStyle="#315bea";'
        'pen.fillRect(0,0,100,60);chart.dataset.ready="true";</script>'
    )
    item = registry.revise_artifact(
        item.id,
        [ArtifactFileInput(path="index.html", text=html)],
        item.content_hash,
        "paint-export-fixture",
    )
    page.goto(f"{live_app.url}/?artifact={item.id}")
    frame = page.frame_locator("#hub-html-preview")
    expect(frame.locator("#chart")).to_have_attribute("data-ready", "true")
    page.get_by_role("button", name="Select", exact=True).click()
    for selector, color in [("#chart", (49, 91, 234, 255)), ("#diagram", (32, 165, 101, 255))]:
        frame.locator(selector).click(button="right")
        with page.expect_download() as downloaded:
            page.get_by_role("menuitem", name="Save as image…", exact=True).click()
        target = tmp_path / f"{selector[1:]}.png"
        downloaded.value.save_as(target)
        with Image.open(target) as image:
            assert image.size == (104, 64)
            assert image.convert("RGBA").getpixel((30, 30)) == color
    assert registry.get_artifact(item.id).content_hash == item.content_hash
    assert registry.feedback(item.id, include_pending=True) == []
