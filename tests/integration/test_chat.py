"""Resize the real chat and render actual MCP agent messages in a browser."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, Route, expect

from artifact_studio.agent_client import Invocation
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
