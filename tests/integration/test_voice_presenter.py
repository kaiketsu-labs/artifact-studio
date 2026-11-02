"""Hosted presenter dictation keeps notes on their original slide and exports stay offline."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from studio.projects import audience_html
from tests.integration.support import DECK_ID, PRIVATE_NOTE, RunningApp
from tests.integration.voice_support import install_voice, voice_end, voice_result

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("SLIDE_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_presenter_dictation_drains_before_navigation_and_saves_the_original_slide(
    page: Page, live_app: RunningApp
) -> None:
    """A final arriving after Next was clicked still belongs to the old slide's notes."""
    install_voice(page)
    _ = page.goto(live_app.url + f"/decks/{DECK_ID}/index.html?presenter=1#cover")
    notes = page.locator("#presenter-notes")
    expect(notes).to_have_value(PRIVATE_NOTE)
    notes.click()
    page.keyboard.press("ControlOrMeta+End")
    page.get_by_role("button", name="Dictate into Speaker notes", exact=True).click()
    voice_result(page, "First spoken note.")
    voice_result(page, "Unfinished old words", final=False)
    page.evaluate(
        """() => {
            document.querySelector('#presenter-next-button').click();
            window.__speech.sessions[0].result('Final old-slide words.');
            window.__speech.sessions[0].end();
        }"""
    )
    expect(page.locator("#presenter-counter")).to_have_text("2 / 2")
    expect(notes).to_have_value("")
    voice_result(page, "Too late for either slide", session=0)
    expect(notes).to_have_value("")
    page.get_by_role("button", name="Dictate into Speaker notes", exact=True).click()
    voice_result(page, "Second slide's spoken note.", session=1)
    page.evaluate(
        """() => {
            document.querySelector('#save-presenter-notes').click();
            window.__speech.sessions[1].result('Final saved words.');
            window.__speech.sessions[1].end();
        }"""
    )
    expect(page.locator("#presenter-note-status")).to_have_text("Saved")
    saved = live_app.store.read(DECK_ID)
    assert saved.slides[0].notes == PRIVATE_NOTE + " First spoken note. Final old-slide words."
    assert saved.slides[1].notes == "Second slide's spoken note. Final saved words."
    assert live_app.store.comments(DECK_ID) == []


def test_hosted_notes_dialog_loads_dictation_only_when_editing_is_requested(
    page: Page, live_app: RunningApp
) -> None:
    """An ordinary slide view makes no voice request; editable Notes shares normal autosave."""
    requested: list[str] = []
    page.on("request", lambda request: requested.append(request.url))
    install_voice(page)
    _ = page.goto(live_app.url + f"/decks/{DECK_ID}/index.html", wait_until="networkidle")
    assert not any("/web/voice-input" in url for url in requested)
    page.locator("#notes-button").click()
    notes = page.locator("#notes-text")
    notes.click()
    page.keyboard.press("ControlOrMeta+End")
    page.get_by_role("button", name="Dictate into Edit speaker notes", exact=True).click()
    voice_result(page, "Dialog note.")
    page.get_by_role("button", name="Stop dictation for Edit speaker notes", exact=True).click()
    voice_end(page)
    expect(page.locator("#message")).to_have_text("Saved")
    assert live_app.store.read(DECK_ID).slides[0].notes == PRIVATE_NOTE + " Dialog note."
    assert sum(url.endswith("/web/voice-input.js") for url in requested) == 1


@pytest.mark.parametrize("destination", ["audience", "offline"])
def test_audience_and_portable_exports_never_load_workspace_voice_assets(
    page: Page, live_app: RunningApp, tmp_path: Path, destination: str
) -> None:
    """Sharing a slide or opening an exported file does not request microphone modules."""
    requested: list[str] = []
    page.on("request", lambda request: requested.append(request.url))
    install_voice(page)
    if destination == "audience":
        _ = page.goto(live_app.url + f"/decks/{DECK_ID}/index.html?audience=1#cover")
    else:
        exported = tmp_path / "audience.html"
        _ = exported.write_text(audience_html(live_app.store, DECK_ID))
        _ = page.goto(exported.as_uri())
        page.locator("#show-presenter").click()
    expect(page.locator("#stage h1")).to_have_text("Review together")
    expect(page.locator(".voice-input-button")).to_have_count(0)
    assert not any("/web/voice-input" in url for url in requested)
    assert int(str(page.evaluate("window.__speech.sessions.length"))) == 0
