"""Exercise the dedicated Designs overview, file context and recoverable removal."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.integration.support import DECK_ID, RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("SLIDE_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


@pytest.mark.parametrize("width", [1440, 390])
def test_design_overview_add_context_files_delete_and_restore(
    page: Page, live_app: RunningApp, artifacts: Path, width: int
) -> None:
    page.set_viewport_size({"width": width, "height": 1000 if width > 700 else 844})
    _ = page.goto(live_app.url + "/?view=designs")
    expect(page.frame_locator(".design-card-art iframe").locator(".slide")).to_be_visible()
    page.get_by_role("button", name="Open test-brand design", exact=True).click()
    expect(page.get_by_role("heading", name="Context files")).to_be_visible()
    expect(page.locator(".design-file-group")).to_contain_text(
        ["Master.pptx", "README.md", "index.html"]
    )
    expect(page.get_by_role("link", name="Open example slides")).to_be_visible()
    assert "deck=" not in page.url
    page.get_by_role("navigation", name="Breadcrumb").get_by_role(
        "button", name="Designs", exact=True
    ).click()
    page.get_by_role("button", name="Add design", exact=True).click()
    page.get_by_role("textbox", name="Name", exact=True).fill("Editorial system")
    page.get_by_role("textbox", name="Description", exact=False).fill(
        "Quiet typography, clear hierarchy and generous space."
    )
    page.locator("#design-library-input").set_input_files(
        [
            {
                "name": "brand-guide.pdf",
                "mimeType": "application/pdf",
                "buffer": b"Fictional brand guide",
            },
            {
                "name": "palette.csv",
                "mimeType": "text/csv",
                "buffer": b"ink,#142a43\naccent,#426bec",
            },
            {"name": "unused.txt", "mimeType": "text/plain", "buffer": b"Remove before saving"},
            {
                "name": "preview.svg",
                "mimeType": "image/svg+xml",
                "buffer": (
                    b'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200">'
                    b'<rect width="300" height="200" fill="#426bec"/></svg>'
                ),
            },
        ]
    )
    page.get_by_role("button", name="Remove unused.txt from selection").click()
    page.get_by_role("button", name="Create design", exact=True).click()
    expect(page.get_by_role("heading", name="Editorial system", exact=True)).to_be_visible()
    expect(page.locator(".design-file-group")).to_contain_text("brand-guide.pdf")
    expect(page.locator(".design-file-group")).not_to_contain_text("unused.txt")
    expect(page.get_by_role("link", name="Open example slides")).to_have_count(0)
    _ = page.screenshot(path=str(artifacts / "design-context.png"), full_page=True)
    _ = page.reload()
    expect(page.get_by_role("heading", name="Editorial system", exact=True)).to_be_visible()
    page.get_by_role("button", name="Add files", exact=True).click()
    page.locator("#design-library-input").set_input_files(
        {
            "name": "layout.fig",
            "mimeType": "application/octet-stream",
            "buffer": b"Fictional design source",
        }
    )
    page.get_by_role("button", name="Add files", exact=True).last.click()
    expect(page.locator(".design-file-group")).to_contain_text("layout.fig")
    page.get_by_role("button", name="Remove palette.csv from design context").click()
    expect(page.locator(".design-file-group")).not_to_contain_text("palette.csv")
    page.get_by_role("navigation", name="Breadcrumb").get_by_role(
        "button", name="Designs", exact=True
    ).click()
    expect(page.get_by_role("button", name="Open Editorial system design")).to_be_visible()
    image = page.locator("[data-open-design]").filter(has_text="Editorial system").locator("img")
    expect(image).to_be_visible()
    expect(image).to_have_js_property("complete", True)
    assert image.evaluate("image => image.naturalWidth") == 300
    _ = page.screenshot(path=str(artifacts / "design-overview.png"), full_page=True)
    page.get_by_role("button", name="Delete Editorial system design", exact=True).click()
    page.get_by_role("button", name="Delete design", exact=True).click()
    expect(page.get_by_role("button", name="Open Editorial system design")).to_have_count(0)
    page.get_by_role("button", name="Deleted designs", exact=True).click()
    page.get_by_role("button", name="Restore", exact=True).click()
    expect(page.get_by_role("button", name="Open Editorial system design")).to_be_visible()
    assert live_app.store.read(DECK_ID).title == "CI workflow"
