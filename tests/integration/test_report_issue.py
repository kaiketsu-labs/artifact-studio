"""Exercise the public GitHub draft handoff with a fictional browser session."""

import os
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Page, Route, expect

from tests.integration.support import DECK_ID, RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02\x00\x00\x00\x0bIDAT\x78\xda"
    b"\x63\x64\xf8\x0f\x00\x01\x05\x01\x01\x27\x18\xe3\x66\x00\x00\x00\x00"
    b"IEND\xaeB\x60\x82"
)


@pytest.mark.parametrize(("path", "width"), [("/", 390), (f"/?deck={DECK_ID}", 1280)])
def test_report_opens_public_draft_without_transferring_screenshot(
    live_app: RunningApp, page: Page, path: str, width: int
) -> None:
    """Review an optional image locally and open only a prefilled issue draft."""

    def draft(route: Route) -> None:
        """Serve an inert stand-in for GitHub without transmitting the test report."""
        route.fulfill(
            status=200, content_type="text/html", body="<title>Fictional issue draft</title>"
        )

    page.context.route("https://github.com/**", draft)
    page.set_viewport_size({"width": width, "height": 700})
    page.goto(live_app.url + path)
    trigger = page.get_by_role("button", name="Report an issue", exact=True)
    expect(trigger).to_be_in_viewport()
    expect(trigger).to_have_css("position", "fixed")
    bounds = trigger.bounding_box()
    assert bounds and bounds["width"] >= 44 and bounds["height"] >= 44
    assert width - bounds["x"] - bounds["width"] <= 24
    assert 0 <= 700 - bounds["y"] - bounds["height"] <= 60
    if width <= 650:
        expect(trigger.locator("span")).to_be_hidden()
    else:
        expect(trigger.locator("span")).to_be_visible()
        composer = page.locator("#feedback-form").bounding_box()
        assert composer and composer["y"] + composer["height"] < bounds["y"]
    trigger.focus()
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog", name="Report an issue")
    expect(dialog.get_by_role("textbox", name="Title")).to_be_focused()
    dialog.get_by_role("textbox", name="Title").fill("Fictional PDF delay")
    dialog.get_by_role("textbox", name="What happened?").fill("Preparing attachment stalled.")
    choose = dialog.get_by_role("button", name="Choose screenshot")
    choose.focus()
    with page.expect_file_chooser() as selection:
        page.keyboard.press("Enter")
    selection.value.set_files({"name": "fictional.png", "mimeType": "image/png", "buffer": PNG})
    expect(dialog.get_by_role("img", name="Screenshot preview")).to_be_visible()
    with page.expect_download() as saved:
        dialog.get_by_role("link", name="Save screenshot").click()
    assert saved.value.suggested_filename == "artifact-studio-issue.png"
    with page.expect_popup() as popup:
        dialog.get_by_role("button", name="Open GitHub draft").click()
    popup.value.wait_for_load_state()
    target = urlsplit(popup.value.url)
    assert target.netloc == "github.com"
    assert target.path == "/kaiketsu-labs/artifact-studio/issues/new"
    assert parse_qs(target.query) == {
        "title": ["Fictional PDF delay"],
        "body": ["Preparing attachment stalled."],
    }
    dialog.get_by_role("button", name="Remove screenshot").click()
    expect(dialog.get_by_role("img", name="Screenshot preview")).to_be_hidden()
    expect(choose).to_be_focused()
    page.keyboard.press("Escape")
    expect(trigger).to_be_focused()
