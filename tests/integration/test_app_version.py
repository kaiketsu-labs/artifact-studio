"""Installed version and release availability in the real library and editor."""

import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, Route, expect

from artifact_studio import __version__
from scripts.preview_favorites import prepare_covers
from tests.integration.support import DECK_ID, RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


@pytest.fixture(autouse=True)
def fixture_covers(live_app: RunningApp, browser: Browser) -> None:
    """Version controls do not depend on the production renderer's OS sandbox."""
    prepare_covers(live_app, browser)


@pytest.mark.parametrize(
    "surface,theme,width,height",
    [
        ("hub", "light", 1280, 800),
        ("hub", "dark", 1280, 800),
        ("hub", "dark", 390, 844),
        ("hub", "light", 320, 700),
        ("editor", "light", 1280, 800),
        ("editor", "dark", 390, 844),
    ],
)
def test_installed_version_and_update_menu(
    live_app: RunningApp,
    page: Page,
    artifacts: Path,
    surface: str,
    theme: str,
    width: int,
    height: int,
) -> None:
    """Discover the version, inspect a release, and return keyboard focus safely."""
    page.set_viewport_size({"width": width, "height": height})
    page.add_init_script(f"localStorage.setItem('artifact-studio:appearance','{theme}')")
    info: dict[str, str | bool | None] = {
        "version": __version__,
        "latest_tag": "v9.0.0",
        "latest_version": "9.0.0",
        "update_available": True,
        "update_check": "ok",
        "include_nightly": False,
        "installed_tag": None,
    }
    page.route("**/api/app-info", lambda route: route.fulfill(json=info))
    page.goto(live_app.url + (f"/?deck={DECK_ID}" if surface == "editor" else "/"))
    menu = page.get_by_role("button", name="Menu", exact=True)
    expect(menu).to_have_attribute("data-app-update", "")
    menu.click()
    entry = page.locator("[data-app-about]")
    expect(entry).not_to_contain_text("Version " + __version__)
    sidebar_version = page.locator(".app-sidebar-footer:last-child [data-server-version]")
    expect(sidebar_version).to_have_text("v" + __version__)
    visible_rows = page.locator("#hub-tools-menu, #workspace-menu").locator(
        '[role="menuitem"]:visible'
    )
    sizes = visible_rows.evaluate_all("(rows) => rows.map(row => getComputedStyle(row).fontSize)")
    assert sizes and set(sizes) == {"13px"}
    expect(entry.locator(".app-update-icon")).to_be_visible()
    entry.click()
    dialog = page.get_by_role("dialog", name="About Artifact Studio")
    expect(dialog).to_be_visible()
    expect(dialog.locator("[data-installed-version]")).to_have_text("Version " + __version__)
    expect(dialog).to_contain_text("Update available: v9.0.0")
    expect(dialog.get_by_role("link", name="View release notes")).to_have_attribute(
        "href", "https://github.com/kaiketsu-labs/artifact-studio/releases/tag/v9.0.0"
    )
    version_bounds = dialog.locator("[data-installed-version]").bounding_box()
    update_bounds = dialog.get_by_role("link", name="View release notes").bounding_box()
    assert version_bounds and update_bounds
    assert (
        abs(
            version_bounds["y"]
            + version_bounds["height"] / 2
            - update_bounds["y"]
            - update_bounds["height"] / 2
        )
        <= 2
    )
    assert version_bounds["x"] + version_bounds["width"] <= update_bounds["x"]
    if width <= 600:
        assert update_bounds["height"] >= 44
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(artifacts / f"version-{surface}-{theme}-{width}.png"))
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(menu).to_be_focused()
    # A failed or disabled check must not keep a stale update marker.
    info.update(
        latest_tag=None, latest_version=None, update_available=False, update_check="unavailable"
    )
    menu.click()
    entry.click()
    expect(dialog).to_contain_text("Update information is unavailable.")
    expect(menu).not_to_have_attribute("data-app-update", "")
    expect(dialog.locator("[data-installed-version]")).to_have_text("Version " + __version__)


@pytest.mark.parametrize("surface,width", [("hub", 1280), ("editor", 390)])
def test_nightly_preference_persists_and_controls_update_offer(
    live_app: RunningApp, page: Page, surface: str, width: int
) -> None:
    """Persist real settings while simulating only the remote release discovery result."""
    page.set_viewport_size({"width": width, "height": 844})
    page.add_init_script("localStorage.setItem('artifact-studio:appearance','dark')")

    def fixture_release(route: Route) -> None:
        response = route.fetch()
        info = response.json()
        nightly = info["include_nightly"]
        info.update(
            latest_tag="nightly-20261106.123" if nightly else "v" + __version__,
            update_available=nightly,
            update_check="ok",
        )
        route.fulfill(response=response, json=info)

    page.route(re.compile(r"/api/app-info(?:/settings)?$"), fixture_release)
    page.goto(live_app.url + (f"/?deck={DECK_ID}" if surface == "editor" else "/"))
    menu = page.get_by_role("button", name="Menu", exact=True)
    expect(menu).not_to_have_attribute("data-app-update", "")
    menu.click()
    entry = page.locator("[data-app-about]")
    expect(entry.locator(".app-update-icon")).to_be_hidden()
    entry.click()
    dialog = page.get_by_role("dialog", name="About Artifact Studio")
    expect(dialog).to_contain_text("You’re up to date.")
    expect(dialog.get_by_role("link", name="View release notes")).to_be_hidden()
    checkbox = dialog.get_by_role("checkbox", name="Include nightly builds")
    expect(checkbox).not_to_be_checked()
    checkbox.check()
    expect(dialog).to_contain_text("Update available: nightly-20261106.123")
    expect(menu).to_have_attribute("data-app-update", "")
    expect(dialog.get_by_role("link", name="View release notes")).to_have_attribute(
        "href", "https://github.com/kaiketsu-labs/artifact-studio/releases/tag/nightly-20261106.123"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.reload()
    menu.click()
    page.locator("[data-app-about]").click()
    expect(checkbox).to_be_checked()
    checkbox.uncheck()
    expect(dialog).to_contain_text("You’re up to date.")
    expect(menu).not_to_have_attribute("data-app-update", "")
    expect(dialog.get_by_role("link", name="View release notes")).to_be_hidden()


def test_failed_update_requests_recover_without_a_stale_offer(
    live_app: RunningApp, browser: Browser
) -> None:
    """Intentional HTTP failures leave the preference intact and clear stale offers."""
    context = browser.new_context()
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.route(
            "**/api/app-info",
            lambda route: route.fulfill(
                json={
                    "version": __version__,
                    "update_available": True,
                    "latest_tag": "v9.0.0",
                    "update_check": "ok",
                    "include_nightly": False,
                }
            ),
        )
        page.goto(live_app.url)
        menu = page.get_by_role("button", name="Menu", exact=True)
        menu.click()
        page.locator("[data-app-about]").click()
        dialog = page.get_by_role("dialog", name="About Artifact Studio")
        expect(dialog).to_contain_text("Update available: v9.0.0")
        page.route("**/api/app-info/settings", lambda route: route.fulfill(status=503))
        checkbox = dialog.get_by_role("checkbox", name="Include nightly builds")
        checkbox.check()
        expect(dialog).to_contain_text("Could not save the update preference.")
        expect(checkbox).not_to_be_checked()
        expect(checkbox).to_be_enabled()
        page.route("**/api/app-info", lambda route: route.fulfill(status=503))
        page.keyboard.press("Escape")
        menu.click()
        page.locator("[data-app-about]").click()
        expect(dialog).to_contain_text("Update information is unavailable.")
        expect(menu).not_to_have_attribute("data-app-update", "")
        expect(dialog.get_by_role("link", name="View release notes")).to_be_hidden()
        assert not errors
    finally:
        context.close()


@pytest.mark.parametrize("surface", ["library", "artifact"])
@pytest.mark.parametrize("activation", ["mouse", "keyboard"])
def test_narrow_sidebar_collapse_removes_its_layout_space(
    live_app: RunningApp, page: Page, tmp_path: Path, surface: str, activation: str
) -> None:
    """Hide the sidebar and its version footer without leaving a blank mobile block."""
    from tests.integration.test_artifact_workspace import register

    _, item, _, _, _ = register(live_app, tmp_path)
    page.set_viewport_size({"width": 390, "height": 844})
    page.emulate_media(color_scheme="dark")
    page.goto(live_app.url + (f"/?artifact={item.id}" if surface == "artifact" else "/"))
    sidebar = page.locator(".hub-sidebar")
    version = sidebar.locator("[data-server-version]")
    expect(version).to_have_text("v" + __version__)
    heading = page.locator(".hub-edit-heading h1" if surface == "artifact" else ".hub-heading h1")
    before = heading.bounding_box()
    sidebar_box = sidebar.bounding_box()
    assert before and sidebar_box and sidebar_box["height"] > 0
    close = page.get_by_role("button", name="Close sidebar", exact=True)
    if activation == "keyboard":
        close.focus()
        close.press("Enter")
    else:
        close.click()
    expect(page.get_by_role("button", name="Open sidebar", exact=True)).to_be_visible()
    assert sidebar.evaluate("element => element.getBoundingClientRect().height") == 0
    after = heading.bounding_box()
    assert after and after["y"] <= before["y"] - sidebar_box["height"] + 2
    page.get_by_role("button", name="Open sidebar", exact=True).click()
    expect(version).to_be_visible()
    page.set_viewport_size({"width": 1280, "height": 800})
    expect(version).to_be_visible()
    assert sidebar.evaluate("element => element.getBoundingClientRect().width") > 0
