"""Host the full owner editor and scoped collaboration in one server."""

import os
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlencode, urlsplit

from starlette.datastructures import Headers
from starlette.responses import RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .deployment import DeploymentConfig
from .multiplayer import create_app as create_collaboration
from .multiplayer_access import Sharing
from .shared_editor import create_shared_editor


class UnifiedStudio:
    """Route requests to the existing security boundaries without a fallback."""

    def __init__(
        self,
        config: DeploymentConfig,
        editor: ASGIApp,
        collaboration: ASGIApp,
        shared_editor: ASGIApp,
        lock_path: Path,
    ) -> None:
        self.editor = editor
        self.collaboration = collaboration
        self.shared_editor = shared_editor
        self.workspace_host = urlsplit(config.public_url).netloc
        self.preview_host = urlsplit(config.preview_url).netloc
        self.lock_path = lock_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # The scoped MCP transport owns the lifespan and websocket policy.
            await self.collaboration(scope, receive, send)
            return
        path = scope["path"]
        hosts = Headers(scope=scope).getlist("host")
        host = hosts[0].lower() if len(hosts) == 1 else ""
        if host == self.preview_host:
            # Signed editor tickets contain a dot. Scoped tickets are opaque hex.
            ticket = path.split("/", 3)[2] if path.startswith("/_preview/") else ""
            target = self.editor if "." in ticket else self.collaboration
            await target(scope, receive, send)
            return
        if host == self.workspace_host and path == "/" and scope["method"] in {"GET", "HEAD"}:
            # Preserve deep links emitted by the standalone collaboration interface.
            try:
                params = parse_qs(scope["query_string"].decode("ascii"), max_num_fields=32)
            except UnicodeError, ValueError:
                params = {}
            if "workspace" in params:
                query = urlencode(
                    {key: params[key][0] for key in ("workspace", "artifact") if key in params}
                )
                await RedirectResponse(f"/together?{query}", status_code=307)(scope, receive, send)
                return
        if path in {
            "/together",
            "/api/together/session",
            "/api/workspaces",
            "/api/invitations/accept",
            "/api/review/hooks",
            "/web/multiplayer.js",
            "/web/multiplayer.css",
            "/mcp",
        } or path.startswith(("/api/workspaces/", "/api/agents/", "/mcp/")):
            scope.setdefault("state", {})["unified_studio"] = True
            await self.collaboration(scope, receive, send)
        else:
            state = scope.setdefault("state", {})
            state["unified_studio"] = True
            state["shared_editor"] = self.shared_editor
            state["collaboration_lock"] = self.lock_path
            await self.editor(scope, receive, send)


def create_app() -> UnifiedStudio:
    """Build from the existing hosted configuration with only the owner as a member."""
    config = DeploymentConfig.from_env()
    owner = os.environ.get("ARTIFACT_STUDIO_MULTIPLAYER_OWNER", "").lower()
    if config.mode != "hosted" or not owner or config.member_emails != (owner,):
        raise ValueError("Unified hosting requires member_emails to contain only the owner.")
    if config.agent_subjects:
        raise ValueError("Unified hosting uses scoped Studio tokens; leave agent_subjects empty.")

    from .app import app, auth_provider

    if app.state.deployment != config or auth_provider is None:
        raise ValueError("The editor must be initialized with this hosted configuration.")
    collaboration = create_collaboration(config, auth_provider)
    sharing = cast(Sharing, collaboration.state.sharing)
    guests = create_shared_editor(config, auth_provider, sharing)
    return UnifiedStudio(config, app, collaboration, guests, sharing.root / "access.lock")
