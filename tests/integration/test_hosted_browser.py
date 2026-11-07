"""Hosted opaque preview behavior in a real browser."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Browser, expect

from artifact_studio.artifacts import ArtifactStore
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


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
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
        expect(page.locator("[data-session-mode]")).to_have_text("Hosted")
        page.locator("#hub-tools-button").click()
        page.locator("[data-hub-connect]").click()
        dialog = page.get_by_role("dialog", name="Agent connection")
        expect(dialog).to_be_visible()
        expect(dialog.locator("input")).to_have_value(WORKSPACE + "/mcp")
        expect(dialog).to_contain_text("CF-Access-Client-Id")
        assert dialog.evaluate("e => e.scrollWidth <= e.clientWidth")
        page.keyboard.press("Escape")
        expect(page.locator("#hub-tools-button")).to_be_focused()
        page.locator("[data-session-mode]").click()
        expect(page.get_by_role("dialog", name="About Artifact Studio")).to_contain_text(
            "reviewer@example.test"
        )
        page.keyboard.press("Escape")
        expect(page.locator("[data-session-mode]")).to_be_focused()
        assert not failures
