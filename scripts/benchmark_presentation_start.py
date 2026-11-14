"""Compare slideshow startup with two runtime scripts and a fictional local deck.

Run: uv run --no-sync python -m scripts.benchmark_presentation_start \
  --baseline-runtime /path/to/baseline.js --browser-executable /usr/bin/chromium

Both runtime requests are fulfilled from local bytes, so timings exclude runtime transfer
and HTTP caching. Each run uses a fresh browser context and the same real local server.
"""

import argparse
import hashlib
import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import TypedDict, cast

from playwright.sync_api import Browser, Request, Route, expect, sync_playwright

from artifact_studio.models import Slide
from tests.integration.support import DECK_ID, RunningApp, markup, running_app
from tests.integration.test_browser import open_editor


class Sample(TypedDict):
    first_slide_ms: float
    viewer_body_descendants: int
    initial_requests: int
    initial_fixture_image_requests: int
    next_slide_title: str
    overview_count: int


class Measurement(TypedDict):
    median_first_slide_ms: float
    runs: list[Sample]


def fixture(app: RunningApp) -> None:
    """Build a 40-slide deck with local images and substantial authored markup."""
    assets = app.store.folder(DECK_ID) / "assets"
    assets.mkdir(exist_ok=True)
    for index in range(38):
        name = f"fictional-{index}.svg"
        (assets / name).write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40">'
            f'<rect width="80" height="40" fill="hsl({index * 9},50%,50%)"/></svg>',
            encoding="utf-8",
        )
        rows = "".join(
            f"<div><span>Fictional metric {row}</span><b>{index * row}</b>"
            "<p>Illustrative observation and context</p></div>"
            for row in range(35)
        )
        html = markup(f"Fictional evidence {index}").replace(
            "</article>",
            f'<section>{rows}</section><img src="assets/{name}" alt="Fictional chart"></article>',
        )
        app.store.add_slide(
            DECK_ID, Slide(id=f"evidence-{index}", title=f"Evidence {index}", html=html)
        )


def measure(browser: Browser, app: RunningApp, runtime: str, runs: int) -> Measurement:
    rows: list[Sample] = []
    for _ in range(runs):
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        requests: list[str] = []

        def serve_runtime(route: Route) -> None:
            route.fulfill(status=200, content_type="text/javascript", body=runtime)

        page.route("**/presentation.js", serve_runtime)

        def record_request(request: Request, target: list[str] = requests) -> None:
            target.append(request.url)

        page.on("request", record_request)
        open_editor(page, app)
        requests.clear()
        started = time.perf_counter()
        page.get_by_role("link", name="Present", exact=True).click()
        viewer = page.frame_locator('iframe[title="Presentation"]')
        viewer.locator("#stage h1").wait_for(state="visible")
        elapsed_ms = (time.perf_counter() - started) * 1000
        page.wait_for_timeout(400)
        nodes = cast(
            int,
            viewer.locator("body").evaluate('(element) => element.querySelectorAll("*").length'),
        )
        initial_requests = len(requests)
        image_requests = sum("/assets/fictional-" in url for url in requests)
        page.keyboard.press("Enter")
        expect(viewer.locator("#stage h1")).to_have_text("Present with confidence")
        next_title = viewer.locator("#stage h1").inner_text()
        viewer.locator("#all-slides-button").click()
        expect(viewer.locator("#all-slides-grid button")).to_have_count(40)
        overview_count = viewer.locator("#all-slides-grid button").count()
        rows.append(
            {
                "first_slide_ms": round(elapsed_ms, 1),
                "viewer_body_descendants": nodes,
                "initial_requests": initial_requests,
                "initial_fixture_image_requests": image_requests,
                "next_slide_title": next_title,
                "overview_count": overview_count,
            }
        )
        context.close()
    return {
        "median_first_slide_ms": statistics.median(row["first_slide_ms"] for row in rows),
        "runs": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-runtime", type=Path, required=True)
    parser.add_argument("--browser-executable", type=Path)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    candidate = Path(__file__).resolve().parents[1] / "runtime/presentation.js"
    with tempfile.TemporaryDirectory(prefix="presentation-start-") as directory:
        root = Path(directory)
        with running_app(root, root / "server.log") as app:
            fixture(app)
            with sync_playwright() as playwright:
                browser = (
                    playwright.chromium.launch(
                        executable_path=str(args.browser_executable), args=["--no-sandbox"]
                    )
                    if args.browser_executable
                    else playwright.chromium.launch()
                )
                try:
                    result = {
                        "browser_version": browser.version,
                        "baseline_runtime_sha256": hashlib.sha256(
                            args.baseline_runtime.read_bytes()
                        ).hexdigest(),
                        "candidate_runtime_sha256": hashlib.sha256(
                            candidate.read_bytes()
                        ).hexdigest(),
                        "method": (
                            "Fresh contexts; both runtime scripts intercepted and fulfilled from "
                            "local bytes; 40 fictional slides, 38 with local SVGs; "
                            "1440x900 viewport."
                        ),
                        "baseline": measure(
                            browser,
                            app,
                            args.baseline_runtime.read_text(encoding="utf-8"),
                            args.runs,
                        ),
                        "candidate": measure(
                            browser, app, candidate.read_text(encoding="utf-8"), args.runs
                        ),
                    }
                    print(json.dumps(result, indent=2))
                finally:
                    browser.close()


if __name__ == "__main__":
    main()
