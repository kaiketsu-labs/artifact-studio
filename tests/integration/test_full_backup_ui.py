"""Full-workspace backup opt-in and cross-device recovery through the real browser UI."""

import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest
from playwright.sync_api import Page, Route, expect

from artifact_studio.agent_client import object_value
from artifact_studio.libraries import Libraries, Library
from artifact_studio.storage import Store
from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_browser import open_editor

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]

LIBRARY_ID = "full-backup-ui-fixture"


def backup_fixture(
    page: Page, live_app: RunningApp
) -> tuple[dict[str, object], list[tuple[str, dict[str, object]]]]:
    """Replace only remote-backed API boundaries with explicitly fictional data."""
    collection = Libraries(live_app.store).list()[0].model_dump()
    repository = Library(
        id=LIBRARY_ID,
        kind="github",
        location="fictional-studio/private-workspace",
        path="/tmp/fictional-full-backup-cache",
    ).model_dump()
    state: dict[str, object] = {
        "library_id": LIBRARY_ID,
        "settings": {"enabled": False, "interval_hours": 24, "keep_count": 7},
        "phase": "disabled",
        "last_backup_at": "",
        "next_backup_at": "",
        "error": "",
        "warning": "",
        "archive_name": "",
        "url": "",
    }
    writes: list[tuple[str, dict[str, object]]] = []

    def settings(route: Route) -> None:
        body = object_value(route.request.post_data_json)
        writes.append(("settings", body))
        state.update(
            settings=body,
            phase="scheduled" if body["enabled"] else "disabled",
            next_backup_at="2026-11-06T10:30:00Z" if body["enabled"] else "",
        )
        route.fulfill(json=state)

    def run(route: Route) -> None:
        writes.append(("run", object_value(route.request.post_data_json)))
        state.update(phase="queued", error="")
        route.fulfill(json=state)

    page.route("**/api/libraries", lambda route: route.fulfill(json=[collection, repository]))
    page.route("**/api/library-sync", lambda route: route.fulfill(json=[]))
    page.route("**/api/workspace/github-backups", lambda route: route.fulfill(json=[state]))
    page.route(f"**/api/libraries/{LIBRARY_ID}/full-backup/settings", settings)
    page.route(f"**/api/libraries/{LIBRARY_ID}/full-backup/run", run)
    return state, writes


@pytest.mark.parametrize("editor,narrow", [(False, False), (True, True)])
def test_full_backup_requires_save_and_preserves_drafts_during_polling(
    page: Page, live_app: RunningApp, artifacts: Path, editor: bool, narrow: bool
) -> None:
    """Opt in explicitly, queue a backup and retain focused settings through status updates."""
    state, writes = backup_fixture(page, live_app)
    if narrow:
        page.set_viewport_size({"width": 390, "height": 844})
        page.emulate_media(color_scheme="dark")
    else:
        page.set_viewport_size({"width": 1440, "height": 900})
    if editor:
        open_editor(page, live_app)
    else:
        # This journey starts from an empty hub; rendering covers is exercised by
        # the separate artifact journeys and is unrelated to backup opt-in.
        _ = live_app.post(f"/api/decks/{DECK_ID}/archive", {})
        page.goto(live_app.url)
    page.locator("#storage-status").click()
    dialog = page.get_by_role("dialog", name="Storage and backup")
    full = dialog.locator(f'details[data-full-backup="{LIBRARY_ID}"]')
    expect(full.locator(":scope > summary")).to_contain_text("Full workspace backups")
    full.locator(":scope > summary").click()
    enabled = full.get_by_role("checkbox", name="Enable full backups", exact=True)
    interval = full.get_by_role("spinbutton", name="Check every (hours)", exact=True)
    retained = full.get_by_role("spinbutton", name="Keep latest (backups)", exact=True)
    expect(enabled).not_to_be_checked()
    expect(interval).to_have_value("24")
    expect(retained).to_have_value("7")
    expect(interval).to_have_attribute("min", "1")
    expect(interval).to_have_attribute("max", "720")
    expect(retained).to_have_attribute("max", "100")
    expect(full).to_contain_text("Saved files, chats")
    expect(full).to_contain_text("approvals")
    expect(full).to_contain_text("attachments")
    expect(full).to_contain_text("history")
    expect(full).to_contain_text("Stored in this private repository.")
    expect(full).to_contain_text("Only changed workspaces are backed up.")
    help_details = full.locator(".full-backup-help")
    expect(help_details.locator("p")).to_be_hidden()
    help_details.locator("summary").click()
    expect(help_details.locator("p")).to_be_visible()
    expect(help_details).to_contain_text("Studio must be running")
    expect(help_details).to_contain_text("after a verified upload")
    help_details.locator("summary").click()
    enabled.focus()
    enabled.press("Space")
    interval.fill("6")
    retained.fill("3")
    with page.expect_request("**/api/workspace/github-backups"):
        expect(enabled).to_be_checked()
    assert writes == []
    save = full.get_by_role("button", name="Save settings", exact=True)
    retained.focus()
    retained.press("Tab")
    expect(save).to_be_focused()
    save.press("Enter")
    expect(full).to_contain_text("Next")
    # Keep the normal opt-in actions reachable without another scroll on a
    # typical desktop. The narrow card still fits one readable group of fields.
    assert full.evaluate("node => node.getBoundingClientRect().height <= 420") is True
    if not narrow:
        assert (
            full.locator(".full-backup-actions").evaluate(
                "node => { const box = node.getBoundingClientRect(); "
                "const dialog = node.closest('dialog').getBoundingClientRect(); "
                "return box.top >= dialog.top && "
                "box.bottom <= Math.min(dialog.bottom, innerHeight); }"
            )
            is True
        )
    assert writes == [("settings", {"enabled": True, "interval_hours": 6, "keep_count": 3})]
    with page.expect_response(f"**/api/libraries/{LIBRARY_ID}/full-backup/run"):
        full.get_by_role("button", name="Check now", exact=True).click()
    assert writes[-1] == ("run", {})
    state.update(
        phase="backed_up",
        last_backup_at="2026-11-05T10:30:00Z",
        next_backup_at="2026-11-05T16:30:00Z",
        warning="An old backup could not be removed. The new backup is safe.",
    )
    interval.fill("72")
    interval.focus()
    with page.expect_request("**/api/workspace/github-backups"):
        expect(interval).to_be_focused()
    expect(full).to_contain_text("The new backup is safe.")
    expect(interval).to_be_focused()
    expect(interval).to_have_value("72")
    expect(retained).to_have_value("3")
    assert full.evaluate("details => details.open") is True
    expect(full.locator('time[datetime="2026-11-05T10:30:00Z"]')).to_be_visible()
    state.update(phase="error", error="GitHub sign-in expired. Sign in before the next backup.")
    expect(full).to_contain_text("GitHub sign-in expired.")
    expect(full.locator('time[datetime="2026-11-05T10:30:00Z"]')).to_be_visible()
    assert len(writes) == 2
    assert dialog.evaluate("dialog => dialog.scrollWidth <= dialog.clientWidth") is True
    assert full.evaluate("details => details.scrollWidth <= details.clientWidth") is True
    _ = page.screenshot(path=str(artifacts / "full-backup-settings.png"))


@pytest.mark.parametrize("narrow", [False, True])
def test_unchanged_manual_check_preserves_success_and_saved_settings(
    page: Page, live_app: RunningApp, artifacts: Path, narrow: bool
) -> None:
    """A completed no-change check keeps the last delivery and remains usable."""
    state, writes = backup_fixture(page, live_app)
    state.update(
        settings={"enabled": True, "interval_hours": 6, "keep_count": 3},
        phase="backed_up",
        last_backup_at="2026-11-05T10:30:00Z",
        next_backup_at="2026-11-05T16:30:00Z",
        archive_name="fictional-verified-workspace.artifact-studio.zip",
        url="https://github.com/fictional-studio/private-workspace/releases/301",
    )
    if narrow:
        page.set_viewport_size({"width": 390, "height": 844})
        page.emulate_media(color_scheme="dark")
    open_editor(page, live_app)
    storage = page.locator("#storage-status")
    expect(storage).to_have_attribute("data-phase", "synced")
    storage.click()
    dialog = page.get_by_role("dialog", name="Storage and backup")
    full = dialog.locator(f'details[data-full-backup="{LIBRARY_ID}"]')
    full.locator(":scope > summary").click()
    status = full.locator("[data-full-status]")
    delivered = status.locator('time[datetime="2026-11-05T10:30:00Z"]')
    expect(delivered).to_be_visible()
    delivered_text = delivered.inner_text()
    run = full.get_by_role("button", name="Check now", exact=True)
    run.click()
    expect(full).to_contain_text("Check queued")
    expect(run).to_be_disabled()
    assert writes == [("run", {})]
    state.update(phase="unchanged", next_backup_at="2026-11-05T17:30:00Z")
    expect(full).to_contain_text("No changes since last backup")
    expect(status.locator(".sync-state")).to_have_class("sync-state sync-synced")
    expect(storage).to_have_attribute("data-phase", "synced")
    expect(storage).to_have_attribute(
        "title", "Saved on this device. Last GitHub backup succeeded."
    )
    expect(delivered).to_have_text(delivered_text)
    expect(status).to_contain_text("Next check")
    expect(status.locator('time[datetime="2026-11-05T17:30:00Z"]')).to_be_visible()
    expect(status.locator('time[datetime="2026-11-05T16:30:00Z"]')).to_have_count(0)
    expect(full.get_by_role("checkbox", name="Enable full backups", exact=True)).to_be_checked()
    expect(full.get_by_role("spinbutton", name="Check every (hours)", exact=True)).to_have_value(
        "6"
    )
    expect(full.get_by_role("spinbutton", name="Keep latest (backups)", exact=True)).to_have_value(
        "3"
    )
    expect(full.locator("[data-full-unsaved]")).to_be_hidden()
    expect(run).to_be_enabled()
    # A second keyboard invocation proves a no-change result is terminal and does
    # not strand the manual control in its queued/disabled state.
    run.focus()
    run.press("Enter")
    expect(full).to_contain_text("Check queued")
    assert writes == [("run", {}), ("run", {})]
    state.update(phase="unchanged", next_backup_at="2026-11-05T18:30:00Z")
    expect(full).to_contain_text("No changes since last backup")
    expect(run).to_be_enabled()
    expect(delivered).to_have_text(delivered_text)
    expect(status.locator('time[datetime="2026-11-05T18:30:00Z"]')).to_be_visible()
    assert dialog.evaluate("node => node.scrollWidth <= node.clientWidth") is True
    assert full.evaluate("node => node.scrollWidth <= node.clientWidth") is True
    _ = page.screenshot(path=str(artifacts / "full-backup-unchanged.png"))


def test_full_github_backup_loads_on_demand_and_restores_another_workspace(
    page: Page, live_app: RunningApp, artifacts: Path
) -> None:
    """Select a backup from another PC, preview real files, then confirm a separate restore."""
    _, writes = backup_fixture(page, live_app)
    with urlopen(live_app.url + "/api/workspace/archive", timeout=10) as response:
        archive = response.read()
    listed: list[str] = []
    selected: list[dict[str, object]] = []
    assets = [
        {
            "asset_id": asset_id,
            "name": f"fictional-workspace-{asset_id}.artifact-studio.zip",
            "created_at": created_at,
            "size": len(archive),
            "workspace_id": workspace_id,
            "url": f"https://github.com/fictional-studio/private-workspace/releases/{asset_id}",
        }
        for asset_id, created_at, workspace_id in [
            (301, "2026-11-05T10:30:00Z", "fictional-current-pc"),
            (302, "2026-11-04T10:30:00Z", "fictional-other-pc"),
        ]
    ]

    def list_archives(route: Route) -> None:
        listed.append(route.request.url)
        route.fulfill(json=assets)

    def prepare_remote_archive(route: Route) -> None:
        body = object_value(route.request.post_data_json)
        selected.append(body)
        # The remote download boundary yields real archive bytes to the existing
        # recovery preparation route; all preview and restore work remains real.
        query = urlencode({"name": str(body["name"])})
        request = Request(
            live_app.url + "/api/workspace/recovery/upload?" + query,
            data=archive,
            headers={"Content-Type": "application/zip"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            prepared = object_value(json.load(response))
        route.fulfill(json=prepared)

    page.route(f"**/api/libraries/{LIBRARY_ID}/full-backup/archives", list_archives)
    page.route("**/api/workspace/recovery/github-archive", prepare_remote_archive)
    open_editor(page, live_app)
    original_url = page.url
    page.locator("#storage-status").click()
    page.get_by_role("button", name="Restore workspace", exact=True).click()
    recovery = page.locator("dialog.workspace-recovery")
    recovery.get_by_role("combobox", name="Recovery source", exact=True).select_option(
        label="Full GitHub backup"
    )
    recovery.get_by_role("combobox", name="Full backup repository", exact=True).select_option(
        LIBRARY_ID
    )
    with page.expect_request("**/api/workspace/github-backups"):
        expect(recovery.get_by_role("button", name="Load backups", exact=True)).to_be_visible()
    assert listed == []
    recovery.get_by_role("button", name="Load backups", exact=True).click()
    backups = recovery.get_by_role("combobox", name="Workspace backup", exact=True)
    expect(backups.locator('option[value="301"]')).to_have_count(1)
    expect(backups.locator('option[value="302"]')).to_have_count(1)
    backups.select_option("302")
    with page.expect_request("**/api/workspace/github-backups"):
        expect(backups).to_have_value("302")
    assert len(listed) == 1
    name = "Recovered fictional other PC"
    target = live_app.store.root.parent / name
    recovery.get_by_label("New folder name", exact=True).fill(name)
    recovery.get_by_role("button", name="Preview full backup", exact=True).click()
    restore = recovery.get_by_role("button", name="Restore files", exact=True)
    expect(restore).to_be_visible()
    assert selected == [{"library_id": LIBRARY_ID, "asset_id": 302, "name": name}]
    assert not (target / DECK_ID / "index.html").exists()
    expect(recovery.locator(".recovery-preview")).to_contain_text(str(target))
    _ = page.screenshot(path=str(artifacts / "full-github-backup-preview.png"))
    restore.click()
    expect(recovery.get_by_text("Workspace restored", exact=True)).to_be_visible()
    recovered = Store(target, target / ".artifact-studio" / "review.sqlite3")
    assert recovered.read(DECK_ID).slides[0].notes == live_app.store.read(DECK_ID).slides[0].notes
    assert page.url == original_url
    assert writes == []
    recovery.get_by_role("button", name="Close recovery", exact=True).click()
