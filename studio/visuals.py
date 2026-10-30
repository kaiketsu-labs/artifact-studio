"""Local, version-anchored slide images and paginated visual review sheets."""

import io
import json
import math
import threading
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Annotated

from PIL import Image, ImageDraw
from playwright.sync_api import Error as BrowserError
from playwright.sync_api import sync_playwright
from pydantic import Field

from .models import Slide, StrictModel
from .projects import AssetEmbedder, root_tag
from .storage import SlideMarkup, Store, digest

PROJECT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 1600, 900
_RENDER_LOCK = threading.Lock()
MAX_ASSET_BYTES = 25 * 1024 * 1024
MAX_DEPENDENCY_BYTES = 64 * 1024 * 1024
MAX_PAGE_BYTES = 96 * 1024 * 1024


class ReviewEmbedder(AssetEmbedder):
    """Bound local dependency reads before allocating or encoding their contents."""

    def __init__(self, folder: Path) -> None:
        """Track distinct files while retaining the ordinary export boundary."""
        super().__init__(folder)
        self.checked: set[Path] = set()
        self.total_bytes = 0
        self.expanded_bytes = 0

    def asset(self, url: str, directory: Path | None = None) -> str:
        """Count every substitution before repeated data URLs are joined into a document."""
        result = super().asset(url, directory)
        self.expanded_bytes += len(result)
        if self.expanded_bytes > MAX_PAGE_BYTES:
            raise ValueError(
                "Expanded review assets exceed the capture limit. Resize repeated assets."
            )
        return result

    def local_path(self, url: str, directory: Path | None = None) -> Path:
        """Reject oversized individual assets and excessive total dependencies."""
        path = super().local_path(url, directory)
        if path not in self.checked:
            size = path.stat().st_size
            if size > MAX_ASSET_BYTES or self.total_bytes + size > MAX_DEPENDENCY_BYTES:
                raise ValueError(
                    "Review assets exceed the local capture size limit. Resize assets first."
                )
            self.total_bytes += size
            self.checked.add(path)
        return path


@contextmanager
def render_slot() -> Generator[None]:
    """Serialize preparation, rendering and persistence so concurrent requests stay bounded."""
    if not _RENDER_LOCK.acquire(blocking=False):
        raise ValueError("A visual capture is running. Retry when it finishes.")
    try:
        yield
    finally:
        _RENDER_LOCK.release()


class CaptureOptions(StrictModel):
    """A reproducible reveal stage and elapsed time within that stage."""

    stage: Annotated[int | None, Field(ge=0, le=50)] = None
    elapsed_ms: Annotated[int, Field(ge=0, le=600_000)] = 0
    reduced_motion: bool = False


class Diagnostics(StrictModel):
    """Rendering observations that need human or agent interpretation."""

    reveal_steps: int
    failed_images: list[str]
    failed_fonts: list[str]
    possible_overflow: list[str]
    media_count: int
    interactive: bool = False
    diagnostics_scope: str = "Static slide DOM"


class Frame(StrictModel):
    """Identify the exact content, runtime and reveal state visible in one image."""

    slide_id: str
    title: str
    slide_number: int
    hidden: bool
    content_hash: str
    expected_hash: str
    runtime_hash: str
    runtime_matches_deck: bool
    render_hash: str
    stage: int | None
    elapsed_ms: int
    reduced_motion: bool
    width: int = WIDTH
    height: int = HEIGHT
    path: str = ""
    stale: bool = False
    diagnostics: Diagnostics | None = None


class VisualResult(StrictModel):
    """A local capture manifest independent of any active browser or MCP session."""

    capture_id: str
    deck_id: str
    captured_at: str
    kind: str
    path: str
    frames: list[Frame]
    total_slides: int
    next_offset: int | None = None
    scope: str = "Slide content only; not presenter controls or physical-display verification."


class Snapshot(StrictModel):
    """An immutable audience-only document prepared before starting the renderer."""

    document: str
    frame: Frame


def snapshot(
    store: Store, deck_id: str, slide: Slide, number: int, options: CaptureOptions
) -> Snapshot:
    """Embed bounded local assets and exclude private context from the renderer document."""
    if slide.interactive_html and options.stage not in {None, 0}:
        raise ValueError(
            "Custom interactive slides use stage 0 plus elapsed_ms, or null for final."
        )
    content_hash = store.content_hash(deck_id, slide)
    SlideMarkup().feed(slide.html)
    folder = store.folder(deck_id)
    embedder = ReviewEmbedder(folder)
    theme = embedder.css(embedder.local_path("theme.css").read_text()).replace("<", r"\3c ")
    markup = embedder.markup(slide.html)
    interactive = slide.interactive_html
    runtime = (PROJECT / "runtime/presentation.js").read_text()
    runtime_styles = (PROJECT / "runtime/slideshow.css").read_text()
    adapter = (PROJECT / "runtime/capture.js").read_text()
    nonce = uuid.uuid4().hex
    scripts = "'unsafe-inline' 'unsafe-eval'" if interactive else f"'nonce-{nonce}'"
    frames = "about:" if interactive else "'none'"
    policy = (
        "default-src 'none'; img-src data: blob:; font-src data:; media-src data: blob:; "
        f"style-src 'unsafe-inline'; script-src {scripts}; "
        f"connect-src 'none'; object-src 'none'; frame-src {frames}; "
        "base-uri 'none'; form-action 'none'"
    )
    root = root_tag((folder / "index.html").read_text()).replace(
        "<html", '<html data-capture="slide"', 1
    )
    document = (
        "<!doctype html>"
        + root
        + '<head><meta charset="utf-8">'
        + '<meta http-equiv="Content-Security-Policy" content="'
        + escape(policy, quote=True)
        + '">'
        + "<style>"
        + theme
        + runtime_styles.replace("<", r"\3c ")
        + "</style><style>"
        + f"html,body{{margin:0;width:{WIDTH}px;height:{HEIGHT}px;overflow:hidden}}"
        + f"#capture-stage{{position:relative;width:{WIDTH}px;height:{HEIGHT}px;"
        + "container-type:inline-size;box-shadow:none}"
        + "#capture-stage>.slide{position:relative;width:100%;height:100%;"
        + "box-sizing:border-box;overflow:hidden}"
        + ".slide [data-build],.slide [data-auto-reveal]{transition:none!important}"
        + "</style></head><body>"
        + '<main id="capture-stage" class="stage-slot">'
        + markup
        + "</main>"
        + '<script id="capture-config" type="application/json">'
        + options.model_dump_json()
        + "</script>"
        + '<script id="interactive-data" type="application/json">'
        + json.dumps(interactive).replace("<", r"\u003c")
        + "</script>"
        + f'<script nonce="{nonce}">'
        + runtime.replace("</script", r"<\/script")
        + "</script>"
        + f'<script nonce="{nonce}">'
        + adapter.replace("</script", r"<\/script")
        + "</script></body></html>"
    )
    if store.content_hash(deck_id, slide) != content_hash:
        raise ValueError("The design changed during snapshot preparation. Retry capture.")
    return Snapshot(
        document=document,
        frame=Frame(
            slide_id=slide.id,
            title=slide.title,
            slide_number=number,
            hidden=slide.hidden,
            content_hash=content_hash,
            expected_hash=digest(slide.model_dump_json()),
            runtime_hash=digest(runtime + runtime_styles + adapter),
            runtime_matches_deck=(folder / "presentation.js").is_file()
            and (folder / "presentation.js").read_text() == runtime
            and (folder / "slideshow.css").is_file()
            and (folder / "slideshow.css").read_text() == runtime_styles,
            render_hash=digest(
                root
                + theme
                + markup
                + interactive
                + runtime
                + runtime_styles
                + adapter
                + options.model_dump_json()
            ),
            stage=options.stage,
            elapsed_ms=options.elapsed_ms,
            reduced_motion=options.reduced_motion,
        ),
    )


def contact_sheet(images: list[bytes], frames: list[Frame]) -> bytes:
    """Compose labeled thumbnails while retaining full-size frames for detailed inspection."""
    columns = min(3, math.ceil(math.sqrt(len(images))))
    tile_width, tile_height, gap = 480, 302, 16
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new(
        "RGB", (columns * (tile_width + gap) + gap, rows * (tile_height + gap) + gap), "#eef1f5"
    )
    draw = ImageDraw.Draw(sheet)
    for index, (data, frame) in enumerate(zip(images, frames, strict=True)):
        left, top = (
            gap + index % columns * (tile_width + gap),
            gap + index // columns * (tile_height + gap),
        )
        with Image.open(io.BytesIO(data)) as source:
            thumb = source.convert("RGB")
            thumb.thumbnail((tile_width, 270), Image.Resampling.LANCZOS)
        sheet.paste(thumb, (left, top))
        label = f"{frame.slide_number:02d} · {frame.title}" + (" · Hidden" if frame.hidden else "")
        draw.text((left + 6, top + 278), label[:70], fill="#172b49", font_size=15)
    output = io.BytesIO()
    sheet.save(output, format="PNG")
    return output.getvalue()


def capture(
    store: Store,
    deck_id: str,
    output: Path,
    *,
    slide_id: str | None = None,
    options: CaptureOptions | None = None,
    offset: int = 0,
    limit: int = 12,
) -> VisualResult:
    """Render a slide or contact-sheet page without opening a server URL or user profile."""
    with render_slot():
        if offset < 0 or not 1 <= limit <= 12:
            raise ValueError("Use offset >= 0 and limit between 1 and 12.")
        options = options or CaptureOptions()
        deck = store.read(deck_id)
        selected = [
            (number, slide)
            for number, slide in enumerate(deck.slides, 1)
            if slide_id is None or slide.id == slide_id
        ]
        if slide_id is not None and not selected:
            raise ValueError("Slide not found.")
        page = selected if slide_id is not None else selected[offset : offset + limit]
        if not page:
            raise ValueError("No slides in this contact-sheet page.")
        prepared: list[Snapshot] = []
        prepared_bytes = 0
        for number, slide in page:
            item = snapshot(store, deck_id, slide, number, options)
            prepared_bytes += len(item.document.encode())
            if prepared_bytes > MAX_PAGE_BYTES:
                raise ValueError(
                    "Review page exceeds 96 MiB. Retry with a smaller contact-sheet limit."
                )
            prepared.append(item)
        images: list[bytes] = []
        try:
            with sync_playwright() as renderer:
                browser = renderer.chromium.launch(headless=True, chromium_sandbox=True)
                try:
                    context = browser.new_context(
                        viewport={"width": WIDTH, "height": HEIGHT},
                        device_scale_factor=1,
                        reduced_motion="reduce" if options.reduced_motion else "no-preference",
                        offline=True,
                        service_workers="block",
                        accept_downloads=False,
                    )
                    context.route("**/*", lambda route: route.abort())
                    for item in prepared:
                        tab = context.new_page()
                        try:
                            tab.set_default_timeout(15_000)
                            tab.set_content(item.document, wait_until="load", timeout=15_000)
                            item.frame.diagnostics = Diagnostics.model_validate(
                                tab.evaluate("() => window.SlideStudioCaptureReady")
                            )
                            images.append(
                                tab.screenshot(type="png", animations="allow", timeout=15_000)
                            )
                        finally:
                            tab.close()
                finally:
                    browser.close()
        except BrowserError as error:
            if "Executable doesn't exist" in str(error):
                raise ValueError(
                    "Install the local renderer: uv run playwright install chromium --only-shell"
                ) from error
            raise ValueError(f"Visual capture failed: {str(error)[:500]}") from error
        capture_id = uuid.uuid4().hex
        folder = output / capture_id
        folder.mkdir(parents=True)
        current = store.read(deck_id)
        structure_changed = [(s.id, s.hidden) for s in current.slides] != [
            (s.id, s.hidden) for s in deck.slides
        ]
        for item, image in zip(prepared, images, strict=True):
            path = folder / f"{item.frame.slide_number:03d}-{item.frame.slide_id}.png"
            _ = path.write_bytes(image)
            item.frame.path = str(path)
            latest = next(
                (slide for slide in current.slides if slide.id == item.frame.slide_id), None
            )
            item.frame.stale = (
                structure_changed
                or latest is None
                or store.content_hash(deck_id, latest) != item.frame.content_hash
                or snapshot(
                    store, deck_id, latest, item.frame.slide_number, options
                ).frame.render_hash
                != item.frame.render_hash
            )
        frames = [item.frame for item in prepared]
        result_path = Path(frames[0].path) if slide_id else folder / "contact-sheet.png"
        if slide_id is None:
            _ = result_path.write_bytes(contact_sheet(images, frames))
        result = VisualResult(
            capture_id=capture_id,
            deck_id=deck_id,
            captured_at=datetime.now(UTC).isoformat(),
            kind="slide" if slide_id else "contact_sheet",
            path=str(result_path),
            frames=frames,
            total_slides=len(deck.slides),
            next_offset=offset + len(page)
            if slide_id is None and offset + len(page) < len(deck.slides)
            else None,
        )
        _ = (folder / "manifest.json").write_text(result.model_dump_json(indent=2))
        return result
