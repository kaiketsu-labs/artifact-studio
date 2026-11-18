"""Direct imported shape edits through the shared slide editor."""

import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from artifact_studio.models import SlidePatch
from tests.integration.multiplayer_fixture import (
    WORKSPACE,
    routed_multiplayer_context,
    running_multiplayer_app,
)
from tests.test_multiplayer_server import accept, grant, human

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_shared_shape_move_and_rotation_persist(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "server", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as editor,
        human(app, "bob") as viewer,
        sync_playwright() as playwright,
    ):
        accept(editor, grant(owner, "alice", app.deck_id, role="edit"))
        accept(viewer, grant(owner, "bob", app.deck_id, role="view"))
        slide = app.store.view(app.deck_id).slides[0]
        markup = (
            '<article class="slide" data-pptx-width="100" data-pptx-height="50">'
            '<div data-pptx-object="Rectangle" style="position:absolute;left:10%;top:20%;'
            'width:30%;height:40%;background:#b8d8cb;"><span>Fictional shape</span>'
            "</div></article>"
        )
        app.store.revise(
            app.deck_id, slide.id, slide.expected_hash, "shape-fixture", SlidePatch(html=markup)
        )
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, "alice") as context:
                page = context.new_page()
                page.goto(f"{WORKSPACE}/?deck={app.deck_id}#{slide.id}")
                shape = page.frame_locator("#preview").locator('[data-pptx-object="Rectangle"]')
                expect(shape).to_be_visible()
                box = shape.bounding_box()
                assert box is not None
                shape.click(position={"x": box["width"] - 5, "y": box["height"] - 5})
                page.get_by_label("Object options").click()
                page.locator("#move-element").focus()
                page.keyboard.press("ArrowRight")
                page.locator("#rotate-element").fill("15")
                with page.expect_response(
                    lambda response: "/imported-transform" in response.url
                ) as saved:
                    page.get_by_label("Save shape change").click()
                assert saved.value.status == 200
                page.reload()
                expect(shape).to_have_attribute("style", re.compile(r"rotate\(15deg\)"))
                style = shape.get_attribute("style") or ""
                left = re.search(r"left:([\d.]+)%", style)
                assert left is not None and float(left.group(1)) == pytest.approx(10.1, abs=0.001)
            with routed_multiplayer_context(browser, app, "bob") as context:
                page = context.new_page()
                page.goto(f"{WORKSPACE}/?deck={app.deck_id}#{slide.id}")
                expect(page.locator("html")).to_have_attribute("data-shared-role", "view")
                status = page.evaluate(
                    """async ({url, body}) => (await fetch(url, {
                    method:'POST', headers:{'Content-Type':'application/json'},
                    body:JSON.stringify({...body, request_id:'viewer-attempt'})})).status""",
                    {"url": saved.value.request.url, "body": saved.value.request.post_data_json},
                )
                assert status in (403, 404)
        finally:
            browser.close()
