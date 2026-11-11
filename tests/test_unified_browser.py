"""Check the owner can collaborate from the full editor and return to the same deck."""

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

from artifact_studio.models import SlidePatch
from tests.integration.multiplayer_fixture import (
    WORKSPACE,
    routed_multiplayer_context,
    running_multiplayer_app,
)
from tests.test_multiplayer_server import accept, grant, human
from tests.test_unified_hosting import seed_artifact_history, seed_isolated_presentation

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_shared_artifact_uses_existing_approve_control(tmp_path: Path) -> None:
    """An editor can approve from the native artifact view and a viewer sees the status."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as editor,
        human(app, "bob") as viewer,
        sync_playwright() as playwright,
    ):
        accept(editor, grant(owner, "alice", app.artifact_id))
        accept(viewer, grant(owner, "bob", app.artifact_id, role="view"))
        browser = playwright.chromium.launch()
        try:
            url = WORKSPACE + "/?artifact=" + app.artifact_id
            with routed_multiplayer_context(browser, app, "alice") as context:
                page = context.new_page()
                page.goto(url)
                expect(page.get_by_role("heading", name="Shared candidate")).to_be_visible()
                approve = page.get_by_role("button", name="Approve", exact=True)
                approve.focus()
                page.keyboard.press("Enter")
                expect(page.get_by_role("button", name="Approved", exact=True)).to_be_disabled()
                expect(page.locator(".hub-version .hub-status")).to_have_text("Approved")
            with routed_multiplayer_context(browser, app, "bob") as context:
                page = context.new_page()
                page.goto(WORKSPACE)
                card = page.get_by_role("button", name=re.compile(r"^Open Shared candidate"))
                expect(card).to_have_accessible_description("Approved")
                card.click()
                expect(page.locator(".hub-version .hub-status")).to_have_text("Approved")
                expect(page.locator(".hub-approve")).to_be_hidden()
        finally:
            browser.close()


def test_shared_slide_update_marker_clears_for_the_reader(tmp_path: Path) -> None:
    """Clicking an updated thumbnail clears its native outline only in that account."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        human(app, "bob") as bob,
        sync_playwright() as playwright,
    ):
        for reader, name in ((alice, "alice"), (bob, "bob")):
            accept(reader, grant(owner, name, app.deck_id, role="view"))
        initial = app.store.view(app.deck_id).slides[1]
        _ = app.store.revise_view(
            app.deck_id,
            initial.id,
            initial.expected_hash,
            "fixture-shared-slide-revision",
            SlidePatch(html='<article class="slide"><h1>Revised slide</h1></article>'),
        )
        browser = playwright.chromium.launch()
        try:
            url = f"{WORKSPACE}/?deck={app.deck_id}#cover"
            with routed_multiplayer_context(browser, app, "alice") as context:
                page = context.new_page()
                page.goto(url)
                marker = page.get_by_role("button", name=re.compile(r"^Slide 02 Next"))
                expect(marker).to_have_class(re.compile(r"\bhas-unseen-changes\b"))
                marker.click()
                expect(marker).not_to_have_class(re.compile(r"\bhas-unseen-changes\b"))
                page.reload()
                expect(marker).to_have_attribute("aria-current", "true")
                expect(marker).not_to_have_class(re.compile(r"\bhas-unseen-changes\b"))
            with routed_multiplayer_context(browser, app, "bob") as context:
                page = context.new_page()
                page.goto(url)
                expect(
                    page.get_by_role("button", name=re.compile(r"^Slide 02 Next"))
                ).to_have_class(re.compile(r"\bhas-unseen-changes\b"))
        finally:
            browser.close()


def test_full_editor_round_trip_and_scoped_agent_entry(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app) as owner:
                page = owner.new_page()
                page.goto(WORKSPACE)
                expect(page.get_by_role("heading", name="All artifacts")).to_be_visible()
                page.get_by_role("button", name=re.compile("Open Fictional team deck")).click()
                expect(page.locator("#slide-title")).to_be_visible()
                page.locator("#slides button").filter(has_text="Next").first.click()
                page.get_by_role("button", name="Collaborate").click()
                expect(page.locator("#slide-title")).to_have_text("Next")
                expect(page.locator("#shared-channel")).to_have_value("shared")
                expect(page).to_have_url(re.compile(r"[?&]chat=shared#next"))
                page.get_by_role("button", name="Local review").click()
                expect(page).to_have_url(re.compile(r"[?&]deck=" + app.deck_id + "#next"))
                expect(page.locator("#slide-title")).to_have_text("Next")
                page.get_by_role("button", name="Menu", exact=True).click()
                page.get_by_role("menuitem", name="Agent connection").click()
                expect(page.get_by_role("dialog", name="Agent connection")).to_be_visible()
        finally:
            browser.close()


def test_invited_guest_opens_normal_editor_with_only_shared_items(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        sync_playwright() as playwright,
    ):
        invite = grant(owner, "alice", app.deck_id, "view")
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, "alice") as guest:
                page = guest.new_page()
                page.goto(f"{WORKSPACE}/#invite={invite}")
                page.get_by_role("button", name="Join", exact=True).click()
                expect(page.locator("#slide-title")).to_be_visible()
                preview = page.frame_locator("#preview")
                expect(preview.locator(".slide")).to_be_visible()
                expect(page.get_by_text("Can view", exact=True)).to_be_visible()
                expect(page.locator("#notes")).to_have_js_property("readOnly", True)
                page.locator("#slides button").filter(has_text="Next").first.click()
                expect(page.locator("#slide-title")).to_have_text("Next")
                expect(preview.get_by_text("Next", exact=True)).to_be_visible()

                expect(page.locator("#shared-channel")).to_have_value("shared")
                expect(page.locator("#feedback-text")).to_be_disabled()
                with human(app, "alice") as guest_api:
                    accept(guest_api, grant(owner, "alice", app.deck_id, "edit"))
                expect(page.locator("#feedback-text")).to_be_enabled(timeout=12000)
                expect(page.get_by_text("Can edit", exact=True)).to_be_visible()
                page.get_by_role("button", name="Menu", exact=True).click()
                page.get_by_role("menuitem", name="Agent connection").click()
                connection = page.get_by_role("dialog", name="Agent connection")
                connection.get_by_label("Agent name").fill("Fictional teammate agent")
                connection.get_by_role("button", name="Connect agent").click()
                expect(connection.get_by_role("button", name="Copy token")).to_be_visible()
                connection.get_by_role("button", name="Close agent connection").click()
                expect(page.locator("#slide-title")).to_have_text("Next")
                page.locator("#feedback-text").fill("Fictional teammate comment")
                page.locator("#feedback-text").press("Enter")
                expect(page.locator("#feedback-list")).to_contain_text("Fictional teammate comment")
                expect(page.locator("#feedback-list").get_by_text("Earlier version")).to_have_count(
                    0
                )
                with routed_multiplayer_context(browser, app) as author:
                    same_deck = author.new_page()
                    same_deck.goto(f"{WORKSPACE}/?deck={app.deck_id}#next")
                    expect(same_deck.locator("#feedback-list")).to_contain_text(
                        "Fictional teammate comment"
                    )
                    expect(same_deck.locator("#feedback-list")).to_contain_text(
                        "alice@example.test"
                    )

                page.goto(WORKSPACE)
                expect(page.get_by_role("heading", name="All artifacts")).to_be_visible()
                expect(
                    page.get_by_role("button", name=re.compile("Open Fictional team deck"))
                ).to_be_visible()
                expect(
                    page.get_by_role("button", name=re.compile("Open Private sibling"))
                ).to_have_count(0)
                expect(page.get_by_role("button", name="Import", exact=True)).to_be_hidden()
        finally:
            browser.close()


def test_owner_creates_link_from_native_context_menu(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "alice") as invitee,
        human(app, "bob") as other,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app) as owner:
                page = owner.new_page()
                page.goto(WORKSPACE)
                page.wait_for_function("window.StudioInvitation?.available === true")
                scopes = (
                    (
                        page.get_by_role("button", name=re.compile("Open Shared candidate")),
                        "Shared candidate",
                    ),
                    (
                        page.get_by_role("button", name=re.compile("Open Fictional team deck")),
                        "Fictional team deck",
                    ),
                    (
                        page.locator(f'[data-tree-node="{app.project_id}"] .project-tree-open'),
                        "Fictional project",
                    ),
                )
                for anchor, title in scopes:
                    anchor.click(button="right")
                    page.get_by_role("menuitem", name="Share link…").click()
                    dialog = page.get_by_role("dialog", name="Share link")
                    expect(dialog.get_by_text(title, exact=True)).to_be_visible()
                    expect(dialog.get_by_label("Access")).to_have_value("view")
                    if title == "Shared candidate":
                        dialog.get_by_label("Email address").fill("alice@example.test")
                        dialog.get_by_role("button", name="Create link").click()
                        field = dialog.get_by_label("Invitation link", exact=True)
                        expect(field).to_have_value(
                            re.compile(r"https://studio\.example\.test/#invite=")
                        )
                        expect(dialog.get_by_role("button", name="Copy link")).to_be_focused()
                        token = field.input_value().split("#invite=")[1]
                        wrong = other.post("/api/invitations/accept", json={"token": token})
                        assert wrong.status_code in {403, 404}
                        accept(invitee, token)
                        assert invitee.get(f"/api/artifacts/{app.artifact_id}").is_success
                        assert invitee.get(f"/api/artifacts/{app.sibling_id}").status_code == 404
                    page.keyboard.press("Escape")
                    expect(anchor).to_be_focused()
        finally:
            browser.close()


def test_isolated_workspace_uses_native_creation_sharing_and_editor(tmp_path: Path) -> None:
    """An owner and a guest open the same scoped editor without leaving the native UI."""
    root = tmp_path / "app"
    with (
        running_multiplayer_app(root, tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner_api,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app) as owner:
                page = owner.new_page()
                page.goto(WORKSPACE)
                page.locator("[data-session-mode]").click()
                page.get_by_role("textbox", name="Workspace name").fill("Separate collection")
                page.get_by_role("button", name="New workspace", exact=True).click()
                expect(page).to_have_url(re.compile(r"/workspaces/[0-9a-f]{24}/$"))
                workspace = urlsplit(page.url).path.split("/")[2]
                home = f"{WORKSPACE}/workspaces/{workspace}/"
                seed_isolated_presentation(root, app, workspace)
                page.reload()
                page.get_by_role("button", name="New project", exact=True).click()
                page.get_by_role("textbox", name="Project name", exact=True).fill(
                    "Isolated project"
                )
                page.get_by_role("button", name="Create project", exact=True).click()
                expect(page.get_by_role("heading", name="Isolated project")).to_be_visible()
                page.locator(".project-tree-open").filter(has_text="Isolated project").click(
                    button="right"
                )
                page.get_by_role("menuitem", name="Share link…").click()
                expect(page.get_by_role("dialog", name="Share link")).to_be_visible()
                page.keyboard.press("Escape")
                invite = owner_api.post(
                    f"/api/workspaces/{workspace}/invitations",
                    json={
                        "kind": "artifact",
                        "resource": app.deck_id,
                        "email": "alice@example.test",
                        "role": "edit",
                    },
                ).json()["url"]
                assert invite.startswith(home + "#invite=")
                with routed_multiplayer_context(browser, app, "alice") as guest:
                    scoped = guest.new_page()
                    scoped.goto(invite)
                    scoped.get_by_role("button", name="Join", exact=True).click()
                    expect(scoped).to_have_url(re.compile(re.escape(home) + r"\?deck="))
                    expect(scoped.locator("#slide-title")).to_have_text("Isolated presentation")
                    expect(scoped.frame_locator("#preview").locator(".slide")).to_be_visible()
                    scoped.get_by_role("link", name="Present", exact=True).click()
                    viewer = scoped.frame_locator('iframe[title="Presentation"]')
                    expect(viewer.locator("#stage .slide")).to_be_visible()
                    viewer.get_by_role("button", name="End presentation", exact=True).click()
                    expect(scoped.locator('iframe[title="Presentation"]')).to_have_count(0)
                    scoped.locator("#feedback-text").fill("Conversation for isolated workspace")
                    scoped.locator("#feedback-text").press("Enter")
                    expect(scoped.locator("#feedback-list")).to_contain_text(
                        "Conversation for isolated workspace"
                    )
                    page.goto(home + "?deck=" + app.deck_id)
                    expect(page.locator("#feedback-list")).to_contain_text(
                        "Conversation for isolated workspace"
                    )
                    expect(page.locator("#present-link")).to_have_attribute(
                        "href", re.compile(r"^/workspaces/" + workspace + "/decks/")
                    )
                    page.locator("#feedback-text").fill("Unsent draft")
                    page.locator("[data-session-mode]").click()
                    page.get_by_role("link", name="My workspace").click()
                    switcher = page.get_by_role("region", name="Workspaces")
                    expect(switcher.get_by_role("alert")).to_have_text(
                        "Finish your current draft or edit before switching workspaces."
                    )
                    page.keyboard.press("Escape")
                    expect(page.locator("#feedback-text")).to_have_value("Unsent draft")
                    page.locator("#feedback-text").fill("")
                    page.locator("[data-session-mode]").click()
                    page.get_by_role("link", name="My workspace").click()
                    expect(page).to_have_url(WORKSPACE + "/")
                    expect(
                        page.get_by_role("button", name=re.compile("Open Fictional team deck"))
                    ).to_be_visible()
        finally:
            browser.close()


def test_personal_workspace_sign_in_and_switch_to_shared_project(tmp_path: Path) -> None:
    """A second account gets its own library and joins a shared project in the same UI."""
    with (
        running_multiplayer_app(
            tmp_path / "app",
            tmp_path / "server.log",
            unified=True,
            personal_workspace_emails=("alice@example.test",),
        ) as app,
        human(app, "owner") as primary,
        human(app, "alice") as personal,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, "alice") as alice:
                page = alice.new_page()
                landing = personal.get("/")
                assert landing.status_code == 307
                home = WORKSPACE + landing.headers["location"]
                page.goto(home)
                expect(page.get_by_role("heading", name="All artifacts")).to_be_visible()
                page.get_by_role("button", name="New project", exact=True).click()
                page.get_by_role("textbox", name="Project name").fill("Private draft")
                page.get_by_role("button", name="Create project", exact=True).click()
                expect(page.get_by_role("heading", name="Private draft")).to_be_visible()

                invite = grant(primary, "alice", app.project_id, "edit", "project")
                page.goto(f"{home}#invite={invite}")
                page.get_by_role("button", name="Join", exact=True).click()
                expect(page.get_by_role("heading", name="Fictional project")).to_be_visible()
                expect(page.get_by_role("button", name="New project", exact=True)).to_be_hidden()
                page.locator("[data-session-mode]").click()
                page.get_by_role("link", name="Personal workspace").click()
                expect(page).to_have_url(home)
                page.locator(".project-tree-open").filter(has_text="Private draft").click()
                expect(page.get_by_role("heading", name="Private draft")).to_be_visible()
        finally:
            browser.close()


def test_personal_project_folder_and_artifact_moves(tmp_path: Path) -> None:
    """Reuse the sidebar and card drop controls with a visible access confirmation."""
    with (
        running_multiplayer_app(
            tmp_path / "app",
            tmp_path / "server.log",
            unified=True,
            personal_workspace_emails=("alice@example.test",),
        ) as app,
        human(app, "alice") as owner,
        human(app, "bob") as guest,
        sync_playwright() as playwright,
    ):
        home = owner.get("/").headers["location"]
        workspace = home.split("/")[2]
        source = (
            owner.post(home + "api/hub/projects", json={"title": "Research"})
            .raise_for_status()
            .json()["id"]
        )
        destination = (
            owner.post(home + "api/hub/projects", json={"title": "Delivery"})
            .raise_for_status()
            .json()["id"]
        )
        artifact = (
            owner.post(
                home + "api/artifacts/import",
                json={
                    "project_id": source,
                    "title": "Fictional notes",
                    "files": [{"name": "notes.md", "data_base64": "IyBOb3Rlcwo="}],
                },
            )
            .raise_for_status()
            .json()["id"]
        )
        invite = (
            owner.post(
                f"/api/workspaces/{workspace}/invitations",
                json={
                    "kind": "project",
                    "resource": destination,
                    "email": "bob@example.test",
                    "role": "view",
                },
            )
            .raise_for_status()
            .json()["url"]
        )
        accept(guest, invite.split("#invite=")[1])
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, "alice") as context:
                page = context.new_page()
                page.goto(WORKSPACE + home)
                page.get_by_role("button", name="Add project or folder").click()
                page.get_by_role("menuitem", name="New folder", exact=True).click()
                page.get_by_role("dialog").get_by_label("Name", exact=True).fill("Private folder")
                page.get_by_role("button", name="Create folder", exact=True).click()
                folder = page.locator(".project-tree-row").filter(
                    has=page.get_by_role("button", name="Private folder", exact=True)
                )
                expect(folder).to_be_visible()
                destination_row = page.locator(f'[data-tree-node="{destination}"]')
                destination_row.drag_to(folder)
                expect(
                    page.locator(".project-tree-children").locator(destination_row)
                ).to_be_visible()
                page.reload()
                expect(
                    page.locator(".project-tree-children").locator(destination_row)
                ).to_be_visible()
                expect(destination_row.locator(".project-tree-name")).to_have_css(
                    "white-space", "normal"
                )
                page.locator(f'[data-drag-artifact="{artifact}"]').drag_to(destination_row)
                dialog = page.get_by_role("dialog", name="Move to project")
                expect(dialog).to_be_visible()
                expect(dialog.get_by_role("combobox")).to_have_value(destination)
                expect(dialog.get_by_role("alert")).to_contain_text("may gain access")
                assert guest.get(f"{home}api/artifacts/{artifact}").status_code == 404
                dialog.get_by_role("button", name="Confirm move").click()
                expect(dialog).to_be_hidden()
                expect(
                    page.get_by_role("button", name=re.compile("^Open Fictional notes.*Delivery"))
                ).to_be_visible()
                assert guest.get(f"{home}api/artifacts/{artifact}").is_success
            with routed_multiplayer_context(browser, app, "bob") as context:
                page = context.new_page()
                page.goto(WORKSPACE + home)
                expect(page.get_by_role("button", name="Delivery", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="Private folder", exact=True)).to_have_count(
                    0
                )
                expect(page.get_by_role("button", name="Add project or folder")).to_be_hidden()
                expect(page.locator(f'[data-drag-artifact="{artifact}"]')).to_have_attribute(
                    "draggable", "false"
                )
        finally:
            browser.close()


def test_personal_favorite_slides_open_in_the_same_workspace(tmp_path: Path) -> None:
    """Use the regular star and gallery in a hosted workspace with overlapping deck IDs."""
    root = tmp_path / "app"
    with (
        running_multiplayer_app(
            root,
            tmp_path / "server.log",
            unified=True,
            personal_workspace_emails=("alice@example.test",),
        ) as app,
        human(app, "alice") as personal,
        sync_playwright() as playwright,
    ):
        home = personal.get("/").headers["location"]
        seed_isolated_presentation(root, app, home.split("/")[2])
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, "alice") as context:
                page = context.new_page()
                page.goto(f"{WORKSPACE}{home}?deck={app.deck_id}#cover")
                star = page.locator('[data-star-kind="slide"][data-star-slide="cover"]')
                star.focus()
                star.press("Enter")
                expect(star).to_have_attribute("aria-pressed", "true")
                page.goto(WORKSPACE + home)
                page.get_by_role("button", name="Favorite slides", exact=True).click()
                gallery = page.get_by_role("dialog", name="Favorite slides")
                expect(gallery.get_by_text("Isolated presentation · 01")).to_be_visible()
                expect(
                    gallery.frame_locator(".favorite-cover iframe").locator(".slide")
                ).to_be_visible()
                gallery.get_by_role("button", name=re.compile("^Add description for")).click()
                description = page.get_by_role("dialog", name="Slide description")
                description.get_by_role("textbox").fill("A useful opener")
                description.get_by_role("button", name="Save", exact=True).click()
                expect(gallery.get_by_text("A useful opener")).to_be_visible()
                gallery.locator(".favorite-pick").click()
                expect(page).to_have_url(f"{WORKSPACE}{home}?deck={app.deck_id}#cover")
                expect(star).to_have_attribute("aria-pressed", "true")
        finally:
            browser.close()


def test_personal_artifact_timeline_reuses_native_preview_and_restore(tmp_path: Path) -> None:
    """Open the native timeline, inspect the pinned old assets and restore a new version."""
    root = tmp_path / "app"
    with (
        running_multiplayer_app(
            root,
            tmp_path / "server.log",
            unified=True,
            personal_workspace_emails=("alice@example.test",),
        ) as app,
        human(app, "alice") as owner,
        sync_playwright() as playwright,
    ):
        home = owner.get("/").headers["location"]
        artifact = seed_artifact_history(root, app, home.split("/")[2])
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, "alice") as context:
                page = context.new_page()
                page.goto(f"{WORKSPACE}{home}?artifact={artifact}")
                page.get_by_role("button", name="Version history", exact=True).click()
                timeline = page.get_by_role("dialog", name="Version history")
                preview = timeline.frame_locator("iframe")
                expect(preview.get_by_role("heading", name="Current draft")).to_be_visible()
                timeline.get_by_text("1 earlier version").click()
                timeline.locator('[data-version="artifact:1"]').click()
                heading = preview.get_by_role("heading", name="Original draft")
                expect(heading).to_have_css("color", "rgb(93, 24, 117)")
                expect(timeline.locator('[data-version="artifact:1"] img')).to_have_js_property(
                    "naturalWidth", 400
                )
                timeline.get_by_role("button", name="Restore version", exact=True).click()
                expect(timeline.get_by_text("Anyone with access", exact=False)).to_be_visible()
                timeline.get_by_role("button", name="Restore", exact=True).click()
                expect(timeline).to_be_hidden()
                expect(
                    page.frame_locator("#hub-html-preview").get_by_role(
                        "heading", name="Original draft"
                    )
                ).to_be_visible()
        finally:
            browser.close()
