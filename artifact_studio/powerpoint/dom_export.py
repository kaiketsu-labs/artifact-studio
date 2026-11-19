"""Read editable object geometry from the offline presentation renderer."""

from collections.abc import Sequence
from typing import cast

from playwright.sync_api import Page

from .. import PROJECT_ROOT
from ..models import Slide
from ..renderer_health import bounded_evaluate
from ..storage import Store
from ..visuals import (
    CaptureOptions,
    ScopedDeckEmbedder,
    Snapshot,
    _render_images,  # pyright: ignore[reportPrivateUsage]
    render_slot,
    slide_viewport,
    snapshot,
)

MAX_RENDERED_SLIDES = 500
MAX_LAYOUT_BYTES = 64 * 1024 * 1024
MAX_LAYOUT_OBJECTS = 24_000


def extract_dom_slides(
    store: Store,
    deck_id: str,
    slides: Sequence[Slide],
    *,
    scoped_embedder: ScopedDeckEmbedder | None = None,
    slide_numbers: Sequence[int] | None = None,
    viewport: tuple[int, int] | None = None,
) -> list[dict[str, object]]:
    """Extract native slide descriptions in source order using the existing browser sandbox."""
    if not slides:
        return []
    if len(slides) > MAX_RENDERED_SLIDES:
        raise ValueError("Editable PowerPoint export supports at most 500 HTML slides at a time.")
    if slide_numbers is None:
        slide_numbers = range(1, len(slides) + 1)
    if len(slide_numbers) != len(slides):
        raise ValueError("Provide one source slide number per rendered HTML slide.")
    # Inspect the normal audience motion rules before a static model can be returned.
    options = CaptureOptions()
    expression = (PROJECT_ROOT / "runtime/powerpoint-export.js").read_text()
    layouts: list[dict[str, object]] = []
    remaining_bytes = MAX_LAYOUT_BYTES
    remaining_objects = MAX_LAYOUT_OBJECTS
    sources = zip(slides, slide_numbers, strict=True)

    def extract(page: Page) -> dict[str, object]:
        nonlocal remaining_bytes, remaining_objects
        slide, number = next(sources)
        prefix = f'Slide {number} ("{slide.title}")'
        result = bounded_evaluate(
            page,
            expression,
            {"maxBytes": remaining_bytes, "maxObjects": remaining_objects},
        )
        if isinstance(result, dict):
            result = cast(dict[str, object], result)
            if detail := result.get("error"):
                raise ValueError(f"{prefix}: {detail}")
            layout = result.get("layout")
            size = result.get("modelBytes")
            count = result.get("nativeObjects")
            if (
                isinstance(layout, dict)
                and type(size) is int
                and type(count) is int
                and 0 < size <= remaining_bytes
                and 0 <= count <= remaining_objects
            ):
                remaining_bytes -= size
                remaining_objects -= count
                return cast(dict[str, object], layout)
        raise ValueError(f"{prefix}: The renderer returned an invalid editable layout.")

    with render_slot():
        # Keep the browser launch shared across a batch while limiting the number of
        # expanded snapshots held in process memory.
        for offset in range(0, len(slides), 4):
            batch = list(
                zip(slides[offset : offset + 4], slide_numbers[offset : offset + 4], strict=True)
            )
            prepared: list[Snapshot] = []
            for slide, number in batch:
                if slide.interactive_html:
                    raise ValueError(
                        f'Slide {number} ("{slide.title}") uses an interactive document. '
                        "Editable PowerPoint export requires a static slide article."
                    )
                natural_viewport = slide_viewport(slide.html)
                if viewport and natural_viewport != (1600, 900) and natural_viewport != viewport:
                    raise ValueError(
                        f'Slide {number} ("{slide.title}") uses a different canvas size.'
                    )
                try:
                    prepared.append(
                        snapshot(
                            store,
                            deck_id,
                            slide,
                            number,
                            options,
                            viewport=viewport,
                            scoped_embedder=scoped_embedder,
                        )
                    )
                except ValueError as error:
                    raise ValueError(f'Slide {number} ("{slide.title}"): {error}') from error

            layouts.extend(_render_images(prepared, options, extract))
    return layouts
