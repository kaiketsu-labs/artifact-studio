"""Exercise real hosted draft conflicts and explicit recovery with fictional content."""

import os
from pathlib import Path
from typing import cast

import pytest
from playwright.sync_api import Route, expect, sync_playwright

from artifact_studio.models import SlidePatch
from tests.integration.multiplayer_fixture import (
    WORKSPACE,
    routed_multiplayer_context,
    running_multiplayer_app,
)
from tests.test_multiplayer_server import agent, human
from tests.test_multiplayer_simulation import Peer

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_hosted_draft_conflict_requires_explicit_review_and_send(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        sync_playwright() as playwright,
    ):
        second_deck = "2026-11-02-fictional-recovery"
        app.store.create(second_deck, "Fictional second deck", "test-brand")
        peer = Peer(app, agent(owner, app.deck_id))
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app) as context:
                page = context.new_page()
                page.goto(f"{WORKSPACE}/?deck={app.deck_id}#cover")
                composer = page.locator("#feedback-text")
                expect(composer).to_be_enabled()
                page.locator("#notes-panel summary").click()
                page.locator("#notes").fill("Fictional presenter note kept during review")
                expect(page.locator("#notes-state")).to_have_text("Saved")
                page.locator("#chat-ask-agent").click()
                composer.fill("Fictional revision request with attachment")
                page.locator("#feedback-file-input").set_input_files(
                    {
                        "name": "fictional.txt",
                        "mimeType": "text/plain",
                        "buffer": b"Fictional reference",
                    }
                )
                expect(page.locator("#feedback-files")).to_contain_text("fictional.txt")
                attempts: list[dict[str, object]] = []

                def race(route: Route) -> None:
                    if route.request.method == "POST":
                        attempts.append(cast(dict[str, object], route.request.post_data_json))
                        if len(attempts) == 1:
                            original = app.store.view(app.deck_id).slides[0]
                            app.store.revise_view(
                                app.deck_id,
                                original.id,
                                original.expected_hash,
                                "draft-race",
                                SlidePatch(title="Fictional newer cover"),
                            )
                    route.fallback()

                page.route(f"**/artifacts/{app.deck_id}/chat", race)
                with page.expect_response(lambda response: response.status == 409):
                    composer.press("Enter")
                expect(page.locator("#draft-recovery")).to_be_visible()
                expect(composer).to_have_value("Fictional revision request with attachment")
                expect(page.locator("#send-feedback")).to_be_disabled()
                assert len(attempts) == 1
                page.locator("#review-draft-slide").click()
                expect(page.locator("#slide-title")).to_have_text("Fictional newer cover")
                expect(page.locator("#notes")).to_have_value(
                    "Fictional presenter note kept during review"
                )
                expect(page.locator("#use-draft-version")).to_be_visible()
                assert len(attempts) == 1
                # A second revision while reviewing invalidates the explicit choice.
                reviewed = app.store.view(app.deck_id).slides[0]
                app.store.revise_view(
                    app.deck_id,
                    reviewed.id,
                    reviewed.expected_hash,
                    "second-review-race",
                    SlidePatch(title="Fictional latest cover"),
                )
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                expect(page.locator("#slide-title")).to_have_text("Fictional latest cover")
                expect(page.locator("#use-draft-version")).to_be_hidden()
                expect(page.locator("#send-feedback")).to_be_disabled()
                page.locator("#review-draft-slide").click()
                expect(page.locator("#use-draft-version")).to_be_visible()
                page.screenshot(path="/tmp/artifact-draft-recovery.png")
                # Switching channels keeps the original draft, route and attachment.
                page.locator("#chat-tab-private").click()
                expect(composer).to_have_value("")
                page.locator("#chat-tab-shared").click()
                expect(composer).to_have_value("Fictional revision request with attachment")
                expect(page.locator("#feedback-files")).to_contain_text("fictional.txt")
                page.locator("#deck-switcher").click()
                page.get_by_role("menuitemradio", name="Fictional second deck").click()
                expect(composer).to_have_value("")
                expect(page.locator("#draft-recovery")).to_be_hidden()
                page.locator("#deck-switcher").click()
                page.get_by_role("menuitemradio", name="Fictional team deck").click()
                expect(composer).to_have_value("Fictional revision request with attachment")
                expect(page.locator("#feedback-files")).to_contain_text("fictional.txt")
                expect(page.locator("#chat-ask-agent")).to_have_attribute("aria-pressed", "true")
                page.locator("#use-draft-version").click()
                expect(page.locator("#draft-recovery")).to_be_hidden()
                assert len(attempts) == 1
                expect(page.locator("#send-feedback")).to_be_enabled()
                composer.press("Enter")
                expect(composer).to_have_value("")
                assert len(attempts) == 2
                assert attempts[0]["request_id"] != attempts[1]["request_id"]
                assert attempts[0]["slide_content_hash"] != attempts[1]["slide_content_hash"]
                assert (
                    attempts[1]["slide_content_hash"]
                    == app.store.view(app.deck_id).slides[0].content_hash
                )
                assert attempts[0]["files"] == attempts[1]["files"]
                events = cast(
                    list[object], peer.value("read_feedback", deck_id=app.deck_id)["events"]
                )
                assert len(events) == 1
                assert "Fictional revision request" in str(events)
                # A selected-object request cannot acquire new anchors by review alone.
                page.frame_locator("#preview").get_by_role("heading", level=1).click()
                page.locator("#chat-ask-agent").click()
                composer.fill("Fictional targeted request")
                current = app.store.view(app.deck_id).slides[0]
                app.store.revise_view(
                    app.deck_id,
                    current.id,
                    current.expected_hash,
                    "target-race",
                    SlidePatch(
                        title="Fictional third cover",
                        html=(
                            '<article class="slide"><h1>Fictional revised heading</h1>'
                            "<p>Fictional supporting text</p></article>"
                        ),
                    ),
                )
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                expect(page.locator("#draft-recovery")).to_be_visible()
                page.locator("#review-draft-slide").click()
                expect(page.locator("#slide-title")).to_have_text("Fictional third cover")
                expect(page.locator("#use-draft-version")).to_be_hidden()
                expect(page.locator("#send-feedback")).to_be_disabled()
                assert len(attempts) == 2
                page.frame_locator("#preview").get_by_role("heading", level=1).click()
                expect(page.locator("#draft-recovery")).to_be_hidden()
                expect(page.locator("#send-feedback")).to_be_enabled()
                page.frame_locator("#preview").get_by_text("Fictional supporting text").click(
                    modifiers=["Shift"]
                )
                expect(page.locator("#target-members .target-member")).to_have_count(2)
                expect(page.locator("#send-feedback")).to_be_enabled()
                composer.press("Enter")
                expect(composer).to_have_value("")
                assert len(attempts) == 3
        finally:
            browser.close()
            peer.client.close()


def test_unconfirmed_delivery_freezes_payload_and_retries_once_after_revision(
    tmp_path: Path,
) -> None:
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        sync_playwright() as playwright,
    ):
        peer = Peer(app, agent(owner, app.deck_id))
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app) as context:
                page = context.new_page()
                page.goto(f"{WORKSPACE}/?deck={app.deck_id}#cover")
                composer = page.locator("#feedback-text")
                expect(composer).to_be_enabled()
                page.frame_locator("#preview").get_by_role("heading", level=1).click()
                page.locator("#chat-ask-agent").click()
                composer.fill("Fictional request whose response is lost")
                page.locator("#feedback-file-input").set_input_files(
                    {"name": "retained.txt", "mimeType": "text/plain", "buffer": b"Fictional file"}
                )
                attempts: list[dict[str, object]] = []

                def lose_first_response(route: Route) -> None:
                    if route.request.method == "POST":
                        payload = cast(dict[str, object], route.request.post_data_json)
                        attempts.append(payload)
                        if len(attempts) == 1:
                            response = owner.post(
                                f"/api/workspaces/default/artifacts/{app.deck_id}/chat",
                                json=payload,
                            )
                            response.raise_for_status()
                            route.abort("connectionfailed")
                            return
                    route.fallback()

                page.route(f"**/artifacts/{app.deck_id}/chat", lose_first_response)
                composer.press("Enter")
                expect(page.locator("#draft-recovery")).to_contain_text("Delivery unconfirmed")
                expect(composer).to_have_attribute("readonly", "")
                for selector in [
                    "#chat-ask-agent",
                    "#attach-feedback",
                    "#reference-slides",
                    "#clear-target",
                    "#select-parent",
                    "#feedback-file-input",
                    "[data-chat-remove]",
                ]:
                    expect(page.locator(selector)).to_be_disabled()
                expect(page.locator("#review-draft-slide")).to_be_hidden()
                expect(page.locator("#use-draft-version")).to_be_hidden()
                expect(page.locator("#send-feedback")).to_be_enabled()
                original = app.store.view(app.deck_id).slides[0]
                app.store.revise_view(
                    app.deck_id,
                    original.id,
                    original.expected_hash,
                    "uncertain-delivery-revision",
                    SlidePatch(title="Fictional advanced cover"),
                )
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                expect(page.locator("#slide-title")).to_have_text("Fictional advanced cover")
                # Selecting objects cannot replace the pending original target or request ID.
                page.frame_locator("#preview").get_by_role("heading", level=1).click()
                page.locator("#chat-tab-private").click()
                expect(composer).not_to_have_attribute("readonly", "")
                page.locator("#chat-tab-shared").click()
                expect(composer).to_have_attribute("readonly", "")
                expect(composer).to_have_value("Fictional request whose response is lost")
                expect(page.locator("#feedback-files")).to_contain_text("retained.txt")
                expect(page.locator("#send-feedback")).to_be_enabled()
                composer.press("Enter")
                expect(composer).to_have_value("")
                assert len(attempts) == 2
                assert attempts[0] == attempts[1]
                events = cast(
                    list[object], peer.value("read_feedback", deck_id=app.deck_id)["events"]
                )
                assert len(events) == 1
                expect(composer).not_to_have_attribute("readonly", "")
                expect(page.locator("#draft-recovery")).to_be_hidden()
        finally:
            browser.close()
            peer.client.close()


def test_narrow_formatting_keeps_neighboring_objects_clickable(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        sync_playwright() as playwright,
    ):
        original = app.store.view(app.deck_id).slides[0]
        app.store.revise_view(
            app.deck_id,
            original.id,
            original.expected_hash,
            "narrow-selection-fixture",
            SlidePatch(
                html=(
                    '<article class="slide" style="width:100%;height:100%;padding:24px;'
                    'box-sizing:border-box"><h1 style="font-size:24px;margin:0">'
                    'Fictional heading</h1><p style="font-size:16px;margin:16px 0">'
                    "Fictional paragraph to select</p></article>"
                )
            ),
        )
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app) as context:
                page = context.new_page()
                page.set_viewport_size({"width": 390, "height": 844})
                page.emulate_media(color_scheme="dark")
                page.goto(f"{WORKSPACE}/?deck={app.deck_id}#cover")
                close_chat = page.get_by_role("button", name="Close chat", exact=True)
                if close_chat.is_visible():
                    close_chat.click()
                frame = page.frame_locator("#preview")
                frame.get_by_role("heading", name="Fictional heading").click()
                toolbar = page.locator("#element-properties")
                expect(toolbar).to_be_visible()
                expect(toolbar.get_by_role("button", name="Bold", exact=True)).to_be_visible()
                toolbar.get_by_role("button", name="Text alignment", exact=True).click()
                expect(
                    toolbar.get_by_role("button", name="Center text", exact=True)
                ).to_be_visible()
                toolbar.get_by_role("button", name="Text alignment", exact=True).click()
                preview_box = page.locator("#preview").bounding_box()
                toolbar_box = toolbar.bounding_box()
                assert preview_box is not None and toolbar_box is not None
                assert toolbar_box["y"] >= preview_box["y"] + preview_box["height"]
                page.locator("#notes-panel summary").click(timeout=3000)
                expect(page.locator("#notes")).to_be_visible()
                page.locator("#notes").fill("Fictional narrow note while object selected")
                expect(toolbar).to_be_visible()
                page.locator("#notes-panel summary").click(timeout=3000)
                page.locator(".sources-panel summary").click(timeout=3000)
                expect(page.locator("#sources")).to_be_visible()
                page.locator(".sources-panel summary").click(timeout=3000)
                page.screenshot(path="/tmp/artifact-narrow-selection.png")
                frame.get_by_text("Fictional paragraph to select", exact=True).click(
                    modifiers=["Shift"], timeout=3000
                )
                expect(toolbar).to_be_hidden()
                assert (
                    page.locator("#preview-wrap").evaluate(
                        "node => node.style.getPropertyValue('--element-toolbar-space')"
                    )
                    == "0px"
                )
                page.get_by_role("button", name="Open chat", exact=True).click()
                expect(page.locator("#target-members .target-member")).to_have_count(2)
        finally:
            browser.close()
