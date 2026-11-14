"""Real MCP publication and human review of independent static element alternatives."""

import os
import re
from pathlib import Path
from typing import Literal, cast

import pytest
from playwright.sync_api import Browser, Locator, Page, expect

from artifact_studio.models import SlidePatch
from artifact_studio.storage import digest
from tests.integration.support import DECK_ID, PRIVATE_NOTE, RunningApp
from tests.integration.test_slide_designs import history_chrome_thumbnail

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]

HEADLINE_STYLE = (
    "font:700 4.5cqw/1.05 Arial,sans-serif;letter-spacing:-.055em;margin:0;max-width:70cqw"
)
TEXT_STYLE = "font:400 1.9cqw/1.55 Arial,sans-serif;color:#4c665d;margin:0;max-width:36cqw"
HEADLINES = [
    "Good slides happen in the loop.",
    "A stronger story, every review.",
    "From first thought to a clear story.",
]
BODY_COPY = [
    "Bring your ideas. Let your agent draft. Refine together, in context.",
    "Brief together. Explore a direction. Point to what needs to change.",
    "One shared brief. A few directions. A clearer result.",
]


def headline(index: int) -> str:
    """Keep alternatives freely authored with the same stable source target."""
    return f'<h2 id="headline" style="{HEADLINE_STYLE}">{HEADLINES[index]}</h2>'


def body_copy(index: int) -> str:
    return f'<p id="supporting-copy" style="{TEXT_STYLE}">{BODY_COPY[index]}</p>'


def process_graphic(index: int = 0) -> str:
    """Three original static compositions, without a fixed authoring catalogue."""
    outer = "display:flex;align-items:center;justify-content:center;gap:1.5cqw;min-height:16cqw"
    if index == 1:
        outer = "position:relative;min-height:19cqw"
    if index == 2:
        outer = "display:flex;flex-direction:column;gap:1cqw;min-height:16cqw"
    parts: list[str] = []
    for number, name in enumerate(("Brief", "Draft", "Review")):
        node = (
            "background:#d8eadf;border:1px solid #b8d0c2;border-radius:.8cqw;"
            "padding:2.2cqw 1.3cqw;min-width:9cqw;text-align:center;color:#2f5147"
        )
        if number == 2:
            node += ";background:#264f42;color:#fff;border-color:#264f42"
        if index == 1:
            placement = ["left:0;top:0", "right:0;top:0", "left:32%;bottom:0"][number]
            node += ";position:absolute;border-radius:5cqw;" + placement
        if index == 2:
            node += ";padding:1.2cqw 2cqw;display:flex;align-items:center;gap:2cqw;text-align:left"
        parts.append(
            f'<div style="{node}"><small style="display:block;font-size:.8cqw">'
            f'{number + 1:02}</small><strong style="display:block;font-size:1.3cqw">'
            f"{name}</strong></div>"
        )
        if index == 0 and number != 2:
            parts.append('<span style="font-size:1.2cqw;color:#648775">→</span>')
    if index == 1:
        parts.append('<span style="position:absolute;left:46%;top:44%;font-size:2cqw">↻</span>')
    return f'<div id="process-graphic" style="{outer}">' + "".join(parts) + "</div>"


def element_markup() -> str:
    """Fictional fixture shared by real browser journeys and documentation captures."""
    return (
        '<article class="slide" style="background:#fffefb;color:#233c37;'
        'padding:6.3cqw;font-family:Arial,sans-serif;position:relative">'
        '<p style="font-size:.9cqw;letter-spacing:.12em;text-transform:uppercase;'
        'color:#5d746b;margin:0 0 4cqw">From first thought to final slide</p>'
        + headline(0)
        + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:5cqw;'
        'margin-top:4cqw;align-items:center">'
        + body_copy(0)
        + process_graphic()
        + '</div><footer style="position:absolute;bottom:3cqw;left:6.3cqw;right:6.3cqw;'
        'display:flex;justify-content:space-between;font-size:.8cqw;color:#526d62">'
        "<span>Artifact Studio / A clearer way to work</span><span>02</span></footer></article>"
    )


def seed_elements(
    app: RunningApp,
    *,
    slide_id: str = "cover",
    groups: tuple[str, ...] = ("headline", "text", "graphic"),
) -> dict[str, dict[str, object]]:
    """Author source once, then publish through the actual registered MCP contract."""
    current = app.store.locate(app.store.read(DECK_ID), slide_id)
    _ = app.store.revise(
        DECK_ID,
        slide_id,
        digest(current.model_dump_json()),
        "element-browser-fixture-" + slide_id,
        SlidePatch(html=element_markup()),
    )
    current = app.call("get_slide", deck_id=DECK_ID, slide_id=slide_id)
    specs = {
        "headline": ("headline", "Headline", ("Clearer promise", "More direct"), headline),
        "text": ("supporting-copy", "Text", ("Action led", "More concise"), body_copy),
        "graphic": (
            "process-graphic",
            "Process graphic",
            ("Continuous loop", "Clear sequence"),
            process_graphic,
        ),
    }
    published: dict[str, dict[str, object]] = {}
    for kind in groups:
        target_id, label, names, render = specs[kind]
        published[kind] = app.call(
            "publish_element_alternatives",
            deck_id=DECK_ID,
            slide_id=slide_id,
            target_id=target_id,
            kind=kind,
            label=label,
            expected_content_hash=current["content_hash"],
            request_id=f"element-browser-{slide_id}-{kind}",
            options=[
                {
                    "name": name,
                    "rationale": "A distinct direction for review.",
                    "html": render(index),
                }
                for index, name in enumerate(names, start=1)
            ],
        )
    return published


def options(group: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], group["options"])


def option(page: Page, group: dict[str, object], index: int) -> Locator:
    return page.locator(
        f'#element-alternative-panel [data-ea-option="{options(group)[index]["id"]}"]'
    )


def open_group(page: Page, group: dict[str, object]) -> None:
    page.locator(f'[data-ea-badge="{group["id"]}"]').click()
    expect(page.locator("#element-alternative-panel")).to_be_visible()


def test_review_alternatives_control_opens_pending_groups(live_app: RunningApp, page: Page) -> None:
    groups = seed_elements(live_app, groups=("headline", "text"))
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    control = page.get_by_role("button", name=re.compile(r"Review alternatives for \d+ elements?"))
    expect(control).to_be_visible()
    expect(control).to_contain_text("2")
    control.click()
    expect(option(page, groups["text"], 1)).to_be_visible()
    option(page, groups["text"], 1).click()
    page.locator("#element-alternative-panel [data-ea-close]").click()
    expect(control).to_contain_text("1")
    control.click()
    expect(option(page, groups["headline"], 1)).to_be_visible()
    option(page, groups["headline"], 1).click()
    page.locator("#element-alternative-panel [data-ea-close]").click()
    expect(control).to_be_hidden()


def test_click_away_saves_last_clicked_not_hovered_and_can_undo(
    live_app: RunningApp, page: Page
) -> None:
    group = seed_elements(live_app, groups=("headline",))["headline"]
    decisions: list[str] = []
    page.on(
        "request",
        lambda request: (
            decisions.append(request.url)
            if request.method == "POST" and request.url.endswith("/element-alternatives/decide")
            else None
        ),
    )
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    open_group(page, group)
    option(page, group, 1).click()
    option(page, group, 2).click()
    option(page, group, 1).hover()
    expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[1])
    page.frame_locator("#preview").locator("footer").click()
    expect(page.locator("#element-alternative-panel")).to_be_hidden()
    expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[2])
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == (
        element_markup().replace(headline(0), headline(2))
    )
    assert len(decisions) == 1
    page.locator("#toast").get_by_role("button", name="Undo", exact=True).click()
    expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[0])
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == element_markup()


def test_hover_and_escape_do_not_save(live_app: RunningApp, page: Page) -> None:
    group = seed_elements(live_app, groups=("headline",))["headline"]
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    open_group(page, group)
    option(page, group, 1).hover()
    expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[1])
    page.keyboard.press("Escape")
    expect(page.locator("#element-alternative-panel")).to_be_hidden()
    expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[0])
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == element_markup()


@pytest.mark.parametrize("modifier", ["Shift", "ControlOrMeta"])
def test_modified_click_away_saves_choice(
    live_app: RunningApp, page: Page, modifier: Literal["Shift", "ControlOrMeta"]
) -> None:
    group = seed_elements(live_app, groups=("headline",))["headline"]
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    open_group(page, group)
    original_frame = page.locator("#preview").element_handle()
    assert original_frame is not None
    option(page, group, 1).click()
    page.frame_locator("#preview").locator("#supporting-copy").click(modifiers=[modifier])
    expect(page.locator("#element-alternative-panel")).to_be_hidden()
    expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[1])
    expect(page.locator("#selection-box")).to_be_visible()
    assert original_frame.evaluate("frame => frame !== document.getElementById('preview')")
    expect(page.locator("#target-label")).to_contain_text("Bring your ideas")
    page.frame_locator("#preview").locator("footer").click(modifiers=[modifier])
    expect(page.locator("#target-label")).to_have_text("2 objects")
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == (
        element_markup().replace(headline(0), headline(1))
    )


def test_hover_then_click_updates_hint(live_app: RunningApp, page: Page) -> None:
    group = seed_elements(live_app, groups=("graphic",))["graphic"]
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    open_group(page, group)
    option(page, group, 1).hover()
    option(page, group, 1).click()
    expect(page.locator("#element-alternative-panel [data-ea-hint]")).to_have_text(
        "Click outside to keep · Esc to cancel"
    )


def test_open_menu_after_choice_saves_and_allows_new_presentation(
    live_app: RunningApp, page: Page
) -> None:
    group = seed_elements(live_app, groups=("headline",))["headline"]
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    open_group(page, group)
    option(page, group, 1).click()
    page.locator("#workspace-menu-button").click()
    page.locator("#new-button").click()
    expect(page.locator("#new-dialog")).to_be_visible()
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == (
        element_markup().replace(headline(0), headline(1))
    )


def test_switching_target_saves_before_opening_next_group(live_app: RunningApp, page: Page) -> None:
    groups = seed_elements(live_app, groups=("headline", "text"))
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    open_group(page, groups["headline"])
    option(page, groups["headline"], 1).click()
    page.locator(f'[data-ea-badge="{groups["text"]["id"]}"]').click()
    expect(option(page, groups["text"], 1)).to_be_visible()
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == (
        element_markup().replace(headline(0), headline(1))
    )
    option(page, groups["text"], 2).click()
    page.locator("#element-alternative-panel [data-ea-close]").click()
    expect(page.locator("#element-alternative-panel")).to_be_hidden()
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == (
        element_markup().replace(headline(0), headline(1)).replace(body_copy(0), body_copy(2))
    )


def test_failed_save_keeps_selected_choice_for_retry(
    live_app: RunningApp, browser: Browser
) -> None:
    group = seed_elements(live_app, groups=("headline",))["headline"]
    context = browser.new_context(viewport={"width": 1600, "height": 1000})
    page = context.new_page()
    try:
        page.goto(f"{live_app.url}/?deck={DECK_ID}")
        open_group(page, group)
        option(page, group, 1).click()
        page.route(
            "**/element-alternatives/decide",
            lambda route: route.fulfill(status=503, json={"detail": "Temporary failure"}),
            times=1,
        )
        option(page, group, 0).hover()
        page.frame_locator("#preview").locator("#supporting-copy").click(modifiers=["Shift"])
        expect(page.locator("#element-alternative-panel .ea-error")).not_to_be_empty()
        expect(option(page, group, 1)).to_have_attribute("aria-selected", "true")
        expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[1])
        assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == element_markup()
        page.locator("#element-alternative-panel [data-ea-close]").click()
        expect(page.locator("#element-alternative-panel")).to_be_hidden()
        assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == (
            element_markup().replace(headline(0), headline(1))
        )
    finally:
        context.close()


def test_element_choices_preserve_siblings_notes_and_saved_history(
    live_app: RunningApp, page: Page, artifacts: Path
) -> None:
    """Two real choices replace only their own targets and keep the other group available."""
    groups = seed_elements(live_app)
    before = live_app.store.locate(live_app.store.read(DECK_ID), "cover")
    live_app.store.approve(DECK_ID, "cover", live_app.store.content_hash(DECK_ID, before), True)
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    page.locator("#notes-panel").evaluate("element=>element.open=true")
    page.locator("#notes").fill("Notes typed while reviewing the proposals.")
    open_group(page, groups["headline"])
    for index, wording in enumerate(HEADLINES):
        expect(option(page, groups["headline"], index)).to_contain_text(wording)
    option(page, groups["headline"], 1).hover()
    expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[1])
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == before.html
    page.mouse.move(1500, 940)
    expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[0])
    option(page, groups["headline"], 1).click()
    page.mouse.move(1500, 940)
    expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[1])
    page.locator("#element-alternative-panel [data-ea-close]").click()
    expect(page.locator("#element-alternative-panel")).to_be_hidden()
    expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[1])
    expect(page.locator("#preview-wrap.has-unseen-changes")).to_have_count(0)
    current = live_app.store.locate(live_app.store.read(DECK_ID), "cover")
    assert current.html == before.html.replace(headline(0), headline(1))
    assert current.notes == "Notes typed while reviewing the proposals."
    assert not live_app.store.view(DECK_ID).slides[0].approved
    open_group(page, groups["text"])
    option(page, groups["text"], 2).click()
    expect(page.frame_locator("#preview").locator("#supporting-copy")).to_have_text(BODY_COPY[2])
    page.locator("#element-alternative-panel [data-ea-close]").click()
    expect(page.locator("#element-alternative-panel")).to_be_hidden()
    expect(page.locator("#preview-wrap.has-unseen-changes")).to_have_count(0)
    current = live_app.store.locate(live_app.store.read(DECK_ID), "cover")
    assert current.html == before.html.replace(headline(0), headline(1)).replace(
        body_copy(0), body_copy(2)
    )
    assert current.notes == "Notes typed while reviewing the proposals."
    expect(page.locator(f'[data-ea-badge="{groups["graphic"]["id"]}"]')).to_be_visible()
    page.reload()
    expect(page.locator("#notes")).to_be_enabled()
    expect(page.locator(f'[data-ea-badge="{groups["headline"]["id"]}"]')).to_have_count(0)
    history_chrome_thumbnail(page)
    page.get_by_role("button", name="Version history", exact=True).click()
    saved = page.locator(".ea-history")
    expect(saved).to_be_visible()
    saved.locator(":scope > summary").click()
    expect(saved).to_contain_text("Clearer promise")
    _ = page.screenshot(path=str(artifacts / "element-saved-history.png"))


def test_element_proposal_feedback_retains_original_proposal_when_switching(
    live_app: RunningApp, page: Page
) -> None:
    """The visible draft stays anchored to its proposal, not the later preview or chosen slide."""
    group = seed_elements(live_app, groups=("headline",))["headline"]
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    open_group(page, group)
    option(page, group, 1).click()
    page.locator("#feedback-text").click()
    page.locator("#feedback-file-input").set_input_files(
        {"name": "brief.txt", "mimeType": "text/plain", "buffer": b"Fictional audience brief."}
    )
    page.locator("#feedback-text").fill("Refine this proposed headline.")
    expect(page.locator("#feedback-design-context")).to_contain_text("Clearer promise")
    option(page, group, 2).click()
    expect(page.locator("#feedback-design-context")).to_contain_text("Clearer promise")
    page.locator("#send-feedback").click()
    expect(page.locator("#feedback-text")).to_have_value("")
    comment = next(item for item in live_app.store.comments(DECK_ID) if item.author == "user")
    assert comment.element_option_id == options(group)[1]["id"]
    assert comment.element_option_name == "Clearer promise"
    assert comment.content_hash == options(group)[1]["source_hash"]
    assert comment.element_context_hash == group["context_hash"]
    assert [file.name for file in comment.files] == ["brief.txt"]
    source = live_app.call(
        "get_element_alternative",
        deck_id=DECK_ID,
        slide_id="cover",
        element_option_id=comment.element_option_id,
        context_hash=comment.element_context_hash,
    )
    assert source["html"] == headline(1)
    assert source["context_hash"] == comment.element_context_hash
    current = live_app.store.locate(live_app.store.read(DECK_ID), "cover")
    assert current.html == element_markup() and current.notes == PRIVATE_NOTE


@pytest.mark.parametrize("width", [320, 390])
def test_touch_partial_option_reveals_full_wording_and_close_saves(
    live_app: RunningApp, browser: Browser, width: int, artifacts: Path
) -> None:
    """A touch tap reveals the full card and closing saves the pinned choice."""
    groups = seed_elements(live_app, groups=("headline", "text"))
    context = browser.new_context(
        viewport={"width": width, "height": 844}, has_touch=True, is_mobile=True
    )
    page = context.new_page()
    try:
        page.goto(f"{live_app.url}/?deck={DECK_ID}")
        page.locator(f'[data-ea-badge="{groups["headline"]["id"]}"]').tap()
        middle = option(page, groups["headline"], 1)
        box = middle.bounding_box()
        assert box
        page.touchscreen.tap(box["x"] + 12, box["y"] + 18)
        expect(middle).to_have_attribute("aria-selected", "true")
        page.wait_for_timeout(400)
        box = middle.bounding_box()
        panel = page.locator("#element-alternative-panel").bounding_box()
        assert box and panel
        assert box["x"] >= panel["x"]
        assert box["x"] + box["width"] <= panel["x"] + panel["width"]
        expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[1])
        assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == element_markup()
        page.locator("#element-alternative-panel [data-ea-close]").tap()
        expect(page.locator("#element-alternative-panel")).to_be_hidden()
        expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[1])
        assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == (
            element_markup().replace(headline(0), headline(1))
        )
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
        _ = page.screenshot(path=str(artifacts / f"element-touch-{width}.png"))
    finally:
        context.close()


def test_keyboard_escape_cancels_and_slide_switch_saves_pinned_choice(
    live_app: RunningApp, page: Page
) -> None:
    """Escape cancels keyboard previews; changing slides saves the pinned choice."""
    group = seed_elements(live_app, groups=("headline",))["headline"]
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    open_group(page, group)
    option(page, group, 0).focus()
    page.keyboard.press("End")
    expect(option(page, group, 2)).to_be_focused()
    expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[2])
    page.keyboard.press("Home")
    expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[0])
    page.keyboard.press("ArrowRight")
    expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[1])
    page.keyboard.press("Escape")
    expect(page.locator("#element-alternative-panel")).to_be_hidden()
    expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[0])
    open_group(page, group)
    option(page, group, 2).focus()
    page.keyboard.press("Enter")
    page.locator('[data-id="next"]').click()
    expect(page.frame_locator("#preview").locator("h1")).to_have_text("Present with confidence")
    page.locator('[data-id="cover"]').click()
    expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[2])
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == (
        element_markup().replace(headline(0), headline(2))
    )


def test_comparison_then_whole_slide_choice_retains_readonly_earlier_element_context(
    live_app: RunningApp, page: Page
) -> None:
    """Compare fits real fragments; a new whole design cannot adopt an old target snapshot."""
    group = seed_elements(live_app, groups=("headline",))["headline"]
    page.add_init_script("""
        const nativeFetch = window.fetch;
        const pause = new Promise(resolve => { window.fixtureResumeAlternatives = resolve; });
        window.fetch = async (...args) => {
            const alternatives = String(args[0]).endsWith('/element-alternatives');
            if (window.fixtureHoldAlternatives && alternatives) {
                window.fixtureWaitingForAlternatives = true;
                await pause;
            }
            return nativeFetch(...args);
        };
        window.EventSource = class extends EventSource {
            addEventListener(type, listener, options) {
                if (type === 'change') {
                    window.fixtureDeckEvents = this;
                    window.fixtureNotifyChange = () => listener(new Event('change'));
                } else super.addEventListener(type, listener, options);
            }
        };
    """)
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    open_group(page, group)
    option(page, group, 1).click()
    page.locator("#element-alternative-panel [data-ea-compare]").click()
    comparison = page.locator(".ea-comparison")
    expect(comparison).to_be_visible()
    expect(comparison.locator(".ea-compare-left .ea-compare-copy")).to_have_text(HEADLINES[0])
    expect(comparison.locator(".ea-compare-right .ea-compare-copy")).to_have_text(HEADLINES[1])
    comparison.locator('[data-ea-view="slide"]').click()
    expect(page.frame_locator(".ea-compare-left iframe").locator("#headline")).to_have_text(
        HEADLINES[0]
    )
    expect(page.frame_locator(".ea-compare-right iframe").locator("#headline")).to_have_text(
        HEADLINES[1]
    )
    new_heading = "A new direction for the room."
    new_source = element_markup().replace(HEADLINES[0], new_heading)
    page.evaluate("window.fixtureHoldAlternatives = true")
    try:
        comparison.locator("[data-ea-use-right]").click()
        expect(comparison).to_be_hidden()
        _ = page.wait_for_function(
            "window.fixtureWaitingForAlternatives && window.fixtureDeckEvents?.readyState === 1"
        )
        current = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
        _ = live_app.call(
            "publish_slide_designs",
            deck_id=DECK_ID,
            slide_id="cover",
            expected_content_hash=current["content_hash"],
            request_id="element-context-whole-slide",
            options=[{"name": "New direction", "html": new_source}],
        )
        page.evaluate("window.fixtureNotifyChange()")
    finally:
        page.evaluate("window.fixtureHoldAlternatives = false; window.fixtureResumeAlternatives()")
    expect(page.locator("#slide-design-tray")).to_be_visible()
    page.get_by_role("tab", name="New direction", exact=True).click()
    page.locator("#slide-design-tray [data-sd-use]").click()
    expect(page.frame_locator("#preview").locator("#headline")).to_have_text(new_heading)
    page.locator("#feedback-text").fill("My unsent thoughts")
    history_chrome_thumbnail(page)
    page.get_by_role("button", name="Version history", exact=True).click()
    history = page.locator(".ea-history")
    history.locator(":scope > summary").click()
    history.locator(f'.ea-saved-group[data-set-id="{group["id"]}"] > summary').click()
    history.locator(f'[data-ea-saved="{options(group)[1]["id"]}"]').click()
    expect(page.locator(".ea-saved-overlay")).to_be_visible()
    expect(page.locator(".ea-saved-label")).to_contain_text("Earlier design")
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == new_source
    page.locator("#element-alternative-panel [data-ea-refresh]").click()
    expect(page.locator("#feedback-text")).to_have_value("My unsent thoughts")
    page.locator("#element-alternative-panel [data-ea-close]").click()
    expect(page.locator(".ea-saved-overlay")).to_be_hidden()
    expect(page.frame_locator("#preview").locator("#headline")).to_have_text(new_heading)
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == new_source


def test_graphic_cards_render_frozen_crops_and_compare_without_changing_source(
    live_app: RunningApp, page: Page
) -> None:
    """Real cropped frames execute the trusted runtime and only an explicit choice mutates."""
    group = seed_elements(live_app, groups=("graphic",))["graphic"]
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    open_group(page, group)
    for index in range(3):
        expect(
            page.frame_locator("#element-alternative-panel iframe").nth(index).locator("html")
        ).to_have_attribute("data-element-preview-ready", "true")
    option(page, group, 1).click()
    expect(page.frame_locator("#preview").locator("#process-graphic")).to_have_attribute(
        "style", "position:relative;min-height:19cqw"
    )
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == element_markup()

    page.locator("#element-alternative-panel [data-ea-compare]").click()
    for side in ("left", "right"):
        expect(page.frame_locator(f".ea-compare-{side} iframe").locator("html")).to_have_attribute(
            "data-element-preview-ready", "true"
        )
    page.locator(".ea-comparison [data-ea-use-left]").click()
    expect(page.locator(".ea-comparison")).to_be_hidden()
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == element_markup()


def test_held_preview_keeps_its_context_and_requires_loading_new_source_before_reopening(
    live_app: RunningApp, browser: Browser, artifacts: Path
) -> None:
    """A disjoint human choice cannot silently rebind an open preview or old canvas."""
    groups = seed_elements(live_app, groups=("headline", "text"))
    context = browser.new_context(viewport={"width": 1600, "height": 1000})
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(f"{live_app.url}/?deck={DECK_ID}")
        page.locator("#auto-update-preview").click()
        expect(page.locator("#auto-update-preview")).to_have_attribute("aria-pressed", "false")
        open_group(page, groups["headline"])
        option(page, groups["headline"], 1).click()
        page.locator("#feedback-text").click()
        page.locator("#feedback-text").fill("Keep this proposal's original context.")
        before = live_app.store.locate(live_app.store.read(DECK_ID), "cover")
        body_group = groups["text"]
        _ = live_app.post(
            f"/api/decks/{DECK_ID}/slides/cover/element-alternatives/decide",
            {
                "set_id": body_group["id"],
                "option_id": options(body_group)[1]["id"],
                "expected_content_hash": live_app.store.content_hash(DECK_ID, before),
                "expected_decision_id": None,
                "request_id": "external-body-choice-while-preview-held",
            },
        )
        expect(page.locator("#update-banner")).to_be_visible()
        expect(page.frame_locator("#preview").locator("#supporting-copy")).to_have_text(
            BODY_COPY[0]
        )
        expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[1])
        expect(page.locator("#feedback-design-context")).to_contain_text("Clearer promise")
        with page.expect_response(
            lambda response: response.url.endswith("/element-alternatives/decide")
        ) as rejected:
            page.locator("#element-alternative-panel [data-ea-close]").click()
        assert rejected.value.status == 409
        expect(page.locator("#element-alternative-panel .ea-error")).not_to_be_empty()
        expect(option(page, groups["headline"], 1)).to_have_attribute("aria-selected", "true")
        page.keyboard.press("Escape")
        page.locator(f'[data-ea-badge="{groups["headline"]["id"]}"]').hover()
        expect(page.locator("#toast")).to_contain_text("Show the latest slide update")
        expect(page.locator("#element-alternative-panel")).to_be_hidden()
        page.locator("#load-update").click()
        expect(page.frame_locator("#preview").locator("#supporting-copy")).to_have_text(
            BODY_COPY[1]
        )
        open_group(page, groups["headline"])
        option(page, groups["headline"], 1).click()
        page.locator("#element-alternative-panel [data-ea-close]").click()
        expect(page.locator("#element-alternative-panel")).to_be_hidden()
        current = live_app.store.locate(live_app.store.read(DECK_ID), "cover")
        assert current.html == element_markup().replace(headline(0), headline(1)).replace(
            body_copy(0), body_copy(1)
        )
        expect(page.locator("#feedback-text")).to_have_value(
            "Keep this proposal's original context."
        )
        page.locator("#send-feedback").click()
        expect(page.locator("#feedback-text")).to_have_value("")
        comment = next(item for item in live_app.store.comments(DECK_ID) if item.author == "user")
        assert comment.element_option_id == options(groups["headline"])[1]["id"]
        assert comment.element_context_hash == groups["headline"]["context_hash"]
        assert not errors
    finally:
        _ = page.screenshot(path=str(artifacts / "held-preview-context.png"))
        context.close()


def test_delayed_whole_design_open_does_not_displace_element_preview(
    live_app: RunningApp, page: Page
) -> None:
    """A dismissed whole-slide request cannot reopen its tray over an element review."""
    group = seed_elements(live_app, groups=("headline",))["headline"]
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "response", lambda response: errors.append(response.url) if response.status >= 400 else None
    )
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    expect(page.locator(f'[data-ea-badge="{group["id"]}"]')).to_be_visible()
    page.evaluate("""() => {
      const original = window.fetch;
      let release;
      const gate = new Promise(resolve => { release = resolve; });
      window.releaseDesignMetadata = release;
      window.heldDesignMetadata = 0;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (typeof args[0] === 'string' && args[0].endsWith('/slides/cover/designs')) {
          window.heldDesignMetadata++;
          await gate;
        }
        return response;
      };
    }""")
    current = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    _ = live_app.call(
        "publish_slide_designs",
        deck_id=DECK_ID,
        slide_id="cover",
        expected_content_hash=current["content_hash"],
        request_id="delayed-whole-design-open",
        options=[{"name": "Whole direction", "html": element_markup()}],
    )
    badge = page.locator('[data-sd-badge][data-sd-slide="cover"]')
    expect(badge).to_be_visible()
    held_before_open = page.evaluate("window.heldDesignMetadata")
    badge.click()
    page.wait_for_function("count => window.heldDesignMetadata > count", arg=held_before_open)
    open_group(page, group)
    option(page, group, 1).click()
    expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[1])
    page.evaluate(
        "async () => { window.releaseDesignMetadata(); await new Promise(r => setTimeout(r, 0)); }"
    )
    expect(page.locator("#element-alternative-panel")).to_be_visible()
    expect(option(page, group, 1)).to_have_attribute("aria-selected", "true")
    expect(page.frame_locator("#preview").locator("#headline")).to_have_text(HEADLINES[1])
    expect(page.locator("#slide-design-tray")).to_be_hidden()
    assert not errors


def test_dismiss_suggestions_preserves_element_and_undo(live_app: RunningApp, page: Page) -> None:
    """Dismiss a pinned preview without adopting it, then restore its pending badge."""
    group = seed_elements(live_app, groups=("headline",))["headline"]
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_app.url}/?deck={DECK_ID}")
    open_group(page, group)
    option(page, group, 1).click()
    page.locator("#element-alternative-panel [data-ea-keep]").click()
    expect(page.get_by_role("button", name=re.compile(r"Review alternatives for"))).to_be_hidden()
    assert live_app.store.locate(live_app.store.read(DECK_ID), "cover").html == element_markup()
    page.locator("#toast").get_by_role("button", name="Undo", exact=True).click()
    expect(page.get_by_role("button", name=re.compile(r"Review alternatives for"))).to_be_visible()
