"""Native share payloads, user activation and usable browser fallbacks."""

import os
import re
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


def test_share_dialog_keeps_navigation_away_from_the_underlying_slide(
    page: Page, live_app: RunningApp
) -> None:
    """Tabbing past an unchecked format group must not expose editor arrow shortcuts."""
    page.goto(f"{live_app.url}/?deck={DECK_ID}#cover")
    feedback = page.get_by_role("textbox", name="Your feedback", exact=True)
    feedback.fill("Keep this draft while choosing an export format.")
    share = page.get_by_role("button", name="Share presentation", exact=True)
    share.click()
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(300)
    expect(page).to_have_url(f"{live_app.url}/?deck={DECK_ID}#cover")
    expect(page.locator(".studio-share")).to_be_visible()
    page.keyboard.press("Escape")
    expect(share).to_be_focused()
    expect(feedback).to_have_value("Keep this draft while choosing an export format.")
    share.blur()
    page.keyboard.press("ArrowRight")
    expect(page).to_have_url(f"{live_app.url}/?deck={DECK_ID}#next")


@pytest.mark.parametrize("width,theme", [(1440, "light"), (390, "dark")])
def test_pdf_hidden_slide_choice_changes_downloaded_pages(
    page: Page,
    live_app: RunningApp,
    artifacts: Path,
    width: int,
    theme: Literal["light", "dark"],
) -> None:
    """Download real PDFs with and without the appendix, invalidating the prepared attachment."""
    live_app.store.set_slide_hidden(DECK_ID, "next", hidden=True, expected_hidden=False)
    page.set_viewport_size({"width": width, "height": 900})
    page.emulate_media(color_scheme=theme)
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    page.get_by_role("button", name="Share presentation", exact=True).click()
    panel = page.locator(".studio-share")
    panel.get_by_role("radio", name="PDF Still pages", exact=True).check()
    option = panel.get_by_role("checkbox", name="Include hidden slides", exact=True)
    expect(option).to_be_checked()
    option.uncheck()
    _ = page.screenshot(path=str(artifacts / f"pdf-visible-slides-{theme}.png"))
    with page.expect_download() as visible:
        panel.get_by_role("button", name="Save file", exact=True).click()
    data = visible.value.path().read_bytes()
    assert data.startswith(b"%PDF-")
    assert len(re.findall(rb"/Type\s*/Page\b", data)) == 1
    option.check()
    with page.expect_download() as complete:
        panel.get_by_role("button", name="Save file", exact=True).click()
    assert len(re.findall(rb"/Type\s*/Page\b", complete.value.path().read_bytes())) == 2
    panel.get_by_role("radio", name="HTML Interactive · images & animations").check()
    expect(option).not_to_be_visible()
    assert live_app.store.read(DECK_ID).slides[1].hidden
    assert live_app.store.comments(DECK_ID) == []


def test_print_preview_hidden_slide_option_and_empty_selection(
    page: Page, live_app: RunningApp, artifacts: Path
) -> None:
    """The browser print path omits hidden pages and keeps an all-hidden deck recoverable."""
    live_app.store.set_slide_hidden(DECK_ID, "next", hidden=True, expected_hidden=False)
    page.goto(f"{live_app.url}/api/decks/{DECK_ID}/exports/pdf?preview=1")
    expect(page.get_by_role("button", name="Save as PDF", exact=True)).to_be_enabled()
    option = page.get_by_role("checkbox", name="Include hidden slides", exact=True)
    option.uncheck()
    expect(page.locator(".print-page:not([hidden])")).to_have_count(1)
    assert len(re.findall(rb"/Type\s*/Page\b", page.pdf(print_background=True))) == 1
    _ = page.screenshot(path=str(artifacts / "pdf-print-options.png"))
    option.check()
    assert len(re.findall(rb"/Type\s*/Page\b", page.pdf(print_background=True))) == 2
    live_app.store.set_slide_hidden(DECK_ID, "cover", hidden=True, expected_hidden=False)
    page.reload()
    option.uncheck()
    expect(page.get_by_role("button", name="Save as PDF", exact=True)).to_be_disabled()
    expect(page.get_by_role("status")).to_contain_text("No visible slides")
    option.check()
    expect(page.get_by_role("button", name="Save as PDF", exact=True)).to_be_enabled()
    assert live_app.store.read(DECK_ID).slides[0].notes == PRIVATE_NOTE


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
