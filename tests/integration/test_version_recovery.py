"""Real browser journeys for lazy history previews and separate workspace recovery."""

import io
import os
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import Page, Route, expect

from artifact_studio.artifact_models import ArtifactFileInput
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.models import SlidePatch
from artifact_studio.storage import Store, digest
from artifact_studio.version_history import HistoryPage
from tests.integration.support import DECK_ID, PRIVATE_NOTE, RunningApp, markup
from tests.integration.test_artifact_workspace import register

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_artifact_timeline_preview_restore_and_one_frame(
    live_app: RunningApp, page: Page, tmp_path: Path, artifacts: Path
) -> None:
    """Group rapid edits, preview an exact old bundle, and restore it as a new current save."""
    registry, item, _, _, _ = register(live_app, tmp_path)
    updated = registry.revise_artifact(
        item.id,
        [ArtifactFileInput(path="index.html", text='<h1 id="title">Revised heading</h1>')],
        item.content_hash,
        "browser-history-revise",
    )
    page.goto(f"{live_app.url}/?artifact={item.id}")
    page.get_by_role("button", name="Version history", exact=True).click()
    timeline = page.locator("dialog.version-history")
    expect(timeline).to_be_visible()
    expect(timeline.locator("summary small")).to_have_text("2 versions")
    expect(timeline.locator("iframe")).to_have_count(1)
    expect(page.frame_locator(".version-preview iframe").locator("#title")).to_have_text(
        "Revised heading"
    )
    expect(timeline.locator('[data-version="artifact:2"] img')).to_have_js_property(
        "naturalWidth", 400
    )
    timeline.locator('[data-version="artifact:1"]').click()
    expect(page.frame_locator(".version-preview iframe").locator("#title")).to_have_text(
        "Original heading"
    )
    expect(timeline.locator("iframe")).to_have_count(1)
    expect(timeline.locator('[data-version="artifact:1"] img')).to_have_js_property(
        "naturalWidth", 400
    )
    _ = page.screenshot(path=str(artifacts / "artifact-history.png"))
    page.emulate_media(color_scheme="dark")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    _ = page.screenshot(path=str(artifacts / "artifact-history-dark.png"))
    page.emulate_media(color_scheme="light")
    timeline.get_by_role("button", name="Restore version", exact=True).click()
    timeline.get_by_role("button", name="Restore", exact=True).click()
    expect(timeline).not_to_be_visible()
    restored = registry.get_artifact(item.id)
    assert restored.revision == updated.revision + 1
    expect(page.frame_locator("#hub-html-preview").locator("#title")).to_have_text(
        "Original heading"
    )


def test_slide_timeline_fits_preview_and_preserves_notes(
    live_app: RunningApp, page: Page, artifacts: Path
) -> None:
    """Restore old visible slide content without losing current private speaker context."""
    store = live_app.store
    before = store.locate(store.read(DECK_ID), "cover")
    previous = store.revise(
        DECK_ID,
        "cover",
        digest(before.model_dump_json()),
        "browser-history-slide-one",
        SlidePatch(html=markup("Earlier message")),
    )
    latest = store.revise(
        DECK_ID,
        "cover",
        digest(previous.model_dump_json()),
        "browser-history-slide-two",
        SlidePatch(html=markup("Current message")),
    )
    latest = store.revise(
        DECK_ID,
        "cover",
        digest(latest.model_dump_json()),
        "browser-history-metadata-only",
        SlidePatch(title="An internal slide label"),
    )
    page.goto(f"{live_app.url}/?editor=1&deck={DECK_ID}#cover")
    page.get_by_role("button", name="Version history", exact=True).click()
    timeline = page.locator("dialog.version-history")
    expect(timeline).to_be_visible()
    expect(timeline.locator("[data-version]")).to_have_count(4)
    expect(timeline.locator('[data-version][aria-current="true"] img')).to_have_js_property(
        "naturalWidth", 400
    )
    timeline.get_by_label("Version filter").select_option("all")
    expect(timeline.locator("[data-version]")).to_have_count(5)
    expect(timeline.get_by_text("Current · Same content", exact=True)).to_be_visible()
    timeline.get_by_label("Version filter").select_option("content")
    expect(timeline.locator("[data-version]")).to_have_count(4)
    history = live_app.get(f"/api/decks/{DECK_ID}/slides/cover/history")
    versions = HistoryPage.model_validate(history).items
    selected = next(item for item in versions if item.revision == previous.revision)
    timeline.locator(f'[data-version="{selected.version_id}"]').click()
    frame = page.frame_locator(".version-preview iframe")
    expect(frame.locator("h1")).to_have_text("Earlier message")
    assert PRIVATE_NOTE not in frame.locator("body").inner_text()
    expect(timeline.locator('[data-version][aria-current="true"] img')).to_have_js_property(
        "naturalWidth", 400
    )
    _ = page.screenshot(path=str(artifacts / "slide-history.png"))
    page.set_viewport_size({"width": 700, "height": 800})
    expect(timeline.get_by_role("button", name="Restore version", exact=True)).to_be_in_viewport()
    expect(timeline.get_by_label("Version filter")).to_be_in_viewport()
    _ = page.screenshot(path=str(artifacts / "slide-history-narrow.png"))
    timeline.get_by_role("button", name="Restore version", exact=True).click()
    timeline.get_by_role("button", name="Restore", exact=True).click()
    expect(timeline).not_to_be_visible()
    restored = store.locate(store.read(DECK_ID), "cover")
    assert restored.revision == latest.revision + 1 and restored.notes == before.notes


def test_timeline_loads_visible_thumbnails_and_scrolls_with_keyboard(
    live_app: RunningApp, page: Page
) -> None:
    """Keep long histories lazy while keyboard selection exposes the relevant saved image."""
    store = live_app.store
    slide = store.locate(store.read(DECK_ID), "cover")
    for index in range(18):
        slide = store.revise(
            DECK_ID,
            slide.id,
            digest(slide.model_dump_json()),
            f"thumbnail-scroll-{index}",
            SlidePatch(html=markup(f"Audience message {index}")),
        )
    versions = HistoryPage.model_validate(
        live_app.get(f"/api/decks/{DECK_ID}/slides/cover/history")
    ).items
    pixels = io.BytesIO()
    Image.new("RGB", (400, 225), "#315fce").save(pixels, format="PNG")
    requests: set[str] = set()

    def thumbnail(route: Route) -> None:
        """Record thumbnail demand separately from the full real preview."""
        requests.add(route.request.url)
        route.fulfill(content_type="image/png", body=pixels.getvalue())

    page.route("**/history/thumbnail?*", thumbnail)
    page.goto(f"{live_app.url}/?editor=1&deck={DECK_ID}#cover")
    page.get_by_role("button", name="Version history", exact=True).click()
    timeline = page.locator("dialog.version-history")
    selected = timeline.locator('[data-version][aria-current="true"]')
    expect(selected.locator("img")).to_have_js_property("naturalWidth", 400)
    assert 0 < len(requests) < len(versions)
    for _ in range(12):
        selected.press("ArrowDown")
    expect(selected).to_have_attribute("data-version", versions[12].version_id)
    expect(selected.locator("img")).to_have_js_property("naturalWidth", 400)
    row = selected.bounding_box()
    listing = timeline.locator(".version-list").bounding_box()
    assert row and listing
    assert listing["y"] <= row["y"] <= listing["y"] + listing["height"] - row["height"]
    expect(timeline.locator("iframe")).to_have_count(1)
    timeline.get_by_role("button", name="Close version history").click()
    expect(timeline.locator("iframe")).to_have_count(0)


def test_local_workspace_download_preview_and_files_only_restore(
    live_app: RunningApp, page: Page, tmp_path: Path, artifacts: Path
) -> None:
    """Download and reopen an actual full archive without replacing or switching current work."""
    _, item, _, _, _ = register(live_app, tmp_path)
    page.goto(live_app.url)
    page.locator("#storage-status").click()
    with page.expect_download() as download_info:
        page.get_by_role("button", name="Save workspace", exact=True).click()
    archive = tmp_path / "workspace.zip"
    download_info.value.save_as(archive)
    page.get_by_role("button", name="Restore workspace", exact=True).click()
    recovery = page.locator("dialog.workspace-recovery")
    recovery.get_by_label("New folder name", exact=True).fill("Recovered browser workspace")
    recovery.get_by_label("Choose workspace archive").set_input_files(archive)
    expect(recovery.get_by_role("button", name="Restore files", exact=True)).to_be_visible()
    _ = page.screenshot(path=str(artifacts / "workspace-recovery.png"))
    recovery.get_by_role("button", name="Restore files", exact=True).click()
    expect(recovery.get_by_text("Workspace restored", exact=True)).to_be_visible()
    target = live_app.store.root.parent / "Recovered browser workspace"
    restored = Store(target, target / ".artifact-studio" / "review.sqlite3")
    assert ArtifactStore(restored).get_artifact(item.id).title == item.title
    assert ArtifactStore(live_app.store).get_artifact(item.id).title == item.title
    assert page.url == live_app.url + "/"
    recovery.get_by_role("button", name="Close recovery").click()
