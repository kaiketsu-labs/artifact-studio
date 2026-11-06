"""Browser regression for canonical ordering of untouched snapshot files."""

import os
from pathlib import Path
from typing import Literal

import pytest
from playwright.sync_api import Browser, expect

from artifact_studio.artifacts import ArtifactStore
from scripts.preview_favorites import prepare_covers
from tests.integration.support import RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


@pytest.mark.parametrize(("width", "theme"), [(1280, "light"), (390, "dark")])
def test_prefix_conflicting_snapshot_remains_available_in_library(
    live_app: RunningApp,
    browser: Browser,
    tmp_path: Path,
    artifacts: Path,
    width: int,
    theme: Literal["light", "dark"],
) -> None:
    """An intact owned bundle must not prevent the legacy presentation from loading."""
    store = ArtifactStore(live_app.store)
    project = store.create_project("Snapshot ordering")
    bundle = tmp_path / "bundle"
    (bundle / "assets").mkdir(parents=True)
    (bundle / "index.html").write_text('<h1 id="title">Intact snapshot</h1>')
    (bundle / "assets.js").write_text("// Fictional script\n")
    (bundle / "assets" / "icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    item = store.register_artifact(project.id, "Intact snapshot", bundle, "index.html")
    approved = store.approve(item.id, item.content_hash, True)
    assert approved.approved
    prepare_covers(live_app, browser)

    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=theme)
    page = context.new_page()
    try:
        page.goto(live_app.url, wait_until="networkidle")
        expect(
            page.get_by_text("An immutable artifact snapshot was modified outside the hub.")
        ).to_have_count(0)
        expect(page.get_by_text("CI workflow", exact=True)).to_be_visible()
        search = page.get_by_role("searchbox", name="Search artifacts")
        search.click()
        search.press("End")
        owned = page.get_by_role(
            "button", name="Open Intact snapshot, HTML page, Snapshot ordering", exact=True
        )
        expect(owned).to_be_visible()
        _ = page.screenshot(path=str(artifacts / f"library-{theme}.png"), full_page=True)
        owned.click()
        expect(page.frame_locator("#hub-html-preview").locator("#title")).to_have_text(
            "Intact snapshot"
        )
        _ = page.screenshot(path=str(artifacts / f"preview-{theme}.png"), full_page=True)
        page.reload(wait_until="networkidle")
        expect(page.frame_locator("#hub-html-preview").locator("#title")).to_have_text(
            "Intact snapshot"
        )
        current = store.get_artifact(item.id)
        assert (current.revision, current.content_hash, current.approved) == (
            approved.revision,
            approved.content_hash,
            True,
        )
    finally:
        context.close()
