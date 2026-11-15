"""Stable browser URLs for feature files moved within the shipped web root."""

from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope


def web_asset_name(path: str) -> str:
    """Map only exact historical names; leave normal static-file validation intact."""
    return {
        "presentation-launcher.js": "presentation/launcher.js",
        "presentation-warmup.js": "presentation/warmup.js",
        "presentations.js": "presentation/library-actions.js",
    }.get(path, path)


class StudioWebFiles(StaticFiles):
    """Serve old and canonical paths from the same source without symlinks or redirects."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        return await super().get_response(web_asset_name(path), scope)
