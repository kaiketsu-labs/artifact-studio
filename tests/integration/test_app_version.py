"""Installed version and release availability in the real library and editor."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from artifact_studio import __version__
from tests.integration.support import DECK_ID, RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


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
    }
    page.route("**/api/app-info", lambda route: route.fulfill(json=info))
    page.goto(live_app.url + (f"/?deck={DECK_ID}" if surface == "editor" else "/"))
    menu = page.get_by_role("button", name="Menu", exact=True)
    expect(menu).to_have_attribute("data-app-update", "")
    menu.click()
    entry = page.locator("[data-app-about]")
    expect(entry).to_contain_text("Version " + __version__)
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
