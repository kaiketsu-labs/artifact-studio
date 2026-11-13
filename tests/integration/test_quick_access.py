"""Use shared quick-access menus with real clicks, keyboard navigation and live leases."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.review import ReviewHookInput, Reviews
from tests.integration.support import DECK_ID, RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


@pytest.mark.parametrize("width", [1440, 390])
def test_shared_recents_and_connected_work_are_direct_and_keyboard_accessible(
    page: Page, live_app: RunningApp, artifacts: Path, width: int
) -> None:
    """Keep five real projects in order and distinguish connected work from project overviews."""
    registry = ArtifactStore(live_app.store)
    names = [
        "Team launch",
        "Customer research",
        "Product strategy",
        "Operations",
        "Quarterly review",
        "A longer project name that wraps clearly in the menu",
    ]
    projects = [registry.create_project(name) for name in names]
    _ = registry.set_artifact_project(DECK_ID, projects[0].id)
    reviews = Reviews(live_app.store)
    assert reviews.bind(
        ReviewHookInput(
            harness="codex",
            session_id="browser-review",
            event="PostToolUse",
            deck_id=DECK_ID,
            auto_start=True,
        )
    ).listening
    page.set_viewport_size({"width": width, "height": 1000 if width > 700 else 844})
    page.goto(live_app.url)
    for project in projects:
        with page.expect_response(lambda response: response.url.endswith("/api/navigation/visit")):
            page.locator(f'button[data-project="{project.id}"]').click()
    expect(
        page.locator(f'button[data-project="{projects[0].id}"] .project-connected-badge')
    ).to_have_attribute("aria-label", "Codex connected")
    button = page.get_by_role("button", name="Menu", exact=True)
    button.click()
    menu = page.locator("#hub-tools-menu")
    connected = menu.get_by_role("group", name="Connected", exact=True)
    recent = menu.get_by_role("menu", name="Recent projects", exact=True)
    expect(connected.get_by_role("menuitem")).to_have_attribute("href", "/?deck=" + DECK_ID)
    expect(connected.locator(".project-connection-dot")).to_be_visible()
    expect(connected.locator("small")).to_have_text("Team launch")
    recent_button = menu.get_by_role("menuitem", name="Recent projects", exact=True)
    expect(recent).not_to_be_visible()
    if width > 700:
        recent_button.hover()
    else:
        recent_button.click()
    expect(recent).to_be_visible()
    if width < 700:
        recent_button.click()
        expect(recent).not_to_be_visible()
        recent_button.click()
        expect(recent).to_be_visible()
    expect(recent.get_by_role("menuitem")).to_have_text(list(reversed(names[1:])))
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    _ = page.screenshot(path=str(artifacts / "quick-access-light.png"))
    recent.get_by_role("menuitem", name="Product strategy", exact=True).click()
    expect(page.get_by_role("heading", name="Product strategy", exact=True)).to_be_visible()
    expect(menu).not_to_be_visible()
    page.reload()
    button.focus()
    page.keyboard.press("ArrowDown")
    expect(menu.get_by_role("menuitem").first).to_be_focused()
    page.keyboard.press("Escape")
    expect(button).to_be_focused()
    page.locator("#appearance-button").click()
    page.locator('[data-appearance="dark"]').click()
    button.click()
    recent_button.focus()
    page.keyboard.press("Enter")
    expect(recent.get_by_role("menuitem").first).to_be_focused()
    expect(recent.get_by_role("menuitem").first).to_have_text("Product strategy")
    _ = page.screenshot(path=str(artifacts / "quick-access-dark.png"))
    page.keyboard.press("ArrowLeft")
    expect(recent_button).to_be_focused()
    expect(recent).not_to_be_visible()
    connected.get_by_role("menuitem").click()
    page.wait_for_url("**/?deck=" + DECK_ID + "*")
    expect(page.locator("#slide-title")).to_have_text("CI workflow")
    page.get_by_role("button", name="Menu", exact=True).click()
    legacy_menu = page.locator("#workspace-menu")
    legacy_menu.get_by_role("menuitem", name="Recent projects", exact=True).focus()
    page.keyboard.press("ArrowRight")
    expect(
        legacy_menu.get_by_role("menu", name="Recent projects", exact=True)
        .get_by_role("menuitem")
        .first
    ).to_have_text("Team launch")
    expect(
        legacy_menu.get_by_role("menuitem", name="Team launch", exact=True).locator(
            ".project-connected-badge"
        )
    ).to_have_attribute("aria-label", "Codex connected")
    _ = page.screenshot(path=str(artifacts / "quick-access-presentation.png"))
    legacy_menu.get_by_role("menu", name="Recent projects", exact=True).get_by_role(
        "menuitem", name="Quarterly review", exact=True
    ).click()
    expect(page.get_by_role("heading", name="Quarterly review", exact=True)).to_be_visible()


def test_ended_connections_disappear_without_losing_recents(
    page: Page, live_app: RunningApp
) -> None:
    """Refresh connection truth when reopening the menu, preserving the shared recent list."""
    page.goto(live_app.url + "/?deck=" + DECK_ID)
    expect(page.locator("#slide-title")).to_have_text("CI workflow")
    reviews = Reviews(live_app.store)
    assert reviews.bind(
        ReviewHookInput(
            harness="claude-code",
            session_id="ending-review",
            event="PostToolUse",
            deck_id=DECK_ID,
            auto_start=True,
        )
    ).listening
    page.get_by_role("button", name="Menu", exact=True).click()
    menu = page.locator("#workspace-menu")
    expect(menu.get_by_role("group", name="Connected", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    reviews.disconnect(DECK_ID)
    page.get_by_role("button", name="Menu", exact=True).click()
    expect(menu.get_by_role("group", name="Connected", exact=True)).to_have_count(0)
    menu.get_by_role("menuitem", name="Recent projects", exact=True).click()
    expect(
        menu.get_by_role("menu", name="Recent projects", exact=True).get_by_role("menuitem")
    ).to_have_text("Presentations")


@pytest.mark.parametrize("presentation", [False, True])
def test_touch_recents_can_close_without_dismissing_the_parent(
    browser: Browser, live_app: RunningApp, presentation: bool
) -> None:
    """Let touch users reach parent commands after opening an overlapping submenu."""
    _ = live_app.post("/api/navigation/visit", {"artifact_id": DECK_ID})
    context = browser.new_context(viewport={"width": 375, "height": 600}, has_touch=True)
    try:
        page = context.new_page()
        page.goto(live_app.url + ("/?deck=" + DECK_ID if presentation else "/"))
        button = page.get_by_role("button", name="Menu", exact=True)
        button.tap()
        menu = page.locator("#workspace-menu" if presentation else "#hub-tools-menu")
        trigger = menu.get_by_role("menuitem", name="Recent projects", exact=True)
        recent = menu.get_by_role("menu", name="Recent projects", exact=True)
        trigger.tap()
        expect(recent).to_be_visible()
        trigger.tap()
        expect(recent).not_to_be_visible()
        expect(menu).to_be_visible()
        trigger.tap()
        expect(recent).to_be_visible()
        outside = page.evaluate("""() => {
            for (const y of [4, 24, innerHeight - 4]) {
                const hit = document.elementFromPoint(4, y);
                if (hit && !hit.closest('iframe,button,a,:popover-open')) return y;
            }
            return null;
        }""")
        assert isinstance(outside, int), "Expected a tap target outside both menus"
        page.touchscreen.tap(4, outside)
        expect(menu).not_to_be_visible()
        expect(recent).not_to_be_visible()
    finally:
        context.close()
