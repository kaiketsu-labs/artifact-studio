"""Build an audience PowerPoint from one consistent revision of a presentation."""

from io import BytesIO
from threading import BoundedSemaphore

from fastapi.responses import Response

from ..models import Deck, Slide
from ..renderer_health import RendererBusy
from ..storage import ConflictError, Store
from ..visuals import WIDTH, ScopedDeckEmbedder, slide_viewport
from .package import P, Package

MIME_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_EXPORTS = BoundedSemaphore(2)


def _visible_slides(deck: Deck) -> list[Slide]:
    slides = [slide for slide in deck.slides if not slide.hidden]
    if not slides:
        raise ValueError("Make at least one slide visible before exporting to PowerPoint.")
    return slides


def _version(store: Store, deck_id: str, slides: list[Slide]) -> tuple[str, list[tuple[str, str]]]:
    theme = store.visual_hash(deck_id)
    return theme, [
        (slide.id, store.content_hash(deck_id, slide, design_hash=theme)) for slide in slides
    ]


def _dimensions(data: bytes) -> tuple[int, int]:
    package = Package(data)
    try:
        size = package.xml(package.presentation).find(P + "sldSz")
        if size is None:
            raise ValueError("The source presentation has no canvas dimensions.")
        return int(size.attrib["cx"]), int(size.attrib["cy"])
    finally:
        package.archive.close()


def _build(store: Store, deck_id: str, deck: Deck) -> bytes:
    from .combine import combine_slides
    from .dom_export import extract_dom_slides
    from .exporter import write_powerpoint
    from .roundtrip import export_powerpoint, has_powerpoint_source, source_slide_ids

    visible = _visible_slides(deck)
    retained: set[str] = (
        source_slide_ids(store, deck_id) if has_powerpoint_source(store, deck_id) else set()
    )
    originals = {slide.id for slide in visible if slide.id in retained}
    if len(originals) == len(visible):
        return export_powerpoint(store, deck_id)
    fresh = [slide for slide in visible if slide.id not in originals]
    if any(
        "data-pptx-width=" in slide.html and "data-pptx-object=" in slide.html for slide in fresh
    ):
        raise ValueError(
            "The source PowerPoint for an imported slide is unavailable. Import the original "
            ".pptx into a new presentation to enable source-preserving export."
        )
    source = export_powerpoint(store, deck_id, slide_ids=originals) if originals else None
    dimensions = _dimensions(source) if source is not None else None
    viewport = (
        (WIDTH, round(WIDTH * dimensions[1] / dimensions[0]))
        if dimensions
        else slide_viewport(deck.slides[0].html)
    )
    numbers = {slide.id: number for number, slide in enumerate(deck.slides, 1)}
    model = extract_dom_slides(
        store,
        deck_id,
        fresh,
        slide_numbers=[numbers[slide.id] for slide in fresh],
        scoped_embedder=ScopedDeckEmbedder(store, deck_id),
        viewport=viewport,
    )
    stream = BytesIO()
    write_powerpoint(model, stream, size_emu=dimensions, title=deck.title)
    if source is None:
        return stream.getvalue()
    return combine_slides(source, stream.getvalue(), [slide.id in originals for slide in visible])


def powerpoint_bytes(store: Store, deck_id: str) -> bytes:
    """Create a complete, editable .pptx or raise before returning any of its bytes."""
    if not _EXPORTS.acquire(blocking=False):
        raise RendererBusy("PowerPoint export is busy. Try again when an export finishes.")
    try:
        folder = store.folder(deck_id)
        if (store.root / deck_id).is_symlink() or any(
            (folder / name).is_symlink() for name in ("index.html", "theme.css")
        ):
            raise ValueError("PowerPoint export requires regular presentation files.")
        deck = store.read(deck_id)
        slides = _visible_slides(deck)
        if len(slides) > 100:
            raise ValueError("PowerPoint export supports up to 100 visible slides.")
        version = _version(store, deck_id, slides)
        result = _build(store, deck_id, deck)
        if version != _version(store, deck_id, _visible_slides(store.read(deck_id))):
            raise ConflictError(
                "The presentation changed during export. Export the latest revision."
            )
        return result
    finally:
        _EXPORTS.release()


def powerpoint_response(data: bytes, deck_id: str) -> Response:
    return Response(
        data,
        media_type=MIME_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{deck_id}.pptx"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
