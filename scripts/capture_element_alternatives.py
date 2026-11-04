"""Capture the production element-options workflow with fictional authored proposals.

Run: uv run python -m scripts.capture_element_alternatives [--output /tmp/element-docs]
"""

import argparse
import hashlib
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from playwright.sync_api import Page, expect, sync_playwright

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.libraries import Libraries, Library
from artifact_studio.models import SlidePatch
from artifact_studio.storage import digest
from scripts.screenshot_fixture import seed
from tests.integration.support import DECK_ID, PROJECT, running_app
from tests.integration.test_element_alternatives import open_group, option, seed_elements
from tests.integration.test_slide_designs import history_chrome_thumbnail


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT)
    parser.add_argument("--axe", type=Path, help="Optional local axe-core script")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="element-alternative-docs-") as directory:
        root = Path(directory)
        staging = root / "captures"
        captures: dict[str, object] = {}
        audits: dict[str, object] = {}
        with running_app(root, root / "server.log") as app, sync_playwright() as playwright:
            _ = seed(app)
            registry = ArtifactStore(app.store)
            project = registry.create_project("Fictional review project")
            registry.set_artifact_project(DECK_ID, project.id)
            groups = seed_elements(app, slide_id="workflow")
            browser = playwright.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1600, "height": 1100},
                color_scheme="light",
                reduced_motion="reduce",
                locale="en-GB",
                timezone_id="Europe/Berlin",
            )
            context.add_init_script(
                "if(window===window.top)localStorage.setItem('artifact-studio:notes-open','true')"
            )
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(error.stack or str(error)))

            def capture(name: str, view: Page = page) -> None:
                _ = view.evaluate("document.fonts.ready")
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                data = view.screenshot(path=target, animations="disabled")
                captures[name] = {
                    "viewport": view.viewport_size,
                    "captured_at": datetime.now(UTC).isoformat(),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
                if args.axe:
                    view.add_script_tag(path=str(args.axe))
                    audits[name] = view.evaluate("async()=> (await axe.run()).violations")
                print(f"Captured {name}", flush=True)

            def appearance(value: str) -> None:
                page.get_by_role("button", name="Menu", exact=True).click()
                page.get_by_role("menuitem", name="Appearance", exact=True).click()
                page.get_by_role("menuitemradio", name=value, exact=True).click()
                expect(page.locator("html")).to_have_attribute("data-theme", value.lower())

            try:
                _ = page.goto(f"{app.url}/?deck={DECK_ID}#workflow")
                expect(page.locator("#element-alternative-toggle")).to_be_visible()
                capture("docs/screenshots/element-alternatives-available.png")
                open_group(page, groups["headline"])
                option(page, groups["headline"], 1).hover()
                expect(page.frame_locator("#preview").locator("#headline")).to_have_text(
                    "A stronger story, every review."
                )
                capture("docs/assets/element-alternatives.png")
                capture("docs/screenshots/element-alternatives-headline.png")
                page.locator("#element-alternative-panel [data-ea-close]").click()
                open_group(page, groups["text"])
                option(page, groups["text"], 2).click()
                capture("docs/screenshots/element-alternatives-text.png")
                page.keyboard.press("Escape")
                open_group(page, groups["graphic"])
                option(page, groups["graphic"], 1).click()
                for index in range(3):
                    expect(
                        page.frame_locator("#element-alternative-panel iframe")
                        .nth(index)
                        .locator("html")
                    ).to_have_attribute("data-element-preview-ready", "true")
                capture("docs/screenshots/element-alternatives-graphic.png")
                page.keyboard.press("Escape")
                open_group(page, groups["headline"])
                option(page, groups["headline"], 1).click()
                page.locator("#element-alternative-panel [data-ea-compare]").click()
                expect(page.locator(".ea-comparison")).to_be_visible()
                capture("docs/screenshots/element-alternatives-compare.png")
                page.locator('[data-ea-view="slide"]').click()
                page.wait_for_timeout(350)
                capture("docs/screenshots/element-alternatives-full-slide.png")
                page.locator(".ea-comparison [data-ea-compare-close]").click()
                page.locator("#element-alternative-panel [data-ea-close]").click()
                expect(page.locator("#element-alternative-panel")).to_be_hidden()
                expect(page.frame_locator("#preview").locator("#headline")).to_have_text(
                    "A stronger story, every review."
                )
                expect(page.locator("#preview-wrap.has-unseen-changes")).to_have_count(0)
                capture("docs/screenshots/element-alternatives-chosen.png")
                history_chrome_thumbnail(page)
                page.get_by_role("button", name="Version history", exact=True).click()
                history = page.locator(".ea-history")
                history.locator(":scope > summary").click()
                history.locator(
                    f'.ea-saved-group[data-set-id="{groups["headline"]["id"]}"] > summary'
                ).click()
                capture("docs/screenshots/element-alternatives-history.png")
                page.keyboard.press("Escape")
                appearance("Dark")
                open_group(page, groups["text"])
                option(page, groups["text"], 2).click()
                capture("docs/screenshots/element-alternatives-dark.png")
                page.keyboard.press("Escape")
                appearance("Light")
                touch = browser.new_context(
                    viewport={"width": 390, "height": 844},
                    has_touch=True,
                    is_mobile=True,
                    reduced_motion="reduce",
                    locale="en-GB",
                )
                try:
                    touch_page = touch.new_page()
                    touch_page.on("pageerror", lambda error: errors.append(str(error)))
                    _ = touch_page.goto(f"{app.url}/?deck={DECK_ID}#workflow")
                    open_group(touch_page, groups["text"])
                    option(touch_page, groups["text"], 1).tap()
                    capture("docs/screenshots/element-alternatives-mobile.png", touch_page)
                    assert touch_page.evaluate(
                        "document.documentElement.scrollWidth === innerWidth"
                    )
                finally:
                    touch.close()
                page.locator("#sorter-view").click()
                expect(page.locator("#sorter-presentation-title")).to_be_visible()
                capture("docs/screenshots/sorter-header.png")
                page.get_by_role("button", name="Edit view", exact=True).click()
                history_html = app.store.locate(app.store.read(DECK_ID), "workflow").html
                for index in range(5):
                    current = app.store.locate(app.store.read(DECK_ID), "workflow")
                    _ = app.store.revise(
                        DECK_ID,
                        "workflow",
                        digest(current.model_dump_json()),
                        f"history-docs-{index}",
                        SlidePatch(
                            html=history_html.replace(
                                '<article class="slide"',
                                f'<article data-review-pass="{index}" class="slide"',
                                1,
                            )
                        ),
                    )
                page.reload()
                history_path = f"/api/decks/{DECK_ID}/slides/workflow/history"
                versions = page.request.get(app.url + history_path + "?include_all=true").json()
                thumbnails: dict[str, bytes] = {}
                thumbnail_page = context.new_page()
                try:
                    for item in versions["items"]:
                        query = urlencode({"version_id": item["version_id"]})
                        thumbnail_page.goto(app.url + history_path + "/preview?" + query)
                        expect(thumbnail_page.locator(".slide")).to_be_visible()
                        thumbnail_page.evaluate("window.ArtifactStudioCaptureReady")
                        thumbnails[item["version_id"]] = thumbnail_page.locator(
                            ".slide"
                        ).screenshot()
                finally:
                    thumbnail_page.close()
                page.route(
                    "**/history/thumbnail?**",
                    lambda route: route.fulfill(
                        content_type="image/png",
                        body=thumbnails[
                            parse_qs(urlsplit(route.request.url).query)["version_id"][0]
                        ],
                    ),
                )
                page.get_by_role("button", name="Version history", exact=True).click()
                expect(page.locator(".version-history .version-name")).not_to_be_empty()
                expect(page.locator(".version-history .version-preview iframe")).to_be_visible()
                expect(
                    page.frame_locator(".version-preview iframe").locator("#headline")
                ).to_have_text("A stronger story, every review.")
                page.frame_locator(".version-preview iframe").locator(".slide").evaluate(
                    "() => window.ArtifactStudioCaptureReady"
                )
                expect(page.locator(".version-list .version-thumb.is-ready")).to_have_count(2)
                capture("docs/screenshots/history-grouped.png")
                page.keyboard.press("Escape")
                collection = Libraries(app.store).list()[0].model_dump()
                backup = Library(
                    id="review-docs-backup",
                    kind="github",
                    location="fictional-slides/demo",
                    path="/tmp/fictional-reference-cache",
                    sync_enabled=True,
                    auto_sync_enabled=True,
                ).model_dump()
                page.route(
                    "**/api/libraries", lambda route: route.fulfill(json=[collection, backup])
                )
                page.route(
                    "**/api/library-sync",
                    lambda route: route.fulfill(
                        json=[
                            {
                                "library_id": backup["id"],
                                "phase": "error",
                                "error": "Connection unavailable. Reconnect, then retry.",
                                "last_synced_at": "",
                            }
                        ]
                    ),
                )
                page.locator("#storage-status").click()
                page.get_by_role("button", name="Retry GitHub backup", exact=True).hover()
                expect(page.get_by_role("tooltip")).to_be_visible()
                capture("docs/screenshots/storage-backup-retry.png")
                if errors:
                    raise RuntimeError("\n".join(errors))
                if args.axe and any(audits.values()):
                    _ = Path("/tmp/element-alternatives-axe.json").write_text(
                        json.dumps(audits, indent=2) + "\n"
                    )
                    _ = shutil.copytree(
                        staging, "/tmp/element-alternatives-capture-failed", dirs_exist_ok=True
                    )
                    raise RuntimeError("Axe violations: /tmp/element-alternatives-axe.json")
                manifest = staging / "docs/screenshots/element-alternatives-captures.json"
                _ = manifest.write_text(
                    json.dumps(
                        {
                            "fixture": "Fictional Studio Blue slides and element proposals",
                            "captured_at": datetime.now(UTC).isoformat(),
                            "screenshots": captures,
                            "timeline_thumbnail": (
                                "Element history uses a fictional PNG; grouped history uses "
                                "browser captures of the exact fictional revisions. Neither "
                                "exercises the protected renderer."
                            ),
                            "backup_status": (
                                "Fictional repository and connection failure; no real backup runs"
                            ),
                            "axe": audits if args.axe else "Not requested",
                        },
                        indent=2,
                    )
                    + "\n"
                )
            finally:
                context.close()
                browser.close()
        for source in staging.rglob("*"):
            if source.is_file():
                target = args.output.resolve() / source.relative_to(staging)
                target.parent.mkdir(parents=True, exist_ok=True)
                _ = shutil.copy2(source, target)


if __name__ == "__main__":
    main()
