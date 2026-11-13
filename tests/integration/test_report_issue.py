"""Exercise the public GitHub draft handoff with a fictional browser session."""

import os
import re
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
def test_report_copies_screenshot_and_opens_public_draft(
    live_app: RunningApp, page: Page, path: str, width: int
) -> None:
    """Copy the reviewed image locally and open a prefilled issue draft."""

    def draft(route: Route) -> None:
        """Serve an inert stand-in for GitHub without transmitting the test report."""
        assert "referer" not in route.request.headers
        route.fulfill(
            status=200, content_type="text/html", body="<title>Fictional issue draft</title>"
        )

    page.context.route("https://github.com/**", draft)
    page.add_init_script("""(() => {
      window.ClipboardItem = class {
        constructor(data) { this.data = data; }
      };
      Object.defineProperty(navigator, 'clipboard', {value: {
        async write([item]) {
          const blob = await item.data['image/png'];
          document.documentElement.dataset.clipboardSize = String(blob.size);
        }
      }});
    })()""")
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
        dialog.get_by_role("button", name="Copy screenshot and open GitHub draft").click()
    popup.value.wait_for_url("https://github.com/**")
    expect(dialog.get_by_role("status")).to_contain_text("Screenshot copied")
    expect(page.locator("html")).to_have_attribute("data-clipboard-size", str(len(PNG)))
    target = urlsplit(popup.value.url)
    assert target.netloc == "github.com"
    assert target.path == "/kaiketsu-labs/artifact-studio/issues/new"
    assert popup.value.evaluate("window.opener === null")
    assert parse_qs(target.query) == {
        "title": ["Fictional PDF delay"],
        "body": ["Preparing attachment stalled."],
    }
    dialog.get_by_role("button", name="Remove screenshot").click()
    expect(dialog.get_by_role("img", name="Screenshot preview")).to_be_hidden()
    expect(choose).to_be_focused()
    page.keyboard.press("Escape")
    expect(trigger).to_be_focused()


@pytest.mark.parametrize("surface", ["browser", "window", "unknown", "monitor", "denied"])
def test_report_capture_handles_picker_outcomes(
    live_app: RunningApp, page: Page, surface: str
) -> None:
    """Use a fictional video stream to exercise capture, disposal and visible errors."""
    page.add_init_script(
        """
        Object.defineProperty(navigator.mediaDevices, 'getDisplayMedia', {value: async () => {
          const surface = __SURFACE__;
          if (surface === 'denied') throw new DOMException('Canceled', 'NotAllowedError');
          const canvas = document.createElement('canvas');
          canvas.width = 64;
          canvas.height = 48;
          const context = canvas.getContext('2d');
          const draw = () => {
            context.fillStyle = '#246b55';
            context.fillRect(0, 0, 64, 48);
          };
          draw();
          const stream = canvas.captureStream(20);
          const track = stream.getVideoTracks()[0];
          const repaint = setInterval(draw, 25);
          const stop = track.stop.bind(track);
          track.stop = () => {
            clearInterval(repaint);
            stop();
            document.documentElement.dataset.captureStopped = track.readyState;
          };
          track.getSettings = () => surface === 'unknown' ? {} : {displaySurface: surface};
          return stream;
        }});
        """.replace("__SURFACE__", repr(surface))
    )
    page.goto(live_app.url)
    page.get_by_role("button", name="Report an issue", exact=True).click()
    dialog = page.get_by_role("dialog", name="Report an issue")
    button = dialog.get_by_role("button", name="Capture tab or window")
    button.click()
    screenshot = dialog.get_by_role("img", name="Screenshot preview")
    if surface == "denied":
        expect(dialog.get_by_role("status")).to_contain_text("canceled or blocked")
    elif surface == "monitor":
        expect(dialog.get_by_role("status")).to_contain_text("instead of the entire screen")
    else:
        expect(screenshot).to_be_visible()
        assert screenshot.evaluate(
            """async img => {
              await img.decode();
              const canvas = document.createElement('canvas');
              canvas.width = img.naturalWidth;
              canvas.height = img.naturalHeight;
              const context = canvas.getContext('2d');
              context.drawImage(img, 0, 0);
              const rgb = [...context.getImageData(0, 0, 1, 1).data].slice(0, 3);
              return canvas.width === 64 && canvas.height === 48 &&
                rgb.every((channel, index) => Math.abs(channel - [36, 107, 85][index]) <= 4);
            }"""
        )
    if surface in {"monitor", "denied"}:
        expect(screenshot).to_be_hidden()
    if surface != "denied":
        expect(page.locator("html")).to_have_attribute("data-capture-stopped", "ended")
    expect(button).to_be_enabled()


def test_report_keeps_a_draft_link_and_image_when_clipboard_or_popup_is_blocked(
    live_app: RunningApp, page: Page
) -> None:
    """Keep the handoff usable when clipboard permission and popup policy deny it."""
    page.add_init_script("""(() => {
      Object.defineProperty(navigator, 'clipboard', {value: {
        write: () => Promise.reject(new DOMException('Denied', 'NotAllowedError'))
      }});
      window.open = () => null;
    })()""")
    page.goto(live_app.url)
    page.get_by_role("button", name="Report an issue", exact=True).click()
    dialog = page.get_by_role("dialog", name="Report an issue")
    dialog.get_by_role("textbox", name="Title").fill("Fictional report")
    dialog.get_by_role("textbox", name="What happened?").fill("Example content")
    dialog.locator("input[type=file]").set_input_files(
        {"name": "fictional.png", "mimeType": "image/png", "buffer": PNG}
    )
    dialog.get_by_role("button", name="Copy screenshot and open GitHub draft").click()
    expect(dialog.get_by_role("status")).to_contain_text("Save the screenshot")
    expect(dialog.get_by_role("link", name="Save screenshot")).to_be_visible()
    link = dialog.get_by_role("link", name="Open the draft in GitHub")
    expect(link).to_have_attribute(
        "href", re.compile(r"^https://github.com/kaiketsu-labs/artifact-studio/issues/new\?")
    )
    dialog.get_by_role("textbox", name="What happened?").fill("Redacted report")
    expect(link).to_be_hidden()
    expect(dialog.locator("[data-draft-link]")).not_to_have_attribute("href", re.compile(".+"))
    dialog.get_by_role("button", name="Copy screenshot and open GitHub draft").click()
    expect(link).to_have_attribute("href", re.compile(r"body=Redacted\+report$"))
