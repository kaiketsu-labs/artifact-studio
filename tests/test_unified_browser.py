"""Check the owner can collaborate from the full editor and return to the same deck."""

import os
import re
import sqlite3
import time
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import pytest
from PIL import ImageFont
from playwright.sync_api import Route, expect, sync_playwright

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.models import SlidePatch
from artifact_studio.storage import Store
from tests.integration.multiplayer_fixture import (
    WORKSPACE,
    routed_multiplayer_context,
    running_multiplayer_app,
)
from tests.test_multiplayer_server import accept, agent, grant, human
from tests.test_multiplayer_simulation import Peer
from tests.test_unified_hosting import seed_artifact_history, seed_isolated_presentation

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


@pytest.mark.parametrize("presentation", [True, False], ids=["presentation", "artifact"])
def test_editor_requests_show_readable_changes_and_keep_exact_agent_payload(
    tmp_path: Path, presentation: bool
) -> None:
    """Show useful text and copy it while the agent receives its original edit envelope."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        sync_playwright() as playwright,
    ):
        if presentation:
            resource = app.deck_id
            url = f"{WORKSPACE}/?deck={resource}#cover"
        else:
            registry = ArtifactStore(app.store)
            project = registry.get_artifact(app.artifact_id).project_id
            source = tmp_path / "page.html"
            source.write_text('<h1 id="title">Fictional editable heading</h1>')
            item = registry.register_artifact(project, "Fictional edit", source, "page.html")
            resource = item.id
            url = f"{WORKSPACE}/?artifact={resource}"
            owner.get("/api/hub").raise_for_status()
        state_url = f"/api/workspaces/default/artifacts/{resource}/state"
        state = owner.get(state_url)
        state.raise_for_status()
        original = str(state.json()["content_hash"])
        peer = Peer(app, agent(owner, resource))
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app) as context:
                context.grant_permissions(["clipboard-read", "clipboard-write"], origin=WORKSPACE)
                page = context.new_page()
                page.goto(url)
                frame_id = "#preview" if presentation else "#hub-html-preview"
                panel_id = "#element-properties" if presentation else "#hub-element-properties"
                thread = page.locator("#feedback-list" if presentation else "#hub-thread")
                if not presentation:
                    page.get_by_role("button", name="Select", exact=True).click()
                page.frame_locator(frame_id).get_by_role("heading", level=1).click()
                panel = page.locator(panel_id)
                panel.get_by_role("button", name="Edit text", exact=True).click()
                replacement = "A clearer story <script>window.editPayloadExecuted=true</script>"
                panel.get_by_role("textbox", name="Text", exact=True).fill(replacement)
                panel.get_by_role("button", name="Send edit", exact=True).click()
                expect(thread).to_contain_text("Text: " + replacement)
                assert "Edit details:" not in thread.inner_text()
                assert "replace_text" not in thread.inner_text()
                assert page.evaluate("window.editPayloadExecuted") is None
                events = cast(
                    list[dict[str, object]],
                    peer.value("read_feedback", artifact_id=resource)["events"],
                )
                assert len(events) == 1
                wire = str(events[0]["message"])
                assert '\n\nEdit details:\n{"action":"replace_text","values":' in wire
                assert replacement in wire
                assert str(events[0]["content_hash"]) == original
                thread.get_by_text("Exact request", exact=True).click()
                expect(thread.locator("pre")).to_have_text(wire)
                assert page.evaluate("window.editPayloadExecuted") is None
                thread.get_by_text("Exact request", exact=True).click()
                page.get_by_role("button", name="Copy conversation", exact=True).click()
                copied = str(page.evaluate("navigator.clipboard.readText()"))
                assert replacement in copied
                assert "Edit details:" not in copied and '"values"' not in copied
                page.reload()
                expect(thread).to_contain_text("Text: " + replacement)
                assert "Edit details:" not in thread.inner_text()
                current = str(owner.get(state_url).json()["content_hash"])
                assert current == original
        finally:
            browser.close()
            peer.client.close()


def test_structure_review_preserves_a_concurrent_edit_and_reaches_origin_agent(
    tmp_path: Path,
) -> None:
    """Two fictional editors share a structural proposal while one reorders the deck."""
    with (
        running_multiplayer_app(
            tmp_path / "app", tmp_path / "server.log", unified=True, startup_timeout=30
        ) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        sync_playwright() as playwright,
    ):
        accept(alice, grant(owner, "alice", app.deck_id))
        peer = Peer(app, agent(owner, app.deck_id))
        unrelated = Peer(app, agent(alice, app.deck_id))
        outline = peer.value("get_presentation_outline", deck_id=app.deck_id)
        _ = peer.value(
            "publish_structure_proposal",
            deck_id=app.deck_id,
            summary="Fictional proposed outline",
            base_slide_ids=["cover", "next"],
            base_sections_hash=outline["sections_hash"],
            proposed_slide_ids=["next", "missing"],
            proposed_titles={"missing": "Fictional additional slide"},
            request_id="fictional-outline-proposal",
        )
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app) as context:
                page = context.new_page()
                page.set_viewport_size({"width": 850, "height": 720})
                page.emulate_media(color_scheme="dark")
                page.goto(f"{WORKSPACE}/?deck={app.deck_id}#cover")
                page.get_by_role("button", name="Open chat", exact=True).click()
                page.locator("#structure-review-trigger").click()
                dialog = page.get_by_role("dialog", name="Review slide structure")
                expect(
                    dialog.locator("li").filter(has_text="Fictional additional slide")
                ).to_be_visible()
                dialog.get_by_role("radio", name="Accept proposal").check()
                message = dialog.get_by_role("textbox", name="Message to the proposing agent")
                message.fill("Keep the recent edit and review the extra slide.")
                response = alice.post(
                    f"/api/decks/{app.deck_id}/slide-order",
                    json={"slide_ids": ["next", "cover"], "expected_slide_ids": ["cover", "next"]},
                )
                response.raise_for_status()
                dialog.get_by_role("button", name="Send decision").click()
                expect(dialog.get_by_role("alert").filter(has_text="changed")).to_be_visible()
                expect(message).to_have_value("Keep the recent edit and review the extra slide.")
                expect(dialog.get_by_role("radio", name="Accept proposal")).to_be_disabled()
                dialog.get_by_role("radio", name="Request changes").check()
                dialog.get_by_role("button", name="Send decision").click()
                expect(dialog.get_by_text(re.compile("Decision: Request changes"))).to_be_visible()
                mine = peer.value("read_feedback", deck_id=app.deck_id)
                other = unrelated.value("read_feedback", deck_id=app.deck_id)
                assert "Keep the recent edit" in str(mine)
                assert "Keep the recent edit" not in str(other)
                assert [item.id for item in app.store.read(app.deck_id).slides] == ["next", "cover"]
        finally:
            unrelated.client.close()
            peer.client.close()
            browser.close()


def test_owner_can_toggle_full_shared_history_for_one_agent(tmp_path: Path) -> None:
    """Exercise the presentation permission through the owner's agent dialog."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        sync_playwright() as playwright,
    ):
        response = owner.post(
            "/api/workspaces/default/agents",
            json={"kind": "workspace", "name": "Fictional workspace agent"},
        )
        response.raise_for_status()
        agent_id = response.json()["id"]
        setting = (
            f"/api/workspaces/default/artifacts/{app.deck_id}/agents/{agent_id}/shared-history"
        )
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, "owner") as context:
                page = context.new_page()
                page.set_viewport_size({"width": 390, "height": 844})
                page.goto(f"{WORKSPACE}/?deck={app.deck_id}#cover")
                page.locator("#workspace-menu-button").click()
                page.locator("#connect-button").click()
                dialog = page.get_by_role("dialog", name="Agent connection")
                permission = dialog.get_by_role(
                    "checkbox", name=re.compile("Full Shared history", re.I)
                )
                expect(permission).not_to_be_checked()
                with page.expect_response(lambda result: result.url.endswith(setting)):
                    permission.check()
                assert owner.get(setting).json() == {"enabled": True}
                with page.expect_response(lambda result: result.url.endswith(setting)):
                    permission.uncheck()
                assert owner.get(setting).json() == {"enabled": False}
        finally:
            browser.close()


def test_library_poll_preserves_open_context_menu(tmp_path: Path) -> None:
    """Keep the menu anchor and keyboard focus until a pending library update can render."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app) as context:
                page = context.new_page()
                page.goto(WORKSPACE)
                card = page.get_by_role("button", name=re.compile("^Open Shared candidate"))
                expect(card).to_be_visible()
                anchor = card.element_handle()
                assert anchor is not None
                card.click(button="right")
                menu = page.get_by_role("menu", name="Artifact actions")
                expect(menu).to_be_visible()
                ArtifactStore(app.store).create_project("Fictional poll update")
                with page.expect_response(
                    lambda response: response.url.endswith("/api/hub"), timeout=20000
                ):
                    pass
                page.wait_for_timeout(500)
                assert anchor.evaluate("element => element.isConnected")
                expect(menu).to_be_visible()
                expect(page.get_by_text("Fictional poll update")).to_have_count(0)
                page.keyboard.press("Escape")
                expect(menu).to_be_hidden()
                expect(page.get_by_text("Fictional poll update")).to_be_visible()
                expect(card).to_be_focused()
        finally:
            browser.close()


def test_presence_stays_visible_through_a_failed_poll(tmp_path: Path) -> None:
    """Keep several active collaborators visible on mobile through a brief network failure."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        sync_playwright() as playwright,
    ):
        endpoint = f"/api/workspaces/default/artifacts/{app.deck_id}/presence"
        for index, name in enumerate(("alice", "bob", "carol", "dana")):
            with human(app, name) as person:
                if name != "dana":
                    accept(person, grant(owner, name, app.deck_id, role="view"))
                reply = person.post(endpoint, json={"tab": str(index) * 32, "slide_id": "cover"})
                assert reply.status_code == (404 if name == "dana" else 200)
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app) as context:
                page = context.new_page()
                page.set_viewport_size({"width": 390, "height": 844})
                page.goto(f"{WORKSPACE}/?deck={app.deck_id}#cover")
                button = page.get_by_role("button", name="People in this deck (3)")
                expect(button.get_by_text("+2", exact=True)).to_be_visible()
                expect(page.locator(".thumb-preview .slide-presence-markers")).to_contain_text("+1")
                button.click()
                people = page.locator("#deck-viewers")
                expect(people).to_contain_text("Alice")
                expect(people).to_contain_text("Bob")
                expect(people).to_contain_text("Carol")
                page.route("**" + endpoint, lambda route: route.fulfill(status=503))
                with page.expect_response(lambda response: response.status == 503) as failed:
                    _ = page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                assert failed.value.url.endswith(endpoint)
                expect(button).to_be_visible()
                expect(people).to_be_visible()
                page.keyboard.press("Escape")
                expect(people).to_be_hidden()
                expect(button).to_be_focused()
                button.click()
                people.get_by_role("button", name="Alice Slide 1").click()
                expect(people).to_be_hidden()
                expect(button).to_be_focused()
                with human(app, "dana") as outsider:
                    assert outsider.get(f"/api/decks/{app.deck_id}").status_code == 404
        finally:
            browser.close()


def test_presence_shows_only_agents_listening_on_this_deck(tmp_path: Path) -> None:
    """Show a collaborator's listener without exposing unrelated activity or routing to it."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        sync_playwright() as playwright,
    ):
        scope = {"kind": "artifact", "resource": app.deck_id}
        accept(alice, grant(owner, "alice", app.deck_id, role="edit"))
        peer = Peer(app, agent(alice, app.deck_id))
        hook = {"harness": "codex", "session_id": "fictional-agent", "deck_id": app.deck_id}
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app) as context:
                page = context.new_page()
                page.set_viewport_size({"width": 390, "height": 844})
                page.goto(f"{WORKSPACE}/?deck={app.deck_id}#cover")
                expect(page.locator("#feedback-text")).to_be_enabled()
                presence = page.locator("#deck-presence")
                expect(presence).to_be_hidden()
                response = peer.client.post(
                    "/api/review/hooks", json={**hook, "event": "PostToolUse", "start_review": True}
                )
                assert response.json()["listening"] is True
                _ = page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                expect(presence).to_have_accessible_name("1 agent listening")
                presence.click()
                roster = page.locator("#deck-viewers")
                expect(roster).to_contain_text("Fictional agent")
                expect(roster).to_contain_text("Listening")
                expect(roster).not_to_contain_text("@example.test")
                expect(page.locator("#chat-ask-agent")).to_be_hidden()
                grants = owner.get("/api/workspaces/default/access", params=scope).json()["grants"]
                response = owner.post(
                    f"/api/workspaces/default/access/{grants[0]['id']}/revoke", json=scope
                )
                assert response.is_success
                accept(alice, grant(owner, "alice", app.deck_id, role="view"))
                _ = page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                expect(presence).to_be_hidden()
                expect(roster).to_be_hidden()
        finally:
            browser.close()
            peer.client.close()


def test_guest_discussion_sends_without_waiting_for_refresh(tmp_path: Path) -> None:
    """A saved message shows and unlocks the composer while refresh requests are pending."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        sync_playwright() as playwright,
    ):
        accept(alice, grant(owner, "alice", app.deck_id, role="comment"))
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, "alice") as context:
                page = context.new_page()
                page.goto(f"{WORKSPACE}/?deck={app.deck_id}#cover")
                composer = page.locator("#feedback-text")
                expect(composer).to_have_attribute("placeholder", "Message collaborators…")
                composer.fill("Fictional guest discussion")
                held: list[Route] = []

                def hold_refresh(route: Route) -> None:
                    if route.request.method == "GET":
                        held.append(route)
                    else:
                        route.fallback()

                page.route(f"**/api/decks/{app.deck_id}", hold_refresh)
                page.route(f"**/artifacts/{app.deck_id}/state", hold_refresh)
                initial = app.store.view(app.deck_id).slides[0]
                _ = app.store.revise_view(
                    app.deck_id,
                    initial.id,
                    initial.expected_hash,
                    "fixture-discussion-revision",
                    SlidePatch(title="Revised cover"),
                )
                with page.expect_request(f"**/api/decks/{app.deck_id}"):
                    _ = page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                composer.press("Enter")
                expect(page.locator("#feedback-list")).to_contain_text("Fictional guest discussion")
                expect(composer).to_have_value("")
                expect(composer).to_be_enabled()
                composer.fill("Second message while refresh is pending")
                composer.press("Enter")
                expect(page.locator("#feedback-list")).to_contain_text(
                    "Second message while refresh is pending"
                )
                response = owner.get(f"/api/workspaces/default/artifacts/{app.deck_id}/chat")
                assert [message["message"] for message in response.json()] == [
                    "Fictional guest discussion",
                    "Second message while refresh is pending",
                ]
                for route in held:
                    route.abort()
        finally:
            browser.close()


def test_unchanged_shared_deck_still_refreshes_discussion(tmp_path: Path) -> None:
    """Use an empty snapshot response without dropping a guest draft or incoming chat."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        sync_playwright() as playwright,
    ):
        accept(alice, grant(owner, "alice", app.deck_id, role="comment"))
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, "alice") as context:
                page = context.new_page()
                page.set_viewport_size({"width": 900, "height": 760})
                page.goto(f"{WORKSPACE}/?deck={app.deck_id}#cover")
                page.get_by_role("button", name="Open chat", exact=True).click()
                composer = page.locator("#feedback-text")
                composer.fill("Fictional unsent draft")
                owner.post(
                    f"/api/workspaces/default/artifacts/{app.deck_id}/chat",
                    json={
                        "channel": "shared",
                        "message": "Fictional incoming comment",
                        "request_id": "conditional-guest-chat",
                        "target": {"kind": "slide", "slide_id": "cover"},
                    },
                ).raise_for_status()
                with page.expect_response(
                    lambda response: (
                        response.url.endswith(f"/api/decks/{app.deck_id}")
                        and response.status == 304
                    ),
                    timeout=10_000,
                ):
                    _ = page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                expect(page.locator("#feedback-list")).to_contain_text("Fictional incoming comment")
                expect(composer).to_have_value("Fictional unsent draft")

                page.get_by_role("button", name="Close chat", exact=True).click()
                auto_update = page.locator("#auto-update-preview")
                expect(auto_update).to_have_attribute("aria-pressed", "true")
                auto_update.click()
                original = app.store.view(app.deck_id).slides[0]
                _ = app.store.revise_view(
                    app.deck_id,
                    original.id,
                    original.expected_hash,
                    "conditional-guest-revision",
                    SlidePatch(title="Fictional revised cover"),
                )
                expect(page.get_by_role("button", name="Show update")).to_be_visible()
                expect(page.locator("#slide-title")).not_to_have_text("Fictional revised cover")
                with page.expect_response(
                    lambda response: (
                        response.url.endswith(f"/api/decks/{app.deck_id}")
                        and response.status == 304
                    ),
                    timeout=10_000,
                ):
                    auto_update.click()
                expect(page.locator("#slide-title")).to_have_text("Fictional revised cover")
                page.get_by_role("button", name="Open chat", exact=True).click()
                expect(composer).to_have_value("Fictional unsent draft")
        finally:
            browser.close()


def test_shared_anchor_retains_its_slide_revision(tmp_path: Path) -> None:
    """An old element comment cannot target the current slide after it changes."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        sync_playwright() as playwright,
    ):
        accept(alice, grant(owner, "alice", app.deck_id, role="comment"))
        initial = app.store.view(app.deck_id).slides[0]
        response = owner.post(
            f"/api/workspaces/default/artifacts/{app.deck_id}/chat",
            json={
                "channel": "shared",
                "message": "Fictional anchored feedback",
                "request_id": "fictional-anchor-1",
                "target": {"kind": "element", "slide_id": "cover", "label": "Heading"},
                "slide_content_hash": initial.content_hash,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["slide_content_hash"] == initial.content_hash
        _ = app.store.revise_view(
            app.deck_id,
            initial.id,
            initial.expected_hash,
            "fictional-anchor-revision",
            SlidePatch(title="Revised cover"),
        )
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, "alice") as context:
                page = context.new_page()
                page.set_viewport_size({"width": 390, "height": 844})
                page.goto(f"{WORKSPACE}/?deck={app.deck_id}#cover")
                page.get_by_role("button", name="Open chat", exact=True).click()
                comment = page.locator("article").filter(has_text="Fictional anchored feedback")
                expect(comment).to_contain_text("Earlier version")
                comment.get_by_role("button", name="Heading").click()
                expect(page.locator("#toast")).to_contain_text("earlier version")
        finally:
            browser.close()


def test_shared_mention_suggests_contacts_and_opens_unread(tmp_path: Path) -> None:
    """A collaborator can mention a verified reader and the reader opens the message."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        sync_playwright() as playwright,
    ):
        accept(alice, grant(owner, "alice", app.deck_id, role="comment"))
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, "owner") as context:
                page = context.new_page()
                page.set_viewport_size({"width": 900, "height": 760})
                page.goto(f"{WORKSPACE}/?deck={app.deck_id}&chat=shared#cover")
                page.get_by_role("button", name="Open chat", exact=True).click()
                composer = page.locator("#feedback-text")
                composer.fill("Hello @ali")
                composer.press("ArrowDown")
                composer.press("Enter")
                composer.press("Enter")
                expect(page.locator("#feedback-list")).to_contain_text("@alice@example.test")
            for index in range(101):
                response = owner.post(
                    f"/api/workspaces/default/artifacts/{app.deck_id}/chat",
                    json={
                        "channel": "shared",
                        "message": f"Later message {index}",
                        "request_id": f"later-message-{index}",
                        "target": {"kind": "slide", "slide_id": "cover"},
                    },
                )
                assert response.status_code == 200, response.text
            with routed_multiplayer_context(browser, app, "alice") as context:
                page = context.new_page()
                page.set_viewport_size({"width": 900, "height": 760})
                page.goto(f"{WORKSPACE}/?deck={app.deck_id}#cover")
                unread = page.get_by_role("button", name="1 unread mention")
                expect(unread).to_be_visible()
                page.get_by_role("button", name="Open chat", exact=True).click()
                page.get_by_role("tab", name="Agent", exact=True).click()
                composer = page.locator("#feedback-text")
                composer.fill("A private unsent note")
                unread.click()
                expect(page.get_by_role("tab", name="Shared", exact=True)).to_have_attribute(
                    "aria-selected", "true"
                )
                expect(page.locator("#feedback-list .mentioned")).to_contain_text("Hello")
                expect(unread).to_be_hidden()
                expect(composer).to_have_value("")
                page.get_by_role("tab", name="Agent", exact=True).click()
                expect(composer).to_have_value("A private unsent note")
        finally:
            browser.close()


def test_mention_acknowledgement_does_not_interrupt_the_next_presentation(tmp_path: Path) -> None:
    """Keep the current presentation's unread count when the previous read finishes late."""
    next_deck = "2026-11-02-fictional-second"
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        sync_playwright() as playwright,
    ):
        app.store.create(next_deck, "Fictional second deck", "test-brand")
        for deck, message in ((app.deck_id, "First deck only"), (next_deck, "Second deck only")):
            accept(alice, grant(owner, "alice", deck, role="comment"))
            response = alice.post(
                f"/api/workspaces/default/artifacts/{deck}/chat",
                json={
                    "channel": "shared",
                    "message": f"{message} @owner@example.test",
                    "request_id": f"mention-{deck}",
                    "target": {"kind": "slide", "slide_id": "cover"},
                },
            )
            assert response.is_success, response.text
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app) as context:
                page = context.new_page()
                held_ack: list[tuple[Route, bytes]] = []
                held_state: list[tuple[Route, bytes]] = []
                first_state = f"/api/workspaces/default/artifacts/{app.deck_id}/state"
                second_state = f"/api/workspaces/default/artifacts/{next_deck}/state"

                def hold_ack(route: Route) -> None:
                    """Apply the read server-side while its browser response remains pending."""
                    response = owner.post(route.request.url.removeprefix(WORKSPACE), json={})
                    assert response.is_success, response.text
                    held_ack.append((route, response.content))

                def hold_state(route: Route) -> None:
                    """Delay the current unread state until the previous acknowledgement arrives."""
                    if held_state:
                        route.fallback()
                        return
                    response = owner.get(second_state)
                    assert response.is_success, response.text
                    held_state.append((route, response.content))

                page.route(f"**/artifacts/{app.deck_id}/mentions/*/read", hold_ack)
                page.route(f"**{second_state}", hold_state)
                page.goto(f"{WORKSPACE}/?deck={app.deck_id}#cover")
                unread = page.get_by_role("button", name="1 unread mention")
                switcher = page.locator("#deck-switcher")
                with page.expect_request(lambda request: "/mentions/" in request.url):
                    unread.click()
                expect(page.locator("#feedback-list .mentioned")).to_contain_text("First deck only")
                with page.expect_request(lambda request: request.url.endswith(second_state)):
                    switcher.click()
                    page.get_by_role("menuitemradio", name="Fictional second deck").click()
                expect(unread).to_be_hidden()
                assert len(held_ack) == len(held_state) == 1
                stale_reads: list[str] = []
                page.on(
                    "request",
                    lambda request: (
                        stale_reads.append(request.url)
                        if request.url.endswith(first_state)
                        else None
                    ),
                )
                ack, ack_body = held_ack.pop()
                with page.expect_response(lambda response: "/mentions/" in response.url):
                    ack.fulfill(status=200, content_type="application/json", body=ack_body)
                page.evaluate("() => new Promise(resolve => requestAnimationFrame(resolve))")
                state, state_body = held_state[0]
                state.fulfill(status=200, content_type="application/json", body=state_body)
                expect(unread).to_be_visible()
                assert stale_reads == []
                unread.click()
                expect(page.locator("#feedback-list .mentioned")).to_contain_text(
                    "Second deck only"
                )
                expect(page.locator("#feedback-list")).not_to_contain_text("First deck only")
                expect(unread).to_be_hidden()
        finally:
            browser.close()


@pytest.mark.parametrize("presentation", [True, False], ids=["slides", "artifact"])
def test_shared_chat_aligns_senders_and_marks_new_days(tmp_path: Path, presentation: bool) -> None:
    """Fictional guests see incoming messages at left and their own replies at right."""
    root = tmp_path / "app"
    with (
        running_multiplayer_app(root, tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        sync_playwright() as playwright,
    ):
        resource = app.deck_id if presentation else app.artifact_id
        accept(alice, grant(owner, "alice", resource, role="comment"))
        endpoint = f"/api/workspaces/default/artifacts/{resource}/chat"
        for index in range(2):
            posted = owner.post(
                endpoint,
                json={
                    "channel": "shared",
                    "message": f"Fictional owner message {index}",
                    "request_id": f"fictional-owner-chat-{index}",
                    "target": {"kind": "slide", "slide_id": "cover"} if presentation else None,
                },
            )
            assert posted.status_code == 200, posted.text
        with sqlite3.connect(root / "sharing" / "sharing.sqlite3") as db:
            _ = db.execute("UPDATE conversations SET created=?", (time.time() - 172800,))

        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, "alice") as context:
                page = context.new_page()
                page.set_viewport_size({"width": 900, "height": 760})
                page.emulate_media(color_scheme="dark")
                route = f"?deck={resource}#cover" if presentation else f"?artifact={resource}"
                page.goto(f"{WORKSPACE}/{route}")
                page.get_by_role("button", name="Open chat", exact=True).click()
                thread = page.locator("#feedback-list" if presentation else "#hub-thread")
                day_markers = thread.locator(".chat-day")
                incoming = thread.locator("article").filter(has_text="Fictional owner message 0")
                expect(incoming).to_be_visible()
                expect(incoming).to_contain_text("owner@example.test")
                expect(day_markers).to_have_count(1)
                composer = page.locator("#feedback-text" if presentation else "#hub-feedback-text")
                composer.fill("Fictional guest reply")
                composer.press("Enter")
                outgoing = thread.locator("article").filter(has_text="Fictional guest reply")
                # Reserve scrollbar space even on systems with overlay scrollbars.
                page.add_style_tag(
                    content="""
                    #feedback-list, #hub-thread {
                        overflow-y: scroll !important; scrollbar-gutter: stable !important;
                    }
                    #feedback-list::-webkit-scrollbar, #hub-thread::-webkit-scrollbar {
                        width: 12px;
                    }
                """
                )
                expect(outgoing).to_be_visible()
                # Check the rendered positions in the thread, independent of how the
                # browser serializes flex alignment keywords.
                positions = thread.evaluate(
                    """thread => {
                      const incoming = [...thread.querySelectorAll('article')].find(
                        row => row.textContent.includes('Fictional owner message 0'));
                      const outgoing = [...thread.querySelectorAll('article')].find(
                        row => row.textContent.includes('Fictional guest reply'));
                      const bounds = element => {
                        const r = element.getBoundingClientRect();
                        return {left:r.left, right:r.right, top:r.top, bottom:r.bottom};
                      };
                      const box = thread.getBoundingClientRect();
                      const style = getComputedStyle(thread);
                      const left = box.left + thread.clientLeft;
                      const content = {
                        left:left + parseFloat(style.paddingLeft),
                        right:left + thread.clientWidth - parseFloat(style.paddingRight)
                      };
                      return {thread:content, incoming:bounds(incoming),
                              outgoing:bounds(outgoing)};
                    }"""
                )
                assert 0 <= positions["incoming"]["left"] - positions["thread"]["left"] <= 6
                assert 0 <= positions["thread"]["right"] - positions["outgoing"]["right"] <= 6
                assert positions["incoming"]["right"] < positions["thread"]["right"] - 20
                assert positions["outgoing"]["left"] > positions["thread"]["left"] + 20
                assert positions["incoming"]["bottom"] <= positions["outgoing"]["top"]
                expect(day_markers).to_have_count(2)
                assert day_markers.nth(0).text_content() != day_markers.nth(1).text_content()
                assert page.locator("html").get_attribute("data-theme") == "dark"
        finally:
            browser.close()


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
                expect(page.get_by_role("tab", name="Shared", exact=True)).to_have_attribute(
                    "aria-selected", "true"
                )
                expect(page.get_by_role("button", name="Collaborate")).to_have_count(0)
                expect(page.get_by_role("button", name="Local review")).to_have_count(0)
                expect(page.locator("#mode-live")).to_have_count(0)
                expect(page.locator("#mode-batch")).to_have_count(0)
                expect(page).to_have_url(re.compile(r"[?&]deck=" + app.deck_id + "#next"))
                expect(page.locator("#slide-title")).to_have_text("Next")
                page.get_by_role("button", name="Menu", exact=True).click()
                page.get_by_role("menuitem", name="Agent connection").click()
                expect(page.get_by_role("dialog", name="Agent connection")).to_be_visible()
        finally:
            browser.close()


def test_hosted_chat_routes_both_tabs_to_one_agent(tmp_path: Path) -> None:
    """Keep shared and private drafts separate and route assigned requests in both tabs."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        sync_playwright() as playwright,
    ):
        accept(alice, grant(owner, "alice", app.deck_id, role="comment"))
        peer = Peer(app, agent(owner, app.deck_id))
        chat = f"/api/workspaces/default/artifacts/{app.deck_id}/chat"
        queued = owner.post(
            chat,
            json={
                "message": "Previously queued shared comment",
                "request_id": "fixture-queued-scoped-comment",
                "released": False,
                "target": {"kind": "slide", "slide_id": "cover"},
            },
        )
        queued.raise_for_status()
        legacy = f"/api/decks/{app.deck_id}"
        owner.post(legacy + "/feedback-mode", json={"mode": "batch"}).raise_for_status()
        cover = app.store.view(app.deck_id).slides[0]
        owner.post(
            legacy + "/slides/cover/feedback",
            json={
                "message": "Earlier private review comment",
                "request_id": "fixture-queued-legacy-comment",
                "content_hash": cover.content_hash,
                "expected_mode": "batch",
            },
        ).raise_for_status()

        browser = playwright.chromium.launch()
        try:
            with (
                routed_multiplayer_context(browser, app, "owner") as owner_context,
                routed_multiplayer_context(browser, app, "alice") as guest_context,
            ):
                page = owner_context.new_page()
                guest = guest_context.new_page()
                page.set_viewport_size({"width": 390, "height": 844})
                page.emulate_media(color_scheme="dark")
                for editor in (page, guest):
                    editor.goto(f"{WORKSPACE}/?deck={app.deck_id}#cover")
                    panel_toggle = editor.get_by_role(
                        "button", name=re.compile(r"^(Open|Close) chat$")
                    )
                    expect(panel_toggle).to_be_visible()
                    if panel_toggle.get_attribute("aria-expanded") == "false":
                        panel_toggle.click()
                    expect(editor.get_by_role("tab", name="Shared", exact=True)).to_be_visible()
                expect(
                    page.get_by_role("button", name="Send saved messages for this presentation (1)")
                ).to_be_visible()
                expect(guest.locator("#feedback-list")).not_to_contain_text("Previously queued")
                page.get_by_role(
                    "button", name="Send saved messages for this presentation (1)"
                ).click()
                expect(guest.locator("#feedback-list")).to_contain_text("Previously queued")

                composer = page.locator("#feedback-text")
                shared = page.locator("#chat-tab-shared")
                agent_tab = page.locator("#chat-tab-private")
                toggle = page.get_by_role("button", name="Ask your agent")
                expect(composer).to_have_attribute("placeholder", "Message collaborators…")
                expect(toggle).to_have_attribute("title", re.compile("visible to collaborators"))
                composer.fill("@@Request in Shared")
                expect(composer).to_have_value("Request in Shared")
                expect(toggle).to_have_attribute("aria-pressed", "true")
                expect(composer).to_have_attribute("placeholder", "Ask agent · shared with others…")
                expect(toggle).to_have_attribute("title", re.compile("visible to collaborators"))
                shared.focus()
                page.keyboard.press("ArrowRight")
                expect(agent_tab).to_be_focused()
                expect(composer).to_have_value("")
                expect(composer).to_have_attribute("placeholder", "Private message to your agent…")
                expect(page.locator("#feedback-list")).to_contain_text("Earlier private review")
                expect(guest.locator("#feedback-list")).not_to_contain_text(
                    "Earlier private review"
                )
                page.get_by_role(
                    "button", name="Send earlier saved messages for this presentation (1)"
                ).click()
                expect(
                    page.get_by_role(
                        "button", name="Send earlier saved messages for this presentation (1)"
                    )
                ).to_have_count(0)
                composer.fill("Request in Agent")
                composer.press("Enter")
                expect(page.locator("#feedback-list")).to_contain_text("Request in Agent")
                shared.click()
                expect(composer).to_have_value("Request in Shared")
                expect(toggle).to_have_attribute("aria-pressed", "true")
                composer.press("Enter")
                expect(guest.locator("#feedback-list .is-agent-request")).to_contain_text(
                    "Request in Shared"
                )
                expect(page.locator("#feedback-list .is-agent-request")).to_have_css(
                    "border-top-color", "rgb(142, 120, 203)"
                )
                expect(toggle).to_have_attribute("aria-pressed", "false")
                expect(guest.locator("#feedback-list")).not_to_contain_text("Request in Agent")
                expect(guest.get_by_role("button", name="Ask your agent")).to_be_hidden()

                events = cast(
                    list[dict[str, object]],
                    peer.value("read_feedback", deck_id=app.deck_id)["events"],
                )
                assert {event["message"] for event in events} == {
                    "Request in Shared",
                    "Request in Agent",
                }
                for event in events:
                    if event["channel"] == "shared":
                        assert "Previously queued shared comment" in str(event["shared_context"])
                    else:
                        assert "shared_context" not in event
                    claimed = peer.value(
                        "claim_feedback",
                        artifact_id=app.deck_id,
                        event_id=event["id"],
                        session_id="fixture-both-tabs",
                    )
                    reply = peer.value(
                        "reply_to_feedback",
                        artifact_id=app.deck_id,
                        event_id=event["id"],
                        claim=claimed["claim"],
                        message=f"Agent reply in {event['channel']}",
                        request_id=f"fixture-reply-{event['channel']}",
                    )
                    assert reply["channel"] == event["channel"]
                expect(agent_tab).to_have_accessible_name("Agent, unread messages")
                expect(page.locator("#feedback-list")).to_contain_text("Agent reply in shared")
                agent_tab.click()
                expect(page.locator("#feedback-list")).to_contain_text("Agent reply in private")
                expect(agent_tab).to_have_accessible_name("Agent")
                expect(guest.locator("#feedback-list")).to_contain_text("Agent reply in shared")
                guest.locator("#chat-tab-private").click()
                expect(guest.locator("#feedback-list")).not_to_contain_text(
                    "Agent reply in private"
                )
        finally:
            browser.close()
            peer.client.close()


def test_artifact_chat_shortcuts_and_robot_toggle(tmp_path: Path) -> None:
    """Send only explicitly addressed Shared messages to the scoped agent."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        sync_playwright() as playwright,
    ):
        peer = Peer(app, agent(owner, app.artifact_id))
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app) as context:
                page = context.new_page()
                page.goto(f"{WORKSPACE}/?artifact={app.artifact_id}")
                composer = page.locator("#hub-feedback-text")
                toggle = page.get_by_role("button", name="Ask your agent")
                thread = page.locator("#hub-thread")
                expect(toggle).to_be_visible()
                expect(composer).to_have_attribute("placeholder", "Message collaborators…")
                expect(toggle).to_have_attribute("title", re.compile("visible to collaborators"))
                composer.fill("@agent@example.test is the reference address.")
                expect(toggle).to_have_attribute("aria-pressed", "false")
                composer.press("Enter")
                expect(thread).to_contain_text("@agent@example.test")
                composer.fill("@agent Please review the headline.")
                expect(toggle).to_have_attribute("aria-pressed", "true")
                assert toggle.evaluate("node => getComputedStyle(node, '::after').content") == (
                    '"Agent on"'
                )
                expect(composer).to_have_value("Please review the headline.")
                expect(composer).to_have_attribute("placeholder", "Ask agent · shared with others…")
                expect(toggle).to_have_attribute("title", re.compile("visible to collaborators"))
                toggle.click()
                expect(toggle).to_have_attribute("aria-pressed", "false")
                expect(composer).to_have_attribute("placeholder", "Message collaborators…")
                composer.press("Enter")
                expect(thread).to_contain_text("Please review the headline.")
                assert peer.value("read_feedback", artifact_id=app.artifact_id)["events"] == []
                composer.fill("Can you simplify it?")
                toggle.focus()
                toggle.press("Space")
                expect(composer).to_be_focused()
                expect(toggle).to_have_attribute("aria-pressed", "true")
                composer.press("Enter")
                addressed = thread.locator(".is-agent-request")
                expect(addressed).to_contain_text("Can you simplify it?")
                expect(addressed.get_by_role("img", name="Agent request")).to_be_visible()
                expect(addressed.locator(".hub-message-body")).to_have_css(
                    "box-shadow", "rgb(148, 124, 202) 0px 0px 0px 1px inset"
                )
                expect(toggle).to_have_attribute("aria-pressed", "false")
                events = cast(
                    list[dict[str, object]],
                    peer.value("read_feedback", artifact_id=app.artifact_id)["events"],
                )
                assert [event["message"] for event in events] == ["Can you simplify it?"]
                assert "Please review the headline." in str(events[0]["shared_context"])
                page.locator("#hub-shared-channel").select_option("private")
                expect(composer).to_have_attribute("placeholder", "Private message to your agent…")
        finally:
            browser.close()
            peer.client.close()


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
                page.goto(WORKSPACE)
                page.wait_for_function("Boolean(window.StudioInvitation && window.ArtifactSession)")
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

                expect(page.get_by_role("tab", name="Shared", exact=True)).to_have_attribute(
                    "aria-selected", "true"
                )
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
                owner.grant_permissions(["clipboard-read", "clipboard-write"], origin=WORKSPACE)
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
                        dialog.get_by_label("Link expires").select_option("never")
                        dialog.get_by_role("button", name="Create link").click()
                        field = dialog.get_by_label("Invitation link", exact=True)
                        expect(field).to_have_value(
                            re.compile(r"https://studio\.example\.test/#invite=")
                        )
                        expect(dialog.get_by_role("button", name="Copy link")).to_be_focused()
                        expect(dialog.get_by_role("status")).to_have_text("Link copied.")
                        copied = page.evaluate("navigator.clipboard.readText()")
                        assert copied == field.input_value()
                        dialog.get_by_text("Manage access").click()
                        pending = dialog.get_by_text("alice@example.test · Can view · No expiry")
                        expect(pending).to_be_visible()
                        token = field.input_value().split("#invite=")[1]
                        wrong = other.post("/api/invitations/accept", json={"token": token})
                        assert wrong.status_code in {403, 404}
                        accept(invitee, token)
                        assert invitee.get(f"/api/artifacts/{app.artifact_id}").is_success
                        assert invitee.get(f"/api/artifacts/{app.sibling_id}").status_code == 404
                    elif title == "Fictional team deck":
                        page.set_viewport_size({"width": 360, "height": 640})
                        _ = page.evaluate("""Object.defineProperty(navigator, 'clipboard', {
                          value: {write: () => Promise.reject(new Error('Permission denied')),
                                  writeText: () => Promise.reject(new Error('Permission denied'))}
                        })""")
                        dialog.get_by_label("Email address").fill("bob@example.test")
                        expect(dialog.get_by_label("Link expires")).to_have_value("7d")
                        dialog.get_by_role("button", name="Create link").click()
                        expect(dialog.get_by_role("status")).to_have_text(
                            "Use Copy link to copy your invitation."
                        )
                        dialog.get_by_role("button", name="Copy link").press("Enter")
                        field = dialog.get_by_label("Invitation link", exact=True)
                        expect(field).to_be_focused()
                        assert field.evaluate("el => el.selectionEnd === el.value.length")
                        page.set_viewport_size({"width": 1600, "height": 1000})
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
                page.locator("#hub-tools-button").click()
                page.locator("[data-app-about]").click()
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
                    sidebar = page.locator(".rail, .hub-sidebar")
                    expect(
                        sidebar.locator(".app-sidebar-footer:last-child [data-server-version]")
                    ).to_be_visible()
                    page.locator("#workspace-menu-button").click()
                    page.locator("[data-app-about]").click()
                    expect(page.locator(".app-security-checks")).to_be_hidden()
                    page.get_by_role("link", name="My workspace").click()
                    switcher = page.get_by_role("region", name="Workspaces")
                    expect(switcher.get_by_role("alert")).to_have_text(
                        "Finish your current draft or edit before switching workspaces."
                    )
                    page.keyboard.press("Escape")
                    expect(page.locator("#feedback-text")).to_have_value("Unsent draft")
                    composer = page.locator("#feedback-text")
                    composer.press("ControlOrMeta+a")
                    composer.press("Backspace")
                    expect(composer).to_have_value("")
                    page.locator("#workspace-menu-button").click()
                    page.locator("[data-app-about]").click()
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
                page.locator("#hub-tools-button").click()
                page.locator("[data-app-about]").click()
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


def style_favorite_slide(store: Store, deck_id: str) -> None:
    """Give the static thumbnail a full-size layout, a local font and a local image."""
    folder = store.folder(deck_id)
    font = ImageFont.load_default()
    assert isinstance(font, ImageFont.FreeTypeFont)
    _ = (folder / "assets/thumbnail.ttf").write_bytes(font.font_bytes)
    _ = (folder / "assets/thumbnail.css").write_text(
        "@font-face{font-family:Thumbnail;src:url(thumbnail.ttf)}"
        ".thumbnail-test h1{font-family:Thumbnail,serif;color:rgb(20,60,120);"
        "position:absolute;left:1360px;top:760px;font-size:42px;margin:0}"
    )
    _ = (folder / "theme.css").write_text(
        '@import "https://blocked.example.test/external.css";'
        '@import "assets/thumbnail.css";'
        ".slide{background:rgb(232,240,248)}"
    )
    _ = (folder / "assets/thumbnail.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80">'
        '<rect width="80" height="80" fill="orange"/></svg>'
    )
    deck = store.read(deck_id)
    deck.slides[0].html = (
        '<article class="slide"><h1>Static title</h1>'
        '<img src="assets/thumbnail.svg" alt="Thumbnail logo"></article>'
    )
    deck.slides[
        0
    ].interactive_html = (
        '<p>Interactive slide only</p><script>document.body.dataset.ran = "yes";</script>'
    )
    store.write(deck_id, deck)
    index = folder / "index.html"
    _ = index.write_text(
        index.read_text().replace('<html lang="en">', '<html class="thumbnail-test" lang="en">')
    )


@pytest.mark.parametrize(("identity", "width"), [("owner", 1400), ("alice", 390), ("bob", 1100)])
def test_hosted_favorite_slides_show_styled_thumbnails_in_the_same_workspace(
    tmp_path: Path, identity: str, width: int
) -> None:
    """Use real favorite thumbnails as the default owner, personal owner and shared viewer."""
    root = tmp_path / "app"
    with (
        running_multiplayer_app(
            root,
            tmp_path / "server.log",
            unified=True,
            personal_workspace_emails=("alice@example.test",),
        ) as app,
        human(app, "owner") as owner,
        human(app, identity) as reader,
        sync_playwright() as playwright,
    ):
        home = reader.get("/").headers.get("location", "/")
        store = app.store
        if identity == "alice":
            workspace = home.split("/")[2]
            seed_isolated_presentation(root, app, workspace)
            area = root / "sharing/workspaces" / workspace
            store = Store(area / "collection", area / "state/review.sqlite3")
        elif identity == "bob":
            accept(reader, grant(owner, identity, app.deck_id, "view"))
        style_favorite_slide(store, app.deck_id)
        deck_title = store.read(app.deck_id).title
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, identity) as context:
                page = context.new_page()
                page.set_viewport_size({"width": width, "height": 900})
                page.goto(f"{WORKSPACE}{home}?deck={app.deck_id}#cover")
                sidebar = page.get_by_role("button", name="Open sidebar", exact=True)
                if width < 600:
                    sidebar.click()
                star = page.locator('[data-star-kind="slide"][data-star-slide="cover"]')
                star.focus()
                star.press("Enter")
                expect(star).to_have_attribute("aria-pressed", "true")
                page.goto(WORKSPACE + home)
                page.get_by_role("button", name="Favorite slides", exact=True).click()
                gallery = page.get_by_role("dialog", name="Favorite slides")
                expect(gallery.get_by_text(f"{deck_title} · 01")).to_be_visible()
                frame = gallery.locator(".favorite-cover iframe")
                thumbnail = frame.content_frame
                expect(frame).to_have_attribute("sandbox", "")
                expect(thumbnail.get_by_text("Interactive slide only")).to_have_count(0)
                heading = thumbnail.get_by_role("heading", name="Static title")
                expect(heading).to_have_css("color", "rgb(20, 60, 120)")
                assert heading.evaluate(
                    "async () => { await document.fonts.ready; "
                    "return [...document.fonts].some(f => f.family === 'Thumbnail' "
                    "&& f.status === 'loaded'); }"
                )
                assert thumbnail.get_by_alt_text("Thumbnail logo").evaluate(
                    "async image => {await image.decode();return image.naturalWidth === 80}"
                )
                bounds = frame.bounding_box()
                text = heading.bounding_box()
                assert bounds and text
                assert text["x"] + text["width"] < bounds["x"] + bounds["width"]
                assert text["y"] + text["height"] < bounds["y"] + bounds["height"]
                gallery.get_by_role("button", name=re.compile("^Add description for")).click()
                description = page.get_by_role("dialog", name="Slide description")
                description.get_by_role("textbox").fill("A useful opener")
                description.get_by_role("button", name="Save", exact=True).click()
                expect(gallery.get_by_text("A useful opener")).to_be_visible()
                gallery.locator(".favorite-pick").click()
                expect(page).to_have_url(f"{WORKSPACE}{home}?deck={app.deck_id}#cover")
                if width < 600:
                    sidebar.click()
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


@pytest.mark.parametrize("width", (1440, 390), ids=("desktop", "narrow"))
def test_collaborator_downloads_through_the_regular_share_panel(tmp_path: Path, width: int) -> None:
    """Reuse the artifact context menu and project header for scoped file downloads."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as reader,
        sync_playwright() as playwright,
    ):
        accept(reader, grant(owner, "alice", app.project_id, "view", "project"))
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, "alice") as context:
                page = context.new_page()
                page.set_viewport_size({"width": width, "height": 844})
                page.goto(WORKSPACE)
                card = page.get_by_role("button", name=re.compile("^Open Shared candidate"))
                card.click(button="right")
                expect(page.get_by_role("menuitem", name="Share link…")).to_have_count(0)
                page.get_by_role("menuitem", name="Share files", exact=True).click()
                panel = page.get_by_role("dialog", name="Share", exact=True)
                expect(panel.get_by_role("button", name="Outlook")).to_be_hidden()
                with page.expect_download() as selected:
                    panel.get_by_role("button", name="Save file").click()
                assert selected.value.suggested_filename == "Shared-candidate.md"
                assert selected.value.path().read_text() == "Fictional shared artifact"
                panel.get_by_role("button", name="Close share panel").click()
                expect(card).to_be_focused()
                page.get_by_role("button", name="Fictional project", exact=True).click()
                page.get_by_role("button", name="Share project", exact=True).click()
                panel = page.get_by_role("dialog", name="Share project", exact=True)
                with page.expect_download() as selected:
                    panel.get_by_role("button", name="Save file").click()
                assert selected.value.suggested_filename == "Fictional-project.zip"
        finally:
            browser.close()


def test_named_workspace_design_review_for_collaborator_and_reader(tmp_path: Path) -> None:
    """Review frozen designs in the normal editor and keep proposal chat attached."""
    root = tmp_path / "app"
    with (
        running_multiplayer_app(root, tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as editor,
        human(app, "bob") as reader,
        sync_playwright() as playwright,
    ):
        workspace = str(
            owner.post("/api/workspaces", json={"title": "Fictional design review"})
            .raise_for_status()
            .json()["id"]
        )
        home = f"/workspaces/{workspace}/"
        seed_isolated_presentation(root, app, workspace)
        for identity, client, role in (("alice", editor, "edit"), ("bob", reader, "view")):
            invite = (
                owner.post(
                    f"/api/workspaces/{workspace}/invitations",
                    json={
                        "kind": "artifact",
                        "resource": app.deck_id,
                        "email": f"{identity}@example.test",
                        "role": role,
                    },
                )
                .raise_for_status()
                .json()["url"]
            )
            accept(client, str(invite).split("#invite=")[1])
        token = str(
            editor.post(
                f"/api/workspaces/{workspace}/agents",
                json={"kind": "artifact", "resource": app.deck_id, "name": "Fictional designer"},
            )
            .raise_for_status()
            .json()["token"]
        )
        peer = Peer(app, token)
        try:
            cover = peer.value("get_slide", deck_id=app.deck_id, slide_id="cover")
            peer.value(
                "publish_slide_designs",
                deck_id=app.deck_id,
                slide_id="cover",
                expected_content_hash=cover["content_hash"],
                request_id="named-designs",
                options=[
                    {
                        "name": "Editorial",
                        "html": '<article class="slide"><h1>Editorial direction</h1></article>',
                    }
                ],
            )
            browser = playwright.chromium.launch()
            try:
                with (
                    routed_multiplayer_context(browser, app, "alice") as alice,
                    routed_multiplayer_context(browser, app, "bob") as bob,
                ):
                    for context, editable in ((bob, False), (alice, True)):
                        page = context.new_page()
                        if editable:
                            page.add_init_script("""
                                const realFetch = window.fetch;
                                const stateGate = new Promise(resolve => {
                                    window.releaseFixtureState = resolve;
                                });
                                window.fetch = async function(input, ...args) {
                                    if (typeof input === 'string' && input.endsWith('/state')) {
                                        window.waitingForFixtureState = true;
                                        await stateGate;
                                    }
                                    return realFetch.call(this, input, ...args);
                                };
                            """)
                        else:
                            page.set_viewport_size({"width": 900, "height": 800})
                        page.goto(f"{WORKSPACE}{home}?deck={app.deck_id}#cover")
                        tray = page.locator("#slide-design-tray")
                        expect(tray).to_be_visible()
                        tray.get_by_role("tab", name="Editorial", exact=True).click()
                        expect(
                            page.frame_locator(".sd-canvas-overlay iframe").locator("h1")
                        ).to_have_text("Editorial direction")
                        if not editable:
                            expect(tray.locator("[data-sd-use]")).to_be_hidden()
                            tray.get_by_role("button", name="Compare", exact=True).click()
                            expect(
                                page.get_by_role("dialog", name="Compare designs")
                            ).to_be_visible()
                            expect(page.locator("[data-sd-use-candidate]")).to_be_hidden()
                            page.keyboard.press("Escape")
                            continue
                        assert page.evaluate("window.waitingForFixtureState")
                        _ = page.evaluate("window.releaseFixtureState()")
                        page.locator("#feedback-text").fill("Make the Editorial title shorter")
                        expect(page.locator("#feedback-design-context")).to_have_text(
                            "Design · Editorial"
                        )
                        page.locator("#send-feedback").click()
                        expect(page.locator("#feedback-list")).to_contain_text(
                            "Make the Editorial title shorter"
                        )
                        events = (
                            editor.get(f"/api/workspaces/{workspace}/artifacts/{app.deck_id}/chat")
                            .raise_for_status()
                            .json()
                        )
                        assert events[0]["proposal"]["name"] == "Editorial"
                        tray.locator("[data-sd-use]").click()
                        expect(page.locator("#slide-design-tray")).to_be_hidden()
                        with page.expect_response("**/designs/undo") as response:
                            page.locator("#toast").get_by_role(
                                "button", name="Undo", exact=True
                            ).click()
                        assert response.value.ok
                        after = peer.value("get_slide", deck_id=app.deck_id, slide_id="cover")
                        assert after["html"] == cover["html"]
            finally:
                browser.close()
        finally:
            peer.client.close()


def test_selected_slide_exports_respect_shared_access(tmp_path: Path) -> None:
    """Export only selected slides for each authorized human in the native menu."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as viewer,
        human(app, "bob") as editor,
        human(app, "charlie") as stranger,
        sync_playwright() as playwright,
    ):
        accept(viewer, grant(owner, "alice", app.deck_id, "view"))
        accept(editor, grant(owner, "bob", app.deck_id, "edit"))
        body = {"scope": "artifact", "id": app.deck_id, "format": "html", "slide_ids": ["next"]}
        assert stranger.post("/api/shares", json=body).status_code in {401, 404}
        for client in (owner, viewer, editor):
            receipt = client.post("/api/shares", json=body).raise_for_status().json()
            html = client.get(receipt["download_url"]).raise_for_status().text
            assert '"id":"next"' in html and '"id":"cover"' not in html
        assert owner.post("/api/shares", json={**body, "slide_ids": ["absent"]}).status_code == 400
        browser = playwright.chromium.launch()
        try:
            for person in ("owner", "alice", "bob"):
                with routed_multiplayer_context(browser, app, person) as context:
                    page = context.new_page()
                    page.goto(f"{WORKSPACE}/?deck={app.deck_id}")
                    page.locator('#slides [data-id="next"]').click(button="right")
                    menu = page.get_by_role("menu", name="Slide options")
                    menu.get_by_role("menuitem", name="Export", exact=True).click()
                    with page.expect_download() as captured:
                        menu.get_by_role("menuitem", name="HTML", exact=True).click()
                    html = captured.value.path().read_text()
                    assert '"id":"next"' in html and '"id":"cover"' not in html
                    if person == "owner":
                        page.locator('#slides [data-id="cover"]').click()
                        page.locator('#slides [data-id="next"]').click(modifiers=["Shift"])
                        page.locator('#slides [data-id="next"]').click(button="right")
                        expect(menu.get_by_label("2 slides selected")).to_be_visible()
                        menu.get_by_role("menuitem", name="Export", exact=True).click()
                        with page.expect_download(timeout=60000) as pdf:
                            menu.get_by_role("menuitem", name="PDF", exact=True).click()
                        data = pdf.value.path().read_bytes()
                        assert data.startswith(b"%PDF-")
                        assert len(re.findall(rb"/Type\s*/Page\b", data)) == 2
                    if person == "alice":
                        page.locator('#slides [data-id="next"]').click(button="right")
                        expect(menu.get_by_role("menuitem", name="Delete slide")).to_be_hidden()
        finally:
            browser.close()


@pytest.mark.parametrize("kind", ["markdown", "html"])
def test_narrow_artifact_composer_stays_visible_when_long_thread_arrives(
    tmp_path: Path, kind: str
) -> None:
    """Keep a focused draft reachable as new shared messages expand the thread."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        sync_playwright() as playwright,
    ):
        if kind == "markdown":
            artifact_id = app.artifact_id
        else:
            artifacts = ArtifactStore(app.store)
            project = artifacts.create_project("Fictional HTML project")
            source = tmp_path / "page.html"
            source.write_text("<h1>Fictional page</h1>")
            artifact_id = artifacts.register_artifact(
                project.id, "Fictional page", source, "page.html"
            ).id
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app) as context:
                page = context.new_page()
                page.set_viewport_size({"width": 390, "height": 500})
                page.emulate_media(color_scheme="dark")
                page.goto(f"{WORKSPACE}/?artifact={artifact_id}")
                page.get_by_role("button", name="Open chat", exact=True).click()
                composer = page.locator("#hub-feedback-text")
                composer.fill("First fictional message")
                composer.press("Enter")
                expect(page.locator("#hub-thread")).to_contain_text("First fictional message")
                composer.fill("Keep this draft")
                page.locator("#hub-feedback-files").set_input_files(
                    {"name": "review.txt", "mimeType": "text/plain", "buffer": b"fictional"}
                )
                for index in range(12):
                    response = owner.post(
                        f"/api/workspaces/default/artifacts/{artifact_id}/chat",
                        json={
                            "message": f"Long discussion {index} " + "example " * 20,
                            "request_id": f"long-thread-{index}",
                        },
                    )
                    assert response.is_success, response.text
                expect(page.locator("#hub-thread")).to_contain_text(
                    "Long discussion 11", timeout=15000
                )
                expect(composer).to_have_value("Keep this draft")
                expect(page.locator("#hub-feedback-attachments")).to_contain_text("review.txt")
                expect(page.locator("#hub-shared-channel")).to_have_value("shared")
                send = page.locator("#hub-send")
                expect(send).to_be_in_viewport(ratio=1)
                assert send.evaluate(
                    "button => button.getBoundingClientRect().bottom <= "
                    "document.querySelector('.studio-report-trigger').getBoundingClientRect().top"
                )
                send.click()
                expect(page.locator("#hub-thread")).to_contain_text("Keep this draft")
                expect(page.locator("#hub-thread")).to_contain_text("review.txt")
        finally:
            browser.close()


def test_preview_retry_keeps_earlier_preview_and_draft_after_disconnect(tmp_path: Path) -> None:
    """A failed stylesheet load does not erase the last usable slide or the new draft."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, "owner") as context:
                page = context.new_page()
                page.goto(f"{WORKSPACE}/?deck={app.deck_id}#cover")
                expect(page.locator("#preview")).to_have_attribute(
                    "data-load-token", re.compile("-ready$")
                )
                old_key = page.locator("#preview").evaluate(
                    "f => f.contentDocument.body.dataset.previewKey"
                )
                page.route("**/theme.css**", lambda route: route.abort("internetdisconnected"))
                page.locator('#slides [data-id="next"]').click()
                page.locator("#feedback-text").fill("Fictional unsent draft")
                expect(page.locator("#preview-message")).to_contain_text("earlier preview")
                expect(page.locator("#retry-preview")).to_be_visible(timeout=15000)
                assert (
                    page.locator("#preview").evaluate(
                        "f => f.contentDocument.body.dataset.previewKey"
                    )
                    == old_key
                )
                expect(page.locator("#feedback-text")).to_have_value("Fictional unsent draft")
                expect(page.locator("#slide-title")).to_contain_text("Next")
                page.unroute("**/theme.css**")
                page.locator("#retry-preview").click()
                expect(page.locator("#preview")).to_have_attribute(
                    "data-load-token", re.compile("/next/.*-ready$"), timeout=15000
                )
                expect(page.locator("#feedback-text")).to_have_value("Fictional unsent draft")
                expect(page.locator("#slide-title")).to_contain_text("Next")
        finally:
            browser.close()


def test_restored_grants_remove_menu_action_after_success(tmp_path: Path) -> None:
    """Review and apply once, then ensure reopening the menu offers no stale action."""
    from artifact_studio.grant_recovery_models import GrantSnapshot, HumanGrant, StagedGrants
    from artifact_studio.multiplayer_access import checksum
    from artifact_studio.workspace_recovery import STAGED_GRANTS

    with (
        running_multiplayer_app(
            tmp_path / "app",
            tmp_path / "server.log",
            unified=True,
            grant_recovery_fixture=True,
            startup_timeout=30,
        ) as app,
        sync_playwright() as playwright,
    ):
        staged = app.store.database.parent / STAGED_GRANTS
        snapshot = GrantSnapshot(
            issuer="https://fictional.cloudflareaccess.com",
            audience="fixture-audience",
            workspace="default",
            owner=checksum("owner"),
            owner_email="owner@example.test",
            grants=[
                HumanGrant(
                    person=checksum("alice"),
                    email="alice@example.test",
                    kind="workspace",
                    resource="",
                    role="view",
                )
            ],
        )
        staged.write_text(
            StagedGrants(archive_sha256="a" * 64, snapshot=snapshot).model_dump_json()
        )
        staged.chmod(0o600)
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("ARTIFACT_STUDIO_CHROMIUM_EXECUTABLE")
        )
        try:
            with routed_multiplayer_context(browser, app) as context:
                page = context.new_page()
                page.goto(WORKSPACE + "/")
                page.locator("#hub-tools-button").click()
                entry = page.get_by_text("Restore human access", exact=True)
                expect(entry).to_be_visible()
                entry.click()
                dialog = page.get_by_role("dialog", name="Share link")
                expect(dialog.get_by_text(re.compile("alice@example.test"))).to_be_visible()
                restore = dialog.get_by_role("button", name="Restore human access")
                expect(restore).to_be_disabled()
                dialog.get_by_label("I reviewed these people and permissions").check()
                restore.click()
                expect(dialog.get_by_role("status")).to_have_text("Human access restored.")
                dialog.get_by_role("button", name="Close share link").click()
                page.locator("#hub-tools-button").click()
                expect(page.locator("[data-restore-access]")).to_be_hidden()
                with human(app, "alice") as alice:
                    assert alice.get("/api/workspaces/default/catalog").status_code == 200
        finally:
            browser.close()


def test_artifact_channel_switch_pending_copy_cannot_export_previous_private_thread(
    tmp_path: Path,
) -> None:
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        sync_playwright() as pw,
    ):
        chat = "/api/workspaces/default/artifacts/" + app.artifact_id + "/chat"
        for channel, message in [
            ("shared", "Fictional artifact shared message"),
            ("private", "Fictional artifact private sentinel"),
        ]:
            response = owner.post(
                chat,
                json={
                    "channel": channel,
                    "message": message,
                    "request_id": "fictional-artifact-" + channel,
                },
            )
            response.raise_for_status()
        browser = pw.chromium.launch()
        with routed_multiplayer_context(browser, app, "owner") as context:
            page = context.new_page()
            context.grant_permissions(["clipboard-read", "clipboard-write"], origin=WORKSPACE)
            page.add_init_script(
                """(() => {
                  const original = fetch;
                  window.holdShared = false; window.sharedHeld = false;
                  const held = new Promise(resolve => window.releaseShared = resolve);
                  window.fetch = async (...args) => {
                    if (window.holdShared && String(args[0]).includes('/chat') &&
                        String(args[0]).includes('channel=shared')) {
                      window.sharedHeld = true; await held;
                    }
                    return original(...args);
                  };
                })();"""
            )
            page.goto(WORKSPACE + "/?artifact=" + app.artifact_id)
            expect(page.locator("#hub-thread")).to_contain_text("Fictional artifact shared message")
            page.locator("#hub-shared-channel").select_option("private")
            expect(page.locator("#hub-thread")).to_contain_text(
                "Fictional artifact private sentinel"
            )
            page.evaluate(
                "async()=>{await navigator.clipboard.writeText('Fictional unchanged clipboard');"
                "window.holdShared=true;}"
            )
            page.locator("#hub-shared-channel").select_option("shared")
            page.wait_for_function("() => window.sharedHeld")
            copy = page.locator('[data-action="copy-conversation"]')
            if copy.is_enabled():
                copy.click()
            copied = page.evaluate("navigator.clipboard.readText()")
            assert "Fictional artifact private sentinel" not in copied, (
                "Copy under selected Shared exported previous private thread"
            )
            page.evaluate("window.releaseShared()")
        browser.close()


def test_delayed_private_refresh_cannot_replace_shared_copy(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        sync_playwright() as pw,
    ):
        chat = "/api/workspaces/default/artifacts/" + app.artifact_id + "/chat"
        for channel, message in [
            ("shared", "Fictional current shared refresh"),
            ("private", "Fictional stale private refresh"),
        ]:
            response = owner.post(
                chat,
                json={
                    "channel": channel,
                    "message": message,
                    "request_id": "fictional-refresh-" + channel,
                },
            )
            response.raise_for_status()
        browser = pw.chromium.launch()
        with routed_multiplayer_context(browser, app, "owner") as context:
            page = context.new_page()
            context.grant_permissions(["clipboard-read", "clipboard-write"], origin=WORKSPACE)
            page.add_init_script(
                """(() => {
                  const original = fetch;
                  window.holdPrivate = false; window.privateHeld = false;
                  window.privateReturned = false;
                  const held = new Promise(resolve => window.releasePrivate = resolve);
                  window.fetch = async (...args) => {
                    if (window.holdPrivate && !window.privateHeld &&
                        String(args[0]).includes('/chat') &&
                        String(args[0]).includes('channel=private')) {
                      const response = await original(...args);
                      window.privateHeld = true; await held;
                      window.privateReturned = true; return response;
                    }
                    return original(...args);
                  };
                })();"""
            )
            page.goto(WORKSPACE + "/?artifact=" + app.artifact_id)
            expect(page.locator("#hub-thread")).to_contain_text("Fictional current shared refresh")
            page.locator("#hub-shared-channel").select_option("private")
            expect(page.locator("#hub-thread")).to_contain_text("Fictional stale private refresh")
            page.evaluate("window.holdPrivate=true")
            page.wait_for_function("() => window.privateHeld", timeout=15000)
            page.locator("#hub-shared-channel").select_option("shared")
            expect(page.locator("#hub-thread")).to_contain_text("Fictional current shared refresh")
            page.evaluate("window.releasePrivate()")
            page.wait_for_function("() => window.privateReturned")
            page.evaluate(
                "() => new Promise(resolve => "
                "requestAnimationFrame(() => requestAnimationFrame(resolve)))"
            )
            expect(page.locator("#hub-thread")).not_to_contain_text(
                "Fictional stale private refresh"
            )
            page.locator('[data-action="copy-conversation"]').click()
            copied = page.evaluate("navigator.clipboard.readText()")
            assert (
                "Fictional current shared refresh" in copied
                and "Fictional stale private refresh" not in copied
            )
        browser.close()
