"""Two-window presenting works without automatic monitor-placement support."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from tests.integration.support import DECK_ID, PRIVATE_NOTE, RunningApp
from tests.integration.test_browser import open_editor

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


@pytest.mark.parametrize("display_access", ["unsupported", "denied"])
@pytest.mark.parametrize("window_host", ["editor", "standalone"])
def test_presenter_click_opens_synchronized_audience_without_display_access(
    page: Page, live_app: RunningApp, artifacts: Path, display_access: str, window_host: str
) -> None:
    """An explicit click opens two views even when monitors cannot be enumerated."""
    script = (
        "delete window.getScreenDetails;"
        if display_access == "unsupported"
        else "window.getScreenDetails = async () => {"
        "throw new DOMException('Display permission denied', 'NotAllowedError');};"
    )
    page.add_init_script(script)
    page.context.add_init_script("""(() => {
        const trace = window.__fullscreenTrace = [];
        const record = token => { if (trace.length < 32) trace.push(token); };
        for (const [prototype, method, prefix] of [
            [Element.prototype, 'requestFullscreen', 'r'],
            [Document.prototype, 'exitFullscreen', 'e'],
        ]) {
            const original = prototype[method];
            if (!original) continue;
            prototype[method] = function(...args) {
                record(prefix);
                record(document.hasFocus() ? 'h1' : 'h0');
                record(navigator.userActivation?.isActive ? 'a1' : 'a0');
                try {
                    const result = original.apply(this, args);
                    result.then(() => record(prefix + 's'), error =>
                        record(prefix + (error?.name === 'NotAllowedError' ? 'n' : 'x')));
                    return result;
                } catch (error) {
                    record(prefix + (error?.name === 'NotAllowedError' ? 'n' : 'x'));
                    throw error;
                }
            };
        }
        document.addEventListener('fullscreenchange', () =>
            record(document.fullscreenElement ? 'c1' : 'c0'));
    })()""")
    _ = (live_app.store.folder(DECK_ID) / "presentation.js").write_text(
        "/* Older offline viewer; the running app supplies current controls. */"
    )
    if window_host == "editor":
        open_editor(page, live_app)
        page.get_by_role("link", name="Present", exact=True).click()
        viewer = page.frame_locator('iframe[title="Presentation"]')
    else:
        page.goto(f"{live_app.url}/decks/{DECK_ID}/index.html#cover")
        page.get_by_role("button", name="Present", exact=True).click()
        viewer = page
    expect(viewer.locator("#stage h1")).to_have_text("Review together")
    with page.expect_popup() as opened:
        viewer.get_by_role("button", name="Presenter view", exact=True).click()
    audience = opened.value
    expect(viewer.locator("#presenter-view")).to_be_visible()
    expect(viewer.get_by_role("textbox", name="Speaker notes", exact=True)).to_have_value(
        PRIVATE_NOTE
    )
    expect(audience.locator("#stage h1")).to_have_text("Review together")
    expect(audience.locator("#presenter-view")).to_be_hidden()
    assert PRIVATE_NOTE not in audience.locator("body").inner_text()
    expect(viewer.locator("#display-status")).to_contain_text(
        "browser" if display_access == "unsupported" else "denied"
    )
    viewer.get_by_role("button", name="Pause timer", exact=True).click()
    viewer.locator("#audience-view-button").click()
    viewer.get_by_role("button", name="Presenter view", exact=True).click()
    expect(viewer.get_by_role("button", name="Resume timer", exact=True)).to_be_visible()
    audience.bring_to_front()
    audience.wait_for_function("document.hasFocus()")
    already_fullscreen = audience.evaluate(
        """() => {
            const active = Boolean(document.fullscreenElement || document.webkitFullscreenElement);
            if (window.__fullscreenTrace.length < 32)
                window.__fullscreenTrace.push(active ? 'q1' : 'q0');
            return active;
        }"""
    )
    if not already_fullscreen:
        audience.evaluate(
            "if (window.__fullscreenTrace.length < 32) window.__fullscreenTrace.push('k')"
        )
        audience.get_by_role("button", name="Fullscreen", exact=True).click()
    try:
        audience.wait_for_function(
            "Boolean(document.fullscreenElement || document.webkitFullscreenElement)"
        )
    except PlaywrightTimeoutError as error:
        trace = audience.evaluate("window.__fullscreenTrace || []")
        error.add_note("FULLSCREEN_TRACE=" + ",".join(trace))
        raise
    viewer.locator("#presenter-next-button").click()
    expect(audience.locator("#stage h1")).to_have_text("Present with confidence")
    expect(viewer.locator("#presenter-current h1")).to_have_text("Present with confidence")
    _ = page.screenshot(path=str(artifacts / "presenter-window.png"))
    _ = audience.screenshot(path=str(artifacts / "audience-window.png"))
    viewer.get_by_role("button", name="End", exact=True).click()
    expect(page.locator('iframe[title="Presentation"]')).to_have_count(0)
    expect(page.frame_locator("#preview").locator("h1")).to_have_text("Present with confidence")
    assert audience.is_closed()


def test_blocked_audience_popup_keeps_presenter_retry_available(
    page: Page, live_app: RunningApp
) -> None:
    """A popup blocker leaves an explicit retry without losing the slide or notes."""
    page.add_init_script("delete window.getScreenDetails; window.open = () => null;")
    open_editor(page, live_app)
    page.get_by_role("link", name="Present", exact=True).click()
    viewer = page.frame_locator('iframe[title="Presentation"]')
    viewer.get_by_role("button", name="Presenter view", exact=True).click()
    expect(viewer.locator("#presenter-view")).to_be_visible()
    expect(viewer.locator("#display-status")).to_contain_text("Pop-up blocked")
    expect(viewer.get_by_role("button", name="Open audience window", exact=True)).to_be_visible()
    expect(viewer.get_by_role("textbox", name="Speaker notes", exact=True)).to_have_value(
        PRIVATE_NOTE
    )
    viewer.get_by_role("button", name="End", exact=True).click()
    expect(page.locator('iframe[title="Presentation"]')).to_have_count(0)


@pytest.mark.parametrize("width", [375, 390])
def test_narrow_presenter_controls_and_notes_remain_usable(
    page: Page, live_app: RunningApp, width: int
) -> None:
    """The presenter can navigate and edit notes without overlapping controls on a phone."""
    page.set_viewport_size({"width": width, "height": 844})
    page.add_init_script("delete window.getScreenDetails; window.open = () => null;")
    open_editor(page, live_app)
    page.get_by_role("link", name="Present", exact=True).click()
    viewer = page.frame_locator('iframe[title="Presentation"]')
    viewer.get_by_role("button", name="Presenter view", exact=True).click()
    next_button = viewer.locator("#presenter-next-button")
    box = next_button.bounding_box()
    assert box and box["width"] >= 44 and box["height"] >= 44
    next_button.click()
    expect(viewer.locator("#presenter-current h1")).to_have_text("Present with confidence")
    viewer.locator("#presenter-previous").click()
    expect(viewer.locator("#presenter-current h1")).to_have_text("Review together")
    notes = viewer.get_by_role("textbox", name="Speaker notes", exact=True)
    expect(notes).to_have_value(PRIVATE_NOTE)
    notes.fill("Updated fictional speaker notes")
    expect(viewer.locator("#presenter-note-status")).to_have_text("Saved")
    viewer.get_by_role("button", name="End", exact=True).click()
    expect(page.locator('iframe[title="Presentation"]')).to_have_count(0)
    expect(page.frame_locator("#preview").locator("h1")).to_have_text("Review together")
    assert live_app.store.read(DECK_ID).slides[0].notes == "Updated fictional speaker notes"


@pytest.mark.parametrize("popup_blocked", [False, True])
def test_previously_granted_displays_preserve_fullscreen_lifecycle(
    page: Page, live_app: RunningApp, popup_blocked: bool
) -> None:
    """Automatic placement preserves the show; a blocked popup keeps Escape usable."""
    page.add_init_script(
        "navigator.permissions.query = async () => ({state: 'granted'});"
        "const current = {label:'This display',left:0,top:0,width:1600,height:1000};"
        "const other = {label:'Audience display',left:1600,top:0,width:1600,height:1000,"
        "availLeft:1600,availTop:0,availWidth:1600,availHeight:1000};"
        "window.getScreenDetails = async () => ({screens:[current,other],"
        "currentScreen:current,addEventListener(){},removeEventListener(){}});"
        + ("window.open = () => null;" if popup_blocked else "")
    )
    open_editor(page, live_app)
    if popup_blocked:
        page.get_by_role("link", name="Present", exact=True).click()
        viewer = page.frame_locator('iframe[title="Presentation"]')
        expect(viewer.locator("#stage h1")).to_have_text("Review together")
        expect(viewer.locator("#presenter-view")).to_be_hidden()
        page.wait_for_function("Boolean(document.fullscreenElement)")
        page.keyboard.press("Escape")
        expect(page.locator('iframe[title="Presentation"]')).to_have_count(0)
    else:
        with page.expect_popup() as opened:
            page.get_by_role("link", name="Present", exact=True).click()
        audience = opened.value
        viewer = page.frame_locator('iframe[title="Presentation"]')
        expect(viewer.locator("#presenter-view")).to_be_visible()
        expect(audience.locator("#stage h1")).to_have_text("Review together")
        viewer.get_by_role("button", name="End", exact=True).click()
        expect(page.locator('iframe[title="Presentation"]')).to_have_count(0)
        assert audience.is_closed()
