"""Exercise favorite discovery, selection and the real local-agent reference handoff."""

import os
from pathlib import Path
from typing import Literal

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
    expect(picker.locator(".favorites-grid")).not_to_contain_text("Loading…")
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
    star = page.get_by_role("button", name="Add to favorites: A clearer way to work", exact=True)
    star.click()
    expect(
        page.get_by_role("button", name="Remove from favorites: A clearer way to work", exact=True)
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


@pytest.mark.parametrize(("width", "theme"), [(1440, "light"), (1440, "dark"), (390, "dark")])
def test_slide_favorites_caption_menu_keyboard_and_sorter(
    page: Page,
    live_app: RunningApp,
    artifacts: Path,
    width: int,
    theme: Literal["light", "dark"],
) -> None:
    """Keep slide favorites clear of artwork and reachable through pointer and keyboard."""
    page.set_viewport_size({"width": width, "height": 900})
    page.emulate_media(color_scheme=theme)
    page.goto(f"{live_app.url}/?deck={DECK_ID}#cover", wait_until="networkidle")
    if width < 600:
        page.get_by_role("button", name="Open sidebar", exact=True).click()
    first = page.locator('.slide-thumb[data-id="cover"]')
    second = page.locator('.slide-thumb[data-id="next"]')
    star = page.locator('.slide-star[data-star-slide="next"]')
    original = live_app.store.read(DECK_ID)
    expect(star).to_have_attribute("title", "Add to favorites: Next steps")
    star.click()
    expect(star).to_have_attribute("aria-label", "Remove from favorites: Next steps")
    expect(first).to_have_attribute("aria-current", "true")
    star.focus()
    page.keyboard.press("Space")
    expect(star).to_have_attribute("aria-pressed", "false")
    expect(star).to_be_focused()
    star.click(button="right")
    menu = page.get_by_role("menu", name="Slide options", exact=True)
    menu.get_by_role("menuitem", name="Add to favorites", exact=True).click()
    expect(menu).to_be_hidden()
    expect(star).to_have_attribute("aria-pressed", "true")
    second.focus()
    page.keyboard.press("Shift+F10")
    remove = menu.get_by_role("menuitem", name="Remove from favorites", exact=True)
    expect(remove).to_be_visible()
    remove.focus()
    page.keyboard.press("Enter")
    expect(star).to_have_attribute("aria-pressed", "false")
    expect(second).to_be_focused()
    assert live_app.store.read(DECK_ID) == original
    star.click()
    expect(star).to_have_attribute("aria-pressed", "true")

    def check_caption() -> None:
        """Check real bounds so all state icons remain outside the slide image."""
        assert star.evaluate("""node => {
          const entry=node.closest('.slide-entry'), star=node.getBoundingClientRect();
          const preview=entry.querySelector('.thumb-preview').getBoundingClientRect();
          const caption=entry.querySelector('.thumb-caption').getBoundingClientRect();
          const dot=entry.querySelector('.thumb-caption>.dot').getBoundingClientRect();
          const title=entry.querySelector('.thumb-title').getBoundingClientRect();
          return star.top >= preview.bottom && star.top >= caption.top - 1
            && star.bottom <= caption.bottom + 1 && dot.right <= star.left
            && title.right <= star.left && star.right <= caption.right + 1;
        }""")

    check_caption()
    _ = page.screenshot(path=str(artifacts / "favorite-caption.png"))
    second.click(button="right")
    _ = page.screenshot(path=str(artifacts / "favorite-menu.png"))
    page.keyboard.press("Escape")
    if width < 600:
        page.get_by_role("button", name="Close sidebar", exact=True).click()
    page.get_by_role("button", name="Slide sorter", exact=True).click()
    check_caption()
    _ = page.screenshot(path=str(artifacts / "favorite-sorter.png"))
    page.reload(wait_until="networkidle")
    expect(star).to_have_attribute("aria-pressed", "true")


@pytest.mark.parametrize("approved", [False, True])
def test_slide_favorites_touch_target_and_review_states(
    browser: Browser, live_app: RunningApp, artifacts: Path, approved: bool
) -> None:
    """Keep touch favorites discoverable without changing hidden, unseen or review state."""
    deck = live_app.store.read(DECK_ID)
    second = deck.slides[1]
    live_app.store.revise(
        DECK_ID,
        second.id,
        digest(second.model_dump_json()),
        "touch-favorite-revision",
        SlidePatch(html=second.html.replace("Present with confidence", "Updated with confidence")),
    )
    deck = live_app.store.read(DECK_ID)
    second = deck.slides[1]
    second.hidden = True
    live_app.store.write(DECK_ID, deck)
    content_hash = live_app.store.content_hash(DECK_ID, second)
    if approved:
        live_app.store.approve(DECK_ID, second.id, content_hash, True)
    assert live_app.store.view_slide(DECK_ID, second).has_unseen_changes is not approved
    expected_classes = (
        "slide-thumb is-hidden" if approved else "slide-thumb has-unseen-changes is-hidden"
    )
    expected_dot = "dot green" if approved else "dot red"
    context = browser.new_context(
        viewport={"width": 390, "height": 844}, has_touch=True, color_scheme="dark"
    )
    page = context.new_page()
    try:
        page.goto(f"{live_app.url}/?deck={DECK_ID}#cover", wait_until="networkidle")
        page.get_by_role("button", name="Open sidebar", exact=True).tap()
        target = page.locator('.slide-thumb[data-id="next"]')
        star = page.locator('.slide-star[data-star-slide="next"]')
        expect(target).to_have_class(expected_classes)
        expect(target.locator(".dot")).to_have_class(expected_dot)
        expect(star).to_have_css("width", "40px")
        expect(star.locator(".ui-icon")).to_have_css("opacity", "1")
        star.tap()
        expect(star).to_have_attribute("aria-pressed", "true")
        expect(target).to_have_class(expected_classes)
        expect(target.locator(".dot")).to_have_class(expected_dot)
        expect(page.locator('.slide-thumb[data-id="cover"]')).to_have_attribute(
            "aria-current", "true"
        )
        assert live_app.store.read(DECK_ID) == deck
        _ = page.screenshot(path=str(artifacts / "favorite-touch-states.png"))
    finally:
        context.close()


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
