"""Privacy-preserving request diagnostics for hosted security boundaries."""

import asyncio
import logging
import time
import uuid

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("artifact_studio.hosted.requests")


class RequestDiagnostics:
    def __init__(self, app: ASGIApp, preview_host: str) -> None:
        self.app, self.preview_host = app, preview_host

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("state", {}).get("request_diagnostics_active"):
            await self.app(scope, receive, send)
            return
        scope.setdefault("state", {})["request_diagnostics_active"] = True
        request_id = uuid.uuid4().hex
        start = time.monotonic()
        status: list[int] = [0]
        disconnected = False
        cancelled = False
        failed = False
        preview = Headers(scope=scope).get("host", "").lower() == self.preview_host

        async def observed_receive() -> Message:
            nonlocal disconnected
            message = await receive()
            disconnected |= message["type"] == "http.disconnect"
            return message

        async def observed_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                status[0] = message["status"]
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, observed_receive, observed_send)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception:
            failed = True
            raise
        finally:
            elapsed_ms = round((time.monotonic() - start) * 1000)
            outcome = (
                "client_disconnect"
                if disconnected
                else "cancelled"
                if cancelled
                else "exception"
                if failed
                else "authorization_failure"
                if status[0] in (401, 403)
                else "preview_capacity"
                if preview and status[0] == 429
                else "origin_timeout"
                if status[0] in (408, 504)
                else "http_error"
                if status[0] >= 400
                else "slow"
                if elapsed_ms >= 5000
                else "ok"
            )
            # Never log paths, query strings, identities, headers, or request bodies.
            logger.log(
                logging.INFO if outcome == "ok" else logging.WARNING,
                "hosted_request id=%s kind=%s outcome=%s status=%d duration_ms=%d",
                request_id,
                "preview" if preview else "workspace",
                outcome,
                status[0],
                elapsed_ms,
            )
