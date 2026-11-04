"""Fresh MCP authoring sessions can style, bundle and execute custom slide documents."""

import os
from html import escape
from pathlib import Path
from typing import Literal

import pytest
from PIL import Image
from playwright.sync_api import Page, expect

from tests.integration.support import DECK_ID, PRIVATE_NOTE, RunningApp
from tests.integration.test_browser import open_editor

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def import_image(app: RunningApp) -> dict[str, object]:
    """Simulate an agent's downloaded image without sending its encoded bytes to MCP."""
    listing = app.call("list_presentation_assets", deck_id=DECK_ID)
    source = Path(str(listing["import_directory"])) / "background.png"
    source.parent.mkdir(exist_ok=True)
    Image.new("RGB", (32, 32), "#e1edfc").save(source)
    return app.call(
        "import_presentation_asset",
        deck_id=DECK_ID,
        source_path=str(source),
        asset_path="assets/authoring/background.png",
    )


@pytest.mark.parametrize("appearance,width", [("light", 1600), ("dark", 800)])
def test_fresh_session_theme_assets_scoped_css_and_selection(
    page: Page,
    live_app: RunningApp,
    artifacts: Path,
    appearance: Literal["light", "dark"],
    width: int,
) -> None:
    """Exercise actual MCP tools, CSS containment, multi-selection and portable presentation."""
    context = live_app.call("get_working_context", deck_id=DECK_ID, include_discovery=False)
    capabilities = context["authoring_capabilities"]
    assert (
        isinstance(capabilities, dict) and capabilities["interactive_field"] == "interactive_html"
    )
    asset = import_image(live_app)
    assert "base64" not in str(asset)
    theme = live_app.call("get_presentation_theme", deck_id=DECK_ID)
    assert theme["css"] == ""
    listing = live_app.call("list_presentation_assets", deck_id=DECK_ID)
    stylesheet = Path(str(listing["import_directory"])) / "imported.css"
    _ = stylesheet.write_text(".slide{--authoring-import:ready}")
    _ = live_app.call(
        "import_presentation_asset",
        deck_id=DECK_ID,
        source_path=str(stylesheet),
        asset_path="assets/authoring/imported.css",
    )
    _ = live_app.call(
        "update_presentation_theme",
        deck_id=DECK_ID,
        css='@import "assets/authoring/imported.css" layer(brand) supports(display:grid) screen;'
        ".slide{font-family:Arial,sans-serif}",
        expected_hash=theme["expected_hash"],
    )
    original = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    html = (
        '<article class="slide" style="padding:7cqw"><style>'
        "@media(min-width:0px){.slide{background:url(assets/authoring/background.png);"
        "background-size:cover}}"
        "h1{color:rgb(19,75,155);font-size:4cqw}p{font-size:2cqw}"
        ".deck-toolbar,textarea{display:none!important}"
        "</style><h1>Review together</h1><p>Create, review and present.</p></article>"
    )
    written = live_app.call(
        "revise_slide",
        deck_id=DECK_ID,
        slide_id="cover",
        expected_hash=original["expected_hash"],
        request_id="fresh-session-style",
        patch={"html": html},
    )
    assert "html" not in written and original["notes"] == PRIVATE_NOTE
    page.emulate_media(color_scheme=appearance)
    page.set_viewport_size({"width": width, "height": 1000})
    open_editor(page, live_app)
    frame = page.frame_locator("#preview")
    heading = frame.get_by_role("heading", level=1)
    expect(heading).to_have_css("color", "rgb(19, 75, 155)")
    expect(frame.locator(".slide")).to_have_css("--authoring-import", "ready")
    expect(frame.locator(".slide")).to_have_css(
        "background-image",
        f'url("{live_app.url}/decks/{DECK_ID}/assets/authoring/background.png")',
    )
    heading.click()
    frame.get_by_text("Create, review and present.").click(modifiers=["Shift"])
    if page.get_by_role("button", name="Open chat", exact=True).is_visible():
        page.get_by_role("button", name="Open chat", exact=True).click()
    expect(page.locator("#target-members")).to_be_visible()
    _ = page.screenshot(path=str(artifacts / f"local-styles-{appearance}.png"))
    _ = page.goto(live_app.url + f"/api/decks/{DECK_ID}/audience")
    expect(page.locator("#stage h1")).to_have_css("color", "rgb(19, 75, 155)")
    expect(page.locator("#stage .slide")).to_have_css("--authoring-import", "ready")
    # A slide-local rule aimed at the host's controls cannot hide them.
    expect(page.locator("#single-next")).to_be_visible()
    page.locator("#show-presenter").click()
    expect(page.locator("#presenter-notes")).to_be_visible()
    assert page.locator("#presenter-notes").input_value() == ""
    expect(page.locator("#presenter-next h1")).not_to_have_css("color", "rgb(19, 75, 155)")
    assert live_app.store.read(DECK_ID).slides[0].notes == PRIVATE_NOTE
    _ = page.screenshot(path=str(artifacts / f"scoped-presenter-{appearance}.png"))


def test_scripts_styles_nested_iframe_and_exports(
    page: Page, live_app: RunningApp, artifacts: Path
) -> None:
    """Author a functioning nested widget, click it in Play/audience, and verify isolation."""
    _ = import_image(live_app)
    listing = live_app.call("list_presentation_assets", deck_id=DECK_ID)
    script = Path(str(listing["import_directory"])) / "widget.js"
    _ = script.write_text(
        "document.querySelector('h2').textContent='Embedded ready';"
        "document.querySelector('button').onclick=()=>{"
        "document.querySelector('h2').textContent='Clicked & working';};"
        "try{top.document.body.dataset.escaped='yes'}catch{document.body.dataset.isolated='yes'};"
    )
    _ = live_app.call(
        "import_presentation_asset",
        deck_id=DECK_ID,
        source_path=str(script),
        asset_path="assets/authoring/widget.js",
    )
    child = (
        "<style>body{background:url(assets/authoring/background.png);font:24px Arial}</style>"
        "<h2>Waiting</h2><button>Change state</button>"
        '<script src="assets/authoring/widget.js"></script>'
    )
    custom = (
        "<style>body{background:#194b99;color:white;font:24px Arial}"
        "iframe{width:90%;height:60%}</style>"
        '<h1>Custom document</h1><iframe id="child" srcdoc="'
        + escape(child, quote=True)
        + '"></iframe>'
    )
    _ = (live_app.project / "slide-document.html").write_text(custom)
    original = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    receipt = live_app.call(
        "revise_slide",
        deck_id=DECK_ID,
        slide_id="cover",
        expected_hash=original["expected_hash"],
        request_id="fresh-custom-widget",
        patch={"interactive_html": custom},
        embed_assets=True,
    )
    assert "interactive_html" not in receipt and "base64" not in str(receipt)
    open_editor(page, live_app)
    page.locator("#play-animations").click()
    custom_frame = page.frame_locator(".animation-preview > iframe").frame_locator("iframe")
    embedded = custom_frame.frame_locator("#child")
    expect(embedded.get_by_role("heading", level=2)).to_have_text("Embedded ready")
    expect(embedded.locator("body")).to_have_attribute("data-isolated", "yes")
    embedded.get_by_role("button", name="Change state").click()
    expect(embedded.get_by_role("heading", level=2)).to_have_text("Clicked & working")
    _ = page.screenshot(path=str(artifacts / "nested-iframe-play.png"))
    page.locator("#play-animations").click()
    expect(page.frame_locator("#preview").get_by_role("heading", level=1)).to_have_text(
        "Review together"
    )
    _ = page.goto(live_app.url + f"/api/decks/{DECK_ID}/audience")
    audience = page.frame_locator("#stage .runtime-slide-canvas > iframe").frame_locator("iframe")
    expect(audience.frame_locator("#child").get_by_role("heading", level=2)).to_have_text(
        "Embedded ready"
    )
    assert PRIVATE_NOTE not in page.content()
    _ = page.goto(live_app.url + f"/api/decks/{DECK_ID}/exports/pdf?preview=1")
    page.wait_for_function("document.documentElement.dataset.printReady === 'true'")
    expect(page.locator(".print-page").first).to_be_visible()
    assert PRIVATE_NOTE not in page.content()
