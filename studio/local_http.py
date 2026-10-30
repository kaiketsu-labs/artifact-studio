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


class NoRedirect(HTTPRedirectHandler):
    """Prevent local redirects from exposing private payloads."""

    def redirect_request(
        self, req: object, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        """Reject all redirects."""
        return None
