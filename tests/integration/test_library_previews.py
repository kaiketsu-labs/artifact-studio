"""User journeys for a responsive library with bounded, persistent thumbnail demand."""

import io
import json
import os
from collections import Counter
from pathlib import Path
from typing import Literal

import pytest
from PIL import Image
from playwright.sync_api import Browser, Page, Route, expect

from artifact_studio.artifacts import ArtifactStore
from tests.integration.support import RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def gallery(app: RunningApp, root: Path, count: int = 30) -> str:
    """Seed independent fictional cards without a browser, renderer or personal data."""
    artifacts = ArtifactStore(app.store)
    project = artifacts.create_project("Preview lab")
    bundle = root / "preview-bundle"
    bundle.mkdir()
    for number in range(count):
        _ = (bundle / "index.html").write_text(
            "<html><head><style>body{font-family:Arial;background:#edf3ef;"
            "color:#173b2c;padding:48px}</style></head><body>"
            f"<h1>Preview {number:02d}</h1><p>Fictional content for responsive browsing.</p>"
            "</body></html>"
        )
        _ = artifacts.register_artifact(project.id, f"Preview {number:02d}", bundle, "index.html")
    return project.id


def fixture_image(color: tuple[int, int, int] = (35, 85, 65)) -> bytes:
    """A deterministic network fixture; production pixel fidelity is tested separately."""
    output = io.BytesIO()
    Image.new("RGB", (640, 400), color).save(output, format="PNG")
    return output.getvalue()


def wait_requests(page: Page, pending: list[Route], minimum: int) -> None:
    """Let real observer/fetch events run without replacing the loader's scheduling."""
    for _ in range(60):
        if len(pending) >= minimum:
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"Expected {minimum} cover requests; received {len(pending)}")


def test_slow_covers_do_not_block_search_opening_or_navigation(
    live_app: RunningApp, browser: Browser, tmp_path: Path
) -> None:
    """Even an indefinitely slow cover must not consume unlimited requests or block opening."""
    project = gallery(live_app, tmp_path)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    pending: list[Route] = []
    errors: list[str] = []
    aborted: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("requestfailed", lambda request: aborted.append(request.url))
    page.route("**/api/hub/previews/**", lambda route: pending.append(route))
    try:
        page.goto(f"{live_app.url}/?project={project}")
        expect(page.locator("#hub-grid .hub-card")).to_have_count(30)
        wait_requests(page, pending, 2)
        page.wait_for_timeout(250)
        assert len(pending) == 2
        page.get_by_role("searchbox", name="Search artifacts").fill("Preview 00")
        expect(page.locator("#hub-grid .hub-card")).to_have_count(1)
        page.get_by_role(
            "button", name="Open Preview 00, HTML page, Preview lab", exact=True
        ).click()
        expect(page.locator("#hub-html-preview")).to_be_visible(timeout=3000)
        expect(
            page.frame_locator("#hub-html-preview").get_by_role("heading", name="Preview 00")
        ).to_be_visible()
        page.wait_for_timeout(250)
        assert any("/api/hub/previews/" in url for url in aborted)
        assert not errors, errors
    finally:
        context.close()


def test_search_and_sort_reuse_loaded_covers_without_repeated_requests(
    live_app: RunningApp, browser: Browser, tmp_path: Path
) -> None:
    """Completed real image decodes survive grid rebuilding and keyboard filtering."""
    project = gallery(live_app, tmp_path, count=8)
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    requests: Counter[str] = Counter()
    png = fixture_image()

    def image(route: Route) -> None:
        requests[route.request.url] += 1
        route.fulfill(status=200, content_type="image/png", body=png)

    page.route("**/api/hub/previews/**", image)
    try:
        page.goto(f"{live_app.url}/?project={project}")
        page.wait_for_function(
            "document.querySelectorAll('#hub-grid img[src^=\"blob:\"]').length >= 4"
        )
        search = page.get_by_role("searchbox", name="Search artifacts")
        search.fill("Preview 07")
        expect(page.locator("#hub-grid .hub-card")).to_have_count(1)
        expect(page.locator("#hub-grid img")).to_be_visible()
        search.fill("")
        page.get_by_role("combobox", name="Sort artifacts").select_option("name")
        page.get_by_role("button", name="Z to A", exact=True).click()
        page.wait_for_timeout(500)
        assert requests and max(requests.values()) == 1
        assert page.locator("#hub-grid iframe").count() == 0
        expect(search).to_have_value("")
    finally:
        context.close()


def test_renderer_failure_keeps_old_preview_and_automatically_recovers(
    live_app: RunningApp, browser: Browser, tmp_path: Path, artifacts: Path
) -> None:
    """A failed replacement retains the saved image and recovers after its cooldown."""
    project = gallery(live_app, tmp_path, count=1)
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.clock.install()
    old_png, new_png = fixture_image(), fixture_image((120, 85, 165))
    attempts = 0
    recovering = False

    def registry(route: Route) -> None:
        response = route.fetch()
        data = response.json()
        for item in data["artifacts"]:
            if item["project_id"] == project:
                item["preview_fallback_url"] = f"/api/hub/previews/{item['id']}?v=saved-fixture"
                item["preview_cached"] = False
        route.fulfill(response=response, body=json.dumps(data), content_type="application/json")

    def image(route: Route) -> None:
        nonlocal attempts
        if "v=saved-fixture" in route.request.url:
            route.fulfill(status=200, content_type="image/png", body=old_png)
        elif recovering:
            attempts += 1
            route.fulfill(status=200, content_type="image/png", body=new_png)
        else:
            attempts += 1
            route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps(
                    {
                        "code": "renderer_unavailable",
                        "detail": "Preview renderer is temporarily unavailable.",
                    }
                ),
                headers={"Retry-After": "30"},
            )

    page.route("**/api/hub", registry)
    page.route("**/api/hub/previews/**", image)
    try:
        page.goto(f"{live_app.url}/?project={project}")
        retry = page.get_by_role("button", name="Retry preview for Preview 00", exact=True)
        expect(retry).to_be_visible()
        expect(page.locator("#hub-grid img")).to_be_visible()
        original_url = page.locator("#hub-grid img").get_attribute("src")
        search = page.get_by_role("searchbox", name="Search artifacts")
        search.fill("Preview")
        page.get_by_role("combobox", name="Sort artifacts").select_option("name")
        page.wait_for_timeout(350)
        assert attempts == 1
        assert page.locator("#hub-grid img").get_attribute("src") == original_url
        assert page.locator("body").evaluate("node => node.scrollWidth <= innerWidth")
        _ = page.screenshot(
            path=str(artifacts / "saved-preview-during-failure.png"), full_page=True
        )
        recovering = True
        expect(retry).to_be_disabled()
        page.clock.fast_forward(31_000)
        expect(retry).to_be_hidden()
        expect(page.locator("#hub-grid img")).to_be_visible()
        assert attempts == 2
        assert original_url is not None
        expect(page.locator("#hub-grid img")).not_to_have_attribute("src", original_url)
        _ = page.screenshot(path=str(artifacts / "preview-recovered.png"), full_page=True)
    finally:
        context.close()


@pytest.mark.parametrize(
    ("width", "height", "scheme"), [(1440, 1000, "light"), (390, 1000, "dark")]
)
def test_cold_library_cover_recovers_from_outage_with_real_renderer(
    live_app: RunningApp, browser: Browser, tmp_path: Path, artifacts: Path,
    width: int, height: int, scheme: Literal["light", "dark"],
) -> None:
    """Cold HTML and slide covers recover without opening, reloading or seeded images."""
    _ = gallery(live_app, tmp_path, count=1)
    context = browser.new_context(
        viewport={"width": width, "height": height}, color_scheme=scheme
    )
    page = context.new_page()
    attempts: Counter[str] = Counter()

    def outage(route: Route) -> None:
        attempts[route.request.url] += 1
        if attempts[route.request.url] == 1:
            route.fulfill(
                status=503, content_type="application/json",
                body=json.dumps({"code": "renderer_unavailable"}),
                headers={"Retry-After": "3"},
            )
        else:
            route.continue_()

    page.route("**/api/hub/previews/**", outage)
    try:
        page.goto(live_app.url)
        cards = [
            page.locator("#hub-grid .hub-card").filter(
                has=page.get_by_role("button", name=title, exact=True)
            )
            for title in [
                "Open Preview 00, HTML page, Preview lab",
                "Open CI workflow, Presentation, Presentations",
            ]
        ]
        for card in cards:
            expect(card).to_be_visible()
        expect(cards[0]).to_have_attribute("data-preview-state", "unavailable")
        _ = page.screenshot(path=str(artifacts / "cold-preview-before.png"), full_page=True)
        dimensions: list[int] = []
        for card in cards:
            card.scroll_into_view_if_needed()
            expect(card).to_have_attribute("data-preview-state", "ready", timeout=35000)
            image = card.locator("img")
            expect(image).to_be_visible()
            dimensions.append(image.evaluate("node => node.naturalWidth"))
        assert dimensions == [1280, 1600]
        assert len(attempts) >= 2 and min(attempts.values()) >= 2
        assert len(list((live_app.store.database.parent / "artifact-previews").glob("*.png"))) >= 2
        page.evaluate("scrollTo(0, 0)")
        _ = page.screenshot(path=str(artifacts / "cold-preview-after.png"), full_page=True)
    finally:
        context.close()
