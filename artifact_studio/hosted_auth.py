"""ASGI security boundary for the hosted workspace and isolated previews."""

import asyncio
from collections.abc import Callable
from typing import cast
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .authentication import AuthenticationError, AuthProvider, Principal
from .deployment import DeploymentConfig


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
            await PlainTextResponse(
                message,
                status_code=status,
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
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
                    path == "/mcp" or path.startswith("/mcp/")
                )
            else:
                member = bool(
                    principal.email and principal.email.lower() in self.config.member_emails
                )
            if not member:
                await reply(403, "Forbidden")
                return
            if (
                headers.get("sec-fetch-dest") == "document"
                and path.startswith("/api/artifacts/")
                and path.endswith("/preview")
            ):
                await reply(403, "Open this preview inside the workspace.")
                return
            mcp = path == "/mcp" or path.startswith("/mcp/")
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
                if not is_preview and path == "/":
                    existing.append((b"content-security-policy", b"frame-ancestors 'none'"))
                message["headers"] = existing
            await send(message)

        if not is_preview and (path == "/mcp" or path.startswith("/mcp/")):
            if self._mcp_slots.locked():
                await reply(429, "Agent connection is busy. Retry shortly.")
                return
            async with self._mcp_slots:
                try:
                    async with asyncio.timeout(self.mcp_timeout):
                        body = bytearray()
                        while True:
                            chunk = await receive()
                            if chunk["type"] == "http.disconnect":
                                return
                            data = chunk.get("body", b"")
                            if len(body) + len(data) > 2 * 1024 * 1024:
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
        await self.app(scope, receive, secured_send)
