"""Exercise hosted Present controls, notes persistence and authored-content isolation."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Browser, expect

from artifact_studio.models import AgentContext
from tests.design_factory import TEMPLATE_ID, make_design
from tests.integration.hosted_fixture import WORKSPACE, routed_hosted_context, running_hosted_app

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_hosted_present_next_back_notes_and_private_audience(
    browser: Browser, tmp_path: Path, artifacts: Path
) -> None:
    with running_hosted_app(tmp_path / "server", artifacts / "server.log") as app:
        make_design(app.store.root / TEMPLATE_ID)
        deck = app.store.read(TEMPLATE_ID)
        deck.slides[0].notes = "PRIVATE_NOTE fictional presenter context"
        deck.slides[0].agent = AgentContext(summary="PRIVATE_AGENT fictional context")
        app.store.write(TEMPLATE_ID, deck)
        with routed_hosted_context(browser, app) as context:
            page = context.new_page()
            page.add_init_script("delete window.getScreenDetails;")
            page.goto(f"{WORKSPACE}/?deck={TEMPLATE_ID}&editor=true")
            expect(page.locator("#present-link")).to_have_attribute(
                "href", f"/decks/{TEMPLATE_ID}/presenter?present=1#cover"
            )
            page.get_by_role("link", name="Present", exact=True).click()
            viewer = page.frame_locator('iframe[title="Presentation"]')
            expect(viewer.locator("#stage h1")).to_have_text("Test")
            viewer.locator("#single-next").click()
            expect(viewer.locator("#stage")).to_contain_text("Test message")
            viewer.locator("#single-previous").click()
            expect(viewer.locator("#stage h1")).to_have_text("Test")
            with page.expect_popup() as opened:
                viewer.get_by_role("button", name="Presenter view", exact=True).click()
            audience = opened.value
            expect(viewer.locator("#presenter-notes")).to_have_value(deck.slides[0].notes)
            expect(audience.locator("#stage h1")).to_have_text("Test")
            assert "PRIVATE_" not in audience.content()
            assert "PRIVATE_" not in (audience.locator("#deck-data").text_content() or "")
            notes = "PRIVATE_EDIT fictional updated notes"
            with page.expect_response(
                lambda response: (
                    response.url.endswith(f"/{TEMPLATE_ID}/slides/cover/notes")
                    and response.request.method == "POST"
                )
            ) as saved:
                viewer.locator("#presenter-notes").fill(notes)
                viewer.get_by_role("button", name="Save notes", exact=True).click()
            assert saved.value.status == 200
            assert app.store.read(TEMPLATE_ID).slides[0].notes == notes
            assert notes not in audience.content()
            viewer.locator("#presenter-next-button").click()
            expect(audience.locator("#stage")).to_contain_text("Test message")
            viewer.locator("#presenter-previous").click()
            expect(audience.locator("#stage h1")).to_have_text("Test")
            page.screenshot(path=str(artifacts / "hosted-presenter.png"))
            audience.screenshot(path=str(artifacts / "hosted-audience.png"))
            viewer.get_by_role("button", name="End", exact=True).click()
            expect(page.locator('iframe[title="Presentation"]')).to_have_count(0)
            assert audience.is_closed()


def test_hosted_presenter_ignores_authored_shell_and_isolates_interactive_code(
    browser: Browser, tmp_path: Path, artifacts: Path
) -> None:
    with running_hosted_app(tmp_path / "server", artifacts / "server.log") as app:
        make_design(app.store.root / TEMPLATE_ID)
        deck = app.store.read(TEMPLATE_ID)
        deck.title = "Fictional </script><script>window.titleInjection=true</script>"
        deck.slides[0].notes = "PRIVATE_NOTE </script><script>window.noteInjection=true</script>"
        deck.slides[0].interactive_html = """<div id="isolation">Waiting</div><script>
        let result;
        try { parent.parent.document.querySelector('#deck-data'); result = 'leaked'; }
        catch { result = 'isolated'; }
        document.querySelector('#isolation').textContent = result;
        fetch('https://studio.example.test/api/session').then(
          () => document.body.dataset.network = 'allowed',
          () => document.body.dataset.network = 'blocked');
        </script>"""
        app.store.write(TEMPLATE_ID, deck)
        folder = app.store.folder(TEMPLATE_ID)
        index = folder / "index.html"
        index.write_text(index.read_text() + "<script>window.authoredShell=true</script>")
        theme = folder / "theme.css"
        theme.write_text(
            theme.read_text() + "/* </style><script>window.themeInjection=true</script> */"
        )
        with routed_hosted_context(browser, app) as context:
            page = context.new_page()
            response = page.goto(f"{WORKSPACE}/decks/{TEMPLATE_ID}/presenter?present=1")
            assert response is not None and response.status == 200
            nested = page.frame_locator("#stage iframe").frame_locator("iframe")
            expect(nested.locator("#isolation")).to_have_text("isolated")
            expect(nested.locator("body")).to_have_attribute("data-network", "blocked")
            assert page.evaluate("""() => [window.titleInjection, window.noteInjection,
                window.themeInjection, window.authoredShell].every(value => value === undefined)""")
            page.screenshot(path=str(artifacts / "hosted-presenter-isolation.png"))
            denied = page.goto(f"{WORKSPACE}/decks/{TEMPLATE_ID}/index.html")
            assert denied is not None and denied.status == 404
