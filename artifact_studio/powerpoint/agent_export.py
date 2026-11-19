"""Compact MCP receipts and a separate authenticated binary transfer."""

import re
from urllib.parse import quote

from ..artifacts import ArtifactStore
from ..storage import SAFE_ID, Store

AGENT_POWERPOINT_IMPORT = re.compile(r"/api/agent/projects/([a-zA-Z0-9_-]+)/powerpoint")
AGENT_POWERPOINT_EXPORT = re.compile(r"/api/agent/decks/([a-z0-9][a-z0-9-]{0,99})/exports/pptx")


def presentation_project(store: Store, deck_id: str) -> str:
    """Resolve the current membership without reading presentation content."""
    if not SAFE_ID.fullmatch(deck_id) or (store.root / deck_id).is_symlink():
        raise FileNotFoundError("Presentation unavailable.")
    return ArtifactStore(store).presentation_project(deck_id)


def export_receipt(deck_id: str, origin: str, *, agent_endpoint: bool = False) -> dict[str, str]:
    """Return the current download location without exporting bytes in MCP."""
    prefix = "/api/agent/decks/" if agent_endpoint else "/api/decks/"
    return {
        "deck_id": deck_id,
        "filename": f"{deck_id}.pptx",
        "download_url": f"{origin}{prefix}{quote(deck_id, safe='')}/exports/pptx",
    }
