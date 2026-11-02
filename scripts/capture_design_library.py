"""Capture the real design library with original fictional sources.

Run: uv run python -m scripts.capture_design_library
"""

import argparse
import base64
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from artifact_studio.design_library import DesignCreate, DesignLibrary
from artifact_studio.models import FileInput
from scripts.screenshot_fixture import seed
from tests.integration.support import PROJECT, running_app


def upload(name: str, content: str) -> FileInput:
    return FileInput(name=name, data_base64=base64.b64encode(content.encode()).decode())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="design-library-docs-") as directory:
        root = Path(directory)
        staging = root / "captures"
        staging.mkdir()
        with running_app(root, root / "server.log") as app, sync_playwright() as playwright:
            _ = seed(app)
            library = DesignLibrary(app.store)
            editorial = library.create(
                DesignCreate(
                    name="Editorial system",
                    description="Quiet typography, clear hierarchy and generous space.",
                    files=[
                        upload(
                            "design-guidelines.md",
                            "# Editorial system\n\nOriginal fictional design reference.\n"
                            "Use clear hierarchy, short headings and generous margins.\n"
                            "Ink #142a43 · accent #426bec · paper #f7f8fa.\n",
                        ),
                        upload(
                            "palette.csv", "role,hex\nink,#142a43\naccent,#426bec\npaper,#f7f8fa\n"
                        ),
                        upload(
                            "layout-reference.svg",
                            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 400">'
                            '<rect width="600" height="400" fill="#f2efe9"/>'
                            '<text x="38" y="45" font-family="Arial" font-size="11" '
                            'letter-spacing="3" fill="#142a43">EDITORIAL / 01</text>'
                            '<text x="38" y="117" font-family="Georgia" font-size="51" '
                            'fill="#142a43">Clarity in</text>'
                            '<text x="38" y="173" font-family="Georgia" font-size="51" '
                            'fill="#142a43">every detail.</text>'
                            '<path d="M40 201H126" stroke="#426bec" stroke-width="5"/>'
                            '<text x="38" y="237" font-family="Arial" font-size="12" '
                            'fill="#667386">Thoughtful type. Room to breathe.</text>'
                            '<rect x="405" y="37" width="157" height="210" rx="80" '
                            'fill="#426bec"/>'
                            '<circle cx="483" cy="141" r="52" fill="#b7c8fc"/>'
                            '<path d="M431 191L483 89L535 191Z" fill="#142a43"/>'
                            '<rect x="38" y="298" width="167" height="64" fill="#142a43"/>'
                            '<rect x="217" y="298" width="167" height="64" fill="#426bec"/>'
                            '<rect x="396" y="298" width="166" height="64" fill="#d8d2c7"/>'
                            "</svg>",
                        ),
                    ],
                )
            )
            _ = library.create(
                DesignCreate(
                    name="Product foundations",
                    description="Shared principles and a small palette for product work.",
                    files=[
                        upload(
                            "principles.md", "# Product foundations\n\nMake each next step clear.\n"
                        ),
                        upload("tokens.json", '{"ink":"#172b4d","accent":"#339d7e"}\n'),
                    ],
                )
            )
            browser = playwright.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1440, "height": 1000},
                color_scheme="light",
                reduced_motion="reduce",
                locale="en-GB",
                timezone_id="Europe/Berlin",
            )
            page = context.new_page()
            page.clock.set_fixed_time("2026-11-02T09:30:00Z")
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "response",
                lambda response: (
                    errors.append(f"HTTP {response.status}: {response.url}")
                    if response.status >= 400
                    else None
                ),
            )
            captures: dict[str, object] = {}

            def capture(name: str) -> None:
                _ = page.evaluate("document.fonts.ready")
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                data = page.screenshot(
                    path=target,
                    animations="disabled",
                    full_page=not page.locator("dialog[open]").count(),
                )
                captures[name] = {
                    "viewport": page.viewport_size,
                    "sha256": hashlib.sha256(data).hexdigest(),
                }

            try:
                _ = page.goto(app.url + "/?view=designs")
                expect(page.locator(".design-card-summary")).to_have_count(3)
                expect(
                    page.frame_locator(".design-card-art iframe").locator(".slide")
                ).to_be_visible()
                expect(page.locator(".design-card-art img")).to_have_js_property("complete", True)
                capture("docs/assets/design-library.png")
                page.get_by_role("button", name="Add design", exact=True).click()
                add_dialog = page.get_by_role("dialog", name="Add design", exact=True)
                add_dialog.get_by_role("textbox", name="Name", exact=True).fill("Editorial system")
                source = library.get(editorial.id)
                page.locator("#design-library-input").set_input_files(
                    [file.local_path for file in source.files if file.name != "palette.csv"]
                )
                capture("docs/assets/add-design.png")
                page.set_viewport_size({"width": 390, "height": 844})
                capture("docs/screenshots/add-design-mobile.png")
                add_dialog.get_by_role("button", name="Close dialog").click()
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.get_by_role("button", name="Open Editorial system design", exact=True).click()
                expect(page.locator(".design-file-group li")).to_have_count(3)
                capture("docs/assets/design-context.png")
                page.get_by_role("button", name="Use with agent", exact=True).click()
                expect(page.get_by_role("dialog", name="Use with agent")).to_be_visible()
                capture("docs/screenshots/design-use-agent.png")
                page.get_by_role("dialog").get_by_role("button", name="Close dialog").click()
                page.set_viewport_size({"width": 390, "height": 844})
                capture("docs/screenshots/design-context-mobile.png")
                page.set_viewport_size({"width": 1440, "height": 1000})
                _ = page.goto(app.url + "/?view=designs&design=studio-blue")
                expect(page.get_by_role("link", name="Open example slides")).to_be_visible()
                capture("docs/screenshots/design-pack-context.png")
                assert library.get(editorial.id).file_count == 3
                if errors:
                    raise RuntimeError("\n".join(errors))
                manifest = staging / "docs/screenshots/design-captures.json"
                _ = manifest.write_text(
                    json.dumps(
                        {"fixture": "Original fictional design library", "screenshots": captures},
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
