"""Expose the usual Studio UI with an explicit, grant-checked guest API."""

import errno
import mimetypes
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from shutil import disk_usage
from typing import Literal, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import Field

from . import __version__
from .artifact_api import ArtifactApproval, ArtifactImport, ProjectInput, import_upload
from .artifact_models import ArtifactView
from .artifact_preview import artifact_file_headers
from .artifacts import LEGACY_PROJECT, ArtifactStore
from .authentication import AuthProvider, Principal
from .deck_edit_api import deck_edit_routes
from .deployment import DeploymentConfig
from .design_library import DesignLibrary
from .design_library_api import design_library_routes
from .history_api import history_routes
from .hosted_policy import deck_asset_response
from .hosted_presenter import hosted_presenter_router
from .hub_previews import cover_metadata, request_current_cover
from .models import CreateInput, DeckView, FeedbackWorkflow, SlideSeenInput, SlideView
from .multiplayer_access import AccessDenied, Actor, Role, Scope, Sharing, workspace_editor_path
from .multiplayer_http import IMPORT_DISK_RESERVE, MultiplayerAuth, owner_upload_limit
from .navigation import NavigationSnapshot
from .sections import sections_hash
from .storage import ConflictError, Store
from .version_authors import revision_author
from .visuals import ScopedDeckEmbedder

PROJECT = Path(__file__).resolve().parents[1]
SHARED_PROJECT = "shared-with-me"
ASSET_SUFFIXES = {".css", ".js", ".svg", ".png", ".woff", ".woff2"}
LEVEL_ROLE: dict[int, Role] = {1: "view", 2: "comment", 3: "edit"}


class WorkspacePresentationInput(CreateInput):
    """Create a presentation in a project owned by the current workspace."""

    project_id: str = Field(default=LEGACY_PROJECT, min_length=1, max_length=100)


def actor_of(request: Request) -> Actor:
    """Read the identity established by the scoped authentication middleware."""
    return cast(Actor, request.state.actor)


def workspace_of(request: Request) -> str:
    """Read the editor workspace selected by the native path dispatcher."""
    return cast(str, getattr(request.state, "editor_workspace", "default"))


def artifact_scope(request: Request, identifier: str) -> Scope:
    """Resolve an artifact within the workspace of this request."""
    return Scope(workspace_of(request), "artifact", identifier)


def has_update(content_hash: str, revision: int, opened: str | None) -> bool:
    """Start with a quiet first version and compare later openings by visible content."""
    return revision > 1 if opened is None else opened != content_hash


def review_fields(item: ArtifactView, opened: dict[str, str]) -> dict[str, object]:
    """Project shared approvals and the current human's independent update marker."""
    unseen = (
        any(
            has_update(content_hash, revision, opened.get(slide))
            for slide, (content_hash, revision) in item.presentation_versions.items()
        )
        if item.legacy_deck_id
        else has_update(item.content_hash, item.revision, opened.get(""))
    )
    return {
        "id": item.id,
        "content_hash": item.content_hash,
        "approved": item.approved,
        "has_unseen_changes": unseen,
        "status": "approved" if item.approved else "updated" if unseen else "draft",
    }


def artifact_payload(
    item: ArtifactView, *, visible_project: bool, opened: dict[str, str]
) -> dict[str, object]:
    """Return editor metadata without host paths or the private parent identity."""
    data = item.model_dump(
        exclude={
            "source_path",
            "original_source_path",
            "storage_project_id",
            "context",
            "context_hash",
        }
    )
    data.update(review_fields(item, opened))
    if not visible_project:
        data.update(project_id=SHARED_PROJECT, project_title="Shared with me")
    if item.legacy_deck_id:
        data["files"] = [
            file.model_dump()
            for file in item.files
            if file.path != "brief-files.json" and not file.path.startswith("brief-attachments/")
        ]
    return data


def shared_deck(store: Store, deck_id: str, opened: dict[str, str]) -> DeckView:
    """Project the current deck and notes without legacy feedback or workspace rules."""
    deck = store.read(deck_id)
    design_hash = store.visual_hash(deck_id)
    approved, _ = store.review_hashes(deck_id)
    slides: list[SlideView] = []
    for slide in deck.slides:
        view = store.view_slide(
            deck_id, slide, design_hash=design_hash, feedback=[], review_hashes=(approved, {})
        )
        slides.append(
            view.model_copy(
                update={
                    "has_unseen_changes": has_update(
                        view.content_hash, view.revision, opened.get(slide.id)
                    ),
                    "agent": type(view.agent)(),
                }
            )
        )
    return DeckView(
        id=deck_id,
        title=deck.title,
        design_id=deck.design_id,
        design_hash=design_hash,
        slides=slides,
        sections=deck.sections,
        sections_hash=sections_hash(deck.sections),
    )


def create_shared_editor(config: DeploymentConfig, provider: AuthProvider, hub: Sharing) -> FastAPI:
    """Build the guest surface without mounting any whole-workspace API."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    app.add_middleware(
        MultiplayerAuth, config=config, provider=provider, sharing=hub, shared_shell=True
    )

    @app.exception_handler(AccessDenied)
    @app.exception_handler(FileNotFoundError)
    @app.exception_handler(ValueError)
    async def missing(_: Request, __: Exception) -> JSONResponse:
        return JSONResponse({"detail": "This item is unavailable."}, status_code=404)

    @app.exception_handler(ConflictError)
    async def stale(_: Request, error: ConflictError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        """Keep malformed upload contents out of validation responses."""
        if owner_upload_limit(request.scope["path"]) is not None:
            return JSONResponse({"detail": "Check the selected files and project."}, 422)
        return await request_validation_exception_handler(request, error)

    @app.get("/")
    def shell(request: Request, deck: str = "", editor: str = "", new: str = "") -> Response:
        workspace = workspace_of(request)
        if (
            workspace == "default"
            and not request.url.query
            and (personal := hub.personal_home(actor_of(request)))
        ):
            return RedirectResponse(workspace_editor_path(personal))
        owner = hub.owner(actor_of(request), workspace)
        root = workspace_editor_path(workspace)
        if not owner and (
            request.query_params.get("view") == "designs" or request.query_params.get("design")
        ):
            return RedirectResponse(root)
        creating = editor == "1" and new == "1" and owner
        page = (PROJECT / "web" / ("index.html" if deck or creating else "hub.html")).read_text()
        owner_attribute = " data-workspace-owner" if owner else ""
        return HTMLResponse(
            page.replace(
                '<html lang="en">',
                f'<html lang="en" data-shared-editor data-studio-workspace="{workspace}"'
                f"{owner_attribute}>",
                1,
            )
            .replace('href="/"', f'href="{root}"')
            .replace('href="/?editor=1&amp;new=1"', f'href="{root}?editor=1&amp;new=1"')
        )

    @app.get("/web/{path:path}")
    @app.get("/runtime/{path:path}")
    def studio_asset(request: Request, path: str) -> FileResponse:
        root = PROJECT / ("web" if request.url.path.startswith("/web/") else "runtime")
        asset = (root / path).resolve()
        if (
            not asset.is_relative_to(root)
            or asset.suffix.lower() not in ASSET_SUFFIXES
            or not asset.is_file()
        ):
            raise AccessDenied
        return FileResponse(asset)

    @app.get("/api/session")
    def session(request: Request) -> dict[str, object]:
        principal = getattr(request.state, "verified_guest", None)
        return {
            "mode": "hosted",
            "public_url": config.public_url,
            "email": principal.email if isinstance(principal, Principal) else None,
            "mcp_url": config.public_url + "/mcp",
            "shared_workspace": True,
            "collaboration_url": "/together",
            "capabilities": {"local_admin": False, "automatic_feedback": False},
            "scoped_editor": True,
            "workspace_owner": hub.owner(actor_of(request), workspace_of(request)),
        }

    @app.get("/api/hub")
    def library(request: Request) -> dict[str, object]:
        actor, workspace = actor_of(request), workspace_of(request)
        with hub.locked():
            try:
                store = hub.store(workspace)
            except AccessDenied:
                return {"projects": [], "artifacts": [], "designs": []}
            service = ArtifactStore(store)
            projects = {
                item.id: {
                    "id": item.id,
                    "title": item.title,
                    "artifact_count": 0,
                    "legacy": item.legacy,
                }
                for item in service.list_projects()
                if hub.level(actor, Scope(workspace, "project", item.id))
            }
            result: list[dict[str, object]] = []
            opened = hub.opened_revisions(actor, workspace)
            for item in service.list_artifacts():
                level = hub.level(actor, artifact_scope(request, item.id))
                if not level:
                    continue
                entry = artifact_payload(
                    item,
                    visible_project=item.project_id in projects,
                    opened=opened.get(item.id, {}),
                )
                entry["access_role"] = LEVEL_ROLE[level]
                entry.update(cover_metadata(store, item, scoped=True))
                project = cast(str, entry["project_id"])
                info = projects.setdefault(
                    project, {"id": project, "title": "Shared with me", "artifact_count": 0}
                )
                info["artifact_count"] = cast(int, info["artifact_count"]) + 1
                result.append(entry)
            designs = DesignLibrary(store).list() if hub.owner(actor, workspace) else []
            return {
                "projects": list(projects.values()),
                "artifacts": result,
                "designs": [item.model_dump() for item in designs],
            }

    @app.post("/api/hub/projects")
    def new_project(request: Request, body: ProjectInput) -> dict[str, str]:
        """Create a project in the selected workspace for its owner."""
        actor, workspace = actor_of(request), workspace_of(request)
        with hub.locked():
            hub.manage(actor, Scope(workspace))
            item = ArtifactStore(hub.store(workspace)).create_project(body.title, body.context)
            return {"id": item.id, "title": item.title}

    @app.post("/api/artifacts/import")
    def import_artifact(request: Request, body: ArtifactImport) -> dict[str, str]:
        """Import owned files into a project in the selected workspace."""
        actor, workspace = actor_of(request), workspace_of(request)
        with hub.locked():
            hub.manage(actor, Scope(workspace, "project", body.project_id))
            store = hub.store(workspace)
            encoded_size = sum(len(file.data_base64) for file in body.files)
            available = min(disk_usage(hub.root).free, disk_usage(store.root).free)
            if available < IMPORT_DISK_RESERVE + 3 * encoded_size:
                raise HTTPException(507, "Not enough workspace storage for these files.")
            try:
                item = import_upload(ArtifactStore(store), body, staging_root=hub.root)
            except ValueError as error:
                raise HTTPException(400, str(error)) from None
            return {"id": item.id}

    @app.get("/api/hub/previews/{artifact_id}")
    def thumbnail(request: Request, artifact_id: str, v: str = "") -> Response:
        """Serve only the current PNG cover for an artifact the guest can still see."""
        with hub.guard(actor_of(request), artifact_scope(request, artifact_id)) as store:
            item = ArtifactStore(store).get_artifact(artifact_id)
            reply = request_current_cover(store, item, v)
            if reply.path is not None:
                return FileResponse(reply.path, media_type="image/png")
            message = reply.message if reply.status < 500 else "The preview is unavailable."
            return JSONResponse(
                {"code": reply.code, "message": message},
                status_code=reply.status,
                headers={"Retry-After": str(reply.retry_after)},
            )

    @app.get("/api/decks")
    def decks(request: Request) -> list[dict[str, str]]:
        with hub.locked():
            try:
                store = hub.store(workspace_of(request))
            except AccessDenied:
                return []
            actor = actor_of(request)
            return [
                entry
                for entry in ArtifactStore(store).presentation_summaries()
                if hub.level(actor, artifact_scope(request, entry["id"]))
            ]

    @app.post("/api/decks")
    def new_presentation(request: Request, body: WorkspacePresentationInput) -> dict[str, str]:
        """Create the native presentation and its project membership in the owned workspace."""
        actor, workspace = actor_of(request), workspace_of(request)
        with hub.locked():
            project_id = "" if body.project_id == LEGACY_PROJECT else body.project_id
            target = Scope(workspace, "project", project_id) if project_id else Scope(workspace)
            hub.manage(actor, target)
            store = hub.store(workspace)
            encoded_size = sum(len(file.data_base64) for file in body.files)
            disk_reserve = IMPORT_DISK_RESERVE + 3 * encoded_size
            if disk_usage(store.root).free < disk_reserve:
                raise HTTPException(507, "Not enough workspace storage for these files.")
            try:
                created = store.create(
                    body.deck_id,
                    body.title,
                    body.design_id,
                    body.brief,
                    body.files,
                    project_id=project_id,
                    scoped=True,
                    disk_reserve_bytes=disk_reserve,
                )
            except OSError as error:
                if error.errno == errno.ENOSPC:
                    raise HTTPException(
                        507, "Not enough workspace storage for this presentation."
                    ) from None
                raise
            return {"id": created.id, "title": created.title}

    @contextmanager
    def owned_workspace(request: Request) -> Generator[Store]:
        """Read private workspace context only as the selected workspace owner."""
        workspace = workspace_of(request)
        with hub.locked():
            hub.manage(actor_of(request), Scope(workspace))
            yield hub.store(workspace)

    app.include_router(design_library_routes(owned_workspace, disk_reserve=IMPORT_DISK_RESERVE))

    @app.get("/api/designs")
    def designs(request: Request) -> list[dict[str, str]]:
        """List only reusable presentation designs owned by this workspace."""
        with owned_workspace(request) as store:
            return [
                {"id": item.id, "name": item.name, "description": item.description}
                for item in store.designs()
            ]

    @app.get("/api/preferences")
    def preferences(request: Request) -> dict[str, str]:
        """Supply this workspace's selected default for the presentation picker."""
        with owned_workspace(request) as store:
            return {"default_design_id": store.default_design_id()}

    @app.get("/api/designs/{design_id}/preview")
    def design_preview(request: Request, design_id: str) -> HTMLResponse:
        """Show only the cover of an owned design in an inert picker frame."""
        with owned_workspace(request) as store:
            folder = store.design(design_id, include_archived=False).folder_id
            slide = store.read(folder).slides[0]
            embedder = ScopedDeckEmbedder(store, folder)
            theme = embedder.css(embedder.local_path("theme.css").read_text()).replace("<", r"\3c ")
            markup = embedder.markup(slide.html)
        return HTMLResponse(
            '<!doctype html><html><head><meta charset="utf-8"><style>'
            + theme
            + "\nhtml,body{width:100%;height:100%;overflow:hidden}</style>"
            + f"</head><body>{markup}</body></html>",
            headers={
                "Content-Security-Policy": (
                    "default-src 'none'; sandbox; style-src 'unsafe-inline'; img-src data:; "
                    "font-src data:; media-src data:; base-uri 'none'; form-action 'none'; "
                    f"frame-ancestors {config.public_url}"
                )
            },
        )

    @contextmanager
    def deck_access(request: Request, deck_id: str, role: Role) -> Generator[Store]:
        """Check the grant and confirm this resource is a legacy presentation."""
        with hub.guard(actor_of(request), artifact_scope(request, deck_id), role) as store:
            if ArtifactStore(store).get_artifact(deck_id).legacy_deck_id != deck_id:
                raise AccessDenied
            principal = getattr(request.state, "verified_guest", None)
            with revision_author(
                principal.email if role == "edit" and isinstance(principal, Principal) else None
            ):
                yield store

    @app.get("/api/decks/{deck_id}")
    def deck(request: Request, deck_id: str) -> dict[str, object]:
        actor, scope = actor_of(request), artifact_scope(request, deck_id)
        with deck_access(request, deck_id, "view") as store:
            opened = hub.opened_revisions(actor, scope.workspace, deck_id).get(deck_id, {})
            result: dict[str, object] = {
                **shared_deck(store, deck_id, opened).model_dump(),
                "access_role": LEVEL_ROLE[hub.level(actor, scope)],
            }
            if hub.owner(actor, scope.workspace):
                result["project_id"] = ArtifactStore(store).get_artifact(deck_id).project_id
                result["brief_files"] = [
                    {"name": file.name, "size": file.size} for file in store.brief_files(deck_id)
                ]
            return result

    @app.post("/api/decks/{deck_id}/slides/{slide_id}/seen")
    def slide_seen(
        request: Request, deck_id: str, slide_id: str, body: SlideSeenInput
    ) -> dict[str, bool]:
        """Acknowledge the slide opened by this reader while the grant is valid."""
        with deck_access(request, deck_id, "view") as store:
            slide = store.locate(store.read(deck_id), slide_id)
            if store.content_hash(deck_id, slide) != body.content_hash:
                return {"seen": False}
            hub.record_opened(
                actor_of(request), artifact_scope(request, deck_id), slide_id, body.content_hash
            )
            return {"seen": True}

    @app.get("/api/decks/{deck_id}/brief-files/{file_index}")
    def brief_attachment(request: Request, deck_id: str, file_index: int) -> FileResponse:
        """Download a private brief attachment as its original filename."""
        with owned_workspace(request), deck_access(request, deck_id, "view") as store:
            files = store.brief_files(deck_id)
            if not 0 <= file_index < len(files):
                raise AccessDenied
            file = files[file_index]
            if not Path(file.path).is_relative_to(store.folder(deck_id) / "brief-attachments"):
                raise AccessDenied
            return FileResponse(
                file.path, filename=file.name, media_type="application/octet-stream"
            )

    @app.get("/api/decks/{deck_id}/workflow")
    def workflow(request: Request, deck_id: str) -> FeedbackWorkflow:
        with deck_access(request, deck_id, "view"):
            return FeedbackWorkflow()

    @contextmanager
    def presenter_access(request: Request, deck_id: str) -> Generator[Store]:
        """Open library examples only for their owner and regular decks by grant."""
        actor, workspace = actor_of(request), workspace_of(request)
        with hub.locked():
            if hub.owner(actor, workspace):
                store = hub.store(workspace)
                if (store.folder(deck_id) / "design.json").is_file():
                    if any(item.folder_id == deck_id for item in store.designs()):
                        yield store
                        return
                    raise AccessDenied
            with deck_access(request, deck_id, "view") as store:
                yield store

    @contextmanager
    def history_access(
        request: Request, kind: Literal["deck", "artifact"], identifier: str, role: Role
    ) -> Generator[Store]:
        """Limit retained history to presentations with an active grant."""
        if kind != "deck":
            raise AccessDenied
        with deck_access(request, identifier, role) as store:
            yield store

    app.include_router(deck_edit_routes(deck_access))
    app.include_router(history_routes(lambda: hub.store("default"), scoped_access=history_access))

    app.include_router(
        hosted_presenter_router(
            lambda: hub.store("default"),
            config.public_url,
            scoped_access=presenter_access,
        )
    )

    @app.get("/decks/{deck_id}/{path:path}")
    def deck_asset(request: Request, deck_id: str, path: str) -> FileResponse:
        with deck_access(request, deck_id, "view") as store:
            return deck_asset_response(store, deck_id, path)

    @app.get("/api/artifacts/{artifact_id}")
    def artifact(request: Request, artifact_id: str) -> dict[str, object]:
        actor, scope = actor_of(request), artifact_scope(request, artifact_id)
        with hub.guard(actor, scope) as store:
            item = ArtifactStore(store).get_artifact(artifact_id)
            visible_project = bool(
                hub.level(actor, Scope(scope.workspace, "project", item.project_id))
            )
            opened = hub.opened_revisions(actor, scope.workspace, artifact_id).get(artifact_id, {})
            return {
                **artifact_payload(item, visible_project=visible_project, opened=opened),
                "access_role": LEVEL_ROLE[hub.level(actor, scope)],
                "feedback": [],
                "mode": "live",
                "workflow": FeedbackWorkflow(),
            }

    @app.get("/api/artifacts/{artifact_id}/preview")
    def preview(request: Request, artifact_id: str) -> RedirectResponse:
        scope = artifact_scope(request, artifact_id)
        with hub.guard(actor_of(request), scope):
            return RedirectResponse(
                f"/api/workspaces/{scope.workspace}/artifacts/{artifact_id}/preview"
            )

    @app.post("/api/artifacts/{artifact_id}/approve")
    def approve_artifact(
        request: Request, artifact_id: str, body: ArtifactApproval
    ) -> dict[str, object]:
        """Record human approval of the reviewed artifact version under the current grant."""
        actor, scope = actor_of(request), artifact_scope(request, artifact_id)
        with hub.guard(actor, scope, "edit") as store:
            service = ArtifactStore(store)
            if service.get_artifact(artifact_id).legacy_deck_id:
                raise AccessDenied
            saved = service.approve(artifact_id, body.content_hash, body.approved)
            opened = hub.opened_revisions(actor, scope.workspace, artifact_id).get(artifact_id, {})
            return review_fields(saved, opened)

    @app.post("/api/artifacts/{artifact_id}/seen")
    def artifact_seen(
        request: Request, artifact_id: str, body: SlideSeenInput
    ) -> dict[str, object]:
        """Acknowledge the opened artifact snapshot for this reader only."""
        actor, scope = actor_of(request), artifact_scope(request, artifact_id)
        with hub.guard(actor, scope) as store:
            item = ArtifactStore(store).get_artifact(artifact_id)
            if item.legacy_deck_id:
                raise AccessDenied
            if item.content_hash != body.content_hash:
                raise ConflictError("Artifact changed. Open the current version first.")
            hub.record_opened(actor, scope, "", item.content_hash)
            return review_fields(item, {"": item.content_hash})

    @app.get("/api/artifacts/{artifact_id}/revisions/{revision}/files/{path:path}")
    @app.get("/api/artifacts/{artifact_id}/files/{path:path}")
    def file(
        request: Request, artifact_id: str, path: str, revision: int | None = None
    ) -> FileResponse:
        with hub.guard(actor_of(request), artifact_scope(request, artifact_id)) as store:
            service = ArtifactStore(store)
            current = service.get_artifact(artifact_id).revision
            if revision is not None and revision != current:
                raise AccessDenied
            target = service.resolve_file(artifact_id, path, current)
            return FileResponse(
                target,
                filename=target.name,
                media_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
                headers=artifact_file_headers(
                    artifact_id, current, config.public_url, config.preview_url
                ),
            )

    @app.get("/api/app-info")
    def app_info() -> dict[str, object]:
        return {"version": __version__, "update_check": "disabled", "update_available": False}

    @app.get("/api/navigation")
    def navigation() -> NavigationSnapshot:
        return NavigationSnapshot(connected=[], recent=[])

    @app.get("/api/favorites")
    def favorites() -> dict[str, object]:
        return {"projects": [], "artifacts": [], "slides": []}

    @app.get("/api/favorites/slides")
    def favorite_slides() -> list[object]:
        return []

    return app
