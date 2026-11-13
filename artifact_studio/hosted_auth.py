"""ASGI security boundary for the hosted workspace and isolated previews."""

import asyncio
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .authentication import AuthenticationError, AuthProvider, Principal
from .deployment import DeploymentConfig
from .design_transfer import MAX_DESIGN_ZIP


class HostedAuth:
    def __init__(
        self,
        app: ASGIApp,
        config: DeploymentConfig,
        provider: AuthProvider,
        authorize: Callable[[str, str], bool],
    ) -> None:
        self.app, self.config, self.provider, self.authorize = app, config, provider, authorize
        self.host = urlsplit(config.public_url).netloc
        self.preview_host = urlsplit(config.preview_url).netloc
        self._mcp_slots = asyncio.Semaphore(4)
        self._hook_slots = asyncio.Semaphore(8)
        self._write_slots = asyncio.Semaphore(4)
        self._upload_slots = asyncio.Semaphore(1)
        self._preview_slots = asyncio.Semaphore(4)
        self._preview_waiters = 0
        self.preview_wait_timeout = 60.0
        self.body_timeout = 120.0
        self.mcp_timeout = 90.0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        headers = Headers(scope=scope)
        hosts = headers.getlist("host")
        host = hosts[0].lower() if len(hosts) == 1 else ""
        path, method = scope["path"], scope["method"].upper()

        async def reply(status: int, message: str) -> None:
            response_headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
            if status == 429:
                response_headers["Retry-After"] = "2"
            if status == 401:
                response_headers["WWW-Authenticate"] = "Bearer"
            await JSONResponse(
                {"detail": message},
                status_code=status,
                headers=response_headers,
            )(scope, receive, send)

        if (
            host
            in ("localhost", "localhost:8790", "127.0.0.1", "127.0.0.1:8790", "[::1]", "[::1]:8790")
            and path == "/api/health"
            and method == "GET"
            and scope.get("client")
            and scope["client"][0] in ("127.0.0.1", "::1")
        ):
            await JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})(
                scope, receive, send
            )
            return
        if host not in (self.host, self.preview_host):
            await reply(400, "Invalid host")
            return
        is_preview = host == self.preview_host
        if is_preview:
            if method not in ("GET", "HEAD") or not path.startswith("/_preview/"):
                await reply(404, "Not found")
                return
        else:
            if not self.authorize(method, path):
                await reply(404, "Not found")
                return
            if (
                len(headers.getlist("authorization")) > 1
                or len(headers.getlist("cf-access-jwt-assertion")) > 1
                or (headers.getlist("authorization") and headers.getlist("cf-access-jwt-assertion"))
            ):
                await reply(401, "Authentication required")
                return
            try:
                principal = cast(object, await self.provider.authenticate(headers))
            except AuthenticationError:
                await reply(401, "Authentication required")
                return
            except Exception:
                await reply(503, "Authentication unavailable")
                return
            if not isinstance(principal, Principal):
                await reply(503, "Authentication unavailable")
                return
            if not principal.subject:
                await reply(503, "Authentication unavailable")
                return
            if principal.service:
                member = principal.subject in self.config.agent_subjects and (
                    path == "/mcp" or path.startswith("/mcp/") or path == "/api/review/hooks"
                )
            else:
                member = bool(
                    principal.email and principal.email.lower() in self.config.member_emails
                )
            if path == "/api/review/hooks" and not principal.service:
                await reply(403, "Service credentials required")
                return
            if not member:
                shared_editor = scope.get("state", {}).get("shared_editor")
                if callable(shared_editor) and not principal.service and principal.email:
                    scope.setdefault("state", {})["verified_guest"] = principal
                    await cast(ASGIApp, shared_editor)(scope, receive, send)
                    return
                await reply(403, "Forbidden")
                return
            if (
                path.startswith("/api/artifacts/")
                and path.count("/") == 4
                and path.endswith("/preview")
                and headers.get("sec-fetch-dest") not in {"iframe", "frame"}
            ):
                await reply(403, "Open this preview inside the workspace.")
                return
            mcp = path == "/mcp" or path.startswith("/mcp/") or path == "/api/review/hooks"
            origins = headers.getlist("origin")
            if (mcp and origins) or (
                not mcp
                and method not in ("GET", "HEAD", "OPTIONS")
                and (len(origins) != 1 or origins[0] != self.config.public_url)
            ):
                await reply(403, "Forbidden")
                return
            scope.setdefault("state", {})["principal"] = principal
        scope["scheme"] = "https"
        scope["server"] = (host, 443)
        scope["headers"] = [
            (k, v)
            for k, v in scope["headers"]
            if k.lower()
            not in (
                b"authorization",
                b"cookie",
                b"cf-access-client-id",
                b"cf-access-client-secret",
                b"cf-access-jwt-assertion",
                b"x-forwarded-host",
                b"x-forwarded-proto",
                b"forwarded",
            )
        ]

        started = False

        async def secured_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                existing = [
                    (key, value)
                    for key, value in cast(list[tuple[bytes, bytes]], message.get("headers", []))
                    if key.lower() not in (b"cache-control", b"x-content-type-options")
                ]
                existing.extend(
                    [(b"x-content-type-options", b"nosniff"), (b"cache-control", b"no-store")]
                )
                if not is_preview and (
                    path == "/" or (path.startswith("/web/") and path.endswith(".html"))
                ):
                    # Every route serving the trusted UI needs this parent policy:
                    # a sandboxed child can still navigate its own frame. Include
                    # signed resources so relative links stay within the bundle.
                    policy = (
                        "frame-ancestors 'none'; object-src 'none'; "
                        f"frame-src {self.config.public_url}/api/artifacts/ "
                        f"{self.config.public_url}/api/designs/ "
                        f"{self.config.public_url}/api/decks/ "
                        f"{self.config.public_url}/decks/ "
                        f"{self.config.preview_url}/_preview/ about:"
                    )
                    existing.append((b"content-security-policy", policy.encode("ascii")))
                message["headers"] = existing
            await send(message)

        if is_preview:
            # Normal parallel asset loads wait for a bounded slot rather than
            # failing immediately. A public capability must not create unlimited
            # concurrent verification reads or response buffers.
            if self._preview_waiters >= 256:
                await reply(429, "Preview capacity is busy. Retry shortly.")
                return
            self._preview_waiters += 1
            try:
                try:
                    async with asyncio.timeout(self.preview_wait_timeout):
                        await self._preview_slots.acquire()
                except TimeoutError:
                    await reply(429, "Preview capacity is busy. Retry shortly.")
                    return
            finally:
                self._preview_waiters -= 1
            try:
                await self.app(scope, receive, secured_send)
            finally:
                self._preview_slots.release()
            return
        if path == "/mcp" or path.startswith("/mcp/") or path == "/api/review/hooks":
            hook = path == "/api/review/hooks"
            slots = self._hook_slots if hook else self._mcp_slots
            if slots.locked():
                await reply(429, "Agent connection is busy. Retry shortly.")
                return
            async with slots:
                try:
                    async with asyncio.timeout(35 if hook else self.mcp_timeout):
                        body = bytearray()
                        while True:
                            chunk = await receive()
                            if chunk["type"] == "http.disconnect":
                                return
                            data = chunk.get("body", b"")
                            if len(body) + len(data) > (64 * 1024 if hook else 2 * 1024 * 1024):
                                await reply(413, "Request too large")
                                return
                            body.extend(data)
                            if not chunk.get("more_body", False):
                                break

                        delivered = False

                        async def bounded_receive() -> Message:
                            nonlocal delivered
                            if delivered:
                                return await receive()
                            delivered = True
                            return {"type": "http.request", "body": bytes(body), "more_body": False}

                        await self.app(scope, bounded_receive, secured_send)
                except TimeoutError:
                    if not started:
                        await reply(504, "Request timed out")
                    return
            return
        if not is_preview and method == "POST":
            # Attachment-bearing routes have existing large base64 upload contracts.
            large = path in {
                "/api/artifacts/import",
                "/api/decks",
                "/api/design-requests",
                "/api/design-library",
                "/api/design-library/import",
                "/api/chat-references",
                "/api/projects/open",
            } or path.endswith(
                (
                    "/feedback",
                    "/references",
                    "/files",
                    "/project",
                    "/send-batch",
                    "/selection-image",
                )
            )
            limit = (
                MAX_DESIGN_ZIP
                if path == "/api/design-library/import"
                else (100 if path == "/api/projects/open" else 256 if large else 16) * 1024 * 1024
            )
            slots = self._upload_slots if large else self._write_slots
            if slots.locked():
                await reply(429, "Request capacity is busy. Retry shortly.")
                return
            async with slots:
                # Read and validate the entire body before invoking any mutating handler.
                # A disk-backed temporary file avoids keeping a second copy in memory.
                with tempfile.TemporaryFile() as body_file:
                    size = 0
                    try:
                        async with asyncio.timeout(self.body_timeout):
                            while True:
                                chunk = await receive()
                                if chunk["type"] == "http.disconnect":
                                    return
                                data = chunk.get("body", b"")
                                size += len(data)
                                if size > limit:
                                    await reply(413, "Request too large")
                                    return
                                body_file.write(data)
                                if not chunk.get("more_body", False):
                                    break
                    except TimeoutError:
                        await reply(408, "Request body timed out")
                        return
                    body_file.seek(0)
                    remaining = size

                    async def replay_receive() -> Message:
                        nonlocal remaining
                        if remaining:
                            data = body_file.read(min(remaining, 64 * 1024))
                            remaining -= len(data)
                            return {
                                "type": "http.request",
                                "body": data,
                                "more_body": remaining > 0,
                            }
                        return await receive()

                    if not size:
                        # Even an empty body must deliver one request event.
                        delivered = False

                        async def empty_receive() -> Message:
                            nonlocal delivered
                            if not delivered:
                                delivered = True
                                return {"type": "http.request", "body": b"", "more_body": False}
                            return await receive()

                        replay = empty_receive
                    else:
                        replay = replay_receive
                    lock_path = scope.get("state", {}).get("collaboration_lock")
                    if isinstance(lock_path, Path):
                        import fcntl

                        # Serialize changes to shared content and project membership with
                        # the collaboration server's grant checks and agent mutations.
                        with lock_path.open("a+b") as lock:
                            try:
                                async with asyncio.timeout(5):
                                    while True:
                                        try:
                                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                                            break
                                        except BlockingIOError:
                                            await asyncio.sleep(0.025)
                            except TimeoutError:
                                await reply(429, "Workspace is busy. Retry shortly.")
                                return
                            await self.app(scope, replay, secured_send)
                    else:
                        await self.app(scope, replay, secured_send)
            return
        await self.app(scope, receive, secured_send)
