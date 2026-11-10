"""Hostile authored JavaScript cannot retain the shared renderer indefinitely."""

import io
import os
import time

import pytest
from PIL import Image
from playwright.sync_api import Browser
from playwright.sync_api import TimeoutError as BrowserTimeout

from artifact_studio import renderer_health
from artifact_studio.renderer_health import bounded_evaluate
from artifact_studio.visuals import capture_queue_status, render_slot

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser tests"
    ),
]


@pytest.mark.parametrize(
    "expression",
    [
        "() => new Promise(() => {})",
        "() => { while (true) {} }",
        "() => ({toJSON() { while (true) {} }})",
        "() => ({get value() { while (true) {} }})",
    ],
    ids=["pending-promise", "busy-script", "busy-serialization", "busy-getter"],
)
def test_authored_execution_deadline_releases_renderer(
    browser: Browser, monkeypatch: pytest.MonkeyPatch, expression: str
) -> None:
    """The driver interrupts execution and serialization, then a fresh capture works."""
    monkeypatch.setattr(renderer_health, "SCRIPT_TIMEOUT_MS", 300)
    context = browser.new_context()
    page = context.new_page()
    started = time.monotonic()
    try:
        with pytest.raises(BrowserTimeout), render_slot():
            _ = bounded_evaluate(page, expression)
    finally:
        context.close()
    assert time.monotonic() - started < 5
    assert capture_queue_status()["running"] is False

    with render_slot():
        context = browser.new_context(viewport={"width": 240, "height": 240})
        try:
            page = context.new_page()
            page.set_content("<style>body{background:rgb(25,140,70)}</style><h1>Control</h1>")
            _ = bounded_evaluate(page, "async () => {await document.fonts.ready;}")
            with Image.open(io.BytesIO(page.screenshot())) as image:
                assert image.convert("RGB").getpixel((200, 200)) == (25, 140, 70)
        finally:
            context.close()


def test_bounded_results_preserve_json_values(browser: Browser) -> None:
    """Falsy values finish immediately and arguments retain normal capture data."""
    context = browser.new_context()
    try:
        page = context.new_page()
        values: tuple[object, ...] = (None, False, 0, "", [], {"frames": [1, "two"]})
        for value in values:
            assert bounded_evaluate(page, "async value => value", value) == value
    finally:
        context.close()
