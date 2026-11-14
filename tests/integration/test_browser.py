"""Real-browser user journeys; assertions target behavior rather than screenshots alone."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, Route, expect

from tests.integration.support import DECK_ID, PRIVATE_NOTE, RunningApp, markup

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def open_editor(page: Page, live_app: RunningApp) -> None:
    """Wait for the actual selected slide, including its rendered iframe."""
    _ = page.goto(live_app.url + "/?deck=" + DECK_ID, wait_until="networkidle")
    expect(page.frame_locator("#preview").get_by_role("heading", level=1)).to_have_text(
        "Review together"
    )


def open_notes(page: Page) -> None:
    """Expand editor notes if they are collapsed."""
    if not page.get_by_label("Speaker notes", exact=True).is_visible():
        page.locator("#notes-panel summary").click()


@pytest.mark.parametrize("selected", [True, False])
def test_copied_agent_prompts_use_the_selected_deck_and_loadable_guides(
    page: Page, live_app: RunningApp, selected: bool
) -> None:
    """Copy handoffs from a deck or reach the empty editor through the artifact hub."""
    page.add_init_script(
        "Object.defineProperty(navigator,'clipboard',{value:{"
        "writeText:async text=>{window.copiedAgentPrompt=text;}}});"
    )
    deck_id = "2026-11-01-prompt-check"
    if selected:
        _ = live_app.post(
            "/api/decks",
            {
                "deck_id": deck_id,
                "title": "Prompt check",
                "brief": "Brief for the board",
                "design_id": "test-brand",
            },
        )
    else:

        def empty_decks(route: Route) -> None:
            """Represent a workspace with no decks while keeping its design library."""
            route.fulfill(json=[])

        page.route("**/api/decks", empty_decks)
    _ = page.goto(
        live_app.url + (f"/?deck={deck_id}" if selected else "/"), wait_until="networkidle"
    )
    if not selected:
        expect(page.get_by_role("heading", name="All artifacts", exact=True)).to_be_visible()
        page.get_by_role("button", name="Menu", exact=True).click()
        page.get_by_role("menuitem", name="Presentation editor", exact=True).click()
        expect(page.locator("#slide-title")).to_have_text("No presentations")

    def check_prompt(setup: bool = False) -> str:
        """Check the copied handoff rather than merely searching the frontend source."""
        prompt = str(page.evaluate("window.copiedAgentPrompt"))
        assert 'get_authoring_guide(topic="live_review")' in prompt
        assert "live_review_guide" not in prompt
        assert f"MCP: {live_app.url}/mcp" in prompt
        target = f'deck_id="{deck_id}"' if selected else ""
        context_args = f"{target}, include_discovery=False" if selected else ""
        assert f"get_working_context({context_args})" in prompt
        assert f"get_live_review_status({target})" in prompt
        if selected:
            assert "automatically" in prompt
            assert "Use start_live_review only to resume" in prompt
            assert f"get_presentation_outline({target}, include_context=True)" in prompt
            assert "brief_files" in prompt
            assert f"Workspace: {live_app.url}/?deck={deck_id}" in prompt
            assert "get_presentation(" not in prompt
        else:
            assert "start_live_review()" in prompt
            assert "read_design_requests()" in prompt
            assert f"Workspace: {live_app.url}/ MCP:" in prompt
            if not setup:
                assert "create_presentation" in prompt and "design_id" in prompt
        return prompt

    start_prompt = ""
    if selected:
        page.locator("#copy-start").click()
        expect(page.locator("#toast")).to_contain_text("Prompt copied")
        start_prompt = check_prompt()
    page.get_by_role("button", name="Menu", exact=True).click()
    page.get_by_role("menuitem", name="Agent connection", exact=True).click()
    page.locator("#copy-agent-prompt").click()
    expect(page.locator("#toast")).to_contain_text("Prompt copied")
    prompt = check_prompt()
    if selected:
        assert start_prompt == prompt
    page.locator("#connect-dialog").get_by_text("Automatic feedback", exact=True).click()
    page.locator("#copy-live-setup").click()
    expect(page.locator("#toast")).to_contain_text("Setup prompt copied")
    assert "review_session" in check_prompt(setup=True)


@pytest.mark.parametrize("width,height", [(1280, 800), (1600, 1000)])
def test_readable_editor_and_thumbnails(
    page: Page, live_app: RunningApp, width: int, height: int
) -> None:
    """Keep navigation, composer and slide content usable at common laptop sizes."""
    page.set_viewport_size({"width": width, "height": height})
    open_editor(page, live_app)
    expect(page.get_by_role("button", name="Menu", exact=True)).to_be_in_viewport()
    expect(page.get_by_role("button", name="Send feedback", exact=True)).to_be_in_viewport()
    expect(page.get_by_role("button", name="Next slide", exact=True)).to_be_in_viewport()
    for selector in ("#preview", 'iframe[title="Thumbnail 1"]'):
        heading = page.frame_locator(selector).get_by_role("heading", level=1)
        expect(heading).to_have_text("Review together")
        assert heading.evaluate(
            "element => {const r=element.getBoundingClientRect();"
            "return r.top>=0 && r.left>=0 && r.right<=innerWidth && r.bottom<=innerHeight}"
        )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_feedback_notes_and_guarded_agent_revision(page: Page, live_app: RunningApp) -> None:
    """Deliver UI feedback to MCP and invalidate approval only after a real content edit."""
    open_editor(page, live_app)
    page.get_by_role("button", name="Approve", exact=True).click()
    expect(page.locator("#progress-text")).to_have_attribute("aria-label", "1 of 2 slides approved")
    page.get_by_label("Your feedback", exact=True).fill("Make the title clearer.")
    page.get_by_role("button", name="Send feedback", exact=True).click()
    expect(page.locator("#feedback-list")).to_contain_text("Make the title clearer.")
    feed = live_app.call("read_feedback", deck_id=DECK_ID)
    assert "Make the title clearer." in str(feed["events"])

    open_notes(page)
    expect(page.get_by_label("Speaker notes", exact=True)).to_be_visible()
    page.get_by_label("Speaker notes", exact=True).fill("Human notes preserved by the agent")
    expect(page.locator("#notes-state")).to_have_text("Saved")
    expect(page.locator("#save-notes")).to_be_hidden()
    page.locator("#notes-panel summary").click()
    current = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    result = live_app.call(
        "revise_slide",
        deck_id=DECK_ID,
        slide_id="cover",
        expected_hash=current["expected_hash"],
        request_id="browser-agent-revision",
        patch={"html": markup("A clearer title")},
    )
    assert result["approved"] is False
    _ = page.reload(wait_until="networkidle")
    expect(page.frame_locator("#preview").get_by_role("heading", level=1)).to_have_text(
        "A clearer title"
    )
    assert live_app.store.read(DECK_ID).slides[0].notes == "Human notes preserved by the agent"


def test_keyboard_navigation_and_recovered_draft(page: Page, live_app: RunningApp) -> None:
    """Navigate with keyboard and keep unsent feedback on its original slide after reload."""
    open_editor(page, live_app)
    page.get_by_label("Your feedback", exact=True).fill("Unsent cover draft")
    thumbnails = page.locator(".slide-thumb")
    thumbnails.nth(0).focus()
    page.keyboard.press("ArrowDown")
    expect(page.locator("#slide-title")).to_have_text("Next steps")
    expect(page.get_by_label("Your feedback", exact=True)).to_have_value("")
    thumbnails.nth(1).focus()
    page.keyboard.press("ArrowUp")
    expect(page.get_by_label("Your feedback", exact=True)).to_have_value("Unsent cover draft")
    _ = page.reload(wait_until="networkidle")
    expect(page.get_by_label("Your feedback", exact=True)).to_have_value("Unsent cover draft")
    assert not live_app.call("read_feedback", deck_id=DECK_ID)["events"]
    page.keyboard.press("F10")
    expect(page.get_by_role("menu", name="Artifact Studio", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("button", name="Menu", exact=True)).to_be_focused()


def test_sorter_and_presentation_keep_notes_private(
    page: Page, live_app: RunningApp, artifacts: Path
) -> None:
    """Present directly, advance, exit to the selected slide and exclude private notes."""
    open_editor(page, live_app)
    page.get_by_role("button", name="Slide sorter", exact=True).click()
    expect(page.get_by_role("button", name="Slide sorter", exact=True)).to_have_attribute(
        "aria-pressed", "true"
    )
    _ = page.screenshot(path=str(artifacts / "sorter.png"), full_page=True)
    page.get_by_role("button", name="Edit view", exact=True).click()
    page.get_by_role("link", name="Present", exact=True).click()
    audience = page.frame_locator('iframe[title="Presentation"]')
    expect(audience.locator("#stage h1")).to_have_text("Review together")
    assert PRIVATE_NOTE not in audience.locator("body").inner_text()
    expect(audience.locator("textarea:visible")).to_have_count(0)
    exported = page.request.get(live_app.url + f"/api/decks/{DECK_ID}/exports/html")
    assert exported.ok
    assert PRIVATE_NOTE not in exported.text()
    _ = page.screenshot(path=str(artifacts / "presentation.png"), full_page=True)
    audience.locator("body").press("ArrowRight")
    expect(audience.locator("#stage h1")).to_have_text("Present with confidence")
    audience.locator("body").press("Escape")
    expect(page.locator(".workspace-presentation")).to_have_count(0)
    expect(page.locator("#slide-title")).to_have_text("Next steps")
