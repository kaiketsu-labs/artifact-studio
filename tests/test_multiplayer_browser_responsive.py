"""Browser regressions for chat history and keyboard navigation during refresh."""

import os
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, expect, sync_playwright

from tests.integration.multiplayer_fixture import (
    WORKSPACE,
    MultiplayerApp,
    routed_multiplayer_context,
    running_multiplayer_app,
)

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def wait_for_poll(page: Page, app: MultiplayerApp, artifact: str) -> None:
    """Wait for a real background chat refresh and allow its rendering to complete."""
    with page.expect_response(
        lambda response: (
            f"/artifacts/{artifact}/chat?channel=" in response.url
            and response.request.method == "GET"
        ),
        timeout=8000,
    ):
        page.wait_for_timeout(3000)
    page.wait_for_timeout(200)


def test_history_and_keyboard_survive_polling(tmp_path: Path) -> None:
    with running_multiplayer_app(tmp_path / "server", tmp_path / "server.log") as app:
        with httpx.Client(
            base_url=app.url,
            headers={"host": "studio.example.test", "x-fixture-auth": "owner", "origin": WORKSPACE},
            timeout=15,
        ) as client:
            for index in range(105):
                response = client.post(
                    f"/api/workspaces/default/artifacts/{app.artifact_id}/chat",
                    json={
                        "message": f"History item {index:03}",
                        "request_id": f"history-{index:03}",
                    },
                )
                assert response.status_code == 200, response.text

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            with routed_multiplayer_context(browser, app) as context:
                page = context.new_page()
                page.goto(WORKSPACE)
                page.locator(f'[data-artifact="{app.artifact_id}"]').click()
                expect(page.get_by_role("heading", name="Shared candidate")).to_be_visible()
                earlier = page.get_by_role("button", name="Earlier messages")
                expect(earlier).to_be_visible()
                earlier.click()
                expect(page.get_by_text("History item 000", exact=True)).to_be_visible()
                expect(earlier).to_be_hidden()
                wait_for_poll(page, app, app.artifact_id)
                expect(page.get_by_text("History item 000", exact=True)).to_be_visible()
                expect(page.get_by_text("History item 104", exact=True)).to_be_attached()
                expect(earlier).to_be_hidden()

                shared = page.get_by_role("tab", name="Shared")
                private = page.get_by_role("tab", name="My agent")
                shared.focus()
                page.keyboard.press("ArrowRight")
                expect(private).to_be_focused()
                expect(private).to_have_attribute("aria-selected", "true")
                expect(shared).to_have_attribute("tabindex", "-1")
                page.keyboard.press("Home")
                expect(shared).to_be_focused()
                expect(shared).to_have_attribute("aria-selected", "true")
                page.keyboard.press("End")
                expect(private).to_be_focused()

                page.get_by_role("button", name="Fictional team deck Private").click()
                slide = page.get_by_role("button", name="1 Fictional team deck")
                expect(slide).to_be_visible()
                slide.focus()
                wait_for_poll(page, app, app.deck_id)
                expect(slide).to_be_focused()
                page.keyboard.press("Tab")
                expect(page.get_by_role("button", name="2 Next")).to_be_focused()
            browser.close()


def test_old_batch_is_delivered_to_collaborator(tmp_path: Path) -> None:
    with running_multiplayer_app(tmp_path / "server", tmp_path / "server.log") as app:
        headers = {"host": "studio.example.test", "x-fixture-auth": "owner", "origin": WORKSPACE}
        with httpx.Client(base_url=app.url, headers=headers, timeout=15) as owner:
            invitation = owner.post(
                "/api/workspaces/default/invitations",
                json={
                    "kind": "artifact",
                    "resource": app.artifact_id,
                    "email": "alice@example.test",
                    "role": "comment",
                },
            )
            assert invitation.status_code == 200, invitation.text
            token = invitation.json()["url"].split("#invite=")[1]
            with httpx.Client(
                base_url=app.url,
                headers={**headers, "x-fixture-auth": "alice"},
                timeout=15,
            ) as alice:
                accepted = alice.post("/api/invitations/accept", json={"token": token})
                assert accepted.status_code == 200, accepted.text

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                with routed_multiplayer_context(browser, app) as owner_context:
                    page = owner_context.new_page()
                    page.goto(WORKSPACE)
                    page.locator(f'[data-artifact="{app.artifact_id}"]').click()
                    expect(page.get_by_role("heading", name="Shared candidate")).to_be_visible()
                    chat_url = f"/api/workspaces/default/artifacts/{app.artifact_id}/chat"
                    saved = owner.post(
                        chat_url,
                        json={
                            "message": "Old batch draft",
                            "request_id": "saved-fixture-001",
                            "released": False,
                        },
                    )
                    assert saved.status_code == 200, saved.text
                    original = owner.get(chat_url, params={"channel": "shared"}).json()[0]
                    assert not original["released"]
                    for index in range(105):
                        response = owner.post(
                            chat_url,
                            json={
                                "message": f"Later item {index:03}",
                                "request_id": f"later-{index:03}",
                            },
                        )
                        assert response.status_code == 200, response.text
                    # A fresh page loads only the latest 100 messages, excluding the draft.
                    page.reload()
                    expect(page.get_by_text("Later item 104")).to_be_attached()
                    expect(page.get_by_text("Old batch draft")).to_have_count(0)
                    send_batch = page.get_by_role(
                        "button", name="Send saved messages for this artifact"
                    )
                    expect(send_batch).to_be_visible()
                    send_batch.click()
                    expect(page.get_by_text("Old batch draft")).to_be_attached()
                    expect(send_batch).to_be_hidden()
                    latest = owner.get(chat_url, params={"channel": "shared"}).json()
                    delivered = next(
                        item for item in latest if item["message"] == "Old batch draft"
                    )
                    assert delivered["released"]
                    assert delivered["id"] == original["id"]
                    assert delivered["created"] == original["created"]
                    assert delivered["target"] == original["target"]

                with routed_multiplayer_context(browser, app, identity="alice") as alice_context:
                    alice_page = alice_context.new_page()
                    alice_page.goto(WORKSPACE)
                    expect(alice_page.get_by_text("Old batch draft")).to_be_attached()
                    expect(alice_page.get_by_text("Later item 104")).to_be_attached()
                browser.close()
