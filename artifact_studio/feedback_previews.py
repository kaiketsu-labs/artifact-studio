"""Bounded raster previews of private chat attachments without serving active content."""

from functools import lru_cache
from pathlib import Path

from fastapi.responses import Response

from .design_thumbnails import design_thumbnail
from .models import FeedbackFile


@lru_cache(maxsize=32)
def _raster(path: Path, content_hash: str, filename: str) -> bytes:
    """Cache compact PNGs using the immutable attachment's identity and original name."""
    return design_thumbnail(path, filename=filename)


def feedback_image_response(file: FeedbackFile, path: Path, *, full: bool = False) -> Response:
    """Return a first-frame image or an empty response for an unsupported attachment."""
    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Content-Security-Policy": "sandbox; default-src 'none'",
    }
    try:
        data = (
            design_thumbnail(path, filename=file.name, maximum=2048)
            if full
            else _raster(path, file.sha256, file.name)
        )
    except FileNotFoundError:
        return Response(status_code=204, headers=headers)
    return Response(data, media_type="image/png", headers=headers)
