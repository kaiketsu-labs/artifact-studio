"""Native share payloads, user activation and usable browser fallbacks."""

import os
from io import BytesIO
from pathlib import Path
from typing import Literal
from zipfile import ZipFile

import pytest
from playwright.sync_api import Page, expect
from pydantic import TypeAdapter

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.design_library import DesignCreate, DesignLibrary
from tests.integration.native_sharing import install_native_share
from tests.integration.support import DECK_ID, PRIVATE_NOTE, RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def open_presentation_share(page: Page, app: RunningApp) -> None:
    """Choose a real audience export through the presentation toolbar."""
    _ = page.goto(f"{app.url}/?deck={DECK_ID}")
    page.get_by_role("button", name="Share presentation", exact=True).click()
    panel = page.locator(".studio-share")
    panel.get_by_role("radio", name="HTML Interactive · images & animations").check()


def shared_bytes(page: Page, index: int = 0) -> bytes:
    return bytes(
        TypeAdapter(list[int]).validate_python(
            page.evaluate(f"window.nativeShares[{index}].files[0].bytes")
        )
    )


@pytest.mark.parametrize("width", [1440, 390])
def test_native_presentation_share_and_cancel(
    page: Page, live_app: RunningApp, artifacts: Path, width: int
) -> None:
    """Hand over actual audience bytes from a fresh click; cancellation never downloads."""
    install_native_share(page)
    page.set_viewport_size({"width": width, "height": 900})
    open_presentation_share(page, live_app)
    panel = page.locator(".studio-share")
    share = panel.get_by_role("button", name="Share…", exact=True)
    expect(share).to_be_enabled()
    _ = page.screenshot(path=str(artifacts / "native-sharing.png"))
    share.click()
    expect(panel.get_by_role("status")).to_have_text("Shared with selected app.")
    assert page.evaluate("window.nativeShares[0].active") is True
    assert page.evaluate("window.nativeShares[0].url === undefined") is True
    assert page.evaluate("window.nativeShares[0].files[0].name") == "CI-workflow.html"
    document = shared_bytes(page).decode()
    assert 'data-export="audience"' in document and PRIVATE_NOTE not in document
    downloads: list[str] = []
    page.on("download", lambda download: downloads.append(download.suggested_filename))
    _ = page.evaluate("window.nativeShareError = 'AbortError'")
    share.click()
    _ = page.wait_for_function("window.nativeShares.length === 2")
    expect(share).to_be_enabled()
    expect(panel.get_by_role("alert")).to_be_hidden()
    assert not downloads
    with page.expect_download() as captured:
        panel.get_by_role("button", name="Save file", exact=True).click()
    assert captured.value.path().read_bytes() == shared_bytes(page)


@pytest.mark.parametrize("mode", ["missing", "files-unsupported", "probe-error"])
def test_unsupported_native_share_retains_download(
    page: Page,
    live_app: RunningApp,
    mode: Literal["missing", "files-unsupported", "probe-error"],
) -> None:
    install_native_share(page, mode)
    open_presentation_share(page, live_app)
    panel = page.locator(".studio-share")
    expect(panel.get_by_role("button", name="Share…", exact=True)).to_be_hidden()
    expect(panel.locator(".studio-share-native-hint")).to_contain_text("Save the file")
    with page.expect_download() as captured:
        panel.get_by_role("button", name="Save file", exact=True).click()
    assert PRIVATE_NOTE not in captured.value.path().read_text()
    assert page.evaluate("window.nativeShares.length") == 0


def test_native_bundle_and_project_share(page: Page, live_app: RunningApp, tmp_path: Path) -> None:
    """Keep relative dependencies in the shared bundle and project ZIP."""
    install_native_share(page)
    store = ArtifactStore(live_app.store)
    project = store.create_project("Share examples")
    source = tmp_path / "bundle"
    source.mkdir()
    _ = (source / "index.html").write_text('<h1>Offline bundle</h1><script src="app.js"></script>')
    _ = (source / "app.js").write_text("document.body.dataset.example='local'")
    # Exercise portable bundle sharing without depending on the separate thumbnail renderer.
    item = store.register_artifact(project.id, "Share page", source, "index.html", kind="file")
    _ = page.goto(f"{live_app.url}/?artifact={item.id}")
    page.get_by_role("button", name="Share artifact", exact=True).click()
    panel = page.locator(".studio-share")
    share = panel.get_by_role("button", name="Share…", exact=True)
    expect(share).to_be_enabled()
    share.click()
    expect(panel.get_by_role("status")).to_have_text("Shared with selected app.")
    with ZipFile(BytesIO(shared_bytes(page))) as bundle:
        assert set(bundle.namelist()) == {"index.html", "app.js"}
    panel.get_by_role("button", name="Close share panel").click()
    _ = page.goto(f"{live_app.url}/?project={project.id}")
    page.get_by_role("button", name="Share project", exact=True).click()
    expect(share).to_be_enabled()
    share.click()
    expect(panel.get_by_role("status")).to_have_text("Shared with selected app.")
    with ZipFile(BytesIO(shared_bytes(page))) as bundle:
        assert set(bundle.namelist()) == {"01-Share-page/index.html", "01-Share-page/app.js"}


def test_failed_native_preparation_can_retry(page: Page, live_app: RunningApp) -> None:
    install_native_share(page)
    page.add_init_script(
        """const originalFetch = window.fetch.bind(window); let failFile = true;
        window.fetch = (...args) => {
          if (failFile && /\\/api\\/shares\\/[^/]+\\/file$/.test(String(args[0]))) {
            failFile=false; return Promise.reject(new TypeError('Connection lost'));
          }
          return originalFetch(...args);
        };"""
    )
    open_presentation_share(page, live_app)
    panel = page.locator(".studio-share")
    panel.get_by_role("button", name="Try again", exact=True).click()
    share = panel.get_by_role("button", name="Share…", exact=True)
    expect(share).to_be_enabled()
    _ = page.evaluate("window.nativeShareError = 'NotAllowedError'")
    share.click()
    expect(panel.get_by_role("alert")).to_contain_text("Try again or save the file")
    with page.expect_download() as captured:
        panel.get_by_role("button", name="Save file", exact=True).click()
    assert captured.value.path().read_bytes() == shared_bytes(page)


@pytest.mark.parametrize("mode", ["supported", "missing"])
def test_design_message_native_share_and_copy(
    page: Page, live_app: RunningApp, mode: Literal["supported", "missing"]
) -> None:
    install_native_share(page, mode)
    item = DesignLibrary(live_app.store).create(
        DesignCreate(name="Editorial", description="Quiet typography and generous space.")
    )
    _ = page.goto(f"{live_app.url}/?view=designs&design={item.id}")
    page.get_by_role("button", name="Use with agent", exact=True).click()
    panel = page.get_by_role("dialog", name="Use with agent")
    message = panel.get_by_role("textbox", name="Message for your agent").input_value()
    share = panel.get_by_role("button", name="Share message", exact=True)
    if mode == "supported":
        share.click()
        _ = page.wait_for_function("window.nativeShares.length === 1")
        assert page.evaluate("window.nativeShares[0].text") == message
        assert page.evaluate("window.nativeShares[0].active") is True
        assert page.evaluate("window.nativeShares[0].files.length") == 0
    else:
        expect(share).to_have_count(0)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=live_app.url)
    panel.get_by_role("button", name="Copy message", exact=True).click()
    assert page.evaluate("navigator.clipboard.readText()") == message
