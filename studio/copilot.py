"""Opt-in, authenticated MCP gateway for a remote Copilot Studio connection."""

import argparse
import asyncio
import hmac
import os
import re
import secrets
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .copilot_tools import BOOTSTRAP, register_tools
from .storage import Store

KEY_HEADER = "X-Slide-Studio-Key"
MAX_REQUEST_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class GatewayConfig:
    """Explicit exposure settings; constructing the gateway never starts a listener."""

    collection: Path
    database: Path
    key_file: Path
    public_host: str
    project_id: str = ""
    whole_workspace: bool = False
    bind: str = "127.0.0.1"
    port: int = 8798
    max_request_bytes: int = MAX_REQUEST_BYTES
    max_concurrent_requests: int = 4
    request_timeout_seconds: float = 90

    def __post_init__(self) -> None:
        """Fail closed for missing scope, unsafe hosts, or inappropriate key locations."""
        if bool(self.project_id) == self.whole_workspace:
            raise ValueError("Choose exactly one project_id or whole_workspace=True.")
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]{1,5})?", self.public_host):
            raise ValueError("public_host must be one lowercase hostname, optionally with a port.")
        if self.bind not in {"127.0.0.1", "::1"}:
            raise ValueError("The gateway binds only to loopback; use an explicit HTTPS proxy.")
        if not 1 <= self.port <= 65535:
            raise ValueError("Use a valid TCP port.")
        if self.key_file.resolve().is_relative_to(self.collection.resolve()):
            raise ValueError("Keep the key file outside the shared collection.")
        if not 1024 <= self.max_request_bytes <= MAX_REQUEST_BYTES:
            raise ValueError("Request limit must be between 1024 bytes and 2 MiB.")
        if not 1 <= self.max_concurrent_requests <= 8:
            raise ValueError("Allow between one and eight concurrent requests.")
        if not 1 <= self.request_timeout_seconds <= 120:
            raise ValueError("Request timeout must be between one and 120 seconds.")


def initialize_key(path: Path) -> None:
    """Create a 256-bit API key privately, refusing to replace any existing file."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        _ = handle.write(secrets.token_urlsafe(32) + "\n")


def read_key(path: Path) -> bytes:
    """Read only a private, owner-controlled regular file without following symlinks."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("The key must be an owner-only regular file (chmod 600).")
        key = handle.read(130).strip()
    if not re.fullmatch(rb"[A-Za-z0-9_-]{43,128}", key) or len(set(key)) < 16:
        raise ValueError("Use a fresh high-entropy key from the init-key command.")
    return key


@dataclass
class GatewayBoundary:
    """Authenticate and bound HTTP before the MCP application sees a request."""

    app: ASGIApp
    config: GatewayConfig
    key: bytes = field(repr=False)
    slots: asyncio.Semaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Allocate an independent request budget for this gateway instance."""
        self.slots = asyncio.Semaphore(self.config.max_concurrent_requests)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Reject browser requests, other routes, unknown hosts and incorrect credentials."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        status, message = self._rejection(scope, headers)
        if status:
            await JSONResponse({"error": message}, status_code=status)(scope, receive, send)
            return
        if self.slots.locked():
            await JSONResponse({"error": "Gateway is busy; retry shortly."}, status_code=429)(
                scope, receive, send
            )
            return
        async with self.slots:
            await self._bounded_request(scope, receive, send)

    def _rejection(self, scope: Scope, headers: Headers) -> tuple[int, str]:
        """Produce fixed responses without echoing secrets or caller-controlled text."""
        if scope["path"] != "/mcp":
            return 404, "Not found."
        if headers.getlist("host") != [self.config.public_host]:
            return 421, "Unexpected gateway host."
        if "origin" in headers:
            return 403, "Browser-origin requests are not supported."
        values = headers.getlist(KEY_HEADER)
        if len(values) != 1 or not hmac.compare_digest(values[0].encode("latin-1"), self.key):
            return 401, "A valid gateway API key is required."
        if scope["method"] != "POST":
            return 405, "Use Streamable HTTP POST requests."
        if headers.get("content-encoding", "identity") != "identity":
            return 415, "Encoded request bodies are not supported."
        lengths = headers.getlist("content-length")
        if lengths and (len(lengths) != 1 or not lengths[0].isdecimal()):
            return 400, "Invalid content length."
        if lengths and int(lengths[0]) > self.config.max_request_bytes:
            return 413, "Request body exceeds the gateway limit."
        return 0, ""

    async def _bounded_request(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Bound streamed bodies and execution, and remove credentials from downstream scope."""
        started = False

        async def tracked_send(message: Message) -> None:
            """Remember response commitment to avoid a second HTTP response after a timeout."""
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            async with asyncio.timeout(self.config.request_timeout_seconds):
                body = bytearray()
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(cast(bytes, message.get("body", b"")))
                    if len(body) > self.config.max_request_bytes:
                        await JSONResponse(
                            {"error": "Request body exceeds the gateway limit."}, 413
                        )(scope, receive, send)
                        return
                    if not message.get("more_body", False):
                        break
                consumed = False

                async def replay() -> Message:
                    """Deliver the bounded request exactly once, then relay disconnect events."""
                    nonlocal consumed
                    if consumed:
                        return await receive()
                    consumed = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}

                safe_scope = dict(scope)
                safe_scope["headers"] = [
                    (name, value)
                    for name, value in scope["headers"]
                    if name.lower() not in {KEY_HEADER.lower().encode(), b"authorization"}
                ]
                await self.app(safe_scope, replay, tracked_send)
        except TimeoutError:
            if not started:
                await JSONResponse({"error": "Gateway request timed out."}, 504)(
                    scope, receive, send
                )


def create_gateway(config: GatewayConfig, *, store: Store | None = None) -> GatewayBoundary:
    """Create an independent MCP-only ASGI application without changing the local server."""
    key = read_key(config.key_file)
    selected_store = store or Store(config.collection, config.database)
    server = MCPServer[None]("Slide Studio Copilot", instructions=BOOTSTRAP)
    register_tools(server, selected_store, config.project_id, config.whole_workspace)
    application = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        max_request_body_size=config.max_request_bytes,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[config.public_host],
            allowed_origins=[],
        ),
        host=config.bind,
    )
    return GatewayBoundary(application, config, key)


def main() -> None:
    """Initialize a private key or run the explicitly configured loopback gateway."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser(
        "init-key", help="Create a private API key without printing it."
    )
    initialize.add_argument("--key-file", type=Path, required=True)
    serve = commands.add_parser(
        "serve", help="Run only the authenticated MCP endpoint on loopback."
    )
    serve.add_argument("--collection", type=Path, required=True)
    serve.add_argument("--database", type=Path, required=True)
    serve.add_argument("--key-file", type=Path, required=True)
    serve.add_argument("--public-host", required=True, help="Exact HTTPS proxy Host header.")
    scope = serve.add_mutually_exclusive_group(required=True)
    scope.add_argument("--project-id", default="")
    scope.add_argument("--whole-workspace", action="store_true")
    serve.add_argument("--bind", default="127.0.0.1", choices=["127.0.0.1", "::1"])
    serve.add_argument("--port", type=int, default=8798)
    args = parser.parse_args()
    try:
        key_file = cast(Path, args.key_file)
        if args.command == "init-key":
            initialize_key(key_file)
            print("Private API key created. Its contents were not printed.")
            return
        config = GatewayConfig(
            collection=cast(Path, args.collection),
            database=cast(Path, args.database),
            key_file=key_file,
            public_host=cast(str, args.public_host),
            project_id=cast(str, args.project_id),
            whole_workspace=cast(bool, args.whole_workspace),
            bind=cast(str, args.bind),
            port=cast(int, args.port),
        )
        app = create_gateway(config)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Gateway setup failed: {error}\n")
    uvicorn.run(app, host=config.bind, port=config.port, access_log=False, proxy_headers=False)


if __name__ == "__main__":
    main()
