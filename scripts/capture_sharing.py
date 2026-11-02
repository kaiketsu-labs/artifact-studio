"""Capture real sharing panels with fictional files and a simulated OS boundary.

The native device picker is outside the browser and is not captured or imitated.
Run: uv run python -m scripts.capture_sharing
"""

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from scripts.screenshot_fixture import seed
from tests.integration.native_sharing import install_native_share
from tests.integration.support import DECK_ID, PROJECT, running_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="native-sharing-docs-") as directory:
        root = Path(directory)
        staging = root / "captures"
        captures: dict[str, object] = {}
        with running_app(root, root / "server.log") as app, sync_playwright() as playwright:
            _ = seed(app)
            browser = playwright.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1440, "height": 1000},
                color_scheme="light",
                reduced_motion="reduce",
                locale="en-GB",
                timezone_id="Europe/Berlin",
            )
            page = context.new_page()
            install_native_share(page)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def capture(name: str) -> None:
                _ = page.evaluate("document.fonts.ready")
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                data = page.screenshot(path=target, animations="disabled")
                captures[name] = {
                    "viewport": page.viewport_size,
                    "sha256": hashlib.sha256(data).hexdigest(),
                }

            try:
                _ = page.goto(f"{app.url}/?deck={DECK_ID}")
                page.get_by_role("button", name="Share presentation", exact=True).click()
                panel = page.locator(".studio-share")
                panel.get_by_role("radio", name="HTML Interactive · images & animations").check()
                expect(panel.get_by_role("button", name="Share…", exact=True)).to_be_enabled()
                capture("docs/assets/sharing.png")
                page.set_viewport_size({"width": 390, "height": 844})
                capture("docs/screenshots/sharing-mobile.png")
                page.set_viewport_size({"width": 1440, "height": 1000})
                panel.get_by_role("button", name="Close share panel").click()
                _ = page.evaluate(
                    "Object.defineProperty(navigator, 'share', "
                    "{configurable:true, value:undefined})"
                )
                page.get_by_role("button", name="Share presentation", exact=True).click()
                panel.get_by_role("radio", name="HTML Interactive · images & animations").check()
                expect(panel.get_by_role("button", name="Share…", exact=True)).to_be_hidden()
                capture("docs/screenshots/sharing-fallback.png")
                if errors:
                    raise RuntimeError("\n".join(errors))
                manifest = staging / "docs/screenshots/sharing-captures.json"
                _ = manifest.write_text(
                    json.dumps(
                        {
                            "fixture": "Original fictional Studio Blue presentation",
                            "native_share": "Simulated API availability; only web UI is captured",
                            "screenshots": captures,
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
