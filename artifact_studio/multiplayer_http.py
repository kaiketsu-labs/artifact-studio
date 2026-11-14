"""Authenticated, bounded transport for the separate multiplayer application."""

import asyncio
import logging
import re
import tempfile
from collections.abc import Awaitable, Callable
from shutil import disk_usage
from time import monotonic
from typing import cast
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .authentication import AuthenticationError, AuthProvider, Principal
from .deployment import DeploymentConfig
from .design_transfer import MAX_DESIGN_ZIP
from .multiplayer_access import AccessDenied, Actor, Sharing, workspace_editor_path
from .multiplayer_access import Scope as GrantScope
from .request_diagnostics import RequestDiagnostics

_CHAT = re.compile(r"/api/workspaces/([^/]+)/artifacts/([^/]+)/chat")
_CHAT_FILE = re.compile(r"/api/workspaces/[^/]+/artifacts/[^/]+/chat/[^/]+/files/[^/]+")
_REQUEST_LIMIT = 2 * 1024 * 1024
_CHAT_LIMIT = 68 * 1024 * 1024
_OWNER_UPLOAD_LIMITS = {
    "/api/artifacts/import": 140 * 1024 * 1024,
    "/api/decks": _CHAT_LIMIT,
    "/api/design-library": _CHAT_LIMIT,
    "/api/design-library/import": MAX_DESIGN_ZIP,
}
_DESIGN_FILES = re.compile(
    r"/api/(?:design-library/[a-z0-9][a-z0-9-]{0,99}/files|"
    r"designs/[a-z0-9][a-z0-9-]{0,99}/references)"
)
_DESIGN_TEXT = re.compile(r"/api/design-library/[a-z0-9][a-z0-9-]{0,99}/files/edit")
IMPORT_DISK_RESERVE = 512 * 1024 * 1024
_LOGGER = logging.getLogger(__name__)
_BUSY_LOG_INTERVAL = 30


def owner_upload_limit(path: str) -> int | None:
    """Allow larger bodies only on the explicit owner upload routes."""
    if _DESIGN_FILES.fullmatch(path):
        return _CHAT_LIMIT
    if _DESIGN_TEXT.fullmatch(path):
        return 7 * 1024 * 1024
    return _OWNER_UPLOAD_LIMITS.get(path)


class MultiplayerAuth:
    def __init__(
        self,
        app: ASGIApp,
        config: DeploymentConfig,
        provider: AuthProvider,
        sharing: Sharing,
        *,
        shared_shell: bool = False,
    ) -> None:
        self.app, self.config, self.provider, self.sharing = app, config, provider, sharing
        self.host = urlsplit(config.public_url).netloc
        self.preview = urlsplit(config.preview_url).netloc
        self.shared_shell = shared_shell
        self.slots = asyncio.Semaphore(8)
        self.asset_slots = asyncio.Semaphore(16)
        self.asset_waiters = 0
        self.hook_slots = asyncio.Semaphore(8)
        self.agent_slots = asyncio.Semaphore(4)
        self.preview_slots = asyncio.Semaphore(4)
        self.chat_upload_slot = asyncio.Semaphore(1)
        self.chat_download_slots = asyncio.Semaphore(2)
        self.owner_upload_slot = asyncio.Semaphore(1)
        self._last_busy_log: dict[tuple[str, str], float] = {}

    def _log_busy(self, pool: str, reason: str) -> None:
        """Report overload using fixed categories without logging request data."""
        now = monotonic()
        key = (pool, reason)
        previous = self._last_busy_log.get(key)
        if previous is None or now - previous >= _BUSY_LOG_INTERVAL:
            self._last_busy_log[key] = now
            _LOGGER.warning(
                "Multiplayer admission busy: surface=%s pool=%s reason=%s",
                "shared_editor" if self.shared_shell else "collaboration",
                pool,
                reason,
            )

    async def _receive_owner_upload(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        reject: Callable[[int, str], Awaitable[None]],
        limit: int,
        label: str,
    ) -> None:
        """Stage a bounded owner upload before parsing its attachments."""
        if disk_usage(self.sharing.root).free < limit + IMPORT_DISK_RESERVE:
            await reject(507, "Not enough workspace storage for these files.")
            return
        with tempfile.TemporaryFile(dir=self.sharing.root) as staged:
            size = 0
            try:
                async with asyncio.timeout(120):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        size += len(chunk)
                        if size > limit:
                            await reject(413, f"{label} request is too large.")
                            return
                        _ = await run_in_threadpool(staged.write, chunk)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                await reject(408, f"{label} request timed out.")
                return
            _ = staged.seek(0)
            remaining, delivered = size, False

            async def replay() -> Message:
                nonlocal remaining, delivered
                if delivered and not remaining:
                    return await receive()
                delivered = True
                data = await run_in_threadpool(staged.read, min(remaining, 64 * 1024))
                remaining -= len(data)
                return {"type": "http.request", "body": data, "more_body": remaining > 0}

            await self.app(scope, replay, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await RequestDiagnostics(self._handle, self.preview)(scope, receive, send)

    async def _handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        headers = Headers(scope=scope)
        path, method = scope["path"], scope["method"]
        chat_write = _CHAT.fullmatch(path) if method == "POST" else None
        hosts = headers.getlist("host")
        host = hosts[0].lower() if len(hosts) == 1 else ""

        async def reject(status: int, detail: str) -> None:
            response_headers = {"Cache-Control": "no-store"}
            if status == 429:
                response_headers["Retry-After"] = "2"
            await JSONResponse({"detail": detail}, status_code=status, headers=response_headers)(
                scope, receive, send
            )

        if (
            path == "/api/health"
            and method == "GET"
            and host == "localhost"
            and scope.get("client")
            and scope["client"][0] in {"127.0.0.1", "::1"}
        ):
            await JSONResponse({"status": "ok"})(scope, receive, send)
            return
        if host not in {self.host, self.preview}:
            await reject(400, "Invalid host")
            return
        if any(part in {".", ".."} for part in path.split("/")) or any(c in path for c in "%\\\0"):
            await reject(404, "Not found")
            return
        if host == self.preview:
            if method not in {"GET", "HEAD"} or not path.startswith("/_preview/"):
                await reject(404, "Not found")
                return
        else:
            if path.startswith("/_preview/"):
                await reject(404, "Not found")
                return
            agent_path = not self.shared_shell and path in {"/mcp", "/mcp/", "/api/review/hooks"}
            origins = headers.getlist("origin")
            if (agent_path and origins) or (
                method not in {"GET", "HEAD"}
                and not agent_path
                and origins != [self.config.public_url]
            ):
                await reject(403, "Invalid origin")
                return
            if any(
                len(headers.getlist(key)) > 1
                for key in ("authorization", "cf-access-jwt-assertion")
            ):
                await reject(401, "Authentication required")
                return
            try:
                if agent_path:
                    authorization = headers.get("authorization", "")
                    if not authorization.startswith("Bearer "):
                        raise AccessDenied
                    actor = self.sharing.agent(authorization[7:])
                else:
                    verified = (
                        scope.get("state", {}).get("verified_guest") if self.shared_shell else None
                    )
                    principal = (
                        verified
                        if isinstance(verified, Principal)
                        else await self.provider.authenticate(headers)
                    )
                    actor = await run_in_threadpool(self.sharing.human, principal)
                    scope.setdefault("state", {})["authenticated_principal"] = principal
                    if self.shared_shell:
                        scope.setdefault("state", {})["verified_guest"] = principal
                scope.setdefault("state", {})["actor"] = actor
            except AuthenticationError, AccessDenied, ValueError:
                await reject(401, "Authentication required")
                return
            except Exception:
                await reject(503, "Authentication unavailable")
                return
        scope["scheme"] = "https"
        scope["server"] = (host, 443)
        scope["headers"] = [
            (key, value)
            for key, value in scope["headers"]
            if key.lower()
            not in {
                b"authorization",
                b"cookie",
                b"cf-access-jwt-assertion",
                b"forwarded",
                b"x-forwarded-for",
                b"x-forwarded-host",
                b"x-forwarded-proto",
            }
        ]

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                current = list(message.get("headers", []))
                current += [
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ]
                if not any(key.lower() == b"content-security-policy" for key, _ in current):
                    origin = self.config.public_url
                    style = " 'unsafe-inline'" if self.shared_shell else ""
                    frames = ""
                    if self.shared_shell:
                        workspace = scope.get("state", {}).get("editor_workspace", "default")
                        prefix = workspace_editor_path(str(workspace))
                        frames = (
                            f"{origin}{prefix}api/artifacts/ {origin}{prefix}api/decks/ "
                            f"{origin}{prefix}api/designs/ "
                            f"{origin}{prefix}decks/ about: "
                        )
                    base = "'self'" if self.shared_shell else "'none'"
                    images = "img-src 'self' data: blob:; " if self.shared_shell else ""
                    policy = (
                        f"default-src 'self'; script-src 'self'; style-src 'self'{style}; "
                        f"{images}"
                        f"frame-src {frames}{origin}/api/workspaces/ "
                        f"{self.config.preview_url}/_preview/; "
                        f"object-src 'none'; base-uri {base}; form-action 'self'; "
                        "frame-ancestors 'none'"
                    )
                    current.append((b"content-security-policy", policy.encode()))
                message["headers"] = current
            await send(message)

        # Keep cold editor asset loads out of the shared data request budget.
        asset = (
            self.shared_shell
            and host == self.host
            and method in {"GET", "HEAD"}
            and path.startswith(("/web/", "/runtime/"))
        )
        if asset and self.asset_waiters >= 256:
            self._log_busy("editor_assets", "queue_full")
            await reject(429, "Server busy. Retry shortly.")
            return
        upload_limit = (
            owner_upload_limit(path)
            if self.shared_shell and host == self.host and method == "POST"
            else None
        )
        if upload_limit is not None:
            actor = cast(Actor, scope["state"]["actor"])
            workspace = cast(str, scope["state"].get("editor_workspace", "default"))
            if not await run_in_threadpool(self.sharing.owner, actor, workspace):
                await reject(404, "Not found")
                return
            if self.owner_upload_slot.locked():
                self._log_busy("owner_upload", "already_active")
                await reject(429, "An upload is in progress. Try again shortly.")
                return
            async with self.owner_upload_slot:
                label = "Import" if path == "/api/artifacts/import" else "Upload"
                await self._receive_owner_upload(
                    scope, receive, secure_send, reject, upload_limit, label
                )
            return
        # Admit before buffering to bound memory even with many slow connections.
        pool, slots = (
            ("preview", self.preview_slots)
            if host == self.preview
            else ("editor_assets", self.asset_slots)
            if asset
            else ("chat_write", self.chat_upload_slot)
            if chat_write is not None
            else ("chat_download", self.chat_download_slots)
            if _CHAT_FILE.fullmatch(path)
            else ("review_hook", self.hook_slots)
            if path == "/api/review/hooks"
            else ("agent", self.agent_slots)
            if path in {"/mcp", "/mcp/"}
            else ("data", self.slots)
        )
        if asset:
            self.asset_waiters += 1
        try:
            await asyncio.wait_for(slots.acquire(), timeout=10 if asset else 1)
        except TimeoutError:
            self._log_busy(pool, "wait_timeout")
            await reject(429, "Server busy. Retry shortly.")
            return
        finally:
            if asset:
                self.asset_waiters -= 1
        try:
            if chat_write is not None:
                # Reject out-of-scope uploads before allocating a request body.
                actor = cast(Actor, scope["state"]["actor"])
                grant = GrantScope(chat_write[1], "artifact", chat_write[2])
                try:
                    await run_in_threadpool(self.sharing.require, actor, grant, "comment")
                except AccessDenied:
                    await reject(404, "Not found")
                    return
            data = bytearray()
            limit = _CHAT_LIMIT if chat_write is not None else _REQUEST_LIMIT
            async with asyncio.timeout(120 if chat_write is not None else 15):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(data) + len(chunk) > limit:
                        await reject(413, f"Request exceeds {limit // (1024 * 1024)} MiB")
                        return
                    data.extend(chunk)
                    if not message.get("more_body", False):
                        break
            delivered = False

            async def replay() -> Message:
                nonlocal delivered
                if delivered:
                    return await receive()
                delivered = True
                body = bytes(data)
                data.clear()
                return {"type": "http.request", "body": body, "more_body": False}

            await self.app(scope, replay, secure_send)
        except TimeoutError:
            await reject(408, "Request timed out")
        finally:
            slots.release()
