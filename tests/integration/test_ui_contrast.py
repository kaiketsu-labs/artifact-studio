"""Measure real UI colors after alpha compositing in both workspace appearances."""

import base64
import os
from typing import Literal

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_browser import open_editor
from tests.integration.test_questions import ask

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]

CONTRAST = """async (element, kind) => {
  await Promise.all(element.getAnimations()
    .filter(animation => animation.effect?.getComputedTiming().endTime !== Infinity)
    .map(animation => animation.finished));
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 1;
  const context = canvas.getContext('2d', {willReadFrequently: true});
  function rgba(color) {
    context.clearRect(0, 0, 1, 1);
    context.fillStyle = color;
    context.fillRect(0, 0, 1, 1);
    return [...context.getImageData(0, 0, 1, 1).data]
      .map((value, index) => index === 3 ? value / 255 : value);
  }
  function composite(foreground, background) {
    return foreground.slice(0, 3)
      .map((value, index) => value * foreground[3] + background[index] * (1 - foreground[3]))
      .concat(1);
  }
  function luminance(color) {
    return color.slice(0, 3).map(value => value / 255)
      .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
      .reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
  }
  const pseudo = kind === 'placeholder' ? '::placeholder' : kind === 'tooltip' ? '::after' : null;
  const style = getComputedStyle(element, pseudo);
  const backgrounds = [];
  const boundary = ['border', 'outline', 'fill', 'tooltip'].includes(kind);
  for (let node = boundary ? element.parentElement : element; node; node = node.parentElement) {
    backgrounds.push(rgba(getComputedStyle(node).backgroundColor));
  }
  let background = [255, 255, 255, 1];
  for (const color of backgrounds.reverse()) background = composite(color, background);
  if (kind === 'tooltip') background = composite(rgba(style.backgroundColor), background);
  const color = kind === 'border' ? style.borderTopColor :
    kind === 'outline' ? style.outlineColor : kind === 'fill' ? style.backgroundColor : style.color;
  const foreground = composite(rgba(color), background);
  const a = luminance(foreground), b = luminance(background);
  return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
}"""

# These tests measure history chrome; renderer isolation is exercised separately.
THUMBNAIL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1Z0AAAAASUVORK5CYII="
)


def history_thumbnail(page: Page) -> None:
    """Supply a fictional image without depending on the production renderer sandbox."""
    page.route(
        "**/history/thumbnail?*",
        lambda route: route.fulfill(status=200, content_type="image/png", body=THUMBNAIL),
    )


def readable(locator: Locator, *, kind: str = "text", minimum: float = 4.5) -> None:
    """Normal text needs 4.5:1; meaningful control state indicators need 3:1."""
    expect(locator).to_be_visible()
    ratio = float(str(locator.evaluate(CONTRAST, kind)))
    assert ratio >= minimum, f"{locator}: {kind} contrast {ratio:.3f}:1 < {minimum}:1"


def menu(page: Page, name: str) -> None:
    page.get_by_role("button", name="Menu", exact=True).click()
    page.get_by_role("menuitem", name=name, exact=True).click()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_editor_secondary_text_placeholders_and_control_states(
    page: Page, live_app: RunningApp, theme: Literal["light", "dark"]
) -> None:
    """Keep small labels, tinted chat metadata and keyboard/selection states legible."""
    page.emulate_media(color_scheme=theme)
    page.set_viewport_size({"width": 1440, "height": 1000})
    history_thumbnail(page)
    open_editor(page, live_app)
    expect(page.locator("html")).to_have_attribute("data-theme", theme)
    for selector in (
        '.slide-thumb[data-id="next"] .thumb-number',
        "#source-count",
    ):
        readable(page.locator(selector))
    readable(page.locator("#feedback-text"), kind="placeholder")
    page.locator("#feedback-text").focus()
    readable(page.locator("#feedback-form"), kind="border", minimum=3)
    page.locator("#feedback-file-input").set_input_files(
        {"name": "review-guide.md", "mimeType": "text/markdown", "buffer": b"Fictional guide"}
    )
    page.locator("#feedback-text").fill("Please review this fictional slide.")
    page.locator("#send-feedback").click()
    readable(page.locator("#feedback-count"))
    readable(page.locator(".comment.user time"))
    readable(page.locator(".chat-sent-file .chat-file-description small"))
    page.keyboard.press("Tab")
    page.locator("#hide-slide").focus()
    assert page.locator("#hide-slide").evaluate("element => element.matches(':focus-visible')")
    readable(page.locator("#hide-slide"), kind="outline", minimum=3)

    event_id = ask(live_app)
    question = page.locator(f'[data-question-id="{event_id}"]')
    readable(question.locator(".question-prompt"))
    readable(question.locator(".question-choice-mark").first, kind="border", minimum=3)
    question.get_by_role("radio", name="Cost savings", exact=True).check()
    readable(question.locator(".question-option").first)
    readable(question.locator(".question-choice-mark").first, kind="border", minimum=3)

    page.get_by_role("button", name="Version history", exact=True).click()
    readable(page.locator('.version-list [aria-current="true"] .version-caption small'))
    page.keyboard.press("Escape")
    page.get_by_role("button", name="Share presentation", exact=True).click()
    for radio in page.locator(".studio-share-radio").all():
        readable(radio, kind="border", minimum=3)
    page.locator('.studio-share-formats input[value="html"]').check()
    readable(page.locator(".studio-share-formats label").first.locator("small"))
    page.keyboard.press("Escape")
    menu(page, "Presentation files")
    page.keyboard.press("Tab")
    page.locator("#save-project").focus()
    readable(page.locator("#save-project"), kind="outline", minimum=3)
    for selector in ("#project-context", ".project-group-label", ".file-choice-copy>span"):
        for label in page.locator(selector).all():
            readable(label)
    page.keyboard.press("Escape")
    menu(page, "New presentation")
    readable(page.locator("#new-brief"), kind="placeholder")
    page.keyboard.press("Escape")

    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("button", name="Open chat", exact=True).click()
    readable(page.locator("#feedback-count"))
    readable(page.locator("#feedback-text"), kind="placeholder")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_agent_status_icon_and_keyboard_tooltip_contrast(
    page: Page, live_app: RunningApp, theme: Literal["light", "dark"]
) -> None:
    """Measure the current icon, both connection states, and focused tooltip text."""
    page.emulate_media(color_scheme=theme)
    open_editor(page, live_app)
    status = page.locator("#chat-agent-status")
    dot = status.locator(".chat-agent-dot")
    expect(status).to_have_attribute("title", "Agent unavailable")
    readable(status, minimum=3)
    readable(dot, kind="border", minimum=3)
    page.keyboard.press("Tab")
    status.focus()
    assert status.evaluate("element => element.matches(':focus-visible')")
    assert status.evaluate("element => getComputedStyle(element, '::after').content") != "none"
    readable(status, kind="outline", minimum=3)
    readable(status, kind="tooltip")

    live_app.store.mark_agent_check(DECK_ID, wait_seconds=120)
    _ = page.reload(wait_until="networkidle")
    expect(status).to_have_attribute("title", "Agent connected")
    readable(status, minimum=3)
    readable(dot, kind="fill", minimum=3)
    page.keyboard.press("Tab")
    status.focus()
    assert status.evaluate("element => element.matches(':focus-visible')")
    assert status.evaluate("element => getComputedStyle(element, '::after').content") != "none"
    readable(status, kind="tooltip")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_library_design_badges_and_history_metadata(
    page: Page, live_app: RunningApp, theme: Literal["light", "dark"]
) -> None:
    """Measure labels on selected surfaces, fallback covers and colored file badges."""
    project = live_app.post("/api/hub/projects", {"title": "Fictional contrast review"})
    artifact = live_app.post(
        "/api/artifacts/import",
        {
            "project_id": project["id"],
            "title": "Fictional brief",
            "entrypoint": "brief.md",
            "files": [
                {
                    "name": "brief.md",
                    "data_base64": base64.b64encode(b"# A fictional brief").decode(),
                }
            ],
        },
    )
    page.emulate_media(color_scheme=theme)
    history_thumbnail(page)
    page.goto(live_app.url)
    expect(page.get_by_role("heading", name="All artifacts", exact=True)).to_be_visible()
    readable(page.locator('[data-project="all"] .hub-count'))
    for label in page.locator(".hub-cover-fallback>span,.hub-cover-fallback>small").all():
        readable(label)
    page.goto(f"{live_app.url}/?artifact={artifact['id']}")
    page.get_by_role("button", name="Version history", exact=True).click()
    readable(page.locator(".version-list .version-group-date"))
    readable(page.locator('.version-list [aria-current="true"] .version-caption small'))
    page.keyboard.press("Escape")
    page.goto(live_app.url + "/?view=designs")
    readable(page.locator(".design-card-heading small"))
    page.get_by_role("button", name="Open test-brand design", exact=True).click()
    badges = page.locator(".design-file-visual:not(:disabled)>.design-file-badge>b:visible")
    expect(badges.first).to_be_visible()
    for badge in badges.all():
        readable(badge)
    page.set_viewport_size({"width": 390, "height": 844})
    for badge in badges.all():
        readable(badge)
