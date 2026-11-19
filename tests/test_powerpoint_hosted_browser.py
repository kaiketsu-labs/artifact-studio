"""Hosted PowerPoint import and shared access in a real browser."""

import os
from pathlib import Path
from zipfile import ZipFile

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.integration.multiplayer_fixture import (
    routed_multiplayer_context,
    running_multiplayer_app,
)
from tests.pptx_fixture import fictional_powerpoint
from tests.test_multiplayer_server import accept, grant, human

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_hosted_import_shared_project(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner_api,
        human(app, "alice") as editor_api,
        human(app, "bob") as viewer_api,
        sync_playwright() as playwright,
    ):
        accept(editor_api, grant(owner_api, "alice", app.project_id, "edit", "project"))
        accept(viewer_api, grant(owner_api, "bob", app.project_id, "view", "project"))
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app) as owner:
                page = owner.new_page()
                page.goto(f"https://studio.example.test/?project={app.project_id}")
                page.get_by_role("button", name="Import", exact=True).click()
                dialog = page.get_by_role("dialog", name="Import artifact")
                dialog.get_by_label("Title", exact=True).fill("Garden plan")
                page.locator("#hub-import-files").set_input_files(
                    {
                        "name": "garden.pptx",
                        "mimeType": "application/vnd.openxmlformats-officedocument."
                        "presentationml.presentation",
                        "buffer": fictional_powerpoint(),
                    }
                )
                dialog.get_by_role("button", name="Import presentation").click()
                expect(page.frame_locator("#preview").get_by_text("Harbor Garden")).to_be_visible(
                    timeout=20000
                )
                url = page.url
                with routed_multiplayer_context(browser, app, "alice") as editor:
                    shared = editor.new_page()
                    shared.goto(url)
                    title = shared.frame_locator("#preview").get_by_text(
                        "Harbor Garden", exact=True
                    )
                    expect(title).to_be_visible()
                    expect(shared.locator("html")).to_have_attribute("data-shared-role", "edit")
                    title.click()
                    shared.locator("#element-properties [data-panel=objects]").click()
                    shared.locator("#edit-element-text").click()
                    title.fill("Harbor Garden revised")
                    with shared.expect_response(
                        lambda response: "/imported-text" in response.url
                    ) as saved:
                        shared.locator("#commit-element-text").click()
                    assert saved.value.status == 200
                    edit_url = saved.value.request.url
                    body = saved.value.request.post_data_json
                    expect(
                        shared.frame_locator("#preview").get_by_text(
                            "Harbor Garden revised", exact=True
                        )
                    ).to_be_visible()
                with routed_multiplayer_context(browser, app, "bob") as viewer:
                    shared = viewer.new_page()
                    shared.goto(url)
                    expect(
                        shared.frame_locator("#preview").get_by_text(
                            "Harbor Garden revised", exact=True
                        )
                    ).to_be_visible()
                    expect(shared.locator("html")).to_have_attribute("data-shared-role", "view")
                    shared.get_by_role("button", name="Menu", exact=True).click()
                    shared.get_by_role("menuitem", name="Export presentation…").click()
                    with shared.expect_download() as transfer:
                        shared.get_by_role("button", name="Export PowerPoint", exact=True).click()
                    archive = transfer.value.path()
                    assert archive is not None and transfer.value.suggested_filename.endswith(
                        ".pptx"
                    )
                    with ZipFile(archive) as pptx:
                        drawing = pptx.read("ppt/slides/slide1.xml")
                        assert b"Harbor Garden revised" in drawing
                        assert not any(
                            name.startswith("ppt/notesSlides/") for name in pptx.namelist()
                        )
                    status = shared.evaluate(
                        """async ({url, body}) => (await fetch(url, {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({...body, request_id: 'viewer-attempt',
                                              text: 'Denied'})
                    })).status""",
                        {"url": edit_url, "body": body},
                    )
                    assert status in (403, 404)
                    assert (
                        "Harbor Garden revised"
                        in app.store.view(edit_url.split("/api/decks/")[1].split("/")[0])
                        .slides[0]
                        .html
                    )
        finally:
            browser.close()
