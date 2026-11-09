"""Shared loopback-only HTTP boundaries for local agent clients."""

from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler


def validate_local_url(url: str) -> None:
    """Allow local HTTP without credentials, query parameters or fragments."""
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid local server URL") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port == 0
    ):
        raise ValueError("server URL must use local HTTP")


def validate_hook_url(url: str) -> None:
    """Accept loopback HTTP or a canonical credential-free HTTPS workspace origin."""
    parsed = urlsplit(url)
    if parsed.scheme == "http":
        validate_local_url(url)
        return
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid workspace origin") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port == 0
        or any(ord(char) < 33 or ord(char) > 126 for char in url)
        or "\\" in url
    ):
        raise ValueError("Hosted hooks require a canonical HTTPS workspace origin")


class NoRedirect(HTTPRedirectHandler):
    """Prevent local redirects from exposing private payloads."""

    def redirect_request(
        self, req: object, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        """Reject all redirects."""
        return None
