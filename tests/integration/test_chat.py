"""Resize the real chat and render actual MCP agent messages in a browser."""

import io
import os
from pathlib import Path
from typing import Literal

import pytest
from PIL import Image, ImageDraw
from playwright.sync_api import Page, Route, expect

from artifact_studio.agent_client import Invocation
from artifact_studio.artifacts import ArtifactStore
from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_browser import open_editor

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def agent_reply(live_app: RunningApp, message: str) -> None:
    """Write through the ordinary HTTP comment and actual MCP reply surfaces."""
    slide = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    comment = live_app.post(
        f"/api/decks/{DECK_ID}/slides/cover/feedback",
        {
            "message": "Explain your changes.",
            "content_hash": slide["content_hash"],
            "request_id": "chat-markdown-question",
        },
    )
    _ = live_app.client().call(
        Invocation(
            tool="reply_to_feedback",
            arguments={
                "deck_id": DECK_ID,
                "slide_id": "cover",
                "event_id": int(str(comment["event_id"])),
                "message": message,
                "request_id": "chat-markdown-answer",
            },
        )
    )


@pytest.mark.parametrize("width,height", [(1280, 800), (1600, 1000)])
def test_drag_chat_persists_and_clamps_without_losing_the_canvas(
    page: Page, live_app: RunningApp, artifacts: Path, width: int, height: int
) -> None:
    """Widen across the preview, reload, shrink the window, and restore the saved width."""
    page.set_viewport_size({"width": width, "height": height})
    open_editor(page, live_app)
    divider = page.get_by_role("separator", name="Resize chat panel")
    before = int(str(divider.get_attribute("aria-valuenow")))
    bounds = divider.bounding_box()
    assert bounds is not None
    x, y = bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x - 220, y, steps=12)
    page.mouse.up()
    resized = int(str(divider.get_attribute("aria-valuenow")))
    assert resized >= before + 200
    assert page.locator(".canvas-area").evaluate(
        "element => element.clientWidth >= parseFloat(getComputedStyle(element.parentElement)"
        ".getPropertyValue('--chat-canvas-min'))"
    )
    _ = page.screenshot(path=str(artifacts / "wider-chat.png"), full_page=True)
    _ = page.reload(wait_until="networkidle")
    expect(divider).to_have_attribute("aria-valuenow", str(resized))
    previous_maximum = int(str(divider.get_attribute("aria-valuemax")))
    page.set_viewport_size({"width": 950, "height": 800})
    # The resize event can run between two protocol reads. Wait for the new range
    # and check both attributes in one browser evaluation.
    page.wait_for_function(
        """previousMaximum => {
          const divider = document.getElementById('chat-resizer');
          const maximum = Number(divider.getAttribute('aria-valuemax'));
          return maximum < previousMaximum &&
            Number(divider.getAttribute('aria-valuenow')) <= maximum;
        }""",
        arg=previous_maximum,
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.set_viewport_size({"width": 800, "height": 900})
    expect(divider).to_be_hidden()
    page.set_viewport_size({"width": width, "height": height})
    expect(divider).to_have_attribute("aria-valuenow", str(resized))
    page.get_by_role("button", name="Slide sorter", exact=True).click()
    expect(divider).to_be_hidden()
    page.get_by_role("button", name="Edit view", exact=True).click()
    expect(divider).to_be_visible()
    expect(divider).to_have_attribute("aria-valuenow", str(resized))


def test_chat_keyboard_reset_and_cancelled_drag(page: Page, live_app: RunningApp) -> None:
    """Give the separator keyboard parity and recover cleanly from an interrupted drag."""
    open_editor(page, live_app)
    divider = page.get_by_role("separator", name="Resize chat panel")
    default = int(str(divider.get_attribute("aria-valuenow")))
    divider.focus()
    page.keyboard.press("ArrowLeft")
    expect(divider).to_have_attribute("aria-valuenow", str(default + 20))
    page.keyboard.press("Home")
    expect(divider).to_have_attribute("aria-valuenow", str(divider.get_attribute("aria-valuemin")))
    page.keyboard.press("End")
    expect(divider).to_have_attribute("aria-valuenow", str(divider.get_attribute("aria-valuemax")))
    page.keyboard.press("Enter")
    expect(divider).to_have_attribute("aria-valuenow", str(default))
    bounds = divider.bounding_box()
    assert bounds is not None
    x, y = bounds["x"] + bounds["width"] / 2, bounds["y"] + 100
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x - 180, y, steps=6)
    divider.evaluate("""element => {
      for (const type of ['pointercancel', 'lostpointercapture']) {
        element.dispatchEvent(new PointerEvent(type, {pointerId: 999}));
      }
    }""")
    assert page.locator(".workspace").evaluate(
        "element => element.classList.contains('is-resizing-chat')"
    )
    page.keyboard.press("Escape")
    page.mouse.up()
    expect(divider).to_have_attribute("aria-valuenow", str(default))
    assert not page.locator(".workspace").evaluate(
        "element => element.classList.contains('is-resizing-chat')"
    )
    divider.press("ArrowLeft")
    divider.dblclick()
    expect(divider).to_have_attribute("aria-valuenow", str(default))


def test_chat_width_still_works_without_preference_storage(
    page: Page, live_app: RunningApp
) -> None:
    """A denied browser preference store must not disable the resizer or the editor."""
    page.add_init_script("""
      for (const method of ['getItem', 'setItem', 'removeItem']) {
        const original = Storage.prototype[method];
        Storage.prototype[method] = function(key, ...values) {
          if (key === 'artifact-studio:chat-width:v1') throw new Error('Preference storage denied');
          return original.call(this, key, ...values);
        };
      }
    """)
    open_editor(page, live_app)
    divider = page.get_by_role("separator", name="Resize chat panel")
    default = int(str(divider.get_attribute("aria-valuenow")))
    divider.press("ArrowLeft")
    expect(divider).to_have_attribute("aria-valuenow", str(default + 20))
    _ = page.reload(wait_until="networkidle")
    expect(divider).to_have_attribute("aria-valuenow", str(default))
    divider.press("Enter")
    expect(divider).to_have_attribute("aria-valuenow", str(default))


def test_agent_markdown_renders_and_stays_readable_in_both_themes(
    page: Page, live_app: RunningApp, artifacts: Path
) -> None:
    """Render actual stored agent replies with semantic Markdown and bounded wide content."""
    open_editor(page, live_app)
    code = (
        'print("<ready>")\n'
        'summary = {"client": "Codex / Claude Code", "status": "ready", '
        '"notes": "Comments remain attached to the selected slide"}\n'
    )
    message = (
        "## What changed\n\n**Wider chat** with *formatted replies*.\n\n"
        "- Drag the divider.\n- `Enter` resets it.\n\n> Your slides stay editable.\n\n"
        f"```python\n{code}```\n\n"
        "| Check | Result |\n| --- | --- |\n| Chat | Ready |\n\n"
        "[Documentation](https://example.com/guide?one=1&two=2)"
    )
    agent_reply(live_app, message)
    reply = page.locator(".comment.agent .chat-markdown")
    expect(reply.get_by_role("heading", name="What changed")).to_be_visible()
    expect(reply.locator("strong")).to_have_text("Wider chat")
    expect(reply.locator("em")).to_have_text("formatted replies")
    expect(reply.locator("li")).to_have_count(2)
    expect(reply.locator("blockquote")).to_contain_text("Your slides stay editable.")
    expect(reply.locator("pre code")).to_have_text(code)
    assert reply.locator("pre").evaluate("element => element.scrollWidth > element.clientWidth")
    expect(reply.get_by_role("cell", name="Ready", exact=True)).to_be_visible()
    expect(reply.get_by_role("link", name="Documentation")).to_have_attribute(
        "rel", "noopener noreferrer"
    )
    assert any(
        event.author == "agent" and event.message == message
        for event in live_app.store.view(DECK_ID).slides[0].feedback
    )
    for _step in range(4):
        page.get_by_role("separator", name="Resize chat panel").press("Shift+ArrowLeft")
    _ = page.screenshot(path=str(artifacts / "markdown-light.png"), full_page=True)
    page.get_by_role("button", name="Menu", exact=True).click()
    page.get_by_role("menuitem", name="Appearance", exact=True).click()
    page.get_by_role("menuitemradio", name="Dark", exact=True).click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    expect(page.frame_locator("#preview").get_by_role("heading", level=1)).to_be_visible()
    page.evaluate("""async () => {
      await document.fonts.ready;
      await new Promise(requestAnimationFrame);
      await new Promise(requestAnimationFrame);
    }""")
    assert page.locator(".discussion").evaluate(
        "element => element.scrollWidth <= element.clientWidth"
    )
    _ = page.screenshot(path=str(artifacts / "markdown-dark.png"), full_page=True)


def test_markdown_cannot_execute_html_or_load_images(page: Page, live_app: RunningApp) -> None:
    """Keep agent-provided HTML and dangerous URLs inert in the real DOM."""
    requests: list[str] = []

    def image_request(route: Route) -> None:
        requests.append(route.request.url)
        route.fulfill(body="", content_type="text/plain")

    page.route("https://chat-image.invalid/**", image_request)
    open_editor(page, live_app)
    agent_reply(
        live_app,
        (
            "<script>window.chatExecuted=true</script>\n\n"
            '<img src="https://chat-image.invalid/raw" onerror="window.chatExecuted=true">\n\n'
            "[Unsafe](javascript:alert(1)) [Data](data:text/html,hello)\n\n"
            "![Image description](https://chat-image.invalid/markdown)\n\n"
            "[Safe](https://example.com/guide)"
        ),
    )
    reply = page.locator(".comment.agent .chat-markdown")
    expect(reply).to_contain_text("Image description")
    expect(reply.locator("script,img,iframe,style")).to_have_count(0)
    expect(reply.get_by_role("link")).to_have_count(1)
    expect(reply.get_by_role("link", name="Safe")).to_have_attribute(
        "href", "https://example.com/guide"
    )
    assert page.evaluate("window.chatExecuted") is None
    assert not requests


@pytest.mark.parametrize("kind", ["presentation", "artifact"])
@pytest.mark.parametrize(("width", "theme"), [(1280, "light"), (390, "dark")])
def test_image_tiles_before_and_after_send(
    page: Page,
    live_app: RunningApp,
    tmp_path: Path,
    artifacts: Path,
    kind: str,
    width: int,
    theme: Literal["light", "dark"],
) -> None:
    """Keep image previews compact, removable and keyboard-accessible without altering bytes."""
    page.set_viewport_size({"width": width, "height": 900})
    page.emulate_media(color_scheme=theme)
    item = None
    if kind == "presentation":
        open_editor(page, live_app)
        toggle = page.get_by_role("button", name="Open chat", exact=True)
        if toggle.is_visible():
            toggle.click()
        input_selector, files_selector, thread = (
            "#feedback-file-input",
            "#feedback-files",
            "#feedback-list",
        )
        send = page.locator("#send-feedback")
    else:
        service = ArtifactStore(live_app.store)
        project = service.create_project("Fictional image review")
        source = tmp_path / "page"
        source.mkdir()
        _ = (source / "index.html").write_text("<h1>Image review</h1>")
        item = service.register_artifact(project.id, "Image review", source, "index.html")
        page.goto(f"{live_app.url}/?artifact={item.id}")
        input_selector, files_selector, thread = (
            "#hub-feedback-files",
            "#hub-feedback-attachments",
            "#hub-thread",
        )
        send = page.locator("#hub-send")
    encoded = io.BytesIO()
    image = Image.new("RGB", (1400, 800), "#edf3ff")
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 100, 1300, 190), fill="#315fce")
    draw.rectangle((100, 300, 700, 650), fill="#2b9466")
    draw.rectangle((760, 420, 1300, 650), fill="#e9a346")
    image.save(encoded, format="PNG")
    original = encoded.getvalue()
    files = page.locator(input_selector)
    files.set_input_files(
        [
            {"name": "chart.png", "mimeType": "image/png", "buffer": original},
            {"name": "extra.png", "mimeType": "image/png", "buffer": original},
            {"name": "context.txt", "mimeType": "text/plain", "buffer": b"Fictional context"},
        ]
    )
    preview = page.locator(files_selector).get_by_role(
        "button", name="Preview chart.png", exact=True
    )
    expect(preview.locator("img")).to_be_visible()
    page.get_by_role("button", name="Remove extra.png", exact=True).click()
    expect(page.locator(files_selector).locator(".chat-image-tile")).to_have_count(1)
    expect(page.locator(files_selector)).to_contain_text("context.txt")
    box = preview.bounding_box()
    assert box is not None and box["width"] <= 96 and box["height"] <= 96
    preview.click()
    dialog = page.get_by_role("dialog", name="Image preview: chart.png", exact=True)
    expect(dialog).to_be_visible()
    expect(dialog.locator("img")).to_have_js_property("complete", True)
    assert dialog.locator("img").evaluate("image => image.complete && image.naturalWidth > 0")
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(preview).to_be_focused()
    panel = page.locator(".discussion" if kind == "presentation" else "#hub-chat")
    _ = panel.screenshot(path=str(artifacts / "draft-images.png"))
    send.click()
    expect(page.locator(files_selector)).to_be_empty()
    sent = page.locator(thread).get_by_role("button", name="Preview chart.png", exact=True)
    expect(sent).to_be_visible()
    download_url = sent.get_attribute("data-image-download")
    assert download_url is not None
    expect(sent.locator("img")).to_have_attribute("src", download_url + "?preview=true")
    sent.click()
    expect(dialog).to_be_visible()
    expect(dialog.locator("img")).to_have_attribute("src", download_url + "?preview=true&full=true")
    with page.expect_download() as pending:
        dialog.get_by_role("link", name="Download original").click()
    download = pending.value
    path = download.path()
    assert path and Path(path).read_bytes() == original
    if item is not None and width == 1280:
        _ = live_app.post(
            f"/api/artifacts/{item.id}/feedback",
            {
                "message": "New review context",
                "content_hash": item.content_hash,
                "request_id": "image-focus-refresh",
            },
        )
        expect(page.locator(thread).get_by_text("New review context", exact=True)).to_be_visible(
            timeout=15000
        )
    page.keyboard.press("Escape")
    expect(sent).to_be_focused()
    assert page.locator("html").evaluate("element => element.scrollWidth <= innerWidth")
    _ = panel.screenshot(path=str(artifacts / "chat-images.png"))
