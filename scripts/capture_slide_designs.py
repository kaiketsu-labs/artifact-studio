"""Capture the real whole-slide alternatives workflow with fictional authored designs.

Run: uv run python -m scripts.capture_slide_designs [--output /tmp/design-docs]
"""

import argparse
import hashlib
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from scripts.screenshot_fixture import seed
from tests.integration.support import DECK_ID, PROJECT, running_app
from tests.integration.test_slide_designs import history_chrome_thumbnail


def directions() -> list[dict[str, str]]:
    """Original documentation artwork authored through the public proposal tool."""
    editorial = """<article class="slide" id="editorial-direction">
    <style>.slide{background:#fffdf7;color:#25372f;padding:6cqw}
    .slide h2{font:5.3cqw/1.03 Georgia,serif;letter-spacing:-.05em;margin-top:5cqw}
    .slide .steps{display:flex;gap:3cqw;margin-top:5cqw}
    .slide .steps>div{flex:1;border-top:1px solid #abb7a9;padding-top:1.3cqw}
    .slide strong{font-size:2cqw;display:block;margin:.7cqw 0}
    .slide .steps p{font-size:1.3cqw;color:#526653}</style>
    <p class="eyebrow">A clearer way to work / 02</p><h2>Good slides happen<br>in the loop.</h2>
    <div class="steps"><div><span>01</span><strong>Brief</strong><p>Bring the story.</p></div>
    <div><span>02</span><strong>Draft</strong><p>Explore the possibilities.</p></div>
    <div><span>03</span><strong>Review</strong><p>Make it better, together.</p></div>
    </div></article>"""
    bold = """<article class="slide" id="bold-direction">
    <style>.slide{background:#284bde;color:#fff;padding:6cqw}
    .slide h2{font-size:7cqw;line-height:.98;margin-top:5cqw;letter-spacing:-.07em}
    .slide .eyebrow{color:#d7e3ff}
    .slide .steps{display:flex;gap:1cqw;margin-top:4cqw}
    .slide .steps span{padding:1.5cqw 2.5cqw;border:1px solid #97b0ff;
    border-radius:4cqw;font-size:1.5cqw}
    .slide .steps span:last-child{background:#d5f5a4;color:#223e25;border-color:#d5f5a4}
    </style><p class="eyebrow">A clearer way to work / 02</p>
    <h2>Better together.<br>Better every loop.</h2><div class="steps"><span>01 Brief</span>
    <span>02 Draft</span><span>03 Review ↗</span></div></article>"""
    diagram = """<article class="slide" id="diagram-direction">
    <style>.slide{background:#eef5f1;color:#163a35;padding:6cqw}
    .slide h2{font-size:4.6cqw;max-width:65cqw}
    .slide .loop{display:flex;gap:1.5cqw;align-items:center;margin-top:6cqw}
    .slide .loop>div{flex:1;background:#fff;padding:2.8cqw 2cqw;
    border:1px solid #b6d4c5;border-radius:1cqw}
    .slide .loop strong{display:block;font-size:2cqw;margin:1.3cqw 0 .6cqw}
    .slide .loop p{font-size:1.3cqw;color:#45675f}
    .slide .loop>b{font-size:2.5cqw;font-weight:400;color:#45675f}
    .slide .return{margin-top:2.5cqw;border-bottom:1px solid #688e7d;
    text-align:center;padding-bottom:1cqw;font-size:1.2cqw;color:#45675f}
    </style><p class="eyebrow">A clearer way to work / 02</p>
    <h2>One clear loop.<br>A stronger story.</h2><div class="loop"><div>01<strong>Brief</strong>
    <p>Audience &amp; ideas</p></div><b>→</b><div>02<strong>Draft</strong>
    <p>Explore directions</p></div>
    <b>→</b><div>03<strong>Review</strong><p>Choose &amp; refine</p></div></div>
    <p class="return">← Keep learning, together</p></article>"""
    return [
        {
            "name": "Editorial",
            "rationale": "Quiet typography and generous space.",
            "html": editorial,
        },
        {"name": "Bold", "rationale": "A confident headline with a vivid accent.", "html": bold},
        {"name": "Diagram", "rationale": "Make the review loop explicit.", "html": diagram},
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT)
    parser.add_argument(
        "--axe", type=Path, help="Optional local axe-core script for sampled checks"
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="slide-design-docs-") as directory:
        root = Path(directory)
        staging = root / "captures"
        captures: dict[str, object] = {}
        audits: dict[str, object] = {}
        with running_app(root, root / "server.log") as app, sync_playwright() as playwright:
            _ = seed(app)
            slide = app.call("get_slide", deck_id=DECK_ID, slide_id="workflow")
            _ = app.call(
                "publish_slide_designs",
                deck_id=DECK_ID,
                slide_id="workflow",
                expected_content_hash=slide["content_hash"],
                request_id="docs-slide-designs",
                options=directions(),
            )
            browser = playwright.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1600, "height": 1100},
                color_scheme="light",
                reduced_motion="reduce",
                locale="en-GB",
                timezone_id="Europe/Berlin",
            )
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def capture(name: str) -> None:
                _ = page.evaluate("document.fonts.ready")
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                data = page.screenshot(path=target, animations="disabled")
                captures[name] = {
                    "viewport": page.viewport_size,
                    "captured_at": datetime.now(UTC).isoformat(),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
                if args.axe:
                    page.add_script_tag(path=str(args.axe))
                    audits[name] = page.evaluate("async()=> (await axe.run()).violations")
                print(f"Captured {name}", flush=True)

            def appearance(value: str) -> None:
                page.get_by_role("button", name="Menu", exact=True).click()
                page.get_by_role("menuitem", name="Appearance", exact=True).click()
                page.get_by_role("menuitemradio", name=value, exact=True).click()
                expect(page.locator("html")).to_have_attribute("data-theme", value.lower())

            try:
                _ = page.goto(f"{app.url}/?deck={DECK_ID}#workflow")
                expect(page.locator("#slide-design-tray")).to_be_visible()
                page.get_by_role("tab", name="Editorial", exact=True).click()
                expect(
                    page.frame_locator(".sd-canvas-overlay iframe").locator("h2")
                ).to_contain_text("Good slides happen")
                capture("docs/assets/slide-designs.png")
                page.locator("[data-sd-badge]").hover()
                expect(page.locator(".sd-peek")).to_be_visible()
                expect(page.locator(".sd-fan iframe")).to_have_count(3)
                # Wait for the real frozen thumbnail documents, not just the popover shell.
                for index in range(3):
                    expect(page.locator(".sd-fan iframe").nth(index)).to_have_attribute(
                        "data-ready", "true"
                    )
                capture("docs/screenshots/slide-designs-hover.png")
                page.keyboard.press("Escape")
                page.locator("[data-sd-compare]").click()
                expect(page.frame_locator(".sd-compare-left iframe").locator("h2")).to_contain_text(
                    "Good slides happen"
                )
                expect(
                    page.frame_locator(".sd-compare-right iframe").locator("h2")
                ).to_contain_text("Good slides happen")
                capture("docs/screenshots/slide-designs-compare.png")
                page.set_viewport_size({"width": 390, "height": 900})
                capture("docs/screenshots/slide-designs-mobile.png")
                page.locator("[data-sd-close]").click()
                page.set_viewport_size({"width": 1600, "height": 1100})
                appearance("Dark")
                capture("docs/screenshots/slide-designs-dark.png")
                appearance("Light")
                page.locator("[data-sd-use]").click()
                expect(page.locator("#slide-design-tray")).to_be_hidden()
                expect(page.frame_locator("#preview").locator("h2")).to_contain_text(
                    "Good slides happen"
                )
                page.mouse.move(1500, 1000)
                capture("docs/screenshots/slide-designs-chosen.png")
                # The unrelated version-timeline thumbnail renderer needs OS isolation unavailable
                # in managed screenshot hosts. Proposal frames above are all real HTTP previews.
                history_chrome_thumbnail(page)
                page.get_by_role("button", name="Version history", exact=True).click()
                saved = page.locator(".sd-history")
                saved.locator(":scope > summary").click()
                expect(saved.locator(".sd-saved-options iframe").first).to_have_attribute(
                    "data-ready", "true"
                )
                capture("docs/screenshots/slide-designs-history.png")
                if errors:
                    raise RuntimeError("\n".join(errors))
                manifest = staging / "docs/screenshots/slide-designs-captures.json"
                _ = manifest.write_text(
                    json.dumps(
                        {
                            "fixture": "Fictional Studio Blue presentation and authored directions",
                            "captured_at": datetime.now(UTC).isoformat(),
                            "screenshots": captures,
                            "timeline_thumbnail": (
                                "Revision thumbnail uses a fictional PNG; proposals are real"
                            ),
                        },
                        indent=2,
                    )
                    + "\n"
                )
                if args.axe:
                    _ = (staging / "axe-results.json").write_text(
                        json.dumps(audits, indent=2) + "\n"
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
