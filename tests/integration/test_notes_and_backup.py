"""Real note persistence and recovery; backup UI states stay separate from Git transport tests."""

import os
from pathlib import Path
from urllib.error import HTTPError
from zipfile import ZipFile

import pytest
from playwright.sync_api import Browser, Page, Route, expect

from artifact_studio.agent_client import object_value
from artifact_studio.libraries import Libraries, Library
from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_browser import open_editor

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_notes_save_on_pause_switch_and_reload(page: Page, live_app: RunningApp) -> None:
    """Persist notes in canonical files while preserving slide context and approval."""
    open_editor(page, live_app)
    page.get_by_role("button", name="Approve", exact=True).click()
    page.get_by_label("Speaker notes", exact=True).fill("Saved without a button")
    expect(page.locator("#notes-state")).to_have_text("Saved")
    assert live_app.store.read(DECK_ID).slides[0].notes == "Saved without a button"
    assert live_app.store.view(DECK_ID).slides[0].approved
    page.get_by_label("Speaker notes", exact=True).fill("First slide's final notes")
    page.locator('.slide-thumb[data-id="next"]').click()
    page.get_by_label("Speaker notes", exact=True).fill("Second slide's notes")
    page.locator('.slide-thumb[data-id="cover"]').click()
    expect(page.get_by_label("Speaker notes", exact=True)).to_have_value(
        "First slide's final notes"
    )
    expect(page.locator("#storage-status")).to_have_attribute("data-phase", "local")
    saved = live_app.store.read(DECK_ID)
    assert saved.slides[0].notes == "First slide's final notes"
    assert saved.slides[1].notes == "Second slide's notes"
    _ = page.reload(wait_until="networkidle")
    expect(page.get_by_label("Speaker notes", exact=True)).to_have_value(
        "First slide's final notes"
    )
    # This exceeds the browser's keepalive byte budget but remains a valid note.
    page.get_by_label("Speaker notes", exact=True).fill("界" * 20_000)
    expect(page.locator("#notes-state")).to_have_text("Saved")
    assert live_app.store.read(DECK_ID).slides[0].notes == "界" * 20_000


def test_immediate_project_save_includes_the_latest_notes(page: Page, live_app: RunningApp) -> None:
    """A handoff archive waits for note persistence instead of racing the debounce timer."""
    open_editor(page, live_app)
    page.get_by_label("Speaker notes", exact=True).fill("Carry these notes to the next workspace")
    with page.expect_download() as downloaded:
        page.keyboard.press("ControlOrMeta+s")
    with ZipFile(downloaded.value.path()) as archive:
        sources = [name for name in archive.namelist() if name.endswith("index.html")]
        assert sources
        assert any(
            b"Carry these notes to the next workspace" in archive.read(name) for name in sources
        )
    assert live_app.store.read(DECK_ID).slides[0].notes == "Carry these notes to the next workspace"
    page.get_by_role("button", name="Menu", exact=True).click()
    page.get_by_role("menuitem", name="Open project", exact=True).click()
    page.locator("#project-file").set_input_files(downloaded.value.path())
    expect(page.locator("#project-dialog")).not_to_be_visible()
    expect(page.get_by_label("Speaker notes", exact=True)).to_have_value(
        "Carry these notes to the next workspace"
    )
    expect(page.locator("#notes-state")).to_have_text("Saved")


def test_deleted_slide_recovery_carries_its_latest_autosaved_notes(
    page: Page, live_app: RunningApp
) -> None:
    """An immediate delete waits for notes so Undo restores the latest text."""
    # Exercise the polling fallback so push cannot refresh a stale source token for us.
    page.route(
        "**/events",
        lambda route: route.fulfill(content_type="text/event-stream", body=": reconnect later\n\n"),
    )
    open_editor(page, live_app)
    page.get_by_label("Speaker notes", exact=True).fill("Keep these notes with the deleted slide")
    page.locator('.slide-thumb[data-id="cover"]').click(button="right")
    page.get_by_role("menuitem", name="Delete slide", exact=True).click()
    expect(page.locator('.slide-thumb[data-id="cover"]')).to_have_count(0)
    page.get_by_role("button", name="Undo", exact=True).click()
    expect(page.locator('.slide-thumb[data-id="cover"]')).to_be_visible()
    page.locator('.slide-thumb[data-id="cover"]').click()
    expect(page.get_by_label("Speaker notes", exact=True)).to_have_value(
        "Keep these notes with the deleted slide"
    )


@pytest.mark.parametrize("choice,width", [("mine", 1440), ("saved", 1440), ("changed-again", 390)])
def test_conflicting_notes_require_a_reviewed_choice(
    browser: Browser, live_app: RunningApp, artifacts: Path, choice: str, width: int
) -> None:
    """Use actual HTTP conflicts and keep both texts visible until the human chooses."""
    context = browser.new_context(viewport={"width": width, "height": 1000 if width > 600 else 844})
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    collided = False

    def competing_editor(route: Route) -> None:
        nonlocal collided
        if not collided:
            collided = True
            slide = live_app.store.read(DECK_ID).slides[0]
            _ = live_app.store.save_notes(
                DECK_ID, "cover", "Saved by another editor", slide.notes, slide.notes_revision
            )
        route.continue_()

    try:
        page.route("**/slides/cover/notes", competing_editor)
        open_editor(page, live_app)
        page.get_by_label("Speaker notes", exact=True).fill("My private draft")
        expect(page.locator("#notes-state")).to_have_text("Needs review")
        assert live_app.store.read(DECK_ID).slides[0].notes == "Saved by another editor"
        page.get_by_role("button", name="Review changes", exact=True).click()
        expect(page.locator("#notes-conflict-draft")).to_have_value("My private draft")
        expect(page.locator("#notes-conflict-saved")).to_have_value("Saved by another editor")
        assert page.locator("#notes-conflict-dialog").evaluate(
            "dialog => dialog.scrollWidth <= dialog.clientWidth"
        )
        _ = page.screenshot(path=str(artifacts / "notes-conflict.png"))
        if choice == "changed-again":
            current = live_app.store.read(DECK_ID).slides[0]
            _ = live_app.store.save_notes(
                DECK_ID, "cover", "A newer saved note", current.notes, current.notes_revision
            )
            page.get_by_role("button", name="Keep my notes", exact=True).click()
            expect(page.locator("#notes-conflict-error")).to_contain_text("latest saved notes")
            assert live_app.store.read(DECK_ID).slides[0].notes == "A newer saved note"
            page.locator("#close-notes-conflict").click()
            page.get_by_role("button", name="Review changes", exact=True).click()
            expect(page.locator("#notes-conflict-saved")).to_have_value("A newer saved note")
        page.get_by_role(
            "button", name="Use saved notes" if choice == "saved" else "Keep my notes", exact=True
        ).click()
        expect(page.locator("#notes-conflict-dialog")).not_to_be_visible()
        expected = "Saved by another editor" if choice == "saved" else "My private draft"
        expect(page.get_by_label("Speaker notes", exact=True)).to_have_value(expected)
        expect(page.locator("#notes-state")).to_have_text("Saved")
        assert live_app.store.read(DECK_ID).slides[0].notes == expected
    finally:
        context.close()
    assert not errors


def test_failed_save_survives_reload_and_retries(
    browser: Browser, live_app: RunningApp, artifacts: Path
) -> None:
    """Keep failed text in browser storage, then save it after a new page reconnects."""
    context = browser.new_context()
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("dialog", lambda dialog: dialog.accept())
    try:
        page.route("**/slides/cover/notes", lambda route: route.abort("connectionfailed"))
        open_editor(page, live_app)
        original = live_app.store.read(DECK_ID).slides[0].notes
        page.get_by_label("Speaker notes", exact=True).fill(
            "Recover this after a connection failure"
        )
        expect(page.locator("#notes-state")).to_have_text("Not saved")
        expect(page.get_by_role("button", name="Retry saving notes")).to_be_visible()
        assert live_app.store.read(DECK_ID).slides[0].notes == original
        _ = page.screenshot(path=str(artifacts / "notes-retry.png"))
        _ = page.reload(wait_until="networkidle")
        expect(page.get_by_label("Speaker notes", exact=True)).to_have_value(
            "Recover this after a connection failure"
        )
        expect(page.locator("#notes-state")).to_have_text("Not saved")
        page.unroute("**/slides/cover/notes")
        page.get_by_role("button", name="Retry saving notes").click()
        expect(page.locator("#notes-state")).to_have_text("Saved")
        assert (
            live_app.store.read(DECK_ID).slides[0].notes
            == "Recover this after a connection failure"
        )
    finally:
        context.close()
    assert not errors


def test_reload_recovers_other_slides_without_visiting_them(
    browser: Browser, live_app: RunningApp
) -> None:
    """A recovered workspace resumes every note draft, including inactive slides."""
    context = browser.new_context()
    page = context.new_page()
    page.on("dialog", lambda dialog: dialog.accept())
    try:
        page.route("**/slides/next/notes", lambda route: route.abort("connectionfailed"))
        open_editor(page, live_app)
        page.locator('.slide-thumb[data-id="next"]').click()
        page.get_by_label("Speaker notes", exact=True).fill("Recover this inactive slide's notes")
        expect(page.locator("#notes-state")).to_have_text("Not saved")
        page.locator('.slide-thumb[data-id="cover"]').click()
        page.unroute("**/slides/next/notes")
        _ = page.reload(wait_until="networkidle")
        expect(page.locator("#slide-title")).to_have_text("CI workflow")
        expect(page.locator("#storage-status")).to_have_attribute("data-phase", "local")
        assert live_app.store.read(DECK_ID).slides[1].notes == "Recover this inactive slide's notes"
    finally:
        context.close()


def test_reload_after_reverting_fences_the_previous_pending_write(
    browser: Browser, live_app: RunningApp
) -> None:
    """Replay a captured older request after recovery; it must not replace the reversal."""
    context = browser.new_context()
    page = context.new_page()
    captured: list[dict[str, object]] = []

    def hold_first_write(route: Route) -> None:
        if not captured:
            captured.append(object_value(route.request.post_data_json))
        else:
            route.continue_()

    try:
        page.route("**/slides/cover/notes", hold_first_write)
        open_editor(page, live_app)
        original = live_app.store.read(DECK_ID).slides[0]
        with page.expect_request("**/slides/cover/notes"):
            page.get_by_label("Speaker notes", exact=True).fill("An older request still in flight")
        page.get_by_label("Speaker notes", exact=True).fill(original.notes)
        _ = page.reload(wait_until="domcontentloaded")
        expect(page.get_by_label("Speaker notes", exact=True)).to_have_value(original.notes)
        expect(page.locator("#notes-state")).to_have_text("Saved")
        assert live_app.store.read(DECK_ID).slides[0].notes_revision > original.notes_revision
        with pytest.raises(HTTPError) as stale:
            _ = live_app.post(f"/api/decks/{DECK_ID}/slides/cover/notes", captured[0])
        assert stale.value.code == 409
        assert live_app.store.read(DECK_ID).slides[0].notes == original.notes
    finally:
        context.close()


def test_present_flushes_editor_notes_before_loading_the_private_view(
    page: Page, live_app: RunningApp
) -> None:
    """The Present click keeps fullscreen activation and carries the newest notes."""
    open_editor(page, live_app)
    page.get_by_label("Speaker notes", exact=True).fill("Notes ready for this presentation")
    page.locator("#present-link").click()
    viewer = page.frame_locator('iframe[title="Presentation"]')
    viewer.locator("#show-presenter").click()
    expect(viewer.get_by_label("Speaker notes", exact=True)).to_have_value(
        "Notes ready for this presentation"
    )
    assert live_app.store.read(DECK_ID).slides[0].notes == "Notes ready for this presentation"


def test_failed_presenter_notes_return_to_the_editor(
    browser: Browser, live_app: RunningApp
) -> None:
    """Ending a show surfaces its failed draft beside the editor's retry control."""
    context = browser.new_context()
    page = context.new_page()
    try:
        open_editor(page, live_app)
        page.locator("#present-link").click()
        viewer = page.frame_locator('iframe[title="Presentation"]')
        viewer.locator("#show-presenter").click()
        page.route("**/slides/cover/notes", lambda route: route.abort("connectionfailed"))
        viewer.get_by_label("Speaker notes", exact=True).fill(
            "Keep this interrupted presenter draft"
        )
        expect(viewer.locator("#presenter-note-status")).to_contain_text("Not saved")
        viewer.locator("#end-presentation").click()
        expect(page.locator('iframe[title="Presentation"]')).not_to_be_visible()
        expect(page.get_by_label("Speaker notes", exact=True)).to_have_value(
            "Keep this interrupted presenter draft"
        )
        expect(page.locator("#notes-state")).to_have_text("Not saved")
        page.unroute("**/slides/cover/notes")
        page.get_by_role("button", name="Retry saving notes").click()
        expect(page.locator("#notes-state")).to_have_text("Saved")
        assert (
            live_app.store.read(DECK_ID).slides[0].notes == "Keep this interrupted presenter draft"
        )
    finally:
        context.close()


def test_local_storage_controls_and_live_backup_status(
    page: Page, live_app: RunningApp, artifacts: Path
) -> None:
    """Keep backup failures visible outside the dialog; UI fixtures never upload real files."""
    collection = Libraries(live_app.store).list()[0].model_dump()
    backup = Library(
        id="backup-ui-fixture",
        kind="github",
        location="fictional-slides/demo",
        path="/tmp/fictional-reference-cache",
        sync_enabled=True,
        auto_sync_enabled=True,
    ).model_dump()
    state = {
        "library_id": "backup-ui-fixture",
        "phase": "error",
        "error": "Sign in to GitHub, then retry.",
        "last_synced_at": "",
    }
    posts: list[str] = []

    def retry(route: Route) -> None:
        posts.append("retry")
        state.update(phase="pending", error="")
        route.fulfill(json=state)

    def settings(route: Route) -> None:
        assert route.request.post_data_json == {"auto_sync_enabled": False}
        posts.append("disable")
        backup.update(auto_sync_enabled=False, sync_enabled=False)
        route.fulfill(json=backup)

    page.add_init_script(
        "Object.defineProperty(navigator,'clipboard',{value:{writeText:async value=>{"
        "window.copiedWorkingFolder=value;}}});"
    )
    page.route("**/api/libraries", lambda route: route.fulfill(json=[collection, backup]))
    page.route("**/api/library-sync", lambda route: route.fulfill(json=[state]))
    page.route("**/api/libraries/backup-ui-fixture/retry", retry)
    page.route("**/api/libraries/backup-ui-fixture/settings", settings)
    open_editor(page, live_app)
    expect(page.locator("#storage-status")).to_have_attribute("data-phase", "error")
    page.locator("#storage-status").click()
    expect(page.get_by_role("dialog", name="Storage and backup")).to_be_visible()
    expect(page.locator("#library-list")).to_contain_text("On this device")
    expect(page.locator("#library-list")).not_to_contain_text("This Mac")
    page.get_by_role("button", name="Copy working folder path").click()
    assert page.evaluate("window.copiedWorkingFolder") == str(live_app.store.root)
    page.get_by_role("button", name="Retry GitHub backup").click()
    expect(page.locator("#storage-status")).to_have_attribute("data-phase", "pending")
    page.locator("#close-libraries").click()
    state.update(phase="synced", last_synced_at="2026-11-02T10:30:00+01:00")
    expect(page.locator("#storage-status")).to_have_attribute("data-phase", "synced")
    page.locator("#storage-status").click()
    expect(page.locator("#library-list time")).to_have_attribute(
        "datetime", "2026-11-02T10:30:00+01:00"
    )
    _ = page.screenshot(path=str(artifacts / "backup-status-ui-fixture.png"))
    page.get_by_role("checkbox", name="Back up all presentations", exact=True).uncheck()
    expect(page.locator("#storage-status")).to_have_attribute("data-phase", "local")
    expect(page.locator("#library-list")).to_contain_text("GitHub reference · read-only")
    assert posts == ["retry", "disable"]


def test_local_reference_folder_stays_read_only(
    page: Page, live_app: RunningApp, tmp_path: Path
) -> None:
    """Connect a real local folder and read later edits without copying workspace files into it."""
    reference = tmp_path / "reference-folder"
    reference.mkdir()
    source = reference / "story.md"
    _ = source.write_text("An original fictional reference.")
    open_editor(page, live_app)
    page.locator("#storage-status").click()
    page.locator("#library-location").fill(str(reference))
    expect(page.locator("#library-sync-setting")).to_be_hidden()
    page.get_by_role("button", name="Connect library", exact=True).click()
    expect(page.locator("#library-list")).to_contain_text("Local reference · read-only")
    libraries = Libraries(live_app.store)
    connected = next(item for item in libraries.list() if item.location == str(reference))
    assert not connected.sync_enabled and not connected.auto_sync_enabled
    _ = source.write_text("A later edit made outside Artifact Studio.")
    assert libraries.files(connected.id)[0].local_path == str(source)
    assert source.read_text() == "A later edit made outside Artifact Studio."
    assert sorted(path.name for path in reference.iterdir()) == ["story.md"]


def test_presenter_notes_autosave_with_the_same_revision_guard(
    page: Page, live_app: RunningApp
) -> None:
    """Private presenter notes use the actual server and stay with their original slide."""
    _ = page.goto(live_app.url + f"/decks/{DECK_ID}/index.html?present=1#cover")
    page.locator("#show-presenter").click()
    page.get_by_label("Speaker notes", exact=True).fill("Automatically saved presenter notes")
    expect(page.locator("#presenter-note-status")).to_have_text("Saved")
    page.locator("#presenter-next-button").click()
    page.get_by_label("Speaker notes", exact=True).fill("Next slide's private notes")
    expect(page.locator("#presenter-note-status")).to_have_text("Saved")
    saved = live_app.store.read(DECK_ID)
    assert saved.slides[0].notes == "Automatically saved presenter notes"
    assert saved.slides[1].notes == "Next slide's private notes"
