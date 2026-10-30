"""Version, privacy and deterministic rendering checks on fictional slide content."""

import base64
import io
import os
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from studio.models import AgentContext, Slide, SlidePatch
from studio.storage import Store, digest
from studio.visuals import CaptureOptions, VisualResult, capture, snapshot
from tests.design_factory import TEMPLATE_ID, make_design

DECK = "2026-10-30-visual-check"
RENDER = pytest.mark.skipif(
    os.environ.get("SLIDE_STUDIO_RENDER_TESTS") != "1", reason="Opt-in local Chromium integration"
)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """Prepare local fictional slides with private context and staged color blocks."""
    make_design(tmp_path / TEMPLATE_ID)
    result = Store(tmp_path, tmp_path / "review.sqlite3")
    result.create(DECK, "Fictional visual capture")
    slide = result.read(DECK).slides[0]
    result.revise(
        DECK,
        slide.id,
        digest(slide.model_dump_json()),
        "visual-fixture-change",
        SlidePatch(
            html="""<article class="slide" style="background:#fff"><h1>Capture check</h1>
<div data-build="1" data-animate-duration="1000"
 style="position:absolute;left:100px;top:300px;width:1000px">
<div data-animate-grow="x" style="height:100px;background:rgb(255,100,0)"></div>
<span data-count-to="80">80</span></div>
<div data-build="2" data-animate-delay="1000"
 style="position:absolute;left:100px;top:600px;width:1000px;height:100px;
 background:rgb(0,160,80)"></div></article>""",
            notes="PRIVATE HUMAN NOTES",
            agent=AgentContext(summary="PRIVATE AGENT DISCUSSION"),
        ),
    )
    result.add_slide(
        DECK,
        Slide(
            id="hidden",
            title="Hidden slide",
            hidden=True,
            html='<article class="slide"><h2>Hidden content</h2></article>',
        ),
    )
    return result


def test_capture_document_preserves_version_and_excludes_private_context(store: Store) -> None:
    """Pass only slide content and embedded assets into the isolated document."""
    slide = store.read(DECK).slides[0]
    prepared = snapshot(store, DECK, slide, 1, CaptureOptions())
    assert "PRIVATE HUMAN NOTES" not in prepared.document
    assert "PRIVATE AGENT DISCUSSION" not in prepared.document
    assert 'data-count-to="80"' in prepared.document
    assert "connect-src &#x27;none&#x27;" in prepared.document
    assert prepared.frame.content_hash == store.content_hash(DECK, slide)
    assert prepared.frame.expected_hash == digest(slide.model_dump_json())
    assert prepared.frame.stage is None


def test_capture_validation(store: Store, tmp_path: Path) -> None:
    """Reject invalid stages, times, slide IDs and unbounded contact pages before rendering."""
    for values in ({"stage": -1}, {"stage": 51}, {"elapsed_ms": -1}, {"elapsed_ms": 600001}):
        with pytest.raises(ValidationError):
            CaptureOptions.model_validate(values)
    for offset, limit in ((-1, 12), (0, 0), (0, 13), (100, 12)):
        with pytest.raises(ValueError):
            capture(store, DECK, tmp_path / "captures", offset=offset, limit=limit)
    with pytest.raises(ValueError, match="Slide not found"):
        capture(store, DECK, tmp_path / "captures", slide_id="missing")


def test_automatic_reveal_markup_and_runtime_styles_reach_capture(store: Store) -> None:
    """Accept declarative entry groups and fingerprint their shared rendering styles."""
    slide = store.read(DECK).slides[0]
    markup = (
        '<article class="slide"><section data-auto-reveal data-animate-delay="250">'
        'First card</section><section data-auto-reveal data-animate-delay="850">'
        'Second card</section><section data-auto-reveal data-animate-delay="1450">'
        'Third card</section><div data-build="1">Logos</div></article>'
    )
    store.revise(
        DECK,
        slide.id,
        digest(slide.model_dump_json()),
        "automatic-reveal-fixture",
        SlidePatch(html=markup),
    )
    prepared = snapshot(store, DECK, store.read(DECK).slides[0], 1, CaptureOptions(stage=0))
    assert markup in prepared.document
    assert ".slide.build-mode [data-auto-reveal].unrevealed{opacity:0" in prepared.document
    assert prepared.frame.runtime_matches_deck
    styles = store.folder(DECK) / "slideshow.css"
    _ = styles.write_text(styles.read_text() + "\n/* Older deck runtime */\n")
    stale = snapshot(store, DECK, store.read(DECK).slides[0], 1, CaptureOptions(stage=0))
    assert not stale.frame.runtime_matches_deck


def pixel(path: str, x: int, y: int) -> tuple[int, ...] | float | None:
    """Read a deterministic color sample from a full-size captured frame."""
    with Image.open(path) as image:
        return image.getpixel((x, y))


@RENDER
def test_real_capture_stages_and_private_state(store: Store, tmp_path: Path) -> None:
    """Verify pixels at initial, intermediate, delayed and complete build states."""
    output = tmp_path / "captures"
    before = (store.folder(DECK) / "index.html").read_bytes()
    frames: dict[str, VisualResult] = {}
    for name, options in {
        "initial": CaptureOptions(stage=0),
        "half": CaptureOptions(stage=1, elapsed_ms=500),
        "later": CaptureOptions(stage=2, elapsed_ms=0),
        "delayed": CaptureOptions(stage=2, elapsed_ms=1001),
        "final": CaptureOptions(),
    }.items():
        result = capture(store, DECK, output, slide_id="cover", options=options)
        frames[name] = result
        assert not result.frames[0].stale
        assert result.frames[0].diagnostics is not None
        assert result.frames[0].diagnostics.reveal_steps == 2
        assert result.frames[0].diagnostics.failed_images == []
        with Image.open(result.path) as image:
            assert image.size == (1600, 900)
    assert pixel(frames["initial"].path, 200, 350) == (255, 255, 255)
    assert pixel(frames["half"].path, 300, 350) == (255, 100, 0)
    assert pixel(frames["half"].path, 900, 350) == (255, 255, 255)
    assert pixel(frames["later"].path, 900, 350) == (255, 100, 0)
    assert pixel(frames["later"].path, 300, 650) == (255, 255, 255)
    assert pixel(frames["delayed"].path, 300, 650) == (0, 160, 80)
    assert pixel(frames["final"].path, 900, 350) == (255, 100, 0)
    assert pixel(frames["final"].path, 300, 650) == (0, 160, 80)
    assert (store.folder(DECK) / "index.html").read_bytes() == before
    with pytest.raises(ValueError, match="2 reveal steps"):
        capture(store, DECK, output, slide_id="cover", options=CaptureOptions(stage=3))


@RENDER
def test_real_contact_sheet_pagination_and_inline_mcp(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Include hidden slides, bound overview pages and expose real MCP image blocks."""
    from studio import app as service

    first = capture(store, DECK, tmp_path / "captures", limit=1)
    assert first.next_offset == 1
    second = capture(store, DECK, tmp_path / "captures", offset=1, limit=1)
    assert second.frames[0].hidden
    assert second.frames[0].slide_number == 2
    assert second.next_offset is None
    monkeypatch.setattr(service, "store", store)
    monkeypatch.setattr(service, "DATABASE", tmp_path / "review.sqlite3")
    result = service.capture_contact_sheet(DECK, limit=2)
    assert result.structured_content is not None
    assert [item.type for item in result.content] == ["text", "image"]
    assert result.content[1].type == "image"
    with Image.open(io.BytesIO(base64.b64decode(result.content[1].data))) as image:
        assert image.width > 900


def test_render_hash_tracks_root_settings_and_imported_css(store: Store) -> None:
    """Track all actual visual dependencies beyond the ordinary approval content hash."""
    folder = store.folder(DECK)
    slide = store.read(DECK).slides[0]
    (folder / "extra.css").write_text("h1 {color:blue}")
    theme = folder / "theme.css"
    theme.write_text('@import "extra.css";\n' + theme.read_text())
    first = snapshot(store, DECK, slide, 1, CaptureOptions())
    (folder / "extra.css").write_text("h1 {color:red}")
    second = snapshot(store, DECK, slide, 1, CaptureOptions())
    assert first.frame.content_hash == second.frame.content_hash
    assert first.frame.render_hash != second.frame.render_hash
    index = folder / "index.html"
    index.write_text(index.read_text().replace('<html lang="en">', '<html lang="en" dir="rtl">'))
    third = snapshot(store, DECK, slide, 1, CaptureOptions())
    assert third.frame.render_hash != second.frame.render_hash


def test_capture_dependency_budget_and_busy_preparation(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject oversized reads and concurrent preparation before entering a browser."""
    from studio import visuals

    monkeypatch.setattr(visuals, "MAX_ASSET_BYTES", 1)
    with pytest.raises(ValueError, match="size limit"):
        snapshot(store, DECK, store.read(DECK).slides[0], 1, CaptureOptions())
    with visuals.render_slot(), pytest.raises(ValueError, match="capture is running"):
        capture(store, "missing", tmp_path / "captures")


@RENDER
def test_reduced_motion_and_dependency_change_detection(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skip motion on request and mark frozen images stale when imported CSS changes."""
    from studio import visuals

    reduced = capture(
        store,
        DECK,
        tmp_path / "captures",
        slide_id="cover",
        options=CaptureOptions(stage=1, elapsed_ms=500, reduced_motion=True),
    )
    assert reduced.frames[0].reduced_motion
    assert pixel(reduced.path, 900, 350) == (255, 100, 0)
    assert pixel(reduced.path, 300, 650) == (255, 255, 255)
    folder = store.folder(DECK)
    theme = folder / "theme.css"
    theme.write_text('@import "extra.css";\n' + theme.read_text())
    (folder / "extra.css").write_text("h1 {color:blue}")
    original = visuals.snapshot
    changed = False

    def prepare(
        source: Store, deck_id: str, slide: Slide, number: int, options: CaptureOptions
    ) -> visuals.Snapshot:
        nonlocal changed
        result = original(source, deck_id, slide, number, options)
        if not changed:
            (folder / "extra.css").write_text("h1 {color:red}")
            changed = True
        return result

    monkeypatch.setattr(visuals, "snapshot", prepare)
    stale = capture(store, DECK, tmp_path / "captures", slide_id="cover")
    assert stale.frames[0].stale


def test_missing_renderer_reports_setup_and_releases_slot(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Give a fresh installation an actionable error without leaving a locked renderer."""
    from typing import Never

    from playwright.sync_api import Error

    from studio import visuals

    def missing() -> Never:
        raise Error("Executable doesn't exist")

    monkeypatch.setattr(visuals, "sync_playwright", missing)
    with pytest.raises(ValueError, match="playwright install chromium"):
        capture(store, DECK, tmp_path / "captures", slide_id="cover")
    assert not (tmp_path / "captures").exists()
    with visuals.render_slot():
        pass


def test_repeated_assets_are_bounded_during_embedding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject repeated substitutions before joining an oversized expanded document."""
    from studio import visuals

    (tmp_path / "small.png").write_bytes(b"x" * 60)
    monkeypatch.setattr(visuals, "MAX_PAGE_BYTES", 200)
    embedder = visuals.ReviewEmbedder(tmp_path)
    with pytest.raises(ValueError, match="Expanded review assets"):
        embedder.markup('<img src="small.png">' * 20)
    assert embedder.total_bytes == 60
