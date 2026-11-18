"""Real MCP publication and human decisions over durable whole-slide proposals."""

import base64
import os
from pathlib import Path

import pytest
from mcp.types import CallToolResult, ImageContent
from playwright.sync_api import Page, Route, expect

from artifact_studio.models import SlidePatch
from artifact_studio.slide_designs import DesignCapture, DesignSet, SlideDesigns
from artifact_studio.storage import digest
from tests.integration.support import DECK_ID, PRIVATE_NOTE, RunningApp, markup

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def publish(app: RunningApp, request: str = "browser-publish-designs") -> dict[str, object]:
    """Publish through the registered MCP contract, not a browser-only test seam."""
    current = app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    return app.call(
        "publish_slide_designs",
        deck_id=DECK_ID,
        slide_id="cover",
        expected_content_hash=current["content_hash"],
        request_id=request,
        options=[
            {
                "name": "Editorial",
                "rationale": "Quiet typography.",
                "html": markup("Editorial direction"),
            },
            {"name": "Bold", "rationale": "A stronger headline.", "html": markup("Bold direction")},
            {
                "name": "Diagram",
                "rationale": "A clearer process.",
                "html": markup("Diagram direction"),
            },
        ],
    )


def history_chrome_thumbnail(page: Page) -> None:
    """Only the existing timeline's unrelated image capture is replaced in this UI journey."""
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aBHsAAAAASUVORK5CYII="
    )
    page.route(
        "**/history/thumbnail?**", lambda route: route.fulfill(content_type="image/png", body=image)
    )


def test_present_plays_the_selected_design_without_choosing_it(
    live_app: RunningApp, page: Page
) -> None:
    theme = live_app.store.folder(DECK_ID) / "theme.css"
    original_theme = theme.read_text()
    _ = theme.write_text(
        original_theme + "\n@keyframes fixture-fade {from{opacity:.2}to{opacity:1}}\n"
        "#motion-title:hover {color:rgb(17, 95, 142)}\n"
    )
    current = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    _ = live_app.call(
        "publish_slide_designs",
        deck_id=DECK_ID,
        slide_id="cover",
        expected_content_hash=current["content_hash"],
        request_id="present-design-fixture",
        options=[
            {"name": "Plain", "html": markup("A different proposal")},
            {
                "name": "Animated",
                "html": (
                    '<article class="slide"><h1 id="motion-title" '
                    'style="animation:fixture-fade 4s linear">'
                    'Motion direction</h1><p data-build="1">Reveal this line</p></article>'
                ),
            },
        ],
    )
    # The option should still use its frozen theme after the deck changes.
    _ = theme.write_text(original_theme)

    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    page.get_by_role("tab", name="Plain", exact=True).click()
    page.get_by_role("button", name="Compare", exact=True).click()
    page.get_by_label("Right design", exact=True).select_option(label="Animated")
    page.get_by_role("button", name="Close design comparison", exact=True).click()
    editor_option = page.get_by_role("tab", name="Animated", exact=True)
    expect(editor_option).to_have_attribute("aria-selected", "true")
    present = page.get_by_role("link", name="Present", exact=True)
    present.click()
    viewer = page.frame_locator('iframe[title="Presentation"]')
    stage = viewer.frame_locator("#stage iframe")
    title = stage.locator("h1")
    expect(title).to_have_text("Motion direction")
    expect(title).to_be_visible()
    time = "node => node.getAnimations().find(item => item.playState === 'running')?.currentTime"
    first = title.evaluate(time)
    assert first is not None
    page.wait_for_timeout(200)
    second = title.evaluate(time)
    assert second is not None and second > first
    build = stage.locator("[data-build='1']")
    expect(build).to_have_class("unrevealed")
    title.hover()
    expect(title).to_have_css("color", "rgb(17, 95, 142)")
    title.click()
    expect(build).not_to_have_class("unrevealed")
    page.keyboard.press("Enter")
    expect(viewer.locator("#stage h1")).to_have_text("Present with confidence")
    expect(viewer.locator("#stage")).to_be_focused()
    page.keyboard.press("Escape")
    expect(present).to_be_focused()
    expect(editor_option).to_have_attribute("aria-selected", "true")
    assert live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")["html"] == current["html"]
    assert not SlideDesigns(live_app.store).list(DECK_ID, "cover").decisions


def test_design_publication_preview_feedback_keep_reload_history_and_undo(
    live_app: RunningApp,
    page: Page,
    artifacts: Path,
) -> None:
    """Previewing never writes; either explicit decision settles and retains the alternatives."""
    before = live_app.store.locate(live_app.store.read(DECK_ID), "cover")
    token = live_app.store.content_hash(DECK_ID, before)
    live_app.store.approve(DECK_ID, "cover", token, True)
    group = publish(live_app)
    assert publish(live_app)["id"] == group["id"]
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    expect(page.locator("#slide-design-tray")).to_be_visible()
    expect(page.locator(".sd-options [role=tab]")).to_have_count(4)
    page.locator("[data-sd-badge]").hover()
    expect(page.locator(".sd-peek")).to_be_visible()
    page.get_by_role("button", name="Preview Editorial design", exact=True).click()
    expect(page.frame_locator(".sd-canvas-overlay iframe").locator("h1")).to_have_text(
        "Editorial direction"
    )
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover") == before
    page.locator("#feedback-text").fill("Refine the Editorial proposal")
    expect(page.locator("#feedback-design-context")).to_have_text("Design · Editorial")
    page.get_by_role("tab", name="Bold", exact=True).click()
    expect(page.locator("#feedback-design-context")).to_have_text("Design · Editorial")
    page.locator("#send-feedback").click()
    expect(page.locator("#feedback-text")).to_have_value("")
    comment = next(item for item in live_app.store.comments(DECK_ID) if item.author == "user")
    assert comment.design_option_id and comment.design_option_name == "Editorial"
    option = live_app.call(
        "get_slide_design", deck_id=DECK_ID, slide_id="cover", design_id=comment.design_option_id
    )
    assert option["html"] == markup("Editorial direction")
    page.locator("[data-sd-compare]").click()
    expect(page.locator(".sd-compare")).to_be_visible()
    page.get_by_role("button", name="Keep Original", exact=True).click()
    expect(page.locator("#slide-design-tray")).to_be_hidden()
    expect(page.locator("#slide-design-toggle")).to_be_hidden()
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover") == before
    assert live_app.store.view(DECK_ID).slides[0].approved
    page.reload()
    expect(page.locator("#slide-design-tray")).to_be_hidden()
    history_chrome_thumbnail(page)
    page.get_by_role("button", name="Version history", exact=True).click()
    saved = page.locator(".sd-history")
    expect(saved).to_be_visible()
    expect(saved).not_to_have_attribute("open", "")
    saved.locator(":scope > summary").click()
    expect(saved).to_contain_text("Kept current design")
    thumbnail = saved.locator(".sd-saved-options .sd-frame-slot").first.bounding_box()
    assert thumbnail and thumbnail["width"] >= 100 and thumbnail["height"] >= 55
    restore = page.locator(".version-restore").bounding_box()
    assert restore and page.viewport_size
    assert restore["y"] + restore["height"] <= page.viewport_size["height"]
    page.get_by_role("button", name="Preview saved Editorial design", exact=True).click()
    expect(page.frame_locator(".sd-canvas-overlay iframe").locator("h1")).to_have_text(
        "Editorial direction"
    )
    page.locator("[data-sd-use]").click()
    expect(page.locator("#slide-design-tray")).to_be_hidden()
    expect(page.locator("#approve-button")).to_have_attribute("aria-pressed", "false")
    chosen = live_app.store.locate(live_app.store.read(DECK_ID), "cover")
    assert chosen.html == markup("Editorial direction") and chosen.notes == PRIVATE_NOTE
    page.locator("#toast").get_by_role("button", name="Undo", exact=True).click()
    expect(page.locator("#slide-design-tray")).to_be_hidden()
    expect(page.locator("#approve-button")).to_have_attribute("aria-pressed", "true")
    restored = live_app.store.locate(live_app.store.read(DECK_ID), "cover")
    assert restored.html == before.html and restored.notes == PRIVATE_NOTE
    _ = page.screenshot(path=str(artifacts / "design-decision-undone.png"))


@pytest.mark.parametrize("width", [390, 768])
def test_designs_arrive_without_changing_slide_or_overwriting_feedback(
    live_app: RunningApp,
    page: Page,
    width: int,
) -> None:
    """Incoming alternatives announce themselves while retaining text, notes and chosen source."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    page.get_by_role("button", name="Open chat", exact=True).click()
    page.locator("#feedback-text").fill("Original slide draft")
    page.get_by_role("button", name="Close chat", exact=True).click()
    before = live_app.store.locate(live_app.store.read(DECK_ID), "cover")
    _ = publish(live_app)
    expect(page.locator("#slide-design-tray")).to_be_visible()
    expect(page.locator("#feedback-text")).to_have_value("Original slide draft")
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover") == before
    page.get_by_role("tab", name="Editorial", exact=True).click()
    page.get_by_role("button", name="Open chat", exact=True).click()
    expect(page.locator("#feedback-design-context")).to_be_hidden()
    page.get_by_role("button", name="Close chat", exact=True).click()
    page.locator("[data-sd-compare]").click()
    page.locator("#sd-candidate").select_option(label="Bold")
    page.locator("#sd-reference").select_option(label="Editorial")
    page.locator(".sd-compare > footer > [data-sd-keep]").click()
    expect(page.locator("#slide-design-tray")).to_be_hidden()
    expect(page.locator("#feedback-text")).to_have_value("Original slide draft")
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover") == before
    assert page.evaluate("document.documentElement.scrollWidth === innerWidth")


def test_external_slide_edit_requires_reviewing_latest_before_replacement(
    live_app: RunningApp,
    page: Page,
) -> None:
    """A proposal generated against older source cannot silently replace a newer human edit."""
    _ = publish(live_app)
    current = live_app.store.locate(live_app.store.read(DECK_ID), "cover")
    _ = live_app.store.revise(
        DECK_ID,
        "cover",
        digest(current.model_dump_json()),
        "external-newer-edit",
        SlidePatch(html=markup("Newer human edit")),
    )
    pending: list[Route] = []
    decisions: list[str] = []
    page.route("**/designs/current-preview?**static=true", lambda route: pending.append(route))
    page.on(
        "request",
        lambda request: decisions.append(request.url) if request.url.endswith("/decide") else None,
    )
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    page.get_by_role("tab", name="Editorial", exact=True).click()
    expect(page.locator("[data-sd-use]")).to_be_disabled()
    expect(page.locator(".sd-conflict")).to_be_visible()
    page.locator("[data-sd-compare]").click()
    expect(page.frame_locator(".sd-compare-left iframe").locator("h1")).to_have_text(
        "Newer human edit"
    )
    page.locator("[data-sd-use-candidate]").click()
    expect(page.locator("[data-sd-use-candidate]")).to_be_disabled()
    active = [route for route in pending if route.request.failure is None]
    assert active and not decisions
    for route in active:
        route.continue_()
    expect(page.locator("#slide-design-tray")).to_be_hidden()
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == markup(
        "Editorial direction"
    )


@pytest.mark.skipif(
    os.environ.get("ARTIFACT_STUDIO_RENDER_TESTS") != "1", reason="Opt-in sandboxed rendering"
)
def test_frozen_design_capture_through_registered_mcp(live_app: RunningApp) -> None:
    """CI runs the ordinary MCP capture wrapper with the production renderer and real PNG."""
    group = DesignSet.model_validate(publish(live_app))
    design_id = group.options[1].id
    before = live_app.store.read(DECK_ID)
    result = CallToolResult.model_validate(
        live_app.client().rpc(
            "tools/call",
            {
                "name": "capture_slide_design",
                "arguments": {"deck_id": DECK_ID, "slide_id": "cover", "design_id": design_id},
            },
        )
    )
    assert not result.is_error
    image = next(item for item in result.content if isinstance(item, ImageContent))
    assert image.mime_type == "image/png"
    assert base64.b64decode(image.data).startswith(b"\x89PNG\r\n\x1a\n")
    manifest = DesignCapture.model_validate(result.structured_content)
    assert manifest.kind == "slide-design" and manifest.selected_slides == 1
    assert manifest.design_id == design_id and manifest.source_hash == group.options[1].source_hash
    assert Path(manifest.path).read_bytes() == base64.b64decode(image.data)
    assert live_app.store.read(DECK_ID) == before


def test_dismiss_suggestions_preserves_history_and_undo(live_app: RunningApp, page: Page) -> None:
    """Dismissal retains the source and allows the pending set to be restored."""
    before = live_app.store.locate(live_app.store.read(DECK_ID), "cover")
    group = publish(live_app)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    page.get_by_role("button", name="Dismiss suggestions", exact=True).click()
    expect(page.locator("[data-sd-badge]")).to_have_count(0)
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover") == before
    assert SlideDesigns(live_app.store).list(DECK_ID, "cover").sets[0].id == group["id"]
    page.locator("#toast").get_by_role("button", name="Undo", exact=True).click()
    expect(page.locator("[data-sd-badge]")).to_have_count(1)
