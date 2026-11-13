"""Exercise the public GitHub draft handoff with a fictional browser session."""

import os
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Page, Route, expect

from tests.integration.support import RunningApp

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


def test_report_opens_public_draft_without_transferring_screenshot(
    live_app: RunningApp, page: Page
) -> None:
    """Review an optional image locally and open only a prefilled issue draft."""

    def draft(route: Route) -> None:
        """Serve an inert stand-in for GitHub without transmitting the test report."""
        route.fulfill(
            status=200, content_type="text/html", body="<title>Fictional issue draft</title>"
        )

    page.context.route("https://github.com/**", draft)
    page.set_viewport_size({"width": 390, "height": 700})
    page.goto(live_app.url)
    menu = page.get_by_role("button", name="Menu", exact=True)
    menu.click()
    page.get_by_role("menuitem", name="Report an issue", exact=True).click()
    dialog = page.get_by_role("dialog", name="Report an issue")
    expect(dialog.get_by_role("textbox", name="Title")).to_be_focused()
    dialog.get_by_role("textbox", name="Title").fill("Fictional PDF delay")
    dialog.get_by_role("textbox", name="What happened?").fill("Preparing attachment stalled.")
    dialog.get_by_label("Choose screenshot").set_input_files(
        {"name": "fictional.png", "mimeType": "image/png", "buffer": PNG}
    )
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
    page.keyboard.press("Escape")
    expect(menu).to_be_focused()
