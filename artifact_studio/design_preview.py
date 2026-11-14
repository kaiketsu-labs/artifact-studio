"""Render inert, self-contained thumbnails for hosted slides and design covers."""

from pathlib import Path
from urllib.parse import urlsplit

from fastapi.responses import HTMLResponse

from .projects import root_tag
from .storage import SlideMarkup, Store
from .visuals import MAX_PAGE_BYTES, ScopedDeckEmbedder

_VIEWER_STYLES = Path(__file__).resolve().parent.parent / "runtime/viewer.css"


def _remote(url: str) -> bool:
    """Identify assets that must stay out of a hosted cover."""
    parsed = urlsplit(url)
    return bool(parsed.netloc or (parsed.scheme and parsed.scheme.lower() != "data"))


class ThumbnailAssets(ScopedDeckEmbedder):
    """Embed local thumbnail assets and omit external references."""

    def asset(self, url: str, directory: Path | None = None) -> str:
        """Replace an external resource with an empty inert URL."""
        return "data:," if _remote(url) else super().asset(url, directory)

    def omit_css_import(self, url: str) -> bool:
        """Keep the local stylesheet rules when an external import cannot load."""
        return _remote(url)


def hosted_design_preview(store: Store, design_id: str, public_url: str) -> HTMLResponse:
    """Embed the authorized pack's cover and its bounded local assets without scripts."""
    folder_id = store.design(design_id, include_archived=False, validate_sources=False).folder_id
    return _thumbnail_response(store, folder_id, public_url, design_id=design_id)


def hosted_slide_thumbnail(
    store: Store, deck_id: str, slide_id: str, public_url: str
) -> HTMLResponse:
    """Show a favorite slide's authored static fallback inside its sandboxed card."""
    return _thumbnail_response(store, deck_id, public_url, slide_id=slide_id)


def _thumbnail_response(
    store: Store, folder_id: str, public_url: str, *, slide_id: str = "", design_id: str = ""
) -> HTMLResponse:
    """Apply the same asset and frame boundaries to either thumbnail surface."""
    try:
        document = _thumbnail_document(store, folder_id, slide_id=slide_id, design_id=design_id)
    except OSError, ValueError:
        document = (
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            "<style>html,body{height:100%;margin:0}body{display:grid;place-items:center;"
            "background:#f6f8fb;color:#52627a;font:64px system-ui,sans-serif}</style>"
            "<body>Preview unavailable</body></html>"
        )
    return HTMLResponse(
        document,
        headers={
            "Content-Security-Policy": (
                "default-src 'none'; sandbox; script-src 'none'; style-src 'unsafe-inline'; "
                "img-src data:; font-src data:; media-src data:; base-uri 'none'; "
                f"form-action 'none'; frame-ancestors {public_url}"
            ),
        },
    )


def _thumbnail_document(store: Store, folder_id: str, *, slide_id: str, design_id: str) -> str:
    """Build one preview document while bounding the input and expanded output."""
    embedder = ThumbnailAssets(store, folder_id, embedded=True)
    with (embedder.folder / "index.html").open("rb") as source:
        data = source.read(MAX_PAGE_BYTES + 1)
    if len(data) > MAX_PAGE_BYTES:
        raise ValueError("Thumbnail source exceeds its size limit.")
    html = data.decode()
    deck = store.parse_deck(html)
    if design_id and deck.design_id != design_id:
        raise ValueError("Design preview source does not match its registered design.")
    slide = store.locate(deck, slide_id) if slide_id else deck.slides[0]
    if slide_id:
        SlideMarkup().feed(slide.html)
    theme = embedder.css(embedder.local_path("theme.css").read_text())
    styles = (_VIEWER_STYLES.read_text() + "\n" + theme if slide_id else theme).replace(
        "<", r"\3c "
    )
    markup = embedder.markup(slide.html)
    document = (
        "<!doctype html>"
        + root_tag(html)
        + '<head><meta charset="utf-8"><style>'
        + styles
        + "\nhtml,body{width:100%;height:100%;margin:0;overflow:hidden}</style></head><body>"
        + markup
        + "</body></html>"
    )
    if len(document.encode()) > MAX_PAGE_BYTES:
        raise ValueError("Thumbnail exceeds its size limit.")
    return document
