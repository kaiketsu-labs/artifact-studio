"""Version, privacy and deterministic rendering checks on fictional slide content."""

import base64
import hashlib
import io
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import Page
from pydantic import ValidationError

from artifact_studio.models import AgentContext, Slide, SlidePatch
from artifact_studio.storage import Store, digest
from artifact_studio.visuals import CaptureOptions, VisualResult, capture, snapshot
from tests.design_factory import TEMPLATE_ID, make_design

DECK = "2026-10-30-visual-check"
RENDER = pytest.mark.skipif(
    os.environ.get("ARTIFACT_STUDIO_RENDER_TESTS") != "1",
    reason="Opt-in local Chromium integration",
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
    for offset, limit in ((-1, 12), (0, 0), (0, 25), (100, 12)):
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
def test_real_renderer_probe_uses_the_production_sandbox() -> None:
    """An explicit readiness probe starts no deck and releases the shared capture queue."""
    from artifact_studio.visuals import capture_queue_status

    status = capture_queue_status(probe_renderer=True)
    assert status["running"] is False and status["queued"] == 0
    assert "probe_error" not in status
    renderer = status["renderer"]
    assert isinstance(renderer, dict)
    assert renderer["status"] == "ready" and renderer["retry_after_seconds"] == 0


@RENDER
def test_real_contact_sheet_pagination_and_inline_mcp(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Include hidden slides, bound overview pages and expose real MCP image blocks."""
    from artifact_studio import app as service

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
        assert image.width > 600


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


def test_capture_dependency_budget_and_recursive_preparation(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject oversized reads and same-thread recursion before entering a browser."""
    from artifact_studio import visuals

    monkeypatch.setattr(visuals, "MAX_ASSET_BYTES", 1)
    with pytest.raises(ValueError, match="size limit"):
        snapshot(store, DECK, store.read(DECK).slides[0], 1, CaptureOptions())
    with visuals.render_slot(), pytest.raises(ValueError, match="another renderer"):
        capture(store, "missing", tmp_path / "captures")


@RENDER
def test_reduced_motion_and_dependency_change_detection(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skip motion on request and mark frozen images stale when imported CSS changes."""
    from artifact_studio import visuals

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

    from artifact_studio import renderer_health, visuals

    monkeypatch.setattr(renderer_health, "HEALTH", renderer_health.RendererHealth())

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
    from artifact_studio import visuals

    (tmp_path / "small.png").write_bytes(b"x" * 60)
    monkeypatch.setattr(visuals, "MAX_PAGE_BYTES", 200)
    embedder = visuals.ReviewEmbedder(tmp_path)
    with pytest.raises(ValueError, match="Expanded review assets"):
        embedder.markup('<img src="small.png">' * 20)
    assert embedder.total_bytes == 60


def test_capture_queue_is_fifo_and_recovers_after_failure() -> None:
    """Concurrent callers wait in arrival order and a failed capture frees the renderer."""
    from artifact_studio.visuals import RenderQueue

    queue = RenderQueue(timeout=2)
    order: list[int] = []

    def work(number: int) -> int:
        with queue.slot() as waited:
            order.append(number)
            if number == 1:
                raise RuntimeError("Fictional renderer failed")
            return waited

    with ThreadPoolExecutor(max_workers=2) as pool:
        with queue.slot():
            first = pool.submit(work, 1)
            with queue.condition:
                assert queue.condition.wait_for(lambda: len(queue.waiters) == 1, timeout=1)
            second = pool.submit(work, 2)
            with queue.condition:
                assert queue.condition.wait_for(lambda: len(queue.waiters) == 2, timeout=1)
            assert queue.status() == {
                "running": True,
                "queued": 2,
                "capacity": 8,
                "wait_timeout_seconds": 2,
            }
        with pytest.raises(RuntimeError, match="Fictional"):
            first.result(timeout=2)
        assert second.result(timeout=2) >= 0
    assert order == [1, 2]
    assert queue.status()["running"] is False
    assert queue.status()["queued"] == 0


def test_capture_queue_bounds_and_cleans_up_timeouts() -> None:
    """A full or timed-out queue does not retain abandoned tickets or lose its owner."""
    from artifact_studio.visuals import RenderQueue

    queue = RenderQueue(capacity=1, timeout=0.1)

    def work() -> None:
        with queue.slot():
            pytest.fail("This request must time out before it starts rendering.")

    with ThreadPoolExecutor(max_workers=2) as pool:
        with queue.slot():
            waiting = pool.submit(work)
            with queue.condition:
                assert queue.condition.wait_for(lambda: len(queue.waiters) == 1, timeout=1)
            rejected = pool.submit(work)
            with pytest.raises(ValueError, match="queue is full"):
                rejected.result(timeout=1)
            with pytest.raises(ValueError, match="timed out while queued"):
                waiting.result(timeout=1)
            assert queue.status()["running"] is True
            assert queue.status()["queued"] == 0
        with queue.slot():
            assert queue.status()["running"] is True
    assert queue.status()["running"] is False


def test_contact_subset_paginates_in_deck_order_and_accepts_24(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selected slide IDs keep original numbering and paginate without unrelated frames."""
    from artifact_studio import visuals

    def render(
        prepared: list[visuals.Snapshot],
        options: CaptureOptions,
        paint: Callable[[Page], bytes],
    ) -> list[bytes]:
        del options, paint
        images: list[bytes] = []
        for item in prepared:
            with Image.new("RGB", (item.frame.width, item.frame.height), "white") as image:
                output = io.BytesIO()
                image.save(output, format="PNG")
                images.append(output.getvalue())
        return images

    monkeypatch.setattr(visuals, "_render_images", render)
    for number in range(24):
        store.add_slide(
            DECK,
            Slide(
                id=f"extra-{number}",
                title=f"Extra {number}",
                html='<article class="slide"><p>Fictional extra slide</p></article>',
            ),
        )
    output = tmp_path / "captures"
    first = capture(store, DECK, output)
    assert len(first.frames) == 24
    assert first.next_offset == 24
    assert first.total_slides == first.selected_slides == 26
    assert first.resolution == "draft"
    assert all((frame.width, frame.height) == (800, 450) for frame in first.frames)
    assert all(not frame.stale for frame in first.frames)
    assert all(
        frame.image_sha256 == hashlib.sha256(Path(frame.path).read_bytes()).hexdigest()
        for frame in first.frames
    )
    chosen = ["extra-21", "hidden", "extra-1"]
    subset = capture(store, DECK, output, slide_ids=chosen, limit=2)
    assert [frame.slide_id for frame in subset.frames] == ["hidden", "extra-1"]
    assert [frame.slide_number for frame in subset.frames] == [2, 4]
    assert subset.next_offset == 2
    assert subset.selected_slides == 3
    assert subset.total_slides == 26
    last = capture(store, DECK, output, slide_ids=chosen, offset=2, limit=2)
    assert [frame.slide_id for frame in last.frames] == ["extra-21"]
    assert last.next_offset is None
    assert VisualResult.model_validate_json(Path(last.path).with_name("manifest.json").read_text())
    for ids in ([], ["missing"], ["hidden", "hidden"]):
        with pytest.raises(ValueError):
            capture(store, DECK, output, slide_ids=ids)
    with pytest.raises(ValueError, match="not both"):
        capture(store, DECK, output, slide_id="cover", slide_ids=["hidden"])


@RENDER
def test_real_draft_capture_preserves_layout_and_version(store: Store, tmp_path: Path) -> None:
    """Draft rasterization uses one quarter the pixels without reflowing fixed-pixel layouts."""
    full = capture(store, DECK, tmp_path, slide_id="cover")
    draft = capture(
        store, DECK, tmp_path, slide_id="cover", options=CaptureOptions(resolution="draft")
    )
    assert draft.resolution == "draft"
    assert draft.frames[0].content_hash == full.frames[0].content_hash
    assert draft.frames[0].expected_hash == full.frames[0].expected_hash
    assert draft.frames[0].render_hash != full.frames[0].render_hash
    assert draft.frames[0].viewport_width == full.frames[0].viewport_width == 1600
    assert draft.frames[0].viewport_height == full.frames[0].viewport_height == 900
    assert not draft.frames[0].stale
    with Image.open(draft.path) as image:
        assert image.size == (800, 450)
    for x, y in ((900, 350), (300, 650), (1400, 800)):
        assert pixel(draft.path, x // 2, y // 2) == pixel(full.path, x, y)
