"""Verify complete local project round trips and self-contained audience exports."""

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import app as service
from studio.models import FileInput
from studio.projects import (
    AssetEmbedder,
    ClientState,
    FeedbackDraft,
    NoteDraft,
    audience_html,
    open_project,
    save_project,
)
from studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design


def test_project_round_trip_preserves_deck_private_history_and_drafts(tmp_path: Path) -> None:
    """Restore into a different collection with exact review hashes and withheld feedback."""
    source = tmp_path / "source"
    make_design(source / TEMPLATE_ID)
    store = Store(source, tmp_path / "source-state/review.sqlite3")
    created = store.create(
        "2026-10-30-project",
        "Project",
        brief="A short overview",
        files=[FileInput(name="reference.docx", data_base64="ZG9jdW1lbnQ=")],
    )
    first = store.view(created.id).slides[0]
    store.approve(created.id, first.id, first.content_hash, True)
    store.set_slide_hidden(created.id, first.id, hidden=True, expected_hidden=False)
    store.save_notes(created.id, first.id, "Human context", first.notes)
    event = store.add_feedback(
        created.id, first.id, "Human feedback", first.content_hash, "first-feedback"
    )
    _ = store.add_feedback(
        created.id, first.id, "Agent reply", first.content_hash, "agent-reply", reply_to=int(event)
    )
    _ = store.set_mode(created.id, "batch")
    _ = store.add_feedback(
        created.id, first.id, "Not released", first.content_hash, "batch-feedback"
    )
    client = ClientState(
        active_slide_id=first.id,
        notes={first.id: NoteDraft(notes="Unfinished note", expected_notes="Human context")},
        feedback={
            first.id: FeedbackDraft(
                feedback="Unsent comment",
                slide_id=first.id,
                context_slide_id=first.id,
                content_hash=first.content_hash,
                request_id="unsent-request",
            )
        },
        open_panels=["notes"],
        presenter_notes={first.id: {"notes": "Unsent presenter note", "expected": "Human context"}},
    )
    archive = save_project(store, created.id, client)
    target = Store(tmp_path / "target", tmp_path / "target-state/review.sqlite3")
    (target.root / "INSTRUCTIONS.md").write_text("Primary rules stay here.")
    restored, recovered = open_project(target, archive)
    assert recovered == client and restored == store.read(created.id)
    before, after = store.view(created.id), target.view(restored.id)
    assert after.slides[0].notes == "Human context" and after.slides[0].hidden
    assert (
        after.slides[0].approved and after.slides[0].content_hash == before.slides[0].content_hash
    )
    assert after.slides[0].has_unseen_changes == before.slides[0].has_unseen_changes
    assert after.workflow.mode == "batch" and after.workflow.pending == 1
    assert not after.workflow.review_connected and not after.workflow.agent_waiting
    messages = after.slides[0].feedback
    assert [item.message for item in messages] == ["Human feedback", "Agent reply", "Not released"]
    assert messages[1].reply_to == messages[0].event_id
    assert not messages[2].released
    assert target.brief_files(restored.id)[0].name == "reference.docx"
    assert Path(target.brief_files(restored.id)[0].path).read_bytes() == b"document"
    assert (target.root / "INSTRUCTIONS.md").read_text() == "Primary rules stay here."
    original = (target.folder(restored.id) / "index.html").read_bytes()
    copy, _ = open_project(target, archive)
    assert copy.id != restored.id and copy.slides == restored.slides
    assert copy.title.endswith(" (copy)")
    assert (target.folder(restored.id) / "index.html").read_bytes() == original
    assert target.view(copy.id).slides[0].approved


def test_project_open_rejects_invalid_archives_without_partial_decks(tmp_path: Path) -> None:
    """Reject corruption, traversal and unsupported formats before publishing a folder."""
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    for name in ("../escape", "/absolute", "files/.hidden", "files/link"):
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            item = zipfile.ZipInfo(name)
            if name.endswith("link"):
                item.external_attr = 0o120777 << 16
            archive.writestr(item, "bad")
        with pytest.raises(ValueError):
            _ = open_project(store, content.getvalue())
    with pytest.raises(ValueError):
        _ = open_project(store, b"not a ZIP")
    assert store.list_decks() == []
    assert not (tmp_path / "escape").exists()


def test_project_includes_assets_in_working_subfolders(tmp_path: Path) -> None:
    """Keep slide images even when their subfolder is named research or qa."""
    make_design(tmp_path / "source" / TEMPLATE_ID)
    source = Store(tmp_path / "source", tmp_path / "source-state/review.sqlite3")
    _ = (source.folder(TEMPLATE_ID) / "assets/.DS_Store").write_bytes(b"asset-folder-metadata")
    for name in ("research", "qa"):
        folder = source.folder(TEMPLATE_ID) / "assets" / name
        folder.mkdir()
        _ = (folder / "chart.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    first = source.view(TEMPLATE_ID).slides[0]
    source.approve(TEMPLATE_ID, first.id, first.content_hash, True)
    data = save_project(source, TEMPLATE_ID, ClientState())
    target = Store(tmp_path / "target", tmp_path / "target-state/review.sqlite3")
    deck, _ = open_project(target, data)
    for name in ("research", "qa"):
        assert (target.folder(deck.id) / "assets" / name / "chart.svg").is_file()
    assert target.view(deck.id).slides[0].approved


def test_markup_export_embeds_responsive_and_background_images_and_keeps_links(
    tmp_path: Path,
) -> None:
    """Embed each local dependency without trying to download ordinary hyperlinks."""
    assets = tmp_path / "assets"
    assets.mkdir()
    _ = (assets / "image.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    _ = (assets / "nested.css").write_text('.image{background:url("image.svg")}')
    embedder = AssetEmbedder(tmp_path)
    html = embedder.markup(
        '<a href="https://example.com">Link</a><img src=assets/image.svg '
        'srcset="assets/image.svg 1x, assets/image.svg 2x">'
        '<div style="background:url(assets/image.svg)"></div>'
        "<style>.image{background:url(assets/image.svg)}</style>"
    )
    assert 'href="https://example.com"' in html
    assert "assets/image.svg" not in html and html.count("data:image/svg+xml;base64,") == 5
    css = embedder.css('@import "assets/nested.css";')
    assert "@import" not in css and "data:image/svg+xml;base64," in css
    metadata = '<div data-href="missing" data-src="missing" title="src=missing"></div>'
    assert embedder.markup(metadata) == metadata
    mixed = embedder.markup('<img srcset="data:image/png;base64,eA== 1x, assets/image.svg 2x">')
    assert "assets/image.svg" not in mixed
    assert "data:image/png;base64,eA== 1x" in mixed and "data:image/svg+xml;base64," in mixed


def test_standalone_html_embeds_images_fonts_and_animations_without_private_text(
    tmp_path: Path,
) -> None:
    """Preserve asset bytes and build markup while omitting notes, drafts and agent context."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    folder = store.folder(TEMPLATE_ID)
    index = folder / "index.html"
    _ = index.write_text(
        index.read_text().replace(
            '<html lang="en">', '<html lang="de" class="brand" dir="rtl" data-progress="none">'
        )
    )
    (folder / "assets/test.woff2").write_bytes(b"font-data")
    with (folder / "theme.css").open("a") as file:
        _ = file.write(
            '@font-face{font-family:Test;src:url("assets/test.woff2")}'
            "@keyframes arrive{to{opacity:1}}"
        )
    deck = store.read(TEMPLATE_ID)
    deck.slides[0].notes = "PRIVATE SPEAKER CONTEXT"
    deck.slides[0].agent.summary = "PRIVATE AGENT CONTEXT"
    deck.slides[1].hidden = True
    deck.slides[
        1
    ].html = '<article class="slide"><h1 data-build="2">Hidden final content</h1></article>'
    store.write(TEMPLATE_ID, deck)
    html = audience_html(store, TEMPLATE_ID)
    assert "data:image/svg+xml;base64," in html and "data:font/woff2;base64," in html
    assert "@keyframes arrive" in html and r"data-build=\"2\"" in html
    assert 'data-export="audience"' in html and 'id="slideshow-styles"' in html
    assert 'class="brand"' in html and 'data-progress="none"' in html
    assert "<script src=" not in html and '<link rel="stylesheet"' not in html
    assert "PRIVATE SPEAKER CONTEXT" not in html and "PRIVATE AGENT CONTEXT" not in html
    pdf = audience_html(store, TEMPLATE_ID, printing=True)
    assert pdf.count('<section class="print-page">') == 2
    assert "Hidden final content" in pdf and "Save as PDF" in pdf
    assert '<html lang="de" class="brand" dir="rtl" data-progress="none">' in pdf
    assert "PRIVATE SPEAKER CONTEXT" not in pdf and "PRIVATE AGENT CONTEXT" not in pdf
    assert "data:image/svg+xml;base64," in pdf


def test_project_and_export_http_routes_and_cross_origin_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise binary downloads, raw uploads, clone restoration and same-origin mutations."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    saved = client.post(f"/api/decks/{TEMPLATE_ID}/project", json={})
    assert saved.status_code == 200 and saved.headers["content-type"] == "application/zip"
    opened = client.post(
        "/api/projects/open", content=saved.content, headers={"Content-Type": "application/zip"}
    )
    assert opened.status_code == 200 and opened.json()["deck_id"] != TEMPLATE_ID
    assert len(store.list_decks()) == 2
    assert client.get(f"/api/decks/{TEMPLATE_ID}/exports/html").status_code == 200
    assert client.get(f"/api/decks/{TEMPLATE_ID}/exports/pdf").status_code == 200
    assert (
        client.post(
            "/api/projects/open", content=saved.content, headers={"Origin": "https://other.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/decks/{TEMPLATE_ID}/project",
            json={},
            headers={"Origin": "https://other.example"},
        ).status_code
        == 403
    )


def test_project_copy_restores_deleted_slides_and_private_history(tmp_path: Path) -> None:
    """Carry deleted slides and withheld batch comments into an independent project copy."""
    from studio.slide_actions import SlideActions
    from studio.storage import digest

    make_design(tmp_path / "collection" / TEMPLATE_ID)
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    view = store.view(TEMPLATE_ID).slides[0]
    store.save_notes(TEMPLATE_ID, view.id, "Original notes", view.notes)
    _ = store.set_mode(TEMPLATE_ID, "batch")
    _ = store.add_feedback(TEMPLATE_ID, view.id, "Withheld", view.content_hash, "withheld-id")
    slides = store.read(TEMPLATE_ID).slides
    ids = [slide.id for slide in slides]
    actions = SlideActions(store)
    deletion = actions.delete(
        TEMPLATE_ID, [ids[0]], ids, {ids[0]: digest(slides[0].model_dump_json())}, "delete-original"
    )
    archive = save_project(store, TEMPLATE_ID, ClientState())
    copy, _ = open_project(store, archive)
    assert copy.id != TEMPLATE_ID
    assert [item.operation_id for item in actions.deleted(copy.id)] == [deletion.operation_id]
    _ = actions.restore(copy.id, deletion.operation_id, "restore-in-copy")
    copied = store.view(copy.id)
    assert copied.slides[0].id == ids[0] and copied.slides[0].notes == "Original notes"
    assert copied.slides[0].feedback[0].message == "Withheld"
    assert not copied.slides[0].feedback[0].released
    assert not store.feedback_feed(copy.id).events
    assert store.workflow(copy.id).pending == 1
    assert [slide.id for slide in store.read(TEMPLATE_ID).slides] == [ids[1]]
    assert actions.deleted(copy.id) == []
    assert len(actions.deleted(TEMPLATE_ID)) == 1
    exported_again = save_project(store, copy.id, ClientState())
    second_copy, _ = open_project(store, exported_again)
    assert actions.deleted(second_copy.id) == []


@pytest.mark.parametrize("prefix", ["", "ns0:"])
def test_embedded_svg_preserves_case_sensitive_tag_names(tmp_path: Path, prefix: str) -> None:
    """Keep SVG clipping, gradients and namespace tags valid when embedding dependencies."""
    import base64
    import xml.etree.ElementTree as ET

    ns = (
        'xmlns:ns0="http://www.w3.org/2000/svg"' if prefix else 'xmlns="http://www.w3.org/2000/svg"'
    )
    svg = f"""<{prefix}svg {ns} viewBox="0 0 20 20"><{prefix}defs>
<{prefix}clipPath id="clip"><{prefix}rect width="20" height="20"/></{prefix}clipPath>
<{prefix}linearGradient id="fill">
<{prefix}stop offset="0" stop-color="red"/></{prefix}linearGradient>
</{prefix}defs>
<{prefix}rect clip-path="url(#clip)" fill="url(#fill)" width="20" height="20"/></{prefix}svg>"""
    (tmp_path / "asset.svg").write_text(svg)
    embedded = AssetEmbedder(tmp_path).asset("asset.svg")
    data = base64.b64decode(embedded.split(",", 1)[1])
    root = ET.fromstring(data)
    assert root.find(".//{http://www.w3.org/2000/svg}clipPath") is not None
    assert root.find(".//{http://www.w3.org/2000/svg}linearGradient") is not None
    assert f"</{prefix}clipPath>" in data.decode()
    assert f"</{prefix}linearGradient>" in data.decode()
