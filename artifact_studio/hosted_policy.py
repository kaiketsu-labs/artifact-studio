"""Explicit hosted workspace route and presentation asset boundaries."""

import re
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .storage import Store

# Every entry names an existing route. New routes are unavailable until reviewed.
_ID = r"[^/]+"
_TAIL = r"[^/]+(?:/[^/]+)*"
_READ = (
    r"/",
    r"/api/deployment/security",
    r"/api/health",
    r"/api/session",
    r"/api/agent/hooks-client",
    r"/api/shares/capabilities",
    rf"/api/shares/{_ID}/file",
    r"/api/app-info",
    r"/api/decks",
    r"/api/removed-presentations",
    r"/api/preferences",
    r"/api/instructions",
    r"/api/designs",
    r"/api/design-requests",
    r"/api/navigation",
    r"/api/hub",
    r"/api/hub/context",
    r"/api/design-library/deleted",
    r"/api/favorites",
    r"/api/favorites/slides",
    r"/api/chat-references",
    rf"/api/decks/{_ID}",
    rf"/api/decks/{_ID}/(?:events|workflow|deleted-slides|audience)",
    rf"/api/decks/{_ID}/exports/(?:html|pdf)",
    rf"/api/decks/{_ID}/brief-files/{_ID}",
    rf"/api/decks/{_ID}/feedback/{_ID}/files/{_ID}",
    rf"/api/decks/{_ID}/files/{_TAIL}",
    rf"/api/decks/{_ID}/slides/{_ID}/preview",
    rf"/api/decks/{_ID}/slides/{_ID}/history(?:/preview|/thumbnail)?",
    rf"/api/decks/{_ID}/slides/{_ID}/designs(?:/current-preview|/{_ID}/preview)?",
    rf"/api/decks/{_ID}/slides/{_ID}/element-alternatives(?:/{_ID}/preview)?",
    rf"/api/designs/{_ID}/(?:references|preview)",
    rf"/api/designs/{_ID}/files/{_TAIL}",
    rf"/api/design-requests/{_ID}/files/{_ID}",
    rf"/api/design-library/{_ID}",
    rf"/api/design-library/{_ID}/(?:files|thumbnail|image)/{_TAIL}",
    rf"/api/artifacts/{_ID}",
    rf"/api/artifacts/{_ID}/(?:preview|history|history/thumbnail)",
    rf"/api/artifacts/{_ID}/feedback/{_ID}/files/{_ID}",
    rf"/api/artifacts/{_ID}/files/{_TAIL}",
    rf"/api/artifacts/{_ID}/revisions/{_ID}/files/{_TAIL}",
    rf"/api/hub/previews/{_ID}",
    rf"/decks/{_ID}/(?:presentation\.js|icons\.js|slideshow\.css|theme\.css)",
    rf"/decks/{_ID}/presenter",
    rf"/decks/{_ID}/assets/{_TAIL}",
    rf"/web/{_TAIL}",
    rf"/runtime/{_TAIL}",
)
_WRITE = (
    r"/api/decks",
    r"/mcp",
    r"/api/review/hooks",
    rf"/api/decks/{_ID}/review/disconnect",
    rf"/api/artifacts/{_ID}/review/disconnect",
    r"/api/artifacts/import",
    r"/api/shares",
    r"/api/projects/open",
    r"/api/instructions",
    r"/api/design-requests",
    r"/api/navigation/visit",
    r"/api/hub/projects",
    r"/api/hub/project-tree",
    r"/api/design-library",
    r"/api/favorites",
    r"/api/chat-references",
    rf"/api/decks/{_ID}/(?:archive|restore|slide-order|sections|slide-actions|restore-slides|slides-visibility|project|feedback-mode|send-batch|feedback)",
    rf"/api/decks/{_ID}/slides/{_ID}/(?:seen|visibility|approve|feedback|notes|selection-image)",
    rf"/api/decks/{_ID}/slides/{_ID}/questions/{_ID}/answer",
    rf"/api/decks/{_ID}/slides/{_ID}/(?:history/restore|designs/decide|designs/undo|element-alternatives/decide|element-alternatives/undo)",
    rf"/api/designs/{_ID}/references(?:/remove)?",
    rf"/api/design-library/{_ID}/(?:default|files|files/remove|files/edit|delete|restore)",
    rf"/api/favorites/slides/{_ID}/{_ID}/(?:description|reference)",
    rf"/api/artifacts/{_ID}/(?:project|feedback|approve|seen|mode|release|selection-image|history/restore)",
)
_READ_PATTERNS = tuple(re.compile(pattern) for pattern in _READ)
_WRITE_PATTERNS = tuple(re.compile(pattern) for pattern in _WRITE)


def authorize(method: str, path: str) -> bool:
    """Accept only explicit routes and canonical decoded paths, never a route prefix."""
    if not path.startswith("/") or any(c in path for c in ("%", "\\", "\x00", "?", "#")):
        return False
    if (
        any(part in {"", ".", ".."} or part.startswith(".") for part in path.split("/")[1:])
        and path != "/"
    ):
        return False
    patterns = (
        _READ_PATTERNS if method in {"GET", "HEAD"} else _WRITE_PATTERNS if method == "POST" else ()
    )
    return any(pattern.fullmatch(path) for pattern in patterns)


# Only passive resources used by presentation previews. Active SVG is sandboxed on navigation.
_ASSET_TYPES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".avif",
    ".svg",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".mp4",
    ".webm",
    ".mp3",
    ".wav",
}


def scoped_deck_routes(get_store: Callable[[], Store]) -> APIRouter:
    """Serve only a real presentation's theme and passive assets, never its HTML."""
    router = APIRouter()

    @router.get("/decks/{deck_id}/{asset_path:path}")
    def deck_asset(deck_id: str, asset_path: str) -> FileResponse:
        parts = asset_path.split("/")
        if any(not part or part.startswith(".") or "\\" in part for part in parts):
            raise HTTPException(status_code=404)
        if asset_path != "theme.css" and not (
            len(parts) >= 2
            and parts[0] == "assets"
            and Path(parts[-1]).suffix.lower() in _ASSET_TYPES
        ):
            raise HTTPException(status_code=404)
        store = get_store()
        try:
            # A valid embedded document establishes that this is an actual deck.
            if (store.root / deck_id).is_symlink():
                raise ValueError("Symlinks are not served")
            store.read(deck_id)
            folder = store.folder(deck_id)
            target = folder
            for part in parts:
                target = target / part
                if target.is_symlink():
                    raise ValueError("Symlinks are not served")
            if not target.is_file() or not target.resolve().is_relative_to(folder):
                raise ValueError("Missing asset")
        except ValueError, OSError:
            raise HTTPException(status_code=404) from None
        return FileResponse(
            target,
            headers={
                "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router
