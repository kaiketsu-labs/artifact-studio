"""Exercise favorite discovery, selection and the real local-agent reference handoff."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from artifact_studio.favorites import DescriptionInput, Favorites, StarInput
from artifact_studio.models import FeedbackFeed, SlidePatch
from artifact_studio.storage import digest
from scripts.preview_favorites import prepare_covers, seed_favorites
from tests.integration.support import DECK_ID, RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


@pytest.fixture
def prepared(live_app: RunningApp, browser: Browser, tmp_path: Path) -> RunningApp:
    seed_favorites(live_app, tmp_path)
    prepare_covers(live_app, browser)
    return live_app


def test_default_sort_star_keyboard_persistence_and_explicit_sort(
    page: Page, prepared: RunningApp
) -> None:
    page.goto(prepared.url)
    sort = page.get_by_role("combobox", name="Sort artifacts")
    expect(sort).to_have_value("starred")
    title = page.locator(".hub-card-title>span")
    assert title.all_text_contents()[:2] == ["Brand direction", "A clearer way to work"]
    button = page.get_by_role("button", name="Star Launch brief", exact=True)
    button.focus()
    page.keyboard.press("Space")
    expect(page.get_by_role("button", name="Unstar Launch brief", exact=True)).to_be_focused()
    assert title.all_text_contents()[:3] == [
        "Launch brief",
        "Brand direction",
        "A clearer way to work",
    ]
    sort.select_option("name")
    expected = title.all_text_contents()
    page.get_by_role("button", name="Star Research notes", exact=True).click()
    expect(page.get_by_role("button", name="Unstar Research notes", exact=True)).to_be_visible()
    assert title.all_text_contents() == expected
    page.reload()
    expect(sort).to_have_value("name")
    expect(page.get_by_role("button", name="Unstar Research notes", exact=True)).to_be_visible()
    # A second distinct click during an in-flight write must be queued, not dropped.
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (url, options) => {
        if (url === '/api/favorites' && options?.method === 'POST')
          await new Promise(resolve => setTimeout(resolve, 500));
        return original(url, options);
      };
    }""")
    page.get_by_role("button", name="Unstar Launch brief", exact=True).click()
    page.get_by_role("button", name="Unstar Research notes", exact=True).click()
    expect(page.get_by_role("button", name="Star Launch brief", exact=True)).to_be_enabled()
    expect(page.get_by_role("button", name="Star Research notes", exact=True)).to_be_enabled()
    page.reload()
    expect(page.get_by_role("button", name="Star Launch brief", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Star Research notes", exact=True)).to_be_visible()


def test_library_previews_search_empty_state_and_mobile_keyboard(
    page: Page, prepared: RunningApp
) -> None:
    page.goto(prepared.url)
    opener = page.get_by_role("button", name="Favorite slides", exact=True)
    opener.click()
    dialog = page.get_by_role("dialog", name="Favorite slides", exact=True)
    expect(dialog.locator(".favorite-card")).to_have_count(3)
    expect(page.frame_locator(".favorite-card iframe").first.locator(".slide")).to_be_visible()
    search = page.get_by_role("searchbox", name="Search favorite slides")
    search.fill("Fieldnotes")
    expect(dialog.locator(".favorite-card")).to_have_count(1)
    search.fill("missing")
    expect(dialog.get_by_text("No matching slides", exact=True)).to_be_visible()
    search.fill("")
    page.keyboard.press("Escape")
    expect(opener).to_be_focused()
    page.get_by_role("button", name="Appearance", exact=True).click()
    page.get_by_role("menuitemradio", name="Dark", exact=True).click()
    page.set_viewport_size({"width": 390, "height": 844})
    opener.click()
    expect(dialog.locator(".favorite-card")).to_have_count(3)
    assert dialog.evaluate("node => node.scrollWidth <= node.clientWidth")
    for remaining in (2, 1, 0):
        dialog.locator("[data-star-kind]").first.click()
        expect(dialog.locator(".favorite-card")).to_have_count(remaining)
    expect(dialog.get_by_text("Keep your best slides handy")).to_be_visible()


def test_cross_deck_reference_drafts_batch_delivery_and_frozen_source(
    page: Page, prepared: RunningApp
) -> None:
    page.goto(f"{prepared.url}/?deck={DECK_ID}#workflow")
    page.get_by_role("button", name="Reference favorite slides", exact=True).click()
    picker = page.get_by_role("dialog", name="Favorite slides", exact=True)
    expect(picker.locator("[data-favorite-pick]")).to_have_count(3)
    picker.get_by_role(
        "button", name="Edit description for One slide. One clear idea.", exact=True
    ).click()
    editor = page.get_by_role("dialog", name="Slide description", exact=True)
    editor.get_by_role("textbox", name="What makes this slide useful?").fill(
        "A comparison layout with a strong hierarchy."
    )
    editor.get_by_role("button", name="Save", exact=True).click()
    expect(editor).to_be_hidden()
    search = picker.get_by_role("searchbox", name="Search favorite slides")
    search.fill("strong hierarchy")
    expect(picker.locator("[data-favorite-pick]")).to_have_count(1)
    search.fill("")
    expect(picker.locator("[data-favorite-pick]")).to_have_count(3)
    picker.locator("[data-favorite-pick]").first.click()
    picker.locator("[data-favorite-pick]").last.click()
    expect(picker.get_by_role("status")).to_have_text("2 selected")
    picker.get_by_role("button", name="Add references", exact=True).click()
    expect(picker).to_be_hidden()
    expect(page.locator(".is-slide-reference")).to_have_count(2)
    expect(page.locator(".is-slide-reference small,.is-slide-reference img")).to_have_count(0)
    expect(page.locator(".is-slide-reference .chat-file-name").last).to_have_text(
        "One slide. One clear idea."
    )
    page.locator('.slide-thumb[data-id="cover"]').click()
    expect(page.locator(".is-slide-reference")).to_have_count(0)
    page.locator('.slide-thumb[data-id="workflow"]').click()
    expect(page.locator(".is-slide-reference")).to_have_count(2)
    # The source changes and is unstarred after attachment; the chat retains its exact bytes.
    other = "2026-11-05-fieldnotes"
    source = prepared.store.locate(prepared.store.read(other), "story")
    _ = prepared.store.revise(
        other,
        "story",
        digest(source.model_dump_json()),
        "changed-after-attach",
        SlidePatch(html='<article class="slide">NEW SOURCE VERSION</article>'),
    )
    _ = Favorites(prepared.store).describe(
        other,
        "story",
        DescriptionInput(
            description="A newer description",
            expected_description="A comparison layout with a strong hierarchy.",
        ),
    )
    _ = Favorites(prepared.store).set(
        StarInput(kind="slide", artifact_id=other, slide_id="story", starred=False)
    )
    page.locator("#mode-batch").click()
    page.locator("#feedback-text").fill("Borrow the first layout and the second visual hierarchy.")
    page.locator("#send-feedback").click()
    expect(page.locator("#feedback-list .comment-pending")).to_be_visible()
    withheld = prepared.call("read_feedback", deck_id=DECK_ID)
    assert "Borrow the first layout" not in str(withheld)
    page.locator("#send-batch").click()
    expect(page.locator("#feedback-list .comment-pending")).to_have_count(0)
    delivered = prepared.call("read_feedback", deck_id=DECK_ID)
    assert "Borrow the first layout" in str(delivered)
    feed = FeedbackFeed.model_validate(delivered)
    message = next(event for event in feed.events if "Borrow the first layout" in event.message)
    assert len(message.files) == 2
    bodies = [Path(file.path).read_text() for file in message.files]
    assert "One slide. One clear idea." in bodies[1]
    assert "NEW SOURCE VERSION" not in bodies[1]
    assert '"role": "read-only visual reference"' in bodies[1]
    assert '"description": "A comparison layout with a strong hierarchy."' in bodies[1]
    assert "A newer description" not in bodies[1]
    assert "Pause on the contrast" not in bodies[1]
    assert message.files[1].name == "One slide. One clear idea. - slide reference.html"
    expect(page.locator("#feedback-list .is-path-reference")).to_have_count(2)
    expect(page.locator("#feedback-list")).not_to_contain_text(" - slide reference.html")


def test_slide_star_and_native_artifact_chat_reference(page: Page, prepared: RunningApp) -> None:
    page.goto(f"{prepared.url}/?deck={DECK_ID}#cover")
    star = page.get_by_role("button", name="Star A clearer way to work", exact=True)
    star.click()
    expect(
        page.get_by_role("button", name="Unstar A clearer way to work", exact=True)
    ).to_be_visible()
    page.get_by_role("link", name="Back to all artifacts", exact=True).click()
    page.get_by_role(
        "button", name="Open Brand direction, HTML page, Fieldnotes", exact=True
    ).click()
    page.get_by_role("button", name="Reference favorite slides", exact=True).click()
    picker = page.get_by_role("dialog", name="Favorite slides", exact=True)
    expect(page.frame_locator(".favorite-card iframe").first.locator(".slide")).to_be_visible()
    picker.locator("[data-favorite-pick]").first.click()
    picker.get_by_role("button", name="Add references", exact=True).click()
    pill = page.locator("#hub-feedback-attachments .is-slide-reference")
    expect(pill).to_have_count(1)
    title = pill.locator(".chat-file-name").inner_text()
    expect(pill.locator("small,img,iframe")).to_have_count(0)
    page.locator("#hub-feedback-text").fill("Use this as the visual direction.")
    page.locator("#hub-send").click()
    expect(page.locator(".hub-message-body")).to_contain_text("Use this as the visual direction.")
    expect(page.locator(".hub-message .hub-attachment")).to_have_count(1)
    expect(page.locator(".hub-message .is-path-reference .chat-file-name")).to_have_text(title)
    expect(page.locator(".hub-message")).not_to_contain_text(" - slide reference.html")


def test_description_save_cancel_search_persistence_and_clear(
    page: Page, prepared: RunningApp
) -> None:
    page.goto(prepared.url)
    page.get_by_role("button", name="Favorite slides", exact=True).click()
    picker = page.get_by_role("dialog", name="Favorite slides", exact=True)
    add = picker.get_by_role("button", name="Add description for Let the story move", exact=True)
    add.click()
    editor = page.get_by_role("dialog", name="Slide description", exact=True)
    textbox = editor.get_by_role("textbox", name="What makes this slide useful?")
    expect(textbox).to_be_focused()
    textbox.fill("A searchable before-and-after chart.")
    editor.get_by_role("button", name="Save", exact=True).click()
    expect(editor).to_be_hidden()
    edit = picker.get_by_role("button", name="Edit description for Let the story move", exact=True)
    expect(edit).to_be_focused()
    edit.click()
    textbox.fill("Do not save this draft")
    page.keyboard.press("Escape")
    expect(editor).to_be_hidden()
    expect(edit).to_contain_text("A searchable before-and-after chart.")
    page.keyboard.press("Escape")
    page.reload()
    page.get_by_role("button", name="Favorite slides", exact=True).click()
    search = picker.get_by_role("searchbox", name="Search favorite slides")
    search.fill("before-and-after")
    expect(picker.locator(".favorite-card")).to_have_count(1)
    edit.click()
    textbox.fill("")
    editor.get_by_role("button", name="Save", exact=True).click()
    expect(editor).to_be_hidden()
    expect(search).to_be_focused()
    expect(picker.get_by_text("No matching slides", exact=True)).to_be_visible()
    search.fill("")
    expect(add).to_be_visible()
