"""Render inert, self-contained examples for the hosted design library and picker."""

from fastapi.responses import HTMLResponse

from .projects import root_tag
from .storage import Store
from .visuals import ScopedDeckEmbedder


def hosted_design_preview(store: Store, design_id: str, public_url: str) -> HTMLResponse:
    """Embed the authorized pack's cover and its bounded local assets without scripts."""
    folder_id = store.design(design_id, include_archived=False).folder_id
    embedder = ScopedDeckEmbedder(store, folder_id, embedded=True)
    slide = store.read(folder_id).slides[0]
    root = root_tag((embedder.folder / "index.html").read_text())
    theme = embedder.css(embedder.local_path("theme.css").read_text()).replace("<", r"\3c ")
    markup = embedder.markup(slide.html)
    return HTMLResponse(
        "<!doctype html>"
        + root
        + '<head><meta charset="utf-8"><style>'
        + theme
        + "\nhtml,body{width:100%;height:100%;margin:0;overflow:hidden}</style></head><body>"
        + markup
        + "</body></html>",
        headers={
            "Content-Security-Policy": (
                "default-src 'none'; sandbox; script-src 'none'; style-src 'unsafe-inline'; "
                "img-src data:; font-src data:; media-src data:; base-uri 'none'; "
                f"form-action 'none'; frame-ancestors {public_url}"
            ),
        },
    )
