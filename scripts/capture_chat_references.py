"""Capture @ path suggestions and cursor previews using the actual fictional fixture."""

import argparse
import hashlib
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from artifact_studio.artifacts import ArtifactStore
from scripts.preview_favorites import prepare_covers, seed_favorites
from tests.integration.support import DECK_ID, PROJECT, running_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--axe", type=Path)
    args = parser.parse_args()
    output = PROJECT / "docs/screenshots"
    captures: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="path-reference-capture-") as temporary:
        root = Path(temporary)
        with running_app(root, root / "server.log") as app, sync_playwright() as renderer:
            seed_favorites(app, root)
            browser = renderer.chromium.launch()
            prepare_covers(app, browser)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})

            def capture(name: str, selector: str | None = None) -> None:
                violations: object = None
                if args.axe:
                    _ = page.add_script_tag(path=str(args.axe))
                    violations = page.evaluate("async()=> (await axe.run()).violations")
                target = page.locator(selector) if selector else page
                data = target.screenshot(path=output / name, animations="disabled")
                captures[name] = {
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "viewport": page.viewport_size,
                    "captured_at": datetime.now(UTC).isoformat(),
                    "axe_ran": bool(args.axe),
                    "axe_violations": violations,
                    "selector": selector,
                }
                print("Captured " + name, flush=True)

            page.goto(f"{app.url}/?deck={DECK_ID}#workflow")
            text = page.locator("#feedback-text")
            text.fill("Use @three-step")
            options = page.get_by_role("listbox", name="Workspace references")
            row = options.get_by_role("option").first
            row.hover(position={"x": 60, "y": 20})
            preview = page.locator(".chat-reference-preview")
            expect(preview).to_be_visible()
            expect(preview.frame_locator("iframe").locator(".slide")).to_be_visible()
            capture("chat-reference-hover.png")
            row.click()
            expect(page.locator(".is-path-reference")).to_have_count(1)
            text.press_sequentially("for the layout and @Launch brief")
            expect(options.get_by_role("option")).to_have_count(1)
            text.press("Enter")
            text.press_sequentially("for the messaging.")
            expect(page.locator(".is-path-reference")).to_have_count(2)
            page.mouse.move(1, 1)
            capture("chat-reference-chips.png")
            capture("reference-picker-slide-star-light.png", "#feedback-form")
            page.locator("form .is-path-reference").first.hover()
            saved_preview = page.locator("#chat-saved-reference-preview")
            expect(saved_preview).to_be_visible()
            expect(saved_preview.frame_locator("iframe").locator(".slide")).to_be_visible()
            capture("chat-reference-pill-hover.png")
            text.hover()
            text.press("Enter")
            expect(page.locator("#feedback-list .is-path-reference")).to_have_count(2)
            capture("chat-reference-sent.png")
            item = next(
                item
                for item in ArtifactStore(app.store).list_artifacts()
                if item.title == "Launch brief"
            )
            page.emulate_media(color_scheme="dark")
            page.goto(f"{app.url}/?artifact={item.id}")
            text = page.locator("#hub-feedback-text")
            text.fill("Compare @Research notes")
            expect(
                page.get_by_role("listbox", name="Workspace references").get_by_role("option")
            ).to_have_count(1)
            text.press("Tab")
            expect(page.locator("form .is-path-reference")).to_have_count(1)
            text.press_sequentially("with this direction.")
            page.locator("form .is-path-reference").hover()
            expect(saved_preview).to_be_visible()
            expect(saved_preview.frame_locator("iframe").locator("h1")).to_have_text(
                "What we heard."
            )
            capture("chat-reference-pill-hover-dark.png")
            text.hover()
            page.set_viewport_size({"width": 390, "height": 844})
            capture("chat-reference-artifact-mobile.png")
            capture("reference-picker-slide-star-dark.png", "#hub-feedback-form")
            browser.close()
    _ = (output / "chat-reference-captures.json").write_text(json.dumps(captures, indent=2) + "\n")


if __name__ == "__main__":
    main()
