"""Short-lived, revision-scoped resources for opaque hosted preview frames."""

import base64
import hashlib
import hmac
import json
import mimetypes
import secrets
import time
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .artifact_preview import artifact_file_headers
from .artifacts import ArtifactStore

_WEB = frozenset(
    {
        "web/artifact-bridge.js",
        "web/vendor/marked-18.1.2.js",
        "web/marquee.js",
        "web/image-export.js",
    }
)
_PROJECT = Path(__file__).resolve().parent.parent


def _https_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Hosted preview origins must be canonical HTTPS origins.")
    return f"https://{parsed.netloc}"


class HostedPreviewService:
    """Issue capabilities signed with a process-local secret."""

    def __init__(
        self, store: ArtifactStore, public_url: str, preview_url: str, ttl: int = 3600
    ) -> None:
        self.store = store
        self.public_url = _https_origin(public_url)
        self.preview_url = _https_origin(preview_url)
        if self.public_url == self.preview_url or ttl < 1:
            raise ValueError(
                "Preview origin must differ from workspace origin and TTL must be positive."
            )
        self.ttl = ttl
        self._key = secrets.token_bytes(32)

    def issue(self, artifact_id: str, revision: int) -> str:
        """Return a resource root bound to an existing immutable revision."""
        self.store.get_artifact_revision(artifact_id, revision)
        payload = json.dumps(
            [artifact_id, revision, int(time.time()) + self.ttl], separators=(",", ":")
        ).encode()
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
        signature = hmac.new(self._key, encoded.encode(), hashlib.sha256).hexdigest()
        return f"{self.preview_url}/_preview/{encoded}.{signature}"

    def verify(self, token: str) -> tuple[str, int]:
        """Authenticate and check a capability without accepting malformed input."""
        try:
            encoded, signature = token.split(".")
            expected = hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            value = cast(
                list[object],
                json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))),
            )
            if (
                len(value) != 3
                or not isinstance(value[0], str)
                or type(value[1]) is not int
                or value[1] < 1
                or type(value[2]) is not int
                or value[2] <= time.time()
            ):
                raise ValueError
            return value[0], value[1]
        except (ValueError, UnicodeError, TypeError, json.JSONDecodeError) as error:
            raise HTTPException(status_code=404, detail="Preview resource unavailable") from error


def hosted_preview_router(service: HostedPreviewService) -> APIRouter:
    """Expose only signed bundle files and the four trusted runtime scripts."""
    router = APIRouter()

    @router.api_route("/_preview/{token}/{path:path}", methods=["GET", "HEAD"])
    def resource(token: str, path: str, request: Request) -> FileResponse:
        if str(request.base_url).rstrip("/") != service.preview_url:
            raise HTTPException(status_code=404, detail="Preview resource unavailable")
        artifact_id, revision = service.verify(token)
        root = f"{service.preview_url}/_preview/{token}"
        # Starlette decodes path parameters, including percent escapes.
        decoded_prefix = f"api/artifacts/{artifact_id}/revisions/{revision}/files/"
        try:
            if path in _WEB:
                target = _PROJECT / path
            elif path.startswith(decoded_prefix):
                target = service.store.resolve_file(
                    artifact_id, path[len(decoded_prefix) :], revision
                )
            else:
                raise ValueError
        except (ValueError, FileNotFoundError, KeyError) as error:
            raise HTTPException(status_code=404, detail="Preview resource unavailable") from error
        return FileResponse(
            target,
            media_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
            headers=artifact_file_headers(artifact_id, revision, service.public_url, root),
        )

    return router
