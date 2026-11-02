"""Sharing and quiet viewing through the actual human workspace."""

import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_artifact_workspace import register

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_share_formats_download_and_project(
    live_app: RunningApp, page: Page, tmp_path: Path
) -> None:
    """Require a presentation format, download an offline file and offer project ZIP."""
    _, item, _, _, _ = register(live_app, tmp_path)
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    share = page.get_by_role("button", name="Share presentation", exact=True)
    expect(share).to_be_enabled()
    share.click()
    dialog = page.locator(".studio-share")
    expect(dialog.get_by_role("button", name="Save file", exact=True)).to_be_disabled()
    dialog.get_by_role("radio", name="HTML Interactive · images & animations").check()
    with page.expect_download() as captured:
        dialog.get_by_role("button", name="Save file", exact=True).click()
    path = captured.value.path()
    assert path is not None
    document = Path(path).read_text()
    assert 'data-export="audience"' in document
    assert "PRIVATE_CI_SPEAKER_NOTE_71b03" not in document
    dialog.get_by_role("button", name="Close share panel").click()
    expect(share).to_be_focused()
    page.goto(f"{live_app.url}/?artifact={item.id}")
    page.get_by_role("button", name="Back to library", exact=True).click()
    page.locator(".hub-sidebar").get_by_role("button", name="Fictional source", exact=False).click()
    page.get_by_role("button", name="Share project", exact=True).click()
    expect(dialog.get_by_text("2 artifacts · ZIP", exact=True)).to_be_visible()
    expect(dialog.get_by_role("radio")).to_have_count(0)
    with page.expect_download() as project_download:
        dialog.get_by_role("button", name="Save file", exact=True).click()
    assert project_download.value.suggested_filename.endswith(".zip")


def test_html_and_slide_focus_restore_without_reloading(
    live_app: RunningApp, page: Page, tmp_path: Path
) -> None:
    """Retain HTML interaction state and restore panels by the revealed edge control."""
    _, item, _, _, _ = register(live_app, tmp_path)
    page.goto(f"{live_app.url}/?artifact={item.id}")
    frame = page.frame_locator("#hub-html-preview")
    frame.locator("#counter").click()
    page.get_by_role("button", name="View", exact=True).click()
    expect(page.locator("body")).to_have_class(re.compile("workspace-clean-view"))
    expect(page.locator(".hub-top")).to_be_hidden()
    expect(frame.locator("#counter")).to_have_text("1")
    exit_control = page.get_by_role("button", name="Exit view", exact=True)
    expect(exit_control).to_have_css("opacity", "0")
    area = page.locator(".workspace-clean-exit-area")
    area.hover()
    exit_control.click()
    expect(page.get_by_role("button", name="View", exact=True)).to_be_focused()
    expect(frame.locator("#counter")).to_have_text("1")
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    view = page.get_by_role("button", name="Focus view", exact=True)
    expect(view).to_be_enabled()
    view.click()
    expect(page.locator(".topbar")).to_be_hidden()
    expect(page.locator("#view-toolbar")).to_be_hidden()
    page.frame_locator("#preview").get_by_role("heading").press("Escape")
    expect(page.locator(".topbar")).to_be_visible()
    expect(view).to_be_focused()
