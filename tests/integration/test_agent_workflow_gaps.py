"""Real MCP design authoring and synchronization preserve human review context."""

import os
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import Page, expect

from tests.integration.support import PRIVATE_NOTE, RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]

DESIGN = "workflow-brand"
DECK = "2026-11-04-design-sync"
SIBLING = "2026-11-04-unsynced-sibling"


def theme(app: RunningApp, color: str) -> None:
    """Edit the reusable brand using current file metadata and no filesystem bundle setup."""
    current = app.call("get_design_file", design_id=DESIGN, path="theme.css")
    receipt = app.call(
        "write_design_file",
        design_id=DESIGN,
        path="theme.css",
        expected_hash=current["expected_hash"],
        text=f".slide{{background:#f5f7fb;padding:6cqw}}h1{{color:{color};font-size:5cqw}}"
        ".brand-image{width:16cqw;height:16cqw}",
    )
    assert "text" not in receipt


def test_create_edit_sync_design_through_mcp_and_render(
    page: Page, live_app: RunningApp, artifacts: Path
) -> None:
    """Simulate source authoring, guarded reuse, independent deck overrides and human review."""
    pack = live_app.call(
        "create_design_pack",
        design_id=DESIGN,
        name="Fictional workflow brand",
        description="A disposable blue design for a real MCP journey.",
    )
    assert pack["design_id"] == DESIGN
    theme(live_app, "#194b99")
    preferences = live_app.call("get_design_file", design_id=DESIGN, path="PREFERENCES.md")
    _ = live_app.call(
        "write_design_file",
        design_id=DESIGN,
        path="PREFERENCES.md",
        text="Use generous spacing and concrete headlines.",
        expected_hash=preferences["expected_hash"],
    )
    source = Path(str(pack["import_directory"])) / "brand.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 40), "#194b99").save(source)
    asset = live_app.call(
        "import_design_asset",
        design_id=DESIGN,
        source_path=str(source),
        asset_path="assets/brand.png",
    )
    assert "base64" not in str(asset)
    for identifier in (DECK, SIBLING):
        _ = live_app.call(
            "create_presentation",
            deck_id=identifier,
            design_id=DESIGN,
            title="Fictional release plan",
            brief="Explain the launch sequence to the fictional product team.",
            request_id=f"create-{identifier}",
        )
    context = live_app.call("get_working_context", deck_id=DECK)
    assert context["discovery_included"] is False
    assert context["selected_design_id"] == DESIGN
    assert context["preferences"] == "Use generous spacing and concrete headlines."
    brief = live_app.call("get_presentation_outline", deck_id=DECK, include_context=True)
    assert "Explain the launch sequence" in str(brief["context_markdown"])
    slide = live_app.call("get_slide", deck_id=DECK, slide_id="cover")
    _ = live_app.call(
        "revise_slide",
        deck_id=DECK,
        slide_id="cover",
        expected_hash=slide["expected_hash"],
        request_id="design-sync-cover",
        patch={
            "html": '<article class="slide"><h1>Ready for launch</h1>'
            '<img class="brand-image" src="assets/brand.png" alt="Brand mark"></article>',
            "notes": PRIVATE_NOTE,
        },
    )
    _ = page.goto(live_app.url + f"/?deck={DECK}")
    heading = page.frame_locator("#preview").get_by_role("heading", level=1)
    expect(heading).to_have_css("color", "rgb(25, 75, 153)")
    page.get_by_role("button", name="Approve", exact=True).click()
    heading.click()
    page.get_by_label("Your feedback", exact=True).fill("Keep this original design review.")
    page.get_by_role("button", name="Send feedback", exact=True).click()
    expect(page.locator("#feedback-list")).to_contain_text("Keep this original design review.")
    original = live_app.call("get_slide", deck_id=DECK, slide_id="cover", include_feedback=True)
    assert original["approved"] is True
    _ = live_app.store.set_mode(DECK, "batch")
    _ = live_app.store.add_feedback(
        DECK,
        "cover",
        "WITHHELD_WORKFLOW_NOTE",
        str(original["content_hash"]),
        "withheld-sync-feedback",
    )
    source_before = live_app.store.read(DECK).model_dump_json()
    sibling_theme = (live_app.store.folder(SIBLING) / "theme.css").read_bytes()
    theme(live_app, "#7030a0")
    preview = live_app.call("get_presentation_design_sync", deck_id=DECK)
    assert not preview["conflicts"]
    assert "theme.css" in str(preview["changes"])
    Image.new("RGB", (40, 40), "#2a8854").save(source)
    _ = live_app.call(
        "import_design_asset",
        design_id=DESIGN,
        source_path=str(source),
        asset_path="assets/brand.png",
        expected_sha256=asset["sha256"],
    )
    with pytest.raises(ValueError):
        _ = live_app.call(
            "sync_presentation_design", deck_id=DECK, expected_hash=preview["expected_hash"]
        )
    preview = live_app.call("get_presentation_design_sync", deck_id=DECK)
    assert not preview["conflicts"]
    synced = live_app.call(
        "sync_presentation_design", deck_id=DECK, expected_hash=preview["expected_hash"]
    )
    assert "text" not in synced and "base64" not in str(synced)
    assert live_app.store.read(DECK).model_dump_json() == source_before
    assert (live_app.store.folder(SIBLING) / "theme.css").read_bytes() == sibling_theme
    sibling_source = live_app.store.read(SIBLING).model_dump_json()
    sibling_preview = live_app.call("get_presentation_design_sync", deck_id=SIBLING)
    assert not sibling_preview["conflicts"]
    _ = live_app.call(
        "sync_presentation_design",
        deck_id=SIBLING,
        expected_hash=sibling_preview["expected_hash"],
    )
    assert (live_app.store.folder(SIBLING) / "theme.css").read_bytes() == (
        live_app.store.folder(DECK) / "theme.css"
    ).read_bytes()
    assert (live_app.store.folder(SIBLING) / "assets/brand.png").read_bytes() == (
        live_app.store.folder(DECK) / "assets/brand.png"
    ).read_bytes()
    assert live_app.store.read(SIBLING).model_dump_json() == sibling_source
    assert Image.open(live_app.store.folder(DECK) / "assets/brand.png").getpixel((0, 0)) == (
        42,
        136,
        84,
    )
    current = live_app.call("get_slide", deck_id=DECK, slide_id="cover", include_feedback=True)
    assert current["expected_hash"] == original["expected_hash"]
    assert current["content_hash"] != original["content_hash"]
    assert current["approved"] is False and current["notes"] == PRIVATE_NOTE
    assert current["feedback"] == original["feedback"]
    assert "Keep this original design review." in str(current["feedback"])
    assert "WITHHELD_WORKFLOW_NOTE" not in str(current)
    assert "WITHHELD_WORKFLOW_NOTE" in str(live_app.store.comments(DECK))
    _ = page.reload()
    expect(heading).to_have_css("color", "rgb(112, 48, 160)")
    expect(page.frame_locator("#preview").get_by_alt_text("Brand mark")).to_be_visible()

    deck_theme = live_app.call("get_presentation_theme", deck_id=DECK)
    _ = live_app.call(
        "update_presentation_theme",
        deck_id=DECK,
        css="h1{color:#c03;font-size:5cqw}",
        expected_hash=deck_theme["expected_hash"],
    )
    theme(live_app, "#c65d13")
    preview = live_app.call("get_presentation_design_sync", deck_id=DECK)
    assert "theme.css" in str(preview["conflicts"])
    with pytest.raises(ValueError):
        _ = live_app.call(
            "sync_presentation_design", deck_id=DECK, expected_hash=preview["expected_hash"]
        )
    _ = page.reload()
    expect(heading).to_have_css("color", "rgb(204, 0, 51)")
    _ = live_app.call(
        "sync_presentation_design",
        deck_id=DECK,
        expected_hash=preview["expected_hash"],
        overwrite=True,
    )
    _ = page.reload()
    expect(heading).to_have_css("color", "rgb(198, 93, 19)")
    assert live_app.store.read(DECK).model_dump_json() == source_before
    _ = page.screenshot(path=str(artifacts / "synced-design-editor.png"))
    _ = page.goto(live_app.url + f"/api/decks/{DECK}/audience")
    expect(page.locator("#stage h1")).to_have_css("color", "rgb(198, 93, 19)")
    assert PRIVATE_NOTE not in page.content() and "WITHHELD_WORKFLOW_NOTE" not in page.content()
