"""Exercise the sandboxed element preview and its message boundary."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_preview_is_bounded_and_cancel_restores_original() -> None:
    """Preview a text and style patch, reject another target, and restore the source DOM."""
    bridge = (Path(__file__).parents[1] / "web" / "artifact-bridge.js").read_text()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(
            "**/bridge.js", lambda route: route.fulfill(body=bridge, content_type="text/javascript")
        )
        page.route(
            "**/",
            lambda route: route.fulfill(
                body="<html><body></body></html>", content_type="text/html"
            ),
        )
        page.goto("http://artifact.test/")
        page.evaluate("""() => {
          window.received = [];
          addEventListener('message', event => window.received.push(event.data));
          const frame = document.createElement('iframe');
          frame.sandbox = 'allow-scripts';
          frame.srcdoc = `<h1 id="title" style="font-size:2rem!important">Original</h1>
            <p id="other">Other</p><script src="http://artifact.test/bridge.js"><\\/script>`;
          document.body.append(frame);
        }""")
        page.wait_for_function("document.querySelector('iframe').contentWindow")
        page.wait_for_timeout(150)
        send = "message => document.querySelector('iframe').contentWindow.postMessage(message, '*')"
        page.evaluate(send, {"type": "artifact:inspect", "selector": "#title"})
        page.wait_for_function("window.received.some(item => item.type === 'artifact:inspector')")
        first = page.evaluate("window.received.find(item => item.type === 'artifact:inspector')")
        assert first["typography"]["font_size_unit"] == "rem"
        page.evaluate(
            send,
            {
                "type": "artifact:edit-preview",
                "selector": "#title",
                "values": {"text": "Changed", "font_size": 3, "font_size_unit": "rem"},
            },
        )
        page.wait_for_function(
            "window.received.some(item => item.type === 'artifact:inspector' && item.dirty)"
        )
        page.evaluate(send, {"type": "artifact:inspect", "selector": "#other"})
        page.wait_for_function("window.received.some(item => item.type === 'artifact:edit-error')")
        page.evaluate(send, {"type": "artifact:edit-cancel"})
        page.wait_for_function(
            "window.received.filter(item => item.type === 'artifact:inspector').length >= 3"
        )
        last = page.evaluate(
            "window.received.filter(item => item.type === 'artifact:inspector').at(-1)"
        )
        assert last["text"] == "Original"
        assert last["typography"]["font_size"] == 2
        assert last["typography"]["font_size_unit"] == "rem"
        assert not last["dirty"]
        browser.close()
