"""Local, version-anchored slide images and paginated visual review sheets."""

import hashlib
import io
import json
import math
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Annotated, Literal

from PIL import Image, ImageDraw
from playwright.sync_api import Error as BrowserError
from playwright.sync_api import Page, sync_playwright
from pydantic import Field

from .models import Slide, StrictModel
from .projects import AssetEmbedder, root_tag
from .renderer_health import check_renderer_available, renderer_status, sandboxed_browser
from .selection_image import SelectionImage, SelectionImageInput, render_selection
from .storage import SlideMarkup, Store, digest

PROJECT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 1600, 900
MAX_ASSET_BYTES = 25 * 1024 * 1024
MAX_DEPENDENCY_BYTES = 64 * 1024 * 1024
MAX_PAGE_BYTES = 96 * 1024 * 1024
MAX_CONTACT_SLIDES = 24


class RenderQueue:
    """A bounded FIFO shared by slide, artifact and object-image captures."""

    def __init__(self, capacity: int = 8, timeout: float = 30) -> None:
        self.condition = threading.Condition()
        self.waiters: deque[object] = deque()
        self.owner: int | None = None
        self.capacity = capacity
        self.timeout = timeout

    def status(self) -> dict[str, object]:
        """Describe queue occupancy without revealing other capture targets."""
        with self.condition:
            return {
                "running": self.owner is not None,
                "queued": len(self.waiters),
                "capacity": self.capacity,
                "wait_timeout_seconds": self.timeout,
            }

    @contextmanager
    def slot(self) -> Generator[int]:
        """Wait in arrival order, removing timed-out requests and releasing on failure."""
        started = time.monotonic()
        ticket = object()
        with self.condition:
            if self.owner == threading.get_ident():
                raise ValueError("A capture cannot request another renderer while it is running.")
            if len(self.waiters) >= self.capacity:
                raise ValueError(
                    "The visual capture queue is full. Retry after a capture finishes."
                )
            self.waiters.append(ticket)
            self.condition.notify_all()
            try:
                while self.owner is not None or self.waiters[0] is not ticket:
                    remaining = self.timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise ValueError(
                            "Visual capture timed out while queued. Check get_capture_status "
                            "and retry; this request did not start rendering."
                        )
                    _ = self.condition.wait(remaining)
                self.owner = threading.get_ident()
            finally:
                self.waiters.remove(ticket)
                self.condition.notify_all()
        try:
            yield round((time.monotonic() - started) * 1000)
        finally:
            with self.condition:
                self.owner = None
                self.condition.notify_all()


_RENDER_QUEUE = RenderQueue()


def capture_queue_status(probe_renderer: bool = False) -> dict[str, object]:
    """Return queue occupancy and last launch status; explicitly probe without loading a deck."""
    if probe_renderer:
        try:
            with _RENDER_QUEUE.slot(), sandboxed_browser(sync_playwright, force=True):
                pass
        except ValueError as error:
            return {
                **_RENDER_QUEUE.status(),
                "renderer": renderer_status(),
                "probe_error": str(error),
            }
    return {**_RENDER_QUEUE.status(), "renderer": renderer_status()}


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
def render_slot() -> Generator[int]:
    """Serialize preparation, rendering and persistence so concurrent requests stay bounded."""
    check_renderer_available()
    with _RENDER_QUEUE.slot() as waited_ms:
        check_renderer_available()
        yield waited_ms


class CaptureOptions(StrictModel):
    """A reproducible reveal stage and elapsed time within that stage."""

    stage: Annotated[int | None, Field(ge=0, le=50)] = None
    elapsed_ms: Annotated[int, Field(ge=0, le=600_000)] = 0
    reduced_motion: bool = False
    resolution: Literal["full", "draft"] = "full"

    @property
    def pixel_ratio(self) -> float:
        """Reduce raster pixels without reflowing the presentation's CSS layout."""
        return 0.5 if self.resolution == "draft" else 1


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
    viewport_width: int = WIDTH
    viewport_height: int = HEIGHT
    path: str = ""
    image_sha256: str = ""
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
    selected_slides: int
    resolution: Literal["full", "draft"] = "full"
    queue_wait_ms: int = 0
    next_offset: int | None = None
    scope: str = "Slide content only; not presenter controls or physical-display verification."


class Snapshot(StrictModel):
    """An immutable audience-only document prepared before starting the renderer."""

    document: str
    frame: Frame


def snapshot(
    store: Store,
    deck_id: str,
    slide: Slide,
    number: int,
    options: CaptureOptions,
    *,
    viewport: tuple[int, int] | None = None,
    preview_html: str = "",
) -> Snapshot:
    """Embed bounded local assets and exclude private context from the renderer document."""
    if slide.interactive_html and options.stage not in {None, 0}:
        raise ValueError(
            "Custom interactive slides use stage 0 plus elapsed_ms, or null for final."
        )
    width, height = viewport or (WIDTH, HEIGHT)
    content_hash = store.content_hash(deck_id, slide)
    SlideMarkup().feed(slide.html)
    folder = store.folder(deck_id)
    embedder = ReviewEmbedder(folder)
    theme = embedder.css(embedder.local_path("theme.css").read_text()).replace("<", r"\3c ")
    markup = embedder.markup(preview_html or slide.html)
    interactive = "" if preview_html else slide.interactive_html
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
        + f"html,body{{margin:0;width:{width}px;height:{height}px;overflow:hidden}}"
        + f"#capture-stage{{position:relative;width:{width}px;height:{height}px;"
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
            width=round(width * options.pixel_ratio),
            height=round(height * options.pixel_ratio),
            viewport_width=width,
            viewport_height=height,
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
                + json.dumps([width, height])
            ),
            stage=options.stage,
            elapsed_ms=options.elapsed_ms,
            reduced_motion=options.reduced_motion,
        ),
    )


def contact_sheet(images: list[bytes], frames: list[Frame], *, draft: bool = False) -> bytes:
    """Compose labeled thumbnails while retaining individual frames at the chosen resolution."""
    columns = min(4, math.ceil(math.sqrt(len(images))))
    tile_width, image_height, gap = (320, 180, 12) if draft else (480, 270, 16)
    tile_height = image_height + 32
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
            thumb.thumbnail((tile_width, image_height), Image.Resampling.LANCZOS)
        sheet.paste(thumb, (left, top))
        label = f"{frame.slide_number:02d} · {frame.title}" + (" · Hidden" if frame.hidden else "")
        draw.text(
            (left + 6, top + image_height + 8),
            label[: 45 if draft else 70],
            fill="#172b49",
            font_size=15,
        )
    output = io.BytesIO()
    sheet.save(output, format="PNG")
    return output.getvalue()


def _render_images[T](
    prepared: list[Snapshot],
    options: CaptureOptions,
    paint: Callable[[Page], T],
    *,
    viewport: tuple[int, int] = (WIDTH, HEIGHT),
    color_scheme: Literal["light", "dark"] = "light",
) -> list[T]:
    """Render prepared audience documents through the shared offline browser boundary."""
    images: list[T] = []
    try:
        with sandboxed_browser(sync_playwright) as browser:
            context = browser.new_context(
                viewport={"width": viewport[0], "height": viewport[1]},
                color_scheme=color_scheme,
                device_scale_factor=options.pixel_ratio,
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
                        tab.evaluate("() => window.ArtifactStudioCaptureReady")
                    )
                    images.append(paint(tab))
                finally:
                    tab.close()
    except BrowserError as error:
        raise ValueError(f"Visual capture failed: {str(error)[:500]}") from error
    return images


def capture_snapshot(prepared: Snapshot, options: CaptureOptions) -> bytes:
    """Capture one prepared saved slide without changing the current presentation."""
    with render_slot():
        return _render_images(
            [prepared],
            options,
            _screenshot,
            viewport=(prepared.frame.viewport_width, prepared.frame.viewport_height),
        )[0]


def _screenshot(page: Page) -> bytes:
    """Keep ordinary visual captures unchanged when no object export was requested."""
    return page.screenshot(type="png", animations="allow", timeout=15_000)


def export_snapshot(prepared: Snapshot, request: SelectionImageInput) -> SelectionImage:
    """Export selected slide objects through the existing sandboxed render boundary."""
    with render_slot():
        return _render_images(
            [prepared],
            CaptureOptions(reduced_motion=True),
            lambda page: render_selection(page, request),
            viewport=(request.width, request.height),
            color_scheme=request.color_scheme,
        )[0]


def capture(
    store: Store,
    deck_id: str,
    output: Path,
    *,
    slide_id: str | None = None,
    slide_ids: list[str] | None = None,
    options: CaptureOptions | None = None,
    offset: int = 0,
    limit: int = MAX_CONTACT_SLIDES,
) -> VisualResult:
    """Render a slide or contact-sheet page without opening a server URL or user profile."""
    if offset < 0 or not 1 <= limit <= MAX_CONTACT_SLIDES:
        raise ValueError(f"Use offset >= 0 and limit between 1 and {MAX_CONTACT_SLIDES}.")
    if slide_id is not None and slide_ids is not None:
        raise ValueError("Choose slide_id or slide_ids, not both.")
    if slide_ids is not None and (
        not 1 <= len(slide_ids) <= 500 or len(slide_ids) != len(set(slide_ids))
    ):
        raise ValueError("Provide 1–500 distinct slide_ids for a contact sheet.")
    with render_slot() as waited_ms:
        options = options or CaptureOptions(resolution="draft" if slide_id is None else "full")
        deck = store.read(deck_id)
        requested = set(slide_ids) if slide_ids is not None else None
        if requested is not None and requested - {slide.id for slide in deck.slides}:
            raise ValueError("One or more slide_ids were not found in this presentation.")
        selected = [
            (number, slide)
            for number, slide in enumerate(deck.slides, 1)
            if (slide_id is None or slide.id == slide_id)
            and (requested is None or slide.id in requested)
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
        images = _render_images(prepared, options, _screenshot)
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
            item.frame.image_sha256 = hashlib.sha256(image).hexdigest()
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
            _ = result_path.write_bytes(
                contact_sheet(images, frames, draft=options.resolution == "draft")
            )
        result = VisualResult(
            capture_id=capture_id,
            deck_id=deck_id,
            captured_at=datetime.now(UTC).isoformat(),
            kind="slide" if slide_id else "contact_sheet",
            path=str(result_path),
            frames=frames,
            total_slides=len(deck.slides),
            selected_slides=len(selected),
            resolution=options.resolution,
            queue_wait_ms=waited_ms,
            next_offset=offset + len(page)
            if slide_id is None and offset + len(page) < len(selected)
            else None,
        )
        _ = (folder / "manifest.json").write_text(result.model_dump_json(indent=2))
        return result
