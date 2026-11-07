"""Bundle and capture the fictional reference-pill motion study."""

import argparse
import base64
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def bundle(destination: Path) -> None:
    """Create an offline, self-contained HTML prototype without application state."""
    root = Path(__file__).resolve().parent
    html = (root / "index.html").read_text()
    html = html.replace(
        '<link rel="stylesheet" href="prototype.css">',
        "<style>" + (root / "prototype.css").read_text() + "</style>",
    )
    html = html.replace(
        '<script src="prototype.js"></script>',
        "<script>" + (root / "prototype.js").read_text() + "</script>",
    )
    image = base64.b64encode((root.parents[1] / "assets/agents-loop.png").read_bytes()).decode()
    html = html.replace("../../assets/agents-loop.png", "data:image/png;base64," + image)
    destination.write_text(html)


def capture(url: str, output: Path, executable: str | None) -> None:
    """Use actual mouse input and retain browser video of unfolding and folding."""
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as driver:
        browser = driver.chromium.launch(executable_path=executable, chromium_sandbox=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.goto(url)
        title = page.locator("#draft-reference .reference-title")
        text = page.locator("#message")
        before = text.bounding_box()
        _ = page.screenshot(path=str(output / "dark-rest.png"), full_page=True)
        title.hover()
        expect(title).to_have_attribute("aria-expanded", "true")
        page.wait_for_timeout(400)
        _ = page.screenshot(path=str(output / "dark-open.png"), full_page=True)
        assert text.bounding_box() == before, "Preview must not shift the composer"
        page.locator("h1").hover()
        expect(title).to_have_attribute("aria-expanded", "false")
        page.get_by_role("button", name="Switch to light appearance").click()
        title.hover()
        page.wait_for_timeout(400)
        _ = page.screenshot(path=str(output / "light-open.png"), full_page=True)
        context.close()

        recording = browser.new_context(
            viewport={"width": 460, "height": 580},
            record_video_dir=output / "video",
            record_video_size={"width": 460, "height": 580},
        )
        filmed = recording.new_page()
        filmed.goto(url)
        filmed.locator("#message").wait_for()
        filmed.evaluate(
            "scrollTo(0, document.querySelector('.conversation').getBoundingClientRect().top "
            "+ scrollY - 14)"
        )
        filmed.wait_for_timeout(500)
        reference = filmed.locator("#draft-reference .reference-title")
        reference.hover()
        expect(reference).to_have_attribute("aria-expanded", "true")
        filmed.wait_for_timeout(900)
        _ = filmed.screenshot(path=str(output / "closeup-open.png"))
        filmed.locator("#message").hover()
        expect(reference).to_have_attribute("aria-expanded", "false")
        filmed.wait_for_timeout(700)
        _ = filmed.screenshot(path=str(output / "closeup-rest.png"))
        reference.hover()
        filmed.wait_for_timeout(900)
        filmed.locator("#message").hover()
        filmed.wait_for_timeout(900)
        video = filmed.video
        assert video is not None
        recording.close()
        video.save_as(output / "motion.webm")
        browser.close()


def main() -> None:
    """Build an offline export and optionally capture the running prototype."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--url")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--browser-executable")
    options = parser.parse_args()
    bundle(options.export)
    if options.url and options.output:
        capture(options.url, options.output, options.browser_executable)


if __name__ == "__main__":
    main()
