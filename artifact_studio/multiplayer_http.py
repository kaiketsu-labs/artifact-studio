"""Authenticated, bounded transport for the separate multiplayer application."""

import asyncio
import re
from typing import cast
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .authentication import AuthenticationError, AuthProvider, Principal
from .deployment import DeploymentConfig
from .multiplayer_access import AccessDenied, Actor, Sharing
from .multiplayer_access import Scope as GrantScope

_CHAT = re.compile(r"/api/workspaces/([^/]+)/artifacts/([^/]+)/chat")
_CHAT_FILE = re.compile(r"/api/workspaces/[^/]+/artifacts/[^/]+/chat/[^/]+/files/[^/]+")
_REQUEST_LIMIT = 2 * 1024 * 1024
_CHAT_LIMIT = 68 * 1024 * 1024


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
        self.hook_slots = asyncio.Semaphore(8)
        self.agent_slots = asyncio.Semaphore(4)
        self.preview_slots = asyncio.Semaphore(4)
        self.chat_upload_slot = asyncio.Semaphore(1)
        self.chat_download_slots = asyncio.Semaphore(2)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
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
            await JSONResponse(
                {"detail": detail}, status_code=status, headers={"Cache-Control": "no-store"}
            )(scope, receive, send)

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
            agent_path = path in {"/mcp", "/mcp/", "/api/review/hooks"}
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
                    frames = (
                        f"{origin}/api/artifacts/ {origin}/api/decks/ about: "
                        if self.shared_shell
                        else ""
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

        # Admit before buffering to bound memory even with many slow connections.
        slots = (
            self.preview_slots
            if host == self.preview
            else self.chat_upload_slot
            if chat_write is not None
            else self.chat_download_slots
            if _CHAT_FILE.fullmatch(path)
            else self.hook_slots
            if path == "/api/review/hooks"
            else self.agent_slots
            if path in {"/mcp", "/mcp/"}
            else self.slots
        )
        try:
            await asyncio.wait_for(slots.acquire(), timeout=1)
        except TimeoutError:
            await reject(429, "Server busy. Retry shortly.")
            return
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
