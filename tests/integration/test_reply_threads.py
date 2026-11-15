"""Real human/MCP replies stay scoped, discoverable and stable during polling."""

import os
from contextlib import closing
from pathlib import Path
from typing import cast

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from artifact_studio.agent_client import Invocation
from artifact_studio.artifacts import ArtifactStore
from tests.integration.multiplayer_fixture import (
    WORKSPACE,
    routed_multiplayer_context,
    running_multiplayer_app,
)
from tests.integration.support import DECK_ID, RunningApp
from tests.integration.test_browser import open_editor
from tests.integration.test_questions import ask
from tests.test_multiplayer_server import accept, grant, human
from tests.test_multiplayer_simulation import Peer

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_slide_thread_keyboard_polling_new_replies_and_copy(
    page: Page, live_app: RunningApp, artifacts: Path
) -> None:
    slide = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    request = live_app.post(
        f"/api/decks/{DECK_ID}/slides/cover/feedback",
        {
            "message": "Make the launch timeline easier to scan.",
            "content_hash": slide["content_hash"],
            "request_id": "thread-launch-request",
            "files": [{"name": "brief.txt", "data_base64": "RmljdGlvbmFsIGJyaWVm"}],
        },
    )
    open_editor(page, live_app)
    page.locator("#feedback-text").fill("My unsent follow-up stays here.")

    def reply(number: int) -> None:
        live_app.client().call(
            Invocation(
                tool="reply_to_feedback",
                arguments={
                    "deck_id": DECK_ID,
                    "slide_id": "cover",
                    "event_id": request["event_id"],
                    "message": f"Timeline update {number}: milestones now have clear dates.",
                    "request_id": f"thread-launch-reply-{number}",
                },
            )
        )

    reply(1)
    thread = page.locator("#feedback-list .chat-replies")
    toggle = thread.locator("summary")
    expect(toggle).to_contain_text("1 reply")
    expect(thread).to_have_attribute("open", "")
    expect(thread.locator(".chat-reply-excerpt")).to_have_text(
        "Make the launch timeline easier to scan."
    )
    expect(page.locator("#feedback-list").get_by_role("link", name="brief.txt")).to_be_visible()
    toggle.focus()
    toggle.press("Enter")
    expect(thread).not_to_have_attribute("open", "")
    reply(2)
    expect(toggle).to_contain_text("2 replies")
    expect(toggle).to_contain_text("1 new")
    expect(toggle).to_be_focused()
    expect(thread).not_to_have_attribute("open", "")
    expect(page.locator("#feedback-text")).to_have_value("My unsent follow-up stays here.")
    page.screenshot(path=str(artifacts / "threads-collapsed-light.png"))
    toggle.press("Space")
    expect(thread).to_have_attribute("open", "")
    expect(thread.locator(".chat-thread-new")).to_be_hidden()
    thread.get_by_role("button", name="Go to message from You").first.press("Enter")
    expect(page.locator(f'[data-comment-id="{request["event_id"]}"]')).to_be_focused()
    page.screenshot(path=str(artifacts / "threads-expanded-light.png"))
    toggle.click()
    page.evaluate(
        "Object.defineProperty(navigator,'clipboard',{configurable:true,value:"
        "{writeText:async text=>{window.threadClipboard=text;}}})"
    )
    page.get_by_role("button", name="Copy conversation", exact=True).click()
    page.wait_for_function("window.threadClipboard?.includes('Timeline update 2')")
    assert "brief.txt" in page.evaluate("window.threadClipboard")
    page.emulate_media(color_scheme="dark")
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("button", name="Open chat", exact=True).click()
    toggle.click()
    expect(thread).to_have_attribute("open", "")
    expect(thread.locator(".comment.agent")).to_have_count(2)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(artifacts / "threads-expanded-narrow-dark.png"))


def test_local_artifact_replies_keep_their_parent_and_draft(
    page: Page, live_app: RunningApp, tmp_path: Path
) -> None:
    store = ArtifactStore(live_app.store)
    project = store.create_project("Fictional thread review")
    source = tmp_path / "launch.md"
    source.write_text("# Fictional launch brief")
    item = store.register_artifact(project.id, "Launch brief", source, source.name)
    request = live_app.post(
        f"/api/artifacts/{item.id}/feedback",
        {
            "message": "Clarify the owner.",
            "content_hash": item.content_hash,
            "request_id": "artifact-thread-request",
        },
    )
    live_app.call(
        "reply_to_artifact_feedback",
        artifact_id=item.id,
        message="Alex owns the fictional launch.",
        content_hash=item.content_hash,
        reply_to=request["event_id"],
        request_id="artifact-thread-answer",
    )
    page.goto(live_app.url + f"/?artifact={item.id}")
    thread = page.locator("#hub-thread .chat-replies")
    expect(thread.locator("summary")).to_have_text("1 reply")
    expect(thread).to_have_attribute("open", "")
    page.locator("#hub-feedback-text").fill("Keep this unsent draft.")
    thread.locator("summary").press("Enter")
    page.locator("#hub-send").click()
    expect(page.locator("#hub-thread")).to_contain_text("Keep this unsent draft.")
    expect(thread).not_to_have_attribute("open", "")
    thread.locator("summary").press("Enter")
    thread.get_by_role("button", name="Go to message from You").click()
    expect(page.locator("#hub-thread .chat-thread > .hub-message")).to_be_focused()


def test_focused_question_stays_visible_when_chat_height_shrinks(
    page: Page, live_app: RunningApp
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    open_editor(page, live_app)
    page.get_by_role("button", name="Open chat", exact=True).click()
    page.locator("#feedback-text").fill("Keep this separate unsent draft.")
    page.locator("#feedback-file-input").set_input_files(
        {"name": "draft.txt", "mimeType": "text/plain", "buffer": b"Fictional draft"}
    )
    identifier = ask(live_app, request_id="thread-small-question")
    card = page.locator(f'[data-question-id="{identifier}"]')
    answer = card.get_by_role("textbox", name="Write your own answer")
    answer.fill("Customer outcomes")
    page.set_viewport_size({"width": 390, "height": 560})
    expect(answer).to_be_focused()
    page.wait_for_function(
        """() => {
          const response = document.activeElement.closest('.question-response');
          const list = document.getElementById('feedback-list').getBoundingClientRect();
          const bounds = response.getBoundingClientRect();
          return bounds.top >= list.top && bounds.bottom <= list.bottom;
        }""",
        timeout=5000,
    )
    expect(card.get_by_role("button", name="Send answer", exact=True)).to_be_in_viewport()
    expect(page.locator("#feedback-text")).to_have_value("Keep this separate unsent draft.")
    expect(page.locator("#feedback-files")).to_contain_text("draft.txt")


@pytest.mark.parametrize("surface", ["presentation", "artifact", "together"])
def test_hosted_two_people_agent_threads_do_not_cross_private_channels(
    tmp_path: Path, surface: str
) -> None:
    with (
        running_multiplayer_app(
            tmp_path / "app",
            tmp_path / "server.log",
            unified=surface != "together",
            startup_timeout=30,
        ) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        sync_playwright() as playwright,
    ):
        artifact = app.deck_id if surface == "presentation" else app.artifact_id
        accept(alice, grant(owner, "alice", artifact))
        connection = owner.post(
            "/api/workspaces/default/agents",
            json={
                "kind": "artifact",
                "resource": artifact,
                "name": "Fictional launch agent",
            },
        ).json()
        endpoint = f"/api/workspaces/default/artifacts/{artifact}"
        for channel in ("shared", "private"):
            owner.post(
                endpoint + "/chat",
                json={
                    "message": "Public launch request"
                    if channel == "shared"
                    else "PRIVATE THREAD SENTINEL",
                    "channel": channel,
                    "assigned": connection["id"],
                    "request_id": f"thread-{channel}",
                    "target": {"kind": "slide", "slide_id": "cover"}
                    if surface == "presentation"
                    else None,
                },
            ).raise_for_status()
        alice.post(
            endpoint + "/chat",
            json={
                "message": "Public launch request",
                "request_id": "alice-same-text",
                "target": {"kind": "slide", "slide_id": "cover"}
                if surface == "presentation"
                else None,
            },
        ).raise_for_status()
        with closing((peer := Peer(app, connection["token"])).client):
            events = cast(
                list[dict[str, object]], peer.value("read_feedback", artifact_id=artifact)["events"]
            )
            for event in events:
                claim = peer.value(
                    "claim_feedback",
                    artifact_id=artifact,
                    event_id=event["id"],
                    session_id="thread-agent",
                )
                peer.value(
                    "reply_to_feedback",
                    artifact_id=artifact,
                    event_id=event["id"],
                    claim=claim["claim"],
                    message="Shared response"
                    if event["channel"] == "shared"
                    else "PRIVATE REPLY SENTINEL",
                    request_id=f"thread-reply-{event['id']}",
                )
        browser = playwright.chromium.launch()
        try:
            for identity in ("owner", "alice"):
                with routed_multiplayer_context(browser, app, identity) as context:
                    page = context.new_page()
                    if surface == "presentation":
                        path, list_selector = (
                            f"/?deck={artifact}#cover",
                            "#feedback-list",
                        )
                    elif surface == "artifact":
                        path, list_selector = (
                            f"/?artifact={artifact}",
                            "#hub-thread",
                        )
                    else:
                        path, list_selector = (
                            f"/?workspace=default&artifact={artifact}",
                            "#messages",
                        )
                    page.goto(WORKSPACE + path)
                    thread = page.locator(list_selector + " .chat-replies")
                    expect(thread).to_have_count(1)
                    expect(thread).to_have_attribute("open", "")
                    expect(thread).to_contain_text("Shared response")
                    expect(page.locator(list_selector)).not_to_contain_text("PRIVATE")
                    expect(thread.locator(".chat-reply-excerpt")).to_have_text(
                        "Public launch request"
                    )
                    # Identical human text must not cause the other user's request to be grouped.
                    expect(
                        page.locator(list_selector + " > article").filter(
                            has_text="Public launch request"
                        )
                    ).to_have_count(1)
                    thread.locator("summary").click()
                    alice.post(
                        endpoint + "/chat",
                        json={
                            "message": "Another visible comment",
                            "request_id": f"poll-{surface}-{identity}",
                        },
                    ).raise_for_status()
                    expect(page.locator(list_selector)).to_contain_text(
                        "Another visible comment", timeout=20000
                    )
                    expect(thread).not_to_have_attribute("open", "")

                    def switch(channel: str, page: Page = page) -> None:
                        if surface == "presentation":
                            page.locator(f"#chat-tab-{channel}").click()
                        elif surface == "artifact":
                            page.locator("#hub-shared-channel").select_option(channel)
                        else:
                            page.locator(f'[data-channel="{channel}"]').click()

                    switch("private")
                    if identity == "owner":
                        expect(thread).to_have_attribute("open", "")
                        expect(thread).to_contain_text("PRIVATE REPLY SENTINEL")
                        expect(thread.locator(".chat-reply-excerpt")).to_have_text(
                            "PRIVATE THREAD SENTINEL"
                        )
                    else:
                        expect(thread).to_have_count(0)
                        expect(page.locator(list_selector)).not_to_contain_text("PRIVATE")
                    expect(page.locator(list_selector)).not_to_contain_text("Shared response")
                    switch("shared")
                    expect(thread.locator("summary")).to_contain_text("1 reply")
                    expect(thread).not_to_have_attribute("open", "")
        finally:
            browser.close()
