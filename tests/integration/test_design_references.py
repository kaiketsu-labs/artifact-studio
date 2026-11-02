"""Browser journeys for reusable, design-scoped PowerPoint references."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.design_factory import make_design, powerpoint_source
from tests.integration.support import DECK_ID, RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def open_editor(page: Page, live_app: RunningApp) -> None:
    """Open the fictional presentation in the real application."""
    _ = page.goto(live_app.url + "/?deck=" + DECK_ID, wait_until="networkidle")
    expect(page.frame_locator("#preview").get_by_role("heading", level=1)).to_have_text(
        "Review together"
    )


def upload_master(page: Page, filename: str, data: bytes) -> None:
    """Save a fictional PowerPoint through the reference dialog."""
    page.locator("#design-reference-input").set_input_files(
        {
            "name": filename,
            "mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "buffer": data,
        }
    )
    page.get_by_role("button", name="Save references").click()
    expect(page.locator("#design-reference-status")).to_have_text("References saved.")
    expect(page.locator("#design-reference-list").get_by_text(filename)).to_be_visible()


@pytest.mark.parametrize("width,height", [(1280, 800), (1600, 1000)])
def test_new_presentation_design_reference(
    page: Page, live_app: RunningApp, artifacts: Path, width: int, height: int
) -> None:
    """Keep the draft, associate the file with the selected design, and expose it to agents."""
    make_design(live_app.store.root / "2026-10-29-second-design", "second-brand")
    page.set_viewport_size({"width": width, "height": height})
    open_editor(page, live_app)
    page.get_by_role("button", name="Menu", exact=True).click()
    page.get_by_role("menuitem", name="New presentation").click()
    page.locator("#new-title").fill("A presentation in progress")
    page.locator("#new-brief").fill("A brief that stays in place")
    page.locator('input[name="design"][value="second-brand"]').check()
    _ = page.screenshot(path=str(artifacts / "new-dialog.png"))
    page.get_by_role("button", name="Manage selected design references").click()
    expect(page.locator("#design-references-name")).to_have_text("second-brand")
    filename = "Corporate master slides.pptx"
    data = powerpoint_source()
    upload_master(page, filename, data)
    _ = page.screenshot(path=str(artifacts / "master-reference.png"))
    link = page.locator("#design-reference-list").get_by_role("link", name=filename)
    response = page.request.get(live_app.url + (link.get_attribute("href") or ""))
    assert response.ok and response.body() == data
    page.get_by_role("dialog", name="Design references").get_by_role("button", name="Close").click()
    expect(page.locator("#new-title")).to_have_value("A presentation in progress")
    expect(page.locator("#new-brief")).to_have_value("A brief that stays in place")
    page.get_by_role("button", name="Manage selected design references").click()
    expect(page.locator("#design-reference-list").get_by_text(filename)).to_be_visible()
    context = live_app.call("get_working_context", design_id="second-brand")
    assert context["selected_design_id"] == "second-brand"
    assert context["design_status"] == "available"
    assert filename in str(context["design_references"])
    assert filename not in str(
        live_app.call("get_working_context", deck_id=DECK_ID)["design_references"]
    )


def test_active_presentation_design_references(page: Page, live_app: RunningApp) -> None:
    """The workspace menu opens the active deck's design and accepts a corrected selection."""
    open_editor(page, live_app)
    page.get_by_role("button", name="Menu", exact=True).click()
    page.get_by_role("menuitem", name="Design references").click()
    expect(page.locator("#design-references-name")).to_have_text("test-brand")
    page.locator("#design-reference-input").set_input_files(
        [
            {"name": f"notes-{index}.txt", "mimeType": "text/plain", "buffer": b"reference"}
            for index in range(6)
        ]
    )
    expect(page.locator("#design-reference-error")).to_contain_text("up to 5 files")
    expect(page.get_by_role("button", name="Save references")).to_be_disabled()
    data = powerpoint_source("Another fictional master")
    upload_master(page, "Team master.pptx", data)
    context = live_app.call("get_working_context", deck_id=DECK_ID)
    assert context["selected_design_id"] == "test-brand"
    assert "Team master.pptx" in str(context["design_references"])
    page.get_by_role("dialog", name="Design references").get_by_role("button", name="Close").click()
    page.get_by_role("button", name="Menu", exact=True).click()
    page.get_by_role("menuitem", name="Design references").click()
    link = page.locator("#design-reference-list").get_by_role("link", name="Team master.pptx")
    expect(link).to_be_visible()
    response = page.request.get(live_app.url + (link.get_attribute("href") or ""))
    assert response.ok and response.body() == data


def test_close_pending_load_retry_and_preserve_selection(
    browser: Browser, live_app: RunningApp
) -> None:
    """A slow or failed load cannot trap the user or discard an unsaved file."""
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    pattern = "**/api/designs/test-brand/references"
    try:
        open_editor(page, live_app)
        page.evaluate("""() => {
          const original = window.fetch.bind(window); let first = true;
          window.fetch = (url, options) => {
            if (first && String(url).includes('/api/designs/test-brand/references')) {
              first = false;
              return new Promise(resolve => { window.resolveOldReferences = resolve; });
            }
            return original(url, options);
          };
        }""")
        page.get_by_role("button", name="Menu", exact=True).click()
        page.get_by_role("menuitem", name="Design references").click()
        expect(page.locator("#design-reference-status")).to_have_text("Loading…")
        expect(page.locator("#design-reference-list")).not_to_contain_text("No reference files")
        page.keyboard.press("Escape")
        expect(page.locator("#design-references-dialog")).not_to_be_visible()
        page.get_by_role("button", name="Menu", exact=True).click()
        page.get_by_role("menuitem", name="Design references").click()
        expect(page.locator("#design-reference-list")).to_contain_text("Master.pptx")
        page.evaluate("""() => window.resolveOldReferences(new Response(
          JSON.stringify([{name: 'Stale.pptx', url: '/stale'}]),
          {status: 200, headers: {'Content-Type': 'application/json'}}))""")
        expect(page.locator("#design-reference-list")).not_to_contain_text("Stale.pptx")
        page.locator("#design-reference-input").set_input_files(
            {
                "name": "Pending.pptx",
                "mimeType": "application/octet-stream",
                "buffer": powerpoint_source(),
            }
        )
        assert not page.evaluate("dispatchEvent(new Event('beforeunload', {cancelable: true}))")
        page.get_by_role("dialog", name="Design references").get_by_role(
            "button", name="Close"
        ).click()
        page.route(pattern, lambda route: route.fulfill(status=503, json={"detail": "Try again"}))
        page.get_by_role("button", name="Menu", exact=True).click()
        page.get_by_role("menuitem", name="Design references").click()
        expect(page.locator("#design-reference-error")).to_have_text("Try again")
        expect(page.locator("#design-reference-list")).not_to_contain_text("No reference files")
        expect(page.locator("#design-reference-pending")).to_contain_text("Pending.pptx")
        page.get_by_role("button", name="Clear selection").click()
        expect(page.locator("#design-reference-pending")).to_be_empty()
        assert page.evaluate("dispatchEvent(new Event('beforeunload', {cancelable: true}))")
        page.unroute(pattern)
        page.get_by_role("button", name="Retry loading references").click()
        expect(page.locator("#design-reference-list")).to_contain_text("Master.pptx")
        page.locator("#design-reference-input").set_input_files(
            {
                "name": "Pending.pptx",
                "mimeType": "application/octet-stream",
                "buffer": powerpoint_source(),
            }
        )
        page.get_by_role("button", name="Save references").click()
        expect(page.locator("#design-reference-status")).to_have_text("References saved.")
        expect(page.locator("#design-reference-list")).to_contain_text("Pending.pptx")
        assert page.evaluate("dispatchEvent(new Event('beforeunload', {cancelable: true}))")
    finally:
        context.close()


def test_close_during_save_keeps_result_with_design(page: Page, live_app: RunningApp) -> None:
    """A save finishing after close updates the design when the dialog reopens."""
    open_editor(page, live_app)
    page.evaluate("""() => {
      const original = window.fetch.bind(window);
      window.fetch = (url, options) => {
        if (String(url).includes('/api/designs/test-brand/references') &&
            options?.method === 'POST') {
          return new Promise((resolve, reject) => {
            window.finishReferenceSave = () => original(url, options).then(resolve, reject);
          });
        }
        return original(url, options);
      };
    }""")
    page.get_by_role("button", name="Menu", exact=True).click()
    page.get_by_role("menuitem", name="Design references").click()
    expect(page.locator("#design-reference-list")).to_contain_text("Master.pptx")
    page.locator("#design-reference-input").set_input_files(
        {
            "name": "In flight.pptx",
            "mimeType": "application/octet-stream",
            "buffer": powerpoint_source(),
        }
    )
    page.get_by_role("button", name="Save references").click()
    expect(page.locator("#design-reference-status")).to_have_text("Saving…")
    page.get_by_role("dialog", name="Design references").get_by_role("button", name="Close").click()
    page.get_by_role("button", name="Menu", exact=True).click()
    page.get_by_role("menuitem", name="Design references").click()
    expect(page.locator("#design-reference-status")).to_have_text("Saving…")
    page.evaluate("() => window.finishReferenceSave()")
    expect(page.locator("#design-reference-status")).to_have_text("References saved.")
    expect(page.locator("#design-reference-list")).to_contain_text("In flight.pptx")


def test_attach_other_files_and_remove_from_agent_context(
    page: Page, live_app: RunningApp, artifacts: Path
) -> None:
    """Attach documents and revoke one without losing unrelated sources or saved drafts."""
    open_editor(page, live_app)
    page.get_by_role("button", name="Menu", exact=True).click()
    page.get_by_role("menuitem", name="Design references").click()
    page.locator("#design-reference-input").set_input_files(
        [
            {
                "name": "Brand guide.pdf",
                "mimeType": "application/pdf",
                "buffer": b"%PDF-1.7 Fictional guide",
            },
            {
                "name": "Audience notes.md",
                "mimeType": "text/markdown",
                "buffer": b"For technical colleagues.",
            },
        ]
    )
    page.get_by_role("button", name="Save references").click()
    expect(page.locator("#design-reference-status")).to_have_text("References saved.")
    expect(page.get_by_role("link", name="Brand guide.pdf", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="Audience notes.md", exact=True)).to_be_visible()
    assert "Brand guide.pdf" in str(
        live_app.call("get_working_context", deck_id=DECK_ID)["design_references"]
    )
    page.set_viewport_size({"width": 1280, "height": 800})
    remove = page.get_by_role(
        "button", name="Remove Brand guide.pdf from design context", exact=True
    )
    expect(remove).to_be_in_viewport()
    _ = page.screenshot(path=str(artifacts / "reference-files.png"), full_page=True)
    remove.click()
    expect(page.locator("#design-reference-status")).to_have_text(
        "Reference removed from future agent context."
    )
    expect(page.get_by_role("link", name="Brand guide.pdf", exact=True)).to_have_count(0)
    expect(page.get_by_role("link", name="Audience notes.md", exact=True)).to_be_visible()
    context = live_app.call("get_working_context", deck_id=DECK_ID, detail="full")
    assert "Brand guide.pdf" not in str(context)
    assert "Audience notes.md" in str(context["design_references"])
    _ = page.screenshot(path=str(artifacts / "reference-removed.png"), full_page=True)
    page.get_by_role("dialog", name="Design references").get_by_role("button", name="Close").click()
    page.get_by_role("button", name="Menu", exact=True).click()
    page.get_by_role("menuitem", name="Design references").click()
    expect(page.get_by_role("link", name="Brand guide.pdf", exact=True)).to_have_count(0)
    expect(page.get_by_role("link", name="Audience notes.md", exact=True)).to_be_visible()
