"""PowerPoint-style sections in the existing rail, sorter and portable project flow."""

import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import Locator, Page, expect

from studio.models import Slide
from tests.integration.support import DECK_ID, RunningApp, markup
from tests.integration.test_browser import open_editor

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("SLIDE_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def add_section(page: Page, name: str) -> None:
    """Start a group at the active slide using the actual name dialog."""
    page.get_by_role("button", name="Add section", exact=True).click()
    page.get_by_role("textbox", name="Section name", exact=True).fill(name)
    page.locator("#section-submit").click()
    expect(page.locator("#section-dialog")).not_to_be_visible()
    expect(page.locator(".section-title", has_text=name)).to_be_visible()


def section(page: Page, name: str) -> Locator:
    """Select one section by its visible header without depending on generated IDs."""
    return page.locator(".slide-section").filter(has=page.locator(".section-title", has_text=name))


@pytest.mark.parametrize("width,height", [(1280, 800), (1600, 1000)])
def test_create_fold_rename_move_remove_and_save_sections(
    page: Page, live_app: RunningApp, artifacts: Path, width: int, height: int
) -> None:
    """Keep grouping and folding across refresh, sorter views and a project round trip."""
    _ = live_app.store.add_slide(
        DECK_ID, Slide(id="closing", title="Next steps", html=markup("Next steps"))
    )
    page.set_viewport_size({"width": width, "height": height})
    open_editor(page, live_app)
    page.get_by_role("button", name="Approve", exact=True).click()
    original = live_app.store.view(DECK_ID).slides[0]
    add_section(page, "Overview")
    expect(page.get_by_role("button", name="Rename section: Overview", exact=True)).to_be_enabled()
    page.locator('.slide-thumb[data-id="next"]').click()
    add_section(page, "Results")
    expect(page.get_by_role("button", name="Rename section: Results", exact=True)).to_be_enabled()
    expect(section(page, "Overview").locator(".section-count")).to_have_text("1")
    expect(section(page, "Results").locator(".section-count")).to_have_text("2")
    _ = page.screenshot(path=str(artifacts / "sections-expanded.png"), full_page=True)
    section(page, "Overview").locator(".section-toggle").click()
    expect(page.locator('.slide-thumb[data-id="cover"]')).to_be_hidden()
    _ = page.reload(wait_until="networkidle")
    expect(section(page, "Overview").locator(".section-toggle")).to_have_attribute(
        "aria-expanded", "false"
    )
    source = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    _ = live_app.call(
        "ask_slide_question",
        deck_id=DECK_ID,
        slide_id="cover",
        question="Who will present this?",
        options=["Team", "Leadership"],
        expected_hash=source["expected_hash"],
        request_id="section-hidden-question",
    )
    expect(section(page, "Overview").locator(".section-question")).to_be_visible()
    _ = page.screenshot(path=str(artifacts / "sections-light.png"), full_page=True)
    page.get_by_role("button", name="Menu", exact=True).click()
    page.get_by_role("menuitem", name="Appearance", exact=True).click()
    page.get_by_role("menuitemradio", name="Dark", exact=True).click()
    _ = page.screenshot(path=str(artifacts / "sections-dark.png"), full_page=True)
    page.get_by_role("button", name="Section options: Results", exact=True).click()
    page.get_by_role("menuitem", name="Rename", exact=True).click()
    page.get_by_role("textbox", name="Section name").fill("Decisions")
    page.locator("#section-submit").click()
    expect(page.locator("#section-dialog")).not_to_be_visible()
    expect(page.locator(".section-title", has_text="Decisions")).to_be_visible()
    page.get_by_role("button", name="Section options: Decisions", exact=True).click()
    page.get_by_role("menuitem", name="Move up", exact=True).click()
    expect(page.locator(".section-title").first).to_have_text("Decisions")
    assert [slide.id for slide in live_app.store.read(DECK_ID).slides] == [
        "next",
        "closing",
        "cover",
    ]
    page.get_by_role("button", name="Slide sorter", exact=True).click()
    expect(page.locator(".workspace")).to_have_class(re.compile(".*is-sorter.*"))
    expect(section(page, "Decisions").locator(".section-toggle")).to_be_visible()
    page.get_by_role("button", name="Edit view", exact=True).click()
    with page.expect_download() as downloaded:
        page.get_by_role("button", name="Menu", exact=True).click()
        page.get_by_role("menuitem", name="Save project", exact=True).click()
    path = downloaded.value.path()
    page.get_by_role("button", name="Menu", exact=True).click()
    page.get_by_role("menuitem", name="Open project", exact=True).click()
    page.locator("#project-file").set_input_files(path)
    expect(page.locator("#project-dialog")).not_to_be_visible()
    expect(section(page, "Overview").locator(".section-toggle")).to_have_attribute(
        "aria-expanded", "false"
    )
    imported_id = page.locator("#deck-picker").input_value()
    assert imported_id != DECK_ID
    imported = live_app.store.view(imported_id)
    cover = next(slide for slide in imported.slides if slide.id == "cover")
    assert cover.approved and cover.content_hash == original.content_hash
    page.get_by_role("button", name="Section options: Decisions", exact=True).click()
    page.get_by_role("menuitem", name="Remove section", exact=True).click()
    expect(page.locator(".section-title")).to_have_count(1)
    assert len(live_app.store.read(imported_id).slides) == 3


def test_drag_into_a_collapsed_section_and_keyboard_undo(page: Page, live_app: RunningApp) -> None:
    """Move a slide into a named group and undo both ordering and membership."""
    _ = live_app.store.add_slide(
        DECK_ID, Slide(id="closing", title="Closing", html=markup("Closing"))
    )
    snapshot = live_app.get(f"/api/decks/{DECK_ID}")
    _ = live_app.post(
        f"/api/decks/{DECK_ID}/sections",
        {
            "slide_ids": ["cover", "next", "closing"],
            "expected_slide_ids": ["cover", "next", "closing"],
            "expected_sections_hash": snapshot["sections_hash"],
            "sections": [
                {"id": "intro", "title": "Introduction", "slide_ids": ["cover", "next"]},
                {"id": "closing", "title": "Closing", "slide_ids": ["closing"]},
            ],
        },
    )
    open_editor(page, live_app)
    page.locator('.slide-thumb[data-id="next"]').click()
    page.keyboard.press("Control+ArrowDown")
    expect(section(page, "Closing").locator(".slide-thumb")).to_have_count(2)
    page.keyboard.press("Control+z")
    expect(section(page, "Introduction").locator(".slide-thumb")).to_have_count(2)

    section(page, "Closing").locator(".section-toggle").click()
    source = page.locator('.slide-thumb[data-id="next"]').bounding_box()
    target = section(page, "Closing").locator(".section-header").bounding_box()
    assert source and target
    page.mouse.move(source["x"] + source["width"] / 2, source["y"] + source["height"] / 2)
    page.mouse.down()
    page.mouse.move(target["x"] + 35, target["y"] + 15, steps=14)
    page.mouse.up()
    expect(section(page, "Closing").locator(".slide-thumb")).to_have_count(2)
    expect(section(page, "Closing").locator(".section-toggle")).to_have_attribute(
        "aria-expanded", "true"
    )
    page.keyboard.press("Control+z")
    expect(section(page, "Introduction").locator(".slide-thumb")).to_have_count(2)


def test_section_boundary_and_new_slide_in_collapsed_group(
    page: Page, live_app: RunningApp, artifacts: Path
) -> None:
    """The contextual action is keyboard accessible and new slides are revealed."""
    open_editor(page, live_app)
    add_section(page, "Opening")
    rename = page.get_by_role("button", name="Rename section: Opening", exact=True)
    rename.focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("dialog", name="Rename section")).to_be_visible()
    page.get_by_role("textbox", name="Section name").fill("Introduction")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(section(page, "Introduction")).to_be_visible()
    section(page, "Introduction").locator(".section-toggle").click()
    page.get_by_role("button", name="New slide", exact=True).click()
    expect(section(page, "Introduction").locator(".section-toggle")).to_have_attribute(
        "aria-expanded", "true"
    )
    expect(page.locator('.slide-thumb[aria-current="true"]')).to_be_visible()
    _ = page.screenshot(path=str(artifacts / "revealed-slide.png"))
    page.locator('.slide-thumb[aria-current="true"]').press("Delete")
    expect(page.locator(".slide-thumb")).to_have_count(2)
    section(page, "Introduction").locator(".section-toggle").click()
    page.get_by_role("button", name="Deleted slides", exact=True).click()
    page.get_by_role("button", name="Restore", exact=True).click()
    expect(section(page, "Introduction").locator(".section-toggle")).to_have_attribute(
        "aria-expanded", "true"
    )
    expect(page.locator('.slide-thumb[aria-current="true"]')).to_be_visible()


def test_sorter_arrows_follow_visible_rows(
    page: Page, live_app: RunningApp, artifacts: Path
) -> None:
    """Vertical keys cross uneven section rows at the nearest column."""
    for index in range(4):
        _ = live_app.store.add_slide(
            DECK_ID, Slide(id=f"extra-{index}", title=f"Extra {index}", html=markup("Extra"))
        )
    snapshot = live_app.get(f"/api/decks/{DECK_ID}")
    ids = ["cover", "next", "extra-0", "extra-1", "extra-2", "extra-3"]
    _ = live_app.post(
        f"/api/decks/{DECK_ID}/sections",
        {
            "slide_ids": ids,
            "expected_slide_ids": ids,
            "expected_sections_hash": snapshot["sections_hash"],
            "sections": [
                {"id": "one", "title": "First", "slide_ids": ids[:1]},
                {"id": "empty", "title": "Empty", "slide_ids": []},
                {"id": "two", "title": "Second", "slide_ids": ids[1:]},
            ],
        },
    )
    open_editor(page, live_app)
    page.get_by_role("button", name="Slide sorter", exact=True).click()
    first = page.locator('.slide-thumb[data-id="cover"]')
    second = page.locator('.slide-thumb[data-id="next"]')
    first.focus()
    page.keyboard.press("ArrowDown")
    expect(second).to_be_focused()
    page.keyboard.press("ArrowRight")
    expect(page.locator('.slide-thumb[data-id="extra-0"]')).to_be_focused()
    page.keyboard.press("ArrowUp")
    expect(first).to_be_focused()
    _ = page.screenshot(path=str(artifacts / "sorter-rows.png"))
