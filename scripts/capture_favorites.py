"""Capture the actual favorites prototype and audit its UI using fictional data."""

import argparse
import hashlib
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from scripts.preview_favorites import prepare_covers, seed_favorites
from tests.integration.support import DECK_ID, PROJECT, running_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--axe", type=Path)
    args = parser.parse_args()
    output = PROJECT / "docs/prototypes/favorites"
    output.mkdir(parents=True, exist_ok=True)
    captures: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="favorite-slides-capture-") as temporary:
        root = Path(temporary)
        with running_app(root, root / "server.log") as app, sync_playwright() as renderer:
            seed_favorites(app, root)
            browser = renderer.chromium.launch()
            prepare_covers(app, browser)
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, color_scheme="light")
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def capture(name: str, *, keep_pointer: bool = False) -> None:
                _ = page.evaluate("document.fonts.ready")
                page.wait_for_function(
                    "[...document.querySelectorAll('#hub-grid img')]"
                    ".every(i=>i.complete&&i.naturalWidth)"
                )
                for frame in page.locator(".favorites-dialog[open] iframe").all():
                    expect(frame.content_frame.locator(".slide")).to_be_visible()
                if not keep_pointer:
                    page.mouse.move(1, 1)
                data = page.screenshot(path=output / name, animations="disabled")
                violations: object = None
                if args.axe:
                    _ = page.add_script_tag(path=str(args.axe))
                    violations = page.evaluate("async()=> (await axe.run()).violations")
                captures[name] = {
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "viewport": page.viewport_size,
                    "captured_at": datetime.now(UTC).isoformat(),
                    "axe_ran": bool(args.axe),
                    "axe_violations": violations,
                }
                print("Captured " + name, flush=True)

            page.goto(app.url)
            expect(page.locator(".hub-card")).to_have_count(5)
            capture("artifacts.png")
            page.get_by_role("button", name="Favorite slides", exact=True).click()
            expect(page.locator(".favorite-card")).to_have_count(3)
            capture("library.png")
            page.get_by_role(
                "button", name="Edit description for One slide. One clear idea.", exact=True
            ).click()
            capture("description.png")
            page.get_by_role("dialog", name="Slide description", exact=True).get_by_role(
                "button", name="Cancel", exact=True
            ).click()
            page.get_by_role("searchbox", name="Search favorite slides").fill("evidence")
            expect(page.locator(".favorite-card")).to_have_count(1)
            capture("description-search.png")
            page.keyboard.press("Escape")
            page.goto(f"{app.url}/?deck={DECK_ID}#workflow")
            star = page.locator('.slide-star[data-star-slide="workflow"]')
            expect(star).to_have_attribute("aria-pressed", "true")
            capture("slide-stars-rest.png")
            star.hover()
            expect(star.locator(".ui-icon")).to_have_css("opacity", "1")
            capture("slide-stars-hover.png", keep_pointer=True)
            page.get_by_role("button", name="Reference favorite slides", exact=True).click()
            expect(page.locator("[data-favorite-pick]")).to_have_count(3)
            page.locator("[data-favorite-pick]").first.click()
            page.locator("[data-favorite-pick]").last.click()
            capture("picker.png")
            page.get_by_role("button", name="Add references", exact=True).click()
            expect(page.locator(".favorites-dialog")).to_be_hidden()
            expect(page.locator(".is-slide-reference")).to_have_count(2)
            page.locator("#feedback-text").fill(
                "Use the first layout and the second slide’s visual hierarchy."
            )
            capture("chat.png")
            page.get_by_role("button", name="Menu", exact=True).click()
            page.get_by_role("menuitem", name="Appearance", exact=True).click()
            page.get_by_role("menuitemradio", name="Dark", exact=True).click()
            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_role("button", name="Open chat", exact=True).click()
            page.get_by_role("button", name="Reference favorite slides", exact=True).click()
            expect(page.locator(".favorite-card")).to_have_count(3)
            capture("mobile-dark.png")
            browser.close()
            assert not errors, errors
    _ = (output / "captures.json").write_text(json.dumps(captures, indent=2) + "\n")


if __name__ == "__main__":
    main()
