"""Fresh MCP and browser journeys for context efficiency and optional pixel text review."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from artifact_studio.agent_client import object_value
from scripts.measure_targeted_reads import assert_same_context, read_sample, seed_bundled_slide
from tests.design_factory import TEMPLATE_ID
from tests.integration.support import DECK_ID, PRIVATE_NOTE, RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_targeted_read_preserves_context_and_interactive_source(
    page: Page, live_app: RunningApp, artifacts: Path
) -> None:
    """Inspect small source, handle a human race, and keep the omitted widget operational."""
    _ = seed_bundled_slide(live_app)
    client = live_app.client()
    full, before = read_sample(client, include_interactive_html=True)
    targeted, after = read_sample(client, include_interactive_html=False)
    assert_same_context(full, targeted)
    assert targeted["notes"] == PRIVATE_NOTE
    assert before["text_utf8_bytes"] > after["text_utf8_bytes"] * 100
    initial = live_app.store.read(DECK_ID).slides[0]
    _ = live_app.store.save_notes(DECK_ID, "cover", PRIVATE_NOTE + " human addition", PRIVATE_NOTE)
    patch = {
        "html": str(targeted["html"]).replace(
            '<article class="slide"', '<article data-reviewed="yes" class="slide"'
        )
    }
    with pytest.raises(ValueError, match="changed"):
        _ = live_app.call(
            "revise_slide",
            deck_id=DECK_ID,
            slide_id="cover",
            patch=patch,
            expected_hash=targeted["expected_hash"],
            request_id="targeted-stale-read",
        )
    refreshed = live_app.call(
        "get_slide", deck_id=DECK_ID, slide_id="cover", include_interactive_html=False
    )
    _ = live_app.call(
        "revise_slide",
        deck_id=DECK_ID,
        slide_id="cover",
        patch=patch,
        expected_hash=refreshed["expected_hash"],
        request_id="targeted-merged-read",
    )
    current = live_app.store.read(DECK_ID).slides[0]
    assert current.interactive_html == initial.interactive_html
    assert current.notes == PRIVATE_NOTE + " human addition"
    _ = page.goto(live_app.url + f"/api/decks/{DECK_ID}/audience")
    widget = page.frame_locator("#stage .runtime-slide-canvas > iframe").frame_locator("iframe")
    expect(widget.get_by_role("heading")).to_have_text("Bundled source remains intact")
    expect(widget.get_by_alt_text("Blue mark")).to_be_visible()
    widget.get_by_role("button", name="Change state").click()
    expect(widget.locator("#state")).to_have_text("Working")
    assert PRIVATE_NOTE not in page.content()
    _ = page.screenshot(path=str(artifacts / "targeted-read-widget.png"))


def test_new_pack_guidance_and_staging_work_without_manual_setup(live_app: RunningApp) -> None:
    """Create a ready-to-author pack without duplicating or rewriting existing brand guidance."""
    original = (live_app.store.folder(TEMPLATE_ID) / "AGENT-GUIDE.md").read_bytes()
    pack = live_app.call("create_design_pack", design_id="concise-brand", name="Concise brand")
    source = Path(str(pack["import_directory"])) / "mark.svg"
    _ = source.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>')
    _ = live_app.call(
        "import_design_asset",
        design_id="concise-brand",
        source_path=str(source),
        asset_path="assets/mark.svg",
    )
    guide = live_app.call("get_design_file", design_id="concise-brand", path="AGENT-GUIDE.md")
    assert len(str(guide["text"]).encode()) < len(original) / 10
    assert (live_app.store.folder(TEMPLATE_ID) / "AGENT-GUIDE.md").read_bytes() == original
    context = live_app.call("get_working_context", deck_id=DECK_ID)
    assert context["selected_design_id"] == "test-brand"
    assert context["discovery_included"] is False
    status = live_app.call("get_capture_status")
    assert object_value(status["renderer"])["status"] == "unknown"


@pytest.mark.skipif(
    os.environ.get("ARTIFACT_STUDIO_RENDER_TESTS") != "1", reason="Opt-in sandboxed capture/OCR"
)
def test_capture_text_review_through_mcp(live_app: RunningApp) -> None:
    """Reuse one captured image for matching/mismatch review, then reject its stale revision."""
    probe = live_app.call("get_capture_status", probe_renderer=True)
    assert object_value(probe["renderer"])["status"] == "ready"
    current = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    saved = live_app.call(
        "revise_slide",
        deck_id=DECK_ID,
        slide_id="cover",
        expected_hash=current["expected_hash"],
        request_id="ocr-readable-source",
        patch={
            "html": '<article class="slide" style="background:white;color:black;'
            'padding:100px;font:80px Arial"><p>COCO CODE COLD</p></article>'
        },
    )
    capture = live_app.client().rpc(
        "tools/call",
        {"name": "capture_slide", "arguments": {"deck_id": DECK_ID, "slide_id": "cover"}},
    )
    assert not capture.get("isError"), capture
    manifest = object_value(capture["structuredContent"])
    for expected, status in (
        ("COCO CODE COLD", "no_mismatch_detected"),
        ("OOOO OODE OOLD", "mismatches_detected"),
    ):
        checked = live_app.call(
            "check_slide_text",
            deck_id=DECK_ID,
            slide_id="cover",
            capture_id=manifest["capture_id"],
            expected_text=expected,
        )
        assert checked["status"] == status
        assert checked["human_review_required"] is True
    _ = live_app.call(
        "revise_slide",
        deck_id=DECK_ID,
        slide_id="cover",
        expected_hash=saved["expected_hash"],
        request_id="ocr-new-revision",
        patch={"html": '<article class="slide"><p>New content</p></article>'},
    )
    stale = live_app.call(
        "check_slide_text",
        deck_id=DECK_ID,
        slide_id="cover",
        capture_id=manifest["capture_id"],
        expected_text="COCO CODE COLD",
    )
    assert stale["status"] == "stale" and stale["stale"] is True
    assert not live_app.store.view(DECK_ID).slides[0].approved
