"""Keep original PowerPoint references usable across all design upload surfaces."""

import hashlib
import os
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import pytest
from playwright.sync_api import Page, expect

from tests.design_factory import powerpoint_source
from tests.integration.support import DECK_ID, RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


@pytest.mark.parametrize("surface", ["editor", "library", "create"])
def test_large_powerpoint_upload_preserves_original(
    page: Page, live_app: RunningApp, tmp_path: Path, surface: str
) -> None:
    """A 31.8 MB source uploads without ZIP-import workarounds and downloads intact."""
    original = tmp_path / "Fictional master.pptx"
    _ = original.write_bytes(powerpoint_source())
    with ZipFile(original, "a", compression=ZIP_STORED) as archive:
        archive.writestr("ppt/media/fictional-source.bin", b"Fictional media.\n" * 1_870_588)
    assert 31_000_000 < original.stat().st_size < 32_000_000
    expected_hash = hashlib.sha256(original.read_bytes()).hexdigest()

    if surface == "editor":
        _ = page.goto(live_app.url + "/?deck=" + DECK_ID)
        page.get_by_role("button", name="Menu", exact=True).click()
        page.get_by_role("menuitem", name="Design references", exact=True).click()
        expect(page.locator(".design-reference-hint")).to_contain_text("50 MB each")
        page.locator("#design-reference-input").set_input_files(original)
        expect(page.locator("#design-reference-error")).to_be_empty()
        # Wait for the actual transfer; a 31.8 MB source is larger than ordinary UI actions.
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/designs/test-brand/references")
                and response.request.method == "POST"
            ),
            timeout=60_000,
        ) as saved:
            page.get_by_role("button", name="Save references", exact=True).click()
        assert saved.value.ok
        expect(page.locator("#design-reference-status")).to_have_text("References saved.")
        link = page.locator("#design-reference-list").get_by_role(
            "link", name=original.name, exact=True
        )
    else:
        route = "/?view=designs" + ("&design=test-brand" if surface == "library" else "")
        _ = page.goto(live_app.url + route)
        action = "Add files" if surface == "library" else "Add design"
        page.get_by_role("button", name=action, exact=True).click()
        dialog = page.get_by_role(
            "dialog", name="Add reference files" if surface == "library" else action
        )
        if surface == "create":
            dialog.get_by_role("textbox", name="Name", exact=True).fill("Fictional large source")
        expect(dialog.locator(".design-upload-limits")).to_contain_text("50 MB each")
        page.locator("#design-library-input").set_input_files(original)
        expect(page.locator("#design-library-error")).to_be_empty()
        endpoint = "/api/design-library" + ("/test-brand/files" if surface == "library" else "")
        with page.expect_response(
            lambda response: response.url.endswith(endpoint) and response.request.method == "POST",
            timeout=60_000,
        ) as saved:
            dialog.get_by_role("button", name=action, exact=True).click()
        assert saved.value.ok
        expect(dialog).not_to_be_visible()
        link = page.locator(".design-file-group").get_by_role("link", name=original.name)

    expect(link).to_be_visible()
    response = page.request.get(live_app.url + (link.get_attribute("href") or ""))
    assert response.ok
    assert hashlib.sha256(response.body()).hexdigest() == expected_hash
