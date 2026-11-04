"""Exercise and capture the fictional element-options draft through a local static server."""

import argparse
import base64
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def write_standalone(root: Path, output: Path) -> None:
    """Bundle only this fictional prototype; it makes no production or network requests."""
    project = root.parents[2]
    html = (root / "index.html").read_text()
    styles = [
        project / "web/style.css",
        root.parent / "slide-alternatives/prototype.css",
        root / "prototype.css",
    ]
    html = re.sub(r'  <link rel="stylesheet"[^>]+>\n', "", html)
    css = "\n".join(path.read_text() for path in styles)
    html = html.replace("</head>", "<style>\n" + css + "\n</style>\n</head>")
    html = re.sub(r'<script src="[^"]+" defer></script>', "", html)
    mark = base64.b64encode((project / "web/brand/mark.svg").read_bytes()).decode()
    html = html.replace("/web/brand/mark.svg", "data:image/svg+xml;base64," + mark)
    html = html.replace('href="./"', 'href="#"')
    scripts = "\n".join(
        "<script>\n" + path.read_text() + "\n</script>"
        for path in (project / "web/icons.js", root / "prototype.js")
    )
    html = html.replace("</body>", scripts + "\n</body>")
    output.parent.mkdir(parents=True, exist_ok=True)
    _ = output.write_text(html)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8792")
    parser.add_argument("--axe", type=Path)
    parser.add_argument("--standalone", type=Path, help="Also write a self-contained review HTML")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    captures: dict[str, object] = {}
    audits: dict[str, object] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1440, "height": 1050}, reduced_motion="reduce", locale="en-GB"
        )
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "response",
            lambda response: errors.append(response.url) if response.status >= 400 else None,
        )
        url = args.url + "/docs/prototypes/element-alternatives/"

        def capture(name: str) -> None:
            _ = page.evaluate("document.fonts.ready")
            target = root / "screenshots" / (name + ".png")
            target.parent.mkdir(exist_ok=True)
            data = page.screenshot(path=target, animations="disabled")
            captures[name] = {
                "viewport": page.viewport_size,
                "sha256": hashlib.sha256(data).hexdigest(),
                "captured_at": datetime.now(UTC).isoformat(),
            }
            if args.axe:
                page.add_script_tag(path=str(args.axe))
                audits[name] = page.evaluate("async()=> (await axe.run()).violations")
            print(name, flush=True)

        def option(index: int):
            if page.locator("#inline-options").is_visible():
                return page.locator(f"[data-inline-option='{index}']")
            return page.locator(f"#element-options [data-option='{index}']")

        def control(name: str):
            return page.locator(
                "#inline-" + name if page.locator("#inline-options").is_visible() else "#" + name
            )

        try:
            _ = page.goto(url)
            for index, wording in enumerate(
                [
                    "Good slides happen in the loop.",
                    "A stronger story, every review.",
                    "From first thought to a clear story.",
                ]
            ):
                expect(option(index).locator(".inline-copy")).to_have_text(wording)
            option(1).hover()
            expect(option(1)).to_have_attribute(
                "aria-label", "Clearer promise A stronger story, every review."
            )
            expect(page.locator("#slide-stage h2")).to_have_text("A stronger story, every review.")
            expect(page.locator("#inline-options [data-chosen=true]")).to_contain_text("Original")
            panel_box = page.locator("#inline-options").bounding_box()
            headline_box = page.locator("#slide-stage h2").bounding_box()
            assert panel_box and headline_box
            assert panel_box["y"] + panel_box["height"] < headline_box["y"]
            capture("01-headline")
            page.mouse.move(1120, 600)
            expect(page.locator("#slide-stage h2")).to_have_text("Good slides happen in the loop.")
            option(1).click()
            page.mouse.move(1120, 600)
            expect(page.locator("#slide-stage h2")).to_have_text("A stronger story, every review.")
            page.locator("#speaker-notes").click()
            expect(page.locator("#inline-options")).to_be_hidden()
            expect(page.locator("#slide-stage h2")).to_have_text("Good slides happen in the loop.")
            badge = page.locator(".canvas-option-badge[data-open-scope=headline]")
            badge.hover()
            expect(page.locator("#inline-options")).to_be_visible()
            start_box, end_box = badge.bounding_box(), option(1).bounding_box()
            assert start_box and end_box
            start_x, start_y = (
                start_box["x"] + start_box["width"] / 2,
                start_box["y"] + start_box["height"] / 2,
            )
            end_x, end_y = end_box["x"] + end_box["width"] / 2, end_box["y"] + end_box["height"] / 2
            for step in range(1, 21):
                page.mouse.move(
                    start_x + (end_x - start_x) * step / 20, start_y + (end_y - start_y) * step / 20
                )
                page.wait_for_timeout(20)
            expect(page.locator("#inline-options")).to_be_visible()
            expect(page.locator("#slide-stage h2")).to_have_text("A stronger story, every review.")
            page.locator("#inline-close").click()
            page.locator("#slide-stage [data-scope=headline]").click()
            option(1).click()
            page.locator("#inline-scope").click()
            expect(page.locator("#scope-menu")).to_be_visible()
            capture("02-navigation")
            page.keyboard.press("Escape")
            option(1).focus()
            page.keyboard.press("ArrowRight")
            expect(page.locator("#slide-stage h2")).to_have_text(
                "From first thought to a clear story."
            )
            page.keyboard.press("ArrowLeft")
            control("compare").click()
            expect(page.locator("#comparison")).to_be_visible()
            capture("03-compare-element")
            page.get_by_role("button", name="Full slide", exact=True).click()
            expect(page.locator("#right-preview h2")).to_have_text(
                "A stronger story, every review."
            )
            expect(page.locator("#left-preview h2")).to_have_text("Good slides happen in the loop.")
            original_font = page.locator("#slide-stage h2").evaluate(
                "node => getComputedStyle(node).fontSize"
            )
            assert (
                page.locator("#right-preview h2").evaluate(
                    "node => getComputedStyle(node).fontSize"
                )
                == original_font
            )
            capture("04-compare-context")
            page.locator("#close-comparison").click()
            original_text = page.locator("#slide-stage .body-copy").inner_text()
            page.locator("#speaker-notes").fill(
                "Ask the team which headline feels most useful to the audience."
            )
            control("use").click()
            expect(page.locator("#component-tray")).to_be_hidden()
            expect(page.locator("#slide-stage h2")).to_have_text("A stronger story, every review.")
            expect(page.locator("#slide-stage .body-copy")).to_have_text(original_text)
            expect(page.locator("#speaker-notes")).to_have_value(
                "Ask the team which headline feels most useful to the audience."
            )
            capture("05-chosen")
            page.locator("#history").click()
            page.locator("#history-content > details > summary").click()
            page.locator("#history-content > details > details").first.locator(
                ":scope > summary"
            ).click()
            capture("06-history")
            page.locator("[data-saved-scope=headline][data-saved-index='0']").click()
            expect(page.locator("#slide-stage h2")).to_have_text("Good slides happen in the loop.")
            control("keep").click()
            expect(page.locator("#component-tray")).to_be_hidden()
            expect(page.locator("#slide-stage h2")).to_have_text("A stronger story, every review.")
            page.locator("#slide-stage [data-scope=graphic]").click()
            option(1).click()
            expect(page.locator("#slide-stage .graphic-orbit")).to_be_visible()
            expect(page.locator("#slide-stage h2")).to_have_text("A stronger story, every review.")
            capture("07-graphic")
            page.locator("#options").click()
            page.get_by_role("menuitem", name="Text 2 options", exact=False).click()
            option(2).click()
            expect(option(2).locator(".inline-copy")).to_have_text(
                "One shared brief. A few directions. A clearer result."
            )
            expect(page.locator("#slide-stage .body-copy")).to_have_text(
                option(2).locator(".inline-copy").inner_text()
            )
            capture("08-text")
            page.locator("#toggle-theme").click()
            page.locator("#slide-stage [data-scope=text]").click()
            option(2).click()
            capture("09-dark")
            page.locator("#toggle-theme").click()
            page.set_viewport_size({"width": 390, "height": 844})
            page.locator("#slide-stage [data-scope=headline]").click()
            expect(page.locator("#component-tray")).to_be_hidden()
            page.locator("#history").click()
            page.locator("#history-content > details > summary").click()
            page.locator("#history-content > details > details").first.locator(
                ":scope > summary"
            ).click()
            page.locator("[data-saved-scope=headline][data-saved-index='2']").click()
            active_box = option(2).bounding_box()
            strip_box = page.locator(".inline-choices").bounding_box()
            assert active_box and strip_box
            assert active_box["x"] >= strip_box["x"]
            assert active_box["x"] + active_box["width"] <= strip_box["x"] + strip_box["width"]
            capture("10-mobile")
            assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
            control("compare").click()
            capture("11-mobile-compare")
            page.locator("#close-comparison").click()
            page.locator("#inline-close").click()
            page.locator("#slide-stage [data-scope=text]").click()
            middle_box = option(1).bounding_box()
            assert middle_box
            page.mouse.click(middle_box["x"] + 12, middle_box["y"] + 18)
            page.wait_for_timeout(400)
            middle_box = option(1).bounding_box()
            strip_box = page.locator(".inline-choices").bounding_box()
            assert middle_box and strip_box
            assert middle_box["x"] >= strip_box["x"]
            assert middle_box["x"] + middle_box["width"] <= strip_box["x"] + strip_box["width"]
            option(2).click()
            expect(page.locator("#slide-stage .body-copy")).to_have_text(
                option(2).locator(".inline-copy").inner_text()
            )
            capture("15-mobile-text")
            page.set_viewport_size({"width": 320, "height": 700})
            assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
            control("compare").click()
            page.get_by_role("button", name="Full slide", exact=True).click()
            expect(page.locator("#right-preview .slide-art")).to_be_visible()
            page.locator("#close-comparison").click()
            page.set_viewport_size({"width": 1440, "height": 1050})
            _ = page.goto(url + "?scenario=question")
            expect(page.locator(".draft-question")).to_be_visible()
            expect(page.locator("#component-tray")).to_be_hidden()
            capture("12-question-first")
            page.get_by_role("button", name="Leadership", exact=True).click()
            expect(page.locator(".draft-question")).to_have_count(0)
            page.locator("[data-open-scope=headline]").first.click()
            expect(page.locator("#inline-options")).to_be_visible()
            _ = page.goto(url + "?scenario=question")
            page.locator("#draft-message").fill("New team members.")
            page.locator("#draft-composer").get_by_role("button", name="Send message").click()
            expect(page.locator(".draft-question")).to_have_count(0)
            expect(page.locator("#messages")).to_contain_text("Your answer is saved for the agent.")
            _ = page.goto(url)
            page.locator("#options").click()
            page.get_by_role("menuitem", name="Slide designs 2 options", exact=False).click()
            option(2).click()
            control("use").click()
            expect(page.locator("#options")).to_be_hidden()
            page.locator("#history").click()
            page.locator("#history-content > details > summary").click()
            page.locator("#history-content > details > details").first.locator(
                ":scope > summary"
            ).click()
            page.locator("[data-saved-scope=headline][data-saved-index='1']").click()
            expect(page.locator(".canvas-context")).to_contain_text("Earlier design")
            expect(control("use")).to_be_disabled()
            capture("13-earlier-context")
            page.locator("#draft-message").fill("My unsent thoughts")
            page.locator("#inline-all").click()
            expect(page.locator("#refresh-options")).to_have_text("Finish draft first")
            page.locator("#refresh-options").click()
            expect(page.locator("#draft-message")).to_have_value("My unsent thoughts")
            page.locator("#draft-message").fill("")
            page.locator("#refresh-options").click()
            expect(page.locator("#draft-message")).to_have_value(
                "Please refresh the headline options for the current slide design."
            )
            control("keep").click()
            expect(page.locator("#component-tray")).to_be_hidden()
            expect(page.locator("#slide-stage .variant-bold")).to_be_visible()
            page.locator("#slide-stage [data-scope=headline]").click()
            expect(page.locator("#component-tray")).to_be_hidden()
            page.locator("#history").click()
            page.locator("#history-content > details > summary").click()
            page.locator("#history-content > details > details").nth(2).locator(
                ":scope > summary"
            ).click()
            expect(page.locator(".saved-element.kind-graphic .graphic-orbit")).to_be_visible()
            capture("14-graphic-history")
            if errors:
                raise AssertionError(errors)
            _ = (root / "captures.json").write_text(
                json.dumps(
                    {
                        "kind": "Fictional UX prototype; no production API or agent execution",
                        "captured_at": datetime.now(UTC).isoformat(),
                        "screenshots": captures,
                    },
                    indent=2,
                )
                + "\n"
            )
            if args.axe:
                _ = Path("/tmp/element-options-axe.json").write_text(
                    json.dumps(audits, indent=2) + "\n"
                )
                assert not any(audits.values()), "Axe violations: /tmp/element-options-axe.json"
            if args.standalone:
                write_standalone(root, args.standalone)
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
