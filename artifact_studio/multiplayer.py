"""Opt-in multi-workspace server, hosted behind an authenticated HTTPS tunnel.

Run with uvicorn artifact_studio.multiplayer:create_app --factory. The ordinary
local application and its collection are never imported or globally retargeted.
"""

import asyncio
import os
import secrets
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal, cast
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from . import __version__
from .artifact_models import ArtifactTarget
from .artifact_preview import artifact_file_headers, preview_document, preview_headers
from .artifacts import ArtifactStore
from .authentication import AuthProvider, load_auth_provider
from .deck_presence import DeckPresence, DeckViewer, PresenceInput
from .deployment import DeploymentConfig
from .design_thumbnails import raster_thumbnail
from .hosted_preview import verified_file_response
from .models import FileInput, StrictModel
from .multiplayer_access import (
    AccessDenied,
    Actor,
    Kind,
    Scope,
    Sharing,
    checksum,
    workspace_editor_path,
)
from .multiplayer_chat import Channel, ChatProposal, Conversations
from .multiplayer_http import MultiplayerAuth
from .multiplayer_tools import MultiplayerTools
from .review import ReviewHookInput
from .storage import ConflictError, Store
from .visuals import ScopedDeckEmbedder

# The separator is outside the ordinary ticket alphabet.
HISTORY_PREVIEW_PREFIX = "history~"

PROJECT = Path(__file__).resolve().parents[1]


class ScopeInput(StrictModel):
    kind: Kind = "workspace"
    resource: str = Field(default="", max_length=200)


class InviteInput(ScopeInput):
    email: str = Field(max_length=254)
    role: Literal["view", "comment", "edit"] = "edit"
    no_expiry: bool = False


class AgentInput(ScopeInput):
    name: str = Field(min_length=1, max_length=80)


class SharedHistoryInput(StrictModel):
    """Set the history permission for a single agent."""

    enabled: bool


class TitleInput(StrictModel):
    title: str = Field(min_length=1, max_length=160)


class AcceptInput(StrictModel):
    token: str = Field(min_length=32, max_length=200)


class ChatInput(StrictModel):
    channel: Channel = "shared"
    message: str = Field(max_length=10000)
    request_id: str = Field(min_length=8, max_length=100)
    assigned: str = Field(default="", max_length=100)
    released: bool = True
    target: ArtifactTarget | None = None
    content_hash: str = Field(default="", max_length=64)
    slide_content_hash: str = Field(default="", max_length=64)
    proposal: ChatProposal | None = None
    files: list[FileInput] = Field(default_factory=list, max_length=5)


def actor_of(request: Request) -> Actor:
    return cast(Actor, request.state.actor)


def create_app(
    config: DeploymentConfig | None = None,
    provider: AuthProvider | None = None,
    sharing: Sharing | None = None,
) -> FastAPI:
    """Build with explicit dependencies so real HTTP tests never touch a user's collection."""
    config = config or DeploymentConfig.from_env()
    if config.mode != "hosted":
        raise ValueError("Multiplayer requires hosted mode and an authenticated HTTPS tunnel.")
    if sharing is None:
        owner = os.environ.get("ARTIFACT_STUDIO_MULTIPLAYER_OWNER", "")
        if not owner or owner.lower() not in config.member_emails:
            raise ValueError("Set ARTIFACT_STUDIO_MULTIPLAYER_OWNER to a configured member email.")
        collection = Path(
            os.environ.get("ARTIFACT_STUDIO_COLLECTION", Path.home() / "Documents/Artifacts")
        )
        database = Path(
            os.environ.get("ARTIFACT_STUDIO_DATABASE", PROJECT / ".state/review.sqlite3")
        )
        root = Path(
            os.environ.get("ARTIFACT_STUDIO_MULTIPLAYER_STATE", database.parent / "multiplayer")
        )
        if root.resolve().is_relative_to(collection.resolve()):
            raise ValueError("Keep multiplayer credentials outside the artifact collection.")
        sharing = Sharing(
            root,
            Store(collection, database),
            owner,
            personal_workspace_emails=config.personal_workspace_emails,
        )
    hub = sharing
    chat = Conversations(hub)
    presence = DeckPresence(hub)
    mcp = MCPServer[None](
        "Artifact Studio Multiplayer",
        version=__version__,
        instructions=(
            "Start with get_working_context. Your token selects one workspace and access scope. "
            "Only assigned released requests appear in read_feedback. Claim before acting. "
            "Use expected hashes and stable request IDs. Replies stay in the original chat."
        ),
    )
    toolset = MultiplayerTools(hub, chat, config.public_url)
    toolset.register(mcp)
    mcp_app = mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        max_request_body_size=2 * 1024 * 1024,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[config.public_url.removeprefix("https://")],
            allowed_origins=[],
        ),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="Artifact Studio Multiplayer",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.sharing, app.state.chat = hub, chat
    app.add_middleware(
        MultiplayerAuth, config=config, provider=provider or load_auth_provider(config), sharing=hub
    )

    @app.exception_handler(AccessDenied)
    async def denied(_: Request, __: AccessDenied) -> JSONResponse:
        return JSONResponse({"detail": "This item is unavailable or your access has changed."}, 404)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, error: RequestValidationError) -> JSONResponse:
        """Return field diagnostics without echoing submitted files or secrets."""
        detail = [
            {
                "loc": [str(part)[:100] for part in item["loc"][:8]],
                "type": item["type"],
                "msg": item["msg"][:200],
            }
            for item in error.errors()[:10]
        ]
        return JSONResponse({"detail": detail}, status_code=422)

    @app.exception_handler(ConflictError)
    async def conflict(_: Request, error: ConflictError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, 409)

    @app.exception_handler(ValueError)
    async def invalid(_: Request, __: ValueError) -> JSONResponse:
        return JSONResponse({"detail": "Invalid request. Check the selected item and fields."}, 400)

    @app.exception_handler(FileNotFoundError)
    async def missing(_: Request, __: FileNotFoundError) -> JSONResponse:
        return JSONResponse({"detail": "Item unavailable."}, 404)

    @app.get("/", response_class=HTMLResponse)
    @app.get("/together", response_class=HTMLResponse)
    def index() -> str:
        return (PROJECT / "web/multiplayer.html").read_text()

    @app.get("/web/{name}")
    def static(name: str) -> FileResponse:
        if name not in {
            "multiplayer.js",
            "multiplayer.css",
            "chat-timeline.js",
            "chat-timeline.css",
        }:
            raise AccessDenied
        return FileResponse(PROJECT / "web" / name)

    @app.get("/api/session")
    @app.get("/api/together/session")
    def session(request: Request) -> dict[str, object]:
        actor = actor_of(request)
        with hub.locked(), hub.connect() as db:
            person = db.execute("SELECT email FROM people WHERE id=?", (actor.person,)).fetchone()
            assert person is not None
            rows = db.execute("SELECT * FROM workspaces ORDER BY title,id").fetchall()
            workspaces: list[dict[str, object]] = []
            for row in rows:
                scope = Scope(cast(str, row["id"]))
                sources = hub.sources(actor, scope)
                grants = db.execute(
                    "SELECT 1 FROM grants WHERE workspace=? AND person=?",
                    (scope.workspace, actor.person),
                ).fetchone()
                if sources or grants:
                    workspaces.append(
                        {
                            "id": scope.workspace,
                            "title": row["title"] if sources else "Shared with me",
                            "owner": row["owner"] == actor.person,
                        }
                    )
            return {
                "person": actor.person,
                "email": person["email"],
                "workspaces": workspaces,
                "can_create": person["email"] == hub.owner_email,
                "mcp_url": config.public_url + "/mcp",
                "editor_available": (
                    person["email"] == hub.owner_email
                    and getattr(request.state, "unified_studio", False)
                ),
                "scoped_editor_available": getattr(request.state, "unified_studio", False),
            }

    @app.post("/api/workspaces")
    def create_workspace(request: Request, body: TitleInput) -> dict[str, str]:
        return {"id": hub.create_workspace(actor_of(request), body.title.strip())}

    @app.post("/api/invitations/accept")
    def accept(request: Request, body: AcceptInput) -> dict[str, str]:
        scope = hub.accept(actor_of(request), body.token)
        return {"workspace": scope.workspace, "kind": scope.kind, "resource": scope.resource}

    @app.get("/api/workspaces/{workspace}/catalog")
    def catalog(request: Request, workspace: str) -> dict[str, object]:
        actor = actor_of(request)
        with hub.locked(), hub.connect() as db:
            store = hub.store(workspace)
            projects = ArtifactStore(store).list_projects()
            visible_projects = {
                item.id: item
                for item in projects
                if hub.level(actor, Scope(workspace, "project", item.id))
            }
            items = [
                item
                for item in ArtifactStore(store).list_artifacts()
                if hub.level(actor, Scope(workspace, "artifact", item.id))
            ]
            if not (items or visible_projects or hub.level(actor, Scope(workspace))):
                raise AccessDenied
            grants = db.execute(
                "SELECT kind,resource FROM grants WHERE workspace=?", (workspace,)
            ).fetchall()

            def badge(scope: Scope) -> str:
                ancestors = hub.ancestors(scope)
                if any((row["kind"], row["resource"]) in ancestors for row in grants):
                    return "Shared"
                if scope.kind == "workspace" and grants:
                    return "Contains shared items"
                if scope.kind == "project" and any(
                    row["kind"] == "artifact"
                    and ("project", scope.resource)
                    in hub.ancestors(Scope(workspace, "artifact", cast(str, row["resource"])))
                    for row in grants
                ):
                    return "Contains shared items"
                return "Private"

            return {
                "owner": hub.owner(actor, workspace),
                "can_create_project": hub.level(actor, Scope(workspace)) >= 3,
                "sharing": badge(Scope(workspace)),
                "projects": [
                    {
                        "id": item.id,
                        "title": item.title,
                        "sharing": badge(Scope(workspace, "project", item.id)),
                    }
                    for item in visible_projects.values()
                ],
                "artifacts": [
                    {
                        "id": item.id,
                        "title": item.title,
                        "kind": item.kind,
                        "project_id": item.project_id
                        if item.project_id in visible_projects
                        else "",
                        "content_hash": item.content_hash,
                        "sharing": badge(Scope(workspace, "artifact", item.id)),
                    }
                    for item in items
                ],
            }

    @app.post("/api/workspaces/{workspace}/projects")
    def create_project(request: Request, workspace: str, body: TitleInput) -> dict[str, str]:
        with hub.guard(actor_of(request), Scope(workspace), "edit") as store:
            item = ArtifactStore(store).create_project(body.title.strip())
            return {"id": item.id, "title": item.title}

    @app.get("/api/workspaces/{workspace}/access")
    def access(
        request: Request, workspace: str, kind: Kind = "workspace", resource: str = ""
    ) -> dict[str, object]:
        return hub.access(actor_of(request), Scope(workspace, kind, resource))

    @app.post("/api/workspaces/{workspace}/invitations")
    def invite(request: Request, workspace: str, body: InviteInput) -> dict[str, str]:
        invitation = hub.invite(
            actor_of(request),
            Scope(workspace, body.kind, body.resource),
            body.email,
            body.role,
            no_expiry=body.no_expiry,
        )
        path = (
            workspace_editor_path(workspace)
            if getattr(request.state, "unified_studio", False)
            else "/"
        )
        # Fragment keeps the secret out of HTTP access logs and Referer headers.
        return {
            "id": invitation["id"],
            "url": config.public_url + path + "#invite=" + invitation["token"],
        }

    @app.post("/api/workspaces/{workspace}/access/{identifier}/revoke")
    def revoke(
        request: Request, workspace: str, identifier: str, body: ScopeInput
    ) -> dict[str, bool]:
        hub.revoke(actor_of(request), Scope(workspace, body.kind, body.resource), identifier)
        return {"ok": True}

    @app.post("/api/workspaces/{workspace}/agents")
    def connect_agent(request: Request, workspace: str, body: AgentInput) -> dict[str, str]:
        result = hub.connect_agent(
            actor_of(request), Scope(workspace, body.kind, body.resource), body.name
        )
        return {**result, "mcp_url": config.public_url + "/mcp"}

    @app.post("/api/agents/{identifier}/revoke")
    def revoke_agent(request: Request, identifier: str) -> dict[str, bool]:
        hub.revoke_agent(actor_of(request), identifier)
        return {"ok": True}

    @app.get("/api/workspaces/{workspace}/agents")
    def own_agents(request: Request, workspace: str) -> list[dict[str, object]]:
        return hub.own_agents(actor_of(request), workspace)

    @app.get("/api/workspaces/{workspace}/artifacts/{artifact}/agents/{agent}/shared-history")
    def agent_shared_history(
        request: Request, workspace: str, artifact: str, agent: str
    ) -> dict[str, bool]:
        """Read the owner's current presentation history permission for one agent."""
        scope = Scope(workspace, "artifact", artifact)
        return {"enabled": chat.shared_history_access(actor_of(request), scope, agent)}

    @app.post("/api/workspaces/{workspace}/artifacts/{artifact}/agents/{agent}/shared-history")
    def set_agent_shared_history(
        request: Request, workspace: str, artifact: str, agent: str, body: SharedHistoryInput
    ) -> dict[str, bool]:
        """Allow or revoke full Shared history for one connection and presentation."""
        scope = Scope(workspace, "artifact", artifact)
        return {
            "enabled": chat.shared_history_access(actor_of(request), scope, agent, body.enabled)
        }

    @app.get("/api/workspaces/{workspace}/artifacts/{artifact}/state")
    def artifact_state(request: Request, workspace: str, artifact: str) -> dict[str, object]:
        actor, scope = actor_of(request), Scope(workspace, "artifact", artifact)
        with hub.guard(actor, scope) as store:
            item = ArtifactStore(store).get_artifact(artifact)
            slides: list[dict[str, str]] = []
            if item.legacy_deck_id:
                slides = [
                    {
                        "id": slide.id,
                        "title": slide.title,
                        "content_hash": item.presentation_versions[slide.id][0],
                    }
                    for slide in store.read(artifact).slides
                    if slide.id in item.presentation_versions
                ]
            with hub.connect() as db:
                draft_counts = {
                    cast(str, row["channel"]): cast(int, row["count"])
                    for row in db.execute(
                        "SELECT channel,COUNT(*) AS count FROM conversations "
                        "WHERE workspace=? AND artifact=? AND person=? AND released=0 "
                        "GROUP BY channel",
                        (workspace, artifact, actor.person),
                    )
                }
            return {
                "id": artifact,
                "title": item.title,
                "content_hash": item.content_hash,
                "revision": item.revision,
                "slides": slides,
                "access": hub.access(actor, scope),
                "agents": chat.agents(actor, scope),
                "draft_counts": draft_counts,
                "contacts": [] if actor.agent else chat.contacts(actor, scope),
                "mentions": [] if actor.agent else chat.mentions(actor, scope),
            }

    @app.post("/api/workspaces/{workspace}/artifacts/{artifact}/presence")
    def deck_presence(
        request: Request, workspace: str, artifact: str, body: PresenceInput
    ) -> list[DeckViewer]:
        """Refresh one human's slide position and return current viewers of this deck."""
        return presence.update(actor_of(request), Scope(workspace, "artifact", artifact), body)

    @app.get("/api/workspaces/{workspace}/artifacts/{artifact}/chat")
    def history(
        request: Request,
        workspace: str,
        artifact: str,
        channel: Channel = "shared",
        before: int = 0,
    ) -> list[dict[str, object]]:
        return chat.history(
            actor_of(request), Scope(workspace, "artifact", artifact), channel, before
        )

    @app.get("/api/workspaces/{workspace}/artifacts/{artifact}/contacts")
    def chat_contacts(request: Request, workspace: str, artifact: str) -> list[dict[str, str]]:
        """Suggest only current collaborators for mentions."""
        return chat.contacts(actor_of(request), Scope(workspace, "artifact", artifact))

    @app.get("/api/workspaces/{workspace}/artifacts/{artifact}/mentions")
    def chat_mentions(request: Request, workspace: str, artifact: str) -> list[dict[str, object]]:
        """Report unread mentions in a currently accessible artifact."""
        return chat.mentions(actor_of(request), Scope(workspace, "artifact", artifact))

    @app.post("/api/workspaces/{workspace}/artifacts/{artifact}/mentions/{event}/read")
    def read_chat_mention(
        request: Request, workspace: str, artifact: str, event: int
    ) -> dict[str, bool]:
        """Acknowledge one mention addressed to the signed-in person."""
        chat.read_mention(actor_of(request), Scope(workspace, "artifact", artifact), event)
        return {"ok": True}

    @app.post("/api/workspaces/{workspace}/artifacts/{artifact}/chat")
    def post(request: Request, workspace: str, artifact: str, body: ChatInput) -> dict[str, object]:
        return chat.post(
            actor_of(request),
            Scope(workspace, "artifact", artifact),
            body.channel,
            body.message,
            body.request_id,
            body.assigned,
            body.released,
            body.target,
            body.content_hash,
            body.files,
            slide_content_hash=body.slide_content_hash,
            proposal=body.proposal,
        )

    @app.get("/api/workspaces/{workspace}/artifacts/{artifact}/chat/{event_id}/files/{index}")
    def chat_file(
        request: Request,
        workspace: str,
        artifact: str,
        event_id: int,
        index: int,
        preview: bool = False,
        full: bool = False,
    ) -> Response:
        """Serve a visible message's original file or a newly encoded raster preview."""
        metadata, data = chat.attachment(
            actor_of(request), Scope(workspace, "artifact", artifact), event_id, index
        )
        headers = {
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Cross-Origin-Resource-Policy": "same-origin",
        }
        if preview:
            try:
                raster = raster_thumbnail(data, maximum=2048 if full else 640)
            except FileNotFoundError:
                return Response(status_code=204, headers=headers)
            return Response(raster, media_type="image/png", headers=headers)
        filename = quote(metadata["name"], safe="")
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{filename}"
        return Response(data, media_type="application/octet-stream", headers=headers)

    @app.post("/api/workspaces/{workspace}/artifacts/{artifact}/release")
    def release(
        request: Request, workspace: str, artifact: str, channel: Channel = "shared"
    ) -> dict[str, bool]:
        chat.release(actor_of(request), Scope(workspace, "artifact", artifact), channel)
        return {"ok": True}

    @app.post("/api/review/hooks")
    async def hook(request: Request, body: ReviewHookInput) -> dict[str, object]:
        actor = actor_of(request)
        deadline = time.monotonic() + 24
        while True:
            result = await run_in_threadpool(chat.hook, actor, body)
            if body.event != "Stop" or not result["listening"] or result["feedback_available"]:
                return result
            if time.monotonic() >= deadline or await request.is_disconnected():
                return result
            await asyncio.sleep(0.3)

    @app.get(
        "/api/workspaces/{workspace}/artifacts/{artifact}/preview", response_class=HTMLResponse
    )
    def preview(
        request: Request,
        workspace: str,
        artifact: str,
        slide_id: str = "",
        history_revision: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
    ) -> HTMLResponse:
        if request.headers.get("sec-fetch-dest") not in {"iframe", "frame"}:
            raise HTTPException(403, "Open this preview inside the workspace.")
        actor = actor_of(request)
        scope = Scope(workspace, "artifact", artifact)
        with hub.guard(actor, scope) as store:
            service = ArtifactStore(store)
            item = service.get_artifact(artifact)
            if history_revision is not None and (
                item.legacy_deck_id or not hub.owner(actor, workspace)
            ):
                raise AccessDenied
            if item.legacy_deck_id:
                deck = store.read(artifact)
                slide = store.locate(deck, slide_id) if slide_id else deck.slides[0]
                embedder = ScopedDeckEmbedder(store, artifact)
                css = (
                    (PROJECT / "runtime/viewer.css").read_text()
                    + "\n"
                    + embedder.css(embedder.local_path("theme.css").read_text())
                )
                css += (
                    "\nhtml,body{height:100%;margin:0;overflow:hidden;"
                    "background:transparent}.slide{height:100%}"
                )
                content = embedder.markup(slide.interactive_html or slide.html)
                document = (
                    "<!doctype html><html><head><meta charset=utf-8><style>"
                    + css.replace("<", r"\3c ")
                    + "</style></head><body>"
                    + content
                    + "</body></html>"
                )
                policy = (
                    "sandbox allow-scripts; default-src 'none'; "
                    "script-src 'unsafe-inline' 'unsafe-eval'; "
                    "style-src 'unsafe-inline'; img-src data: blob:; "
                    "font-src data:; media-src data: blob:; "
                    "connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; "
                    f"form-action 'none'; frame-ancestors {config.public_url}"
                )
                return HTMLResponse(document, headers={"Content-Security-Policy": policy})
            token = secrets.token_urlsafe(32)
            if history_revision is not None:
                token = HISTORY_PREVIEW_PREFIX + token
            revision = history_revision if history_revision is not None else item.revision
            root = config.preview_url + "/_preview/" + token
            document = preview_document(service, artifact, config.public_url, revision, root)
            with hub.connect() as db:
                db.execute("DELETE FROM preview_tickets WHERE expires<?", (time.time(),))
                db.execute(
                    "INSERT INTO preview_tickets VALUES (?,?,?,?,?,?)",
                    (
                        checksum(token),
                        actor.person,
                        workspace,
                        artifact,
                        revision,
                        time.time() + 3600,
                    ),
                )
            return HTMLResponse(
                document, headers=preview_headers(artifact, revision, config.public_url, root)
            )

    @app.api_route("/_preview/{token}/{path:path}", methods=["GET", "HEAD"])
    def preview_file(token: str, path: str, request: Request) -> Response:
        with hub.locked(), hub.connect() as db:
            row = db.execute(
                "SELECT * FROM preview_tickets WHERE token=? AND expires>?",
                (checksum(token), time.time()),
            ).fetchone()
            if not row:
                raise AccessDenied
            actor, scope = (
                Actor(cast(str, row["person"])),
                Scope(cast(str, row["workspace"]), "artifact", cast(str, row["artifact"])),
            )
            hub.require(actor, scope)
            if token.startswith(HISTORY_PREVIEW_PREFIX) and not hub.owner(actor, scope.workspace):
                raise AccessDenied
            revision = cast(int, row["revision"])
            root = config.preview_url + "/_preview/" + token
            prefix = f"api/artifacts/{scope.resource}/revisions/{revision}/files/"
            headers = artifact_file_headers(scope.resource, revision, config.public_url, root)
            if request.headers.get("sec-fetch-dest", "document") in {"document", "object", "embed"}:
                headers["Content-Disposition"] = "attachment"
            if path in {
                "web/artifact-bridge.js",
                "web/vendor/marked-18.1.2.js",
                "web/marquee.js",
                "web/image-export.js",
            }:
                return FileResponse(PROJECT / path, headers=headers)
            if not path.startswith(prefix):
                raise AccessDenied
            data = ArtifactStore(hub.store(scope.workspace)).verified_revision_file(
                scope.resource, revision, path[len(prefix) :]
            )
            return verified_file_response(data, path, headers, request)

    app.mount("/", mcp_app)
    return app
