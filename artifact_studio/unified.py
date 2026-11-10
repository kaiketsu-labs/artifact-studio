"""Host the full owner editor and scoped collaboration in one server."""

import os
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .deployment import DeploymentConfig
from .multiplayer import create_app as create_collaboration
from .multiplayer_access import AccessDenied, Sharing, workspace_editor_path
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
        path, method = scope["path"], scope["method"]
        hosts = Headers(scope=scope).getlist("host")
        host = hosts[0].lower() if len(hosts) == 1 else ""
        if host == self.preview_host:
            # Signed editor tickets contain a dot. Scoped tickets are opaque hex.
            ticket = path.split("/", 3)[2] if path.startswith("/_preview/") else ""
            target = self.editor if "." in ticket else self.collaboration
            await target(scope, receive, send)
            return
        if host == self.workspace_host and path.startswith("/workspaces/"):
            workspace, slash, tail = path.removeprefix("/workspaces/").partition("/")
            try:
                root = workspace_editor_path(workspace)
                if workspace == "default":
                    raise AccessDenied
            except AccessDenied:
                await JSONResponse({"detail": "Not found"}, status_code=404)(scope, receive, send)
                return
            if not slash:
                query_string = scope["query_string"].decode("latin1")
                await RedirectResponse(root + (f"?{query_string}" if query_string else ""))(
                    scope, receive, send
                )
                return
            # A workspace path can only enter the grant-checked editor. In
            # particular, it must never fall back to the default owner's API.
            editor_path = "/" + tail
            editor_scope: Scope = {
                **scope,
                "path": editor_path,
                "raw_path": editor_path.encode(),
                "state": {**scope.get("state", {}), "editor_workspace": workspace},
            }
            await self.shared_editor(editor_scope, receive, send)
            return
        if host == self.workspace_host and path in {"/", "/together"} and method in {"GET", "HEAD"}:
            # Preserve links from older invitations and agent context responses.
            try:
                params = parse_qs(
                    scope["query_string"].decode("ascii"), keep_blank_values=True, max_num_fields=32
                )
            except UnicodeError, ValueError:
                params = {}
            workspaces = params.get("workspace")
            if workspaces is not None:
                try:
                    if len(workspaces) != 1:
                        raise AccessDenied
                    root = workspace_editor_path(workspaces[0])
                except AccessDenied:
                    await JSONResponse({"detail": "Not found"}, status_code=404)(
                        scope, receive, send
                    )
                    return
                query = urlencode(
                    {
                        key: params[key][0]
                        for key in ("deck", "artifact", "project")
                        if key in params
                    }
                )
                slide = params.get("slide", [""])[0]
                target = (
                    root + (f"?{query}" if query else "") + (f"#{quote(slide)}" if slide else "")
                )
                await RedirectResponse(target)(scope, receive, send)
                return
            if path == "/together":
                await RedirectResponse("/")(scope, receive, send)
                return
        if path in {
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
