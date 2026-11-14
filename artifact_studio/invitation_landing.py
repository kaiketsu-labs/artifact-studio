"""Static invitation handoff; the protected continuation owns authentication."""

import base64
import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Literal

from starlette.responses import HTMLResponse

LANDING_PATH = "/_invitation"
CONTINUE_PATH = "/invitation"
_WEB = Path(__file__).resolve().parents[1] / "web"


def _hash(source: str) -> str:
    return base64.b64encode(hashlib.sha256(source.encode()).digest()).decode()


@lru_cache(maxsize=3)
def _page(mode: Literal["landing", "continue", "unavailable"]) -> tuple[str, str]:
    script = (_WEB / "invitation-handoff.js").read_text()
    style = (_WEB / "invitation-handoff.css").read_text()
    html = (_WEB / "invitation-handoff.html").read_text()
    html = html.replace("<!--STYLE-->", f"<style>{style}</style>")
    html = html.replace("<!--SCRIPT-->", f"<script>{script}</script>")
    html = html.replace('data-mode="landing"', f'data-mode="{mode}"')
    policy = (
        "default-src 'none'; "
        f"script-src 'sha256-{_hash(script)}'; style-src 'sha256-{_hash(style)}'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    return html, policy


def handoff_response(
    mode: Literal["landing", "continue", "unavailable"], *, head: bool = False
) -> HTMLResponse:
    """Return no identity, invitation metadata, external resources or credentials."""
    html, policy = _page(mode)
    return HTMLResponse(
        "" if head else html,
        status_code=401 if mode == "unavailable" else 200,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": policy,
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )
