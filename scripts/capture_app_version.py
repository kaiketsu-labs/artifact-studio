"""Capture real version controls using fictional release metadata and a disposable workspace."""

import argparse
import hashlib
import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import Route, expect, sync_playwright

from artifact_studio import __version__
from scripts.preview_favorites import prepare_covers
from scripts.screenshot_fixture import seed
from tests.integration.support import PROJECT, running_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--axe", type=Path)
    args = parser.parse_args()
    output = PROJECT / "docs/screenshots"
    captures: dict[str, object] = {}
    stable_update = True
    with tempfile.TemporaryDirectory(prefix="version-capture-") as temporary:
        root = Path(temporary)
        with running_app(root, root / "server.log") as app, sync_playwright() as renderer:
            _ = seed(app)
            browser = renderer.chromium.launch()
            prepare_covers(app, browser)
            page = browser.new_page(viewport={"width": 1280, "height": 800})

            def releases(route: Route) -> None:
                response = route.fetch()
                info = response.json()
                nightly = info["include_nightly"]
                info.update(
                    latest_tag="nightly-20261106.123"
                    if nightly
                    else "v0.3.0"
                    if stable_update
                    else "v" + __version__,
                    update_available=nightly or stable_update,
                    update_check="ok",
                )
                route.fulfill(response=response, json=info)

            def capture(name: str) -> None:
                violations: object = None
                if args.axe:
                    _ = page.add_script_tag(path=str(args.axe))
                    violations = page.evaluate("async()=> (await axe.run()).violations")
                data = page.screenshot(path=output / name, animations="disabled")
                captures[name] = {
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "viewport": page.viewport_size,
                    "captured_at": datetime.now(UTC).isoformat(),
                    "axe_ran": bool(args.axe),
                    "axe_violations": violations,
                }
                print("Captured " + name, flush=True)

            page.route(re.compile(r"/api/app-info(?:/settings)?$"), releases)
            page.goto(app.url)
            menu = page.get_by_role("button", name="Menu", exact=True)
            expect(menu).to_have_attribute("data-app-update", "")
            menu.click()
            capture("app-version-menu.png")
            stable_update = False
            page.locator("[data-app-about]").click()
            dialog = page.get_by_role("dialog", name="About Artifact Studio")
            expect(dialog).to_contain_text("You’re up to date.")
            capture("app-version-current.png")
            dialog.get_by_role("checkbox", name="Include nightly builds").check()
            expect(dialog).to_contain_text("Update available: nightly-20261106.123")
            capture("app-version-nightly.png")
            page.set_viewport_size({"width": 390, "height": 844})
            page.evaluate("localStorage.setItem('artifact-studio:appearance','dark')")
            page.reload()
            menu.click()
            page.locator("[data-app-about]").click()
            expect(dialog.get_by_role("checkbox", name="Include nightly builds")).to_be_checked()
            expect(dialog).to_contain_text("nightly-20261106.123")
            capture("app-version-mobile.png")
            browser.close()
    _ = (output / "app-version-captures.json").write_text(json.dumps(captures, indent=2) + "\n")


if __name__ == "__main__":
    main()
