"""Explicitly scoped MCP surface; no legacy global routes or feedback are exposed."""

import base64
import codecs
import time
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from shutil import disk_usage
from typing import Annotated, Literal, cast
from urllib.parse import urlencode

from fastapi import Request
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult, ToolAnnotations
from pydantic import Field

from .agent_io import deck_receipt, guide_fields, presentation_sizing, rule_fields
from .artifacts import LEGACY_PROJECT, ArtifactStore
from .copilot_tools import (
    MAX_READ_BYTES,
    READ,
    WRITE,
    RequestID,
    ScopedTools,
    attachment_page,
    public_object,
    remote_tool,
)
from .deck_presence import AGENT_ACTIVITY_TTL, DeckPresence
from .design_authoring import DesignAuthoring
from .design_library import DesignLibrary
from .element_alternatives import ElementAlternatives, ElementInput
from .hosted_policy import public_deck_asset
from .interactive import prepare_document
from .libraries import file_hash
from .models import DeckSection, DesignFile, FileInput
from .multiplayer_access import (
    AccessDenied,
    Actor,
    Kind,
    Role,
    Scope,
    Sharing,
    workspace_editor_path,
)
from .multiplayer_chat import Channel, Conversations
from .multiplayer_http import IMPORT_DISK_RESERVE
from .presentation_files import PresentationFiles
from .sections import Sections, section_layout
from .slide_actions import SlideActions
from .slide_designs import DesignInput, SlideDesigns
from .storage import Store
from .structure_proposals import StructureProposals
from .version_authors import revision_author

Ctx = Context[None, Request]
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True)
NewDeckID = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*$", max_length=100)]


class MultiplayerTools:
    def __init__(
        self, sharing: Sharing, chat: Conversations, public_url: str, presence: DeckPresence
    ) -> None:
        self.sharing, self.chat, self.public_url = sharing, chat, public_url
        self.presence = presence

    def actor(self, ctx: Ctx) -> Actor:
        request = ctx.request_context.request
        if request is None:
            raise AccessDenied
        actor = cast(Actor, request.state.actor)
        self.sharing.agent_record(actor)
        with self.sharing.connect() as db:
            db.execute("UPDATE agents SET last_seen=? WHERE id=?", (time.time(), actor.agent))
        return actor

    def scope(self, actor: Actor, artifact: str = "", project: str = "") -> Scope:
        row = self.sharing.agent_record(actor)
        return Scope(
            cast(str, row["workspace"]),
            "artifact" if artifact else "project" if project else cast(Kind, row["kind"]),
            artifact or project or cast(str, row["resource"]),
        )

    @contextmanager
    def deck_store(self, actor: Actor, deck_id: str, role: Role = "view") -> Generator[Store]:
        """Restrict deck tools to the presentation associated with the authorized artifact."""
        with self.sharing.guard(actor, self.scope(actor, deck_id), role) as store:
            if ArtifactStore(store).get_artifact(deck_id).legacy_deck_id != deck_id:
                raise AccessDenied
            if role == "edit":
                record = self.sharing.agent_record(actor)
                with revision_author(str(record["name"]) + " · agent"):
                    yield store
            else:
                yield store

    def catalog(self, actor: Actor, scope: Scope) -> dict[str, object]:
        with self.sharing.locked():
            self.sharing.require(actor, scope)
            store = self.sharing.store(scope.workspace)
            artifacts = ArtifactStore(store)
            visible = [
                item
                for item in artifacts.list_artifacts()
                if self.sharing.level(actor, Scope(scope.workspace, "artifact", item.id))
            ]
            return {
                "artifacts": [
                    {
                        "id": item.id,
                        "title": item.title,
                        "kind": item.kind,
                        "revision": item.revision,
                        "content_hash": item.content_hash,
                    }
                    for item in visible
                ],
                "projects": [
                    {"id": item.id, "title": item.title}
                    for item in artifacts.list_projects()
                    if self.sharing.level(actor, Scope(scope.workspace, "project", item.id))
                ],
            }

    def get_working_context(
        self, ctx: Ctx, artifact_id: str = "", known_rules_hash: str = ""
    ) -> dict[str, object]:
        """Read the selected scope and workflow. Pass known_rules_hash only for retained rules."""
        actor = self.actor(ctx)
        scope = self.scope(actor, artifact_id)
        with self.sharing.guard(actor, scope) as store:
            result: dict[str, object] = {
                "workspace_id": scope.workspace,
                "scope": {"kind": scope.kind, "resource": scope.resource},
                "workspace_url": self.public_url + "/?workspace=" + scope.workspace,
                "mcp_url": self.public_url + "/mcp",
                "agent_id": actor.agent,
                "workflow": "Read source and hashes before editing. Read_feedback returns only "
                "requests assigned to you. Claim each request with a stable session_id before "
                "acting; reply_to_feedback with that claim acknowledges it. Renew claims for "
                "work beyond 10 minutes. Private replies stay private; artifact edits are shared. "
                "After a conflict, re-read and reconcile. Retry writes with the same request_id. "
                "Use get_authoring_guide for details and slide_designs for proposals.",
            }
            if scope.kind == "artifact":
                scoped = ScopedTools(store, "", True, hosted_hooks=True)
                item = scoped.artifact(scope.resource)
                metadata = scoped.metadata(item, full=True)
                # Parent project context and identity are not part of an artifact grant.
                if not self.sharing.level(
                    actor, Scope(scope.workspace, "project", item.project_id)
                ):
                    metadata.pop("project_id", None)
                    metadata.pop("project_title", None)
                result["artifact"] = metadata
                if item.legacy_deck_id:
                    result["outline"] = self.presentation_outline(store, actor, scope)
                    result["presentation_sizing"] = presentation_sizing()
            else:
                result.update(self.catalog(actor, scope))
                if scope.kind == "project":
                    project = next(
                        item
                        for item in ArtifactStore(store).list_projects()
                        if item.id == scope.resource
                    )
                    result["project_context"] = project.context
            if self.sharing.level(actor, Scope(scope.workspace)):
                result.update(
                    rule_fields(
                        store.authoring_instructions(),
                        store.learned_preferences(),
                        known_rules_hash,
                    )
                )
            return result

    def list_artifacts(self, ctx: Ctx) -> dict[str, object]:
        """List only artifacts and projects available to this particular agent connection."""
        actor = self.actor(ctx)
        return self.catalog(actor, self.scope(actor))

    @contextmanager
    def design_store(self, ctx: Ctx) -> Generator[Store]:
        """Limit private reusable designs to the owner's workspace-wide connection."""
        actor = self.actor(ctx)
        scope = self.scope(actor)
        if scope.kind != "workspace" or not self.sharing.owner(
            Actor(actor.person), scope.workspace
        ):
            raise AccessDenied
        with self.sharing.guard(actor, scope, "edit") as store:
            yield store

    def list_design_contexts(self, ctx: Ctx) -> list[dict[str, object]]:
        """Discover reusable packs and references with the owner's workspace connection."""
        with self.design_store(ctx) as store:
            return [item.model_dump() for item in DesignLibrary(store).list()]

    def get_design_context(self, ctx: Ctx, design_id: str) -> dict[str, object]:
        """List current design references and source hashes without server filesystem paths."""
        with self.design_store(ctx) as store:
            item = DesignLibrary(store).get(design_id)
            return item.model_dump(exclude={"files": {"__all__": {"local_path"}}})

    def read_design_reference(
        self,
        ctx: Ctx,
        design_id: str,
        path: str,
        offset: int = 0,
        max_bytes: int = 65536,
        encoding: Literal["utf-8", "base64"] = "utf-8",
    ) -> dict[str, object]:
        """Read a bounded page of an inventoried file from a private design."""
        if not 4 <= max_bytes <= MAX_READ_BYTES:
            raise ValueError("Read 4–1048576 bytes per design file page.")
        with self.design_store(ctx) as store:
            target = DesignLibrary(store).file(design_id, path)
            size = target.stat().st_size
            if size > 25_000_000:
                raise ValueError("Files above 25 MB are available from the Designs download.")
            metadata = DesignFile(name=target.name, path=path, size=size, sha256=file_hash(target))
            return attachment_page(
                target,
                store.root,
                metadata,
                offset,
                max_bytes,
                encoding,
            )

    def create_design_pack(
        self, ctx: Ctx, design_id: str, name: str, description: str = "", css: str = ""
    ) -> dict[str, object]:
        """Scaffold a reusable HTML slide pack in the owner's selected workspace."""
        with self.design_store(ctx) as store:
            created = DesignAuthoring(store).create(design_id, name, description, css)
            created.pop("import_directory", None)
            return created

    def get_design_file(self, ctx: Ctx, design_id: str, path: str) -> dict[str, object]:
        """Read one author-editable design text file and its current hash."""
        with self.design_store(ctx) as store:
            return DesignAuthoring(store).read_file(design_id, path)

    def write_design_file(
        self, ctx: Ctx, design_id: str, path: str, text: str, expected_hash: str = ""
    ) -> dict[str, object]:
        """Save guidance, theme CSS or a text asset with its current design hash."""
        with self.design_store(ctx) as store:
            return DesignAuthoring(store).write_file(design_id, path, text, expected_hash)

    def get_authoring_guide(
        self,
        ctx: Ctx,
        topic: Literal["workflow", "live_review", "slide_designs"] = "workflow",
        known_hash: str = "",
    ) -> dict[str, object]:
        """Load detailed authoring or host-hook guidance only when needed."""
        actor = self.actor(ctx)
        with self.sharing.guard(actor, self.scope(actor)) as store:
            if topic == "slide_designs":
                return guide_fields(
                    "Use publish_slide_designs for whole slides, or publish_element_alternatives "
                    "for a static headline, text or graphic. Read get_slide first and publish "
                    "against its content_hash. For element options, target a unique HTML id "
                    "and keep that id in each complete replacement element. For a slide with "
                    "custom interactions, use a whole-slide design. Offer two or three distinct "
                    "options, capture the proposals, and leave adoption to the human. Use "
                    "get_slide_designs or get_element_alternatives for decisions. Assigned option "
                    "feedback is in read_feedback. Follow its proposal ID and context_hash to "
                    "read or capture the reviewed option. Publish a new set against current "
                    "source when refining. A reusable design library requires a workspace-wide "
                    "owner connection. Existing slide source and theme are available through "
                    "the presentation tools within the current scope.",
                    known_hash,
                )
            if topic == "live_review":
                return {
                    **guide_fields(
                        "Install get_agent_hook_client's client using normal native host trust. "
                        "Set ARTIFACT_STUDIO_AGENT_TOKEN in the host environment, never in chat. "
                        "Configure hooks with this HTTPS origin and the same MCP server name. "
                        "start_live_review requests a connection; get_live_review_status reports "
                        "whether hooks are actually listening. Other agents remain connected. "
                        "A hook signals pending assigned requests. read_feedback and "
                        "claim_feedback retrieve/claim them. Polling works without hooks.",
                        known_hash,
                    ),
                    "origin": self.public_url,
                }
            guide = ScopedTools(store, "", True).get_authoring_guide()
            guide.update(
                guide_fields(
                    "Use get_working_context for the selected scope. For presentations, read "
                    "get_presentation_outline (brief, order, hashes), then get_slide only for "
                    "slides "
                    "you need. revise_slide takes SlidePatch JSON and the expected_hash you read. "
                    "For shareable slide work, set_slide_activity shows an expiring marker; clear "
                    "it when done and omit it for private work. "
                    "Use this connection's registered tool schemas before writing; inspect missing "
                    "tool details once and reuse them. Hosted "
                    "add_slide takes slide_json (a JSON string), request_id, after_slide_id, "
                    "expected_slide_ids and expected_sections_hash. Use an empty after_slide_id "
                    "to insert at the beginning; obtain slide IDs and sections_hash from the "
                    "outline. "
                    "add_slide, reorder_slides and set_presentation_sections use the outline's "
                    "order "
                    "and sections hashes. Load the slide_designs topic for design and element "
                    "alternatives. Remove only slides the human asked to delete, using "
                    "remove_slide "
                    "and the current expected_hash; list and recover with get_deleted_slides and "
                    "restore_slides. create_presentation creates a native deck in an owned "
                    "workspace with a workspace or appropriate project connection. For artifacts, "
                    "get_artifact returns the manifest; read_artifact_files loads selected files. "
                    "revise_artifact takes files_json, expected_revision and request_id. "
                    "create_artifact needs an editable project and inline file bundle. "
                    "capture_slide, capture_contact_sheet and capture_artifact return real pixels "
                    "for visual review. "
                    "Use arbitrary HTML/CSS/SVG/JavaScript inside the existing file contract. "
                    "Read only assigned requests with read_feedback. Claim with claim_feedback "
                    "before acting. Reply with reply_to_feedback using the claim and a stable "
                    "request_id. Reply visibility follows the original chat; never summarize "
                    "private chat publicly without permission. Read_chat provides shared context "
                    "or your owner's private conversation. Read_chat_attachment retrieves one "
                    "visible file by event ID and index. Withheld drafts stay private. "
                    "On conflicts re-read and reconcile; on lost responses retry the same "
                    "request_id.",
                    known_hash,
                )
            )
            return guide

    def get_agent_hook_client(self, ctx: Ctx) -> dict[str, object]:
        """Get the optional stdlib hook client; respect native host installation and trust."""
        actor = self.actor(ctx)
        with self.sharing.guard(actor, self.scope(actor)) as store:
            return ScopedTools(store, "", True).get_agent_hook_client()

    def get_artifact(self, ctx: Ctx, artifact_id: str) -> dict[str, object]:
        """Read this artifact's manifest and brief, excluding private legacy discussion."""
        return cast(dict[str, object], self.get_working_context(ctx, artifact_id)["artifact"])

    def read_artifact_files(
        self, ctx: Ctx, artifact_id: str, paths: list[str], revision: int = 0
    ) -> list[dict[str, object]]:
        """Read declared owned files, at most 1 MiB; revision=0 selects current source."""
        actor = self.actor(ctx)
        scope = self.scope(actor, artifact_id)
        with self.sharing.guard(actor, scope) as store:
            if (
                revision
                and not self.sharing.owner(actor, scope.workspace)
                and revision != ArtifactStore(store).get_artifact(artifact_id).revision
            ):
                raise AccessDenied
            return ScopedTools(store, "", True).read_artifact_files(artifact_id, paths, revision)

    def revise_artifact(
        self,
        ctx: Ctx,
        artifact_id: str,
        files_json: Annotated[
            str,
            Field(
                description="JSON array of file changes with path and text, data_base64, or delete."
            ),
        ],
        expected_revision: Annotated[int, Field(ge=1)],
        request_id: RequestID,
    ) -> dict[str, object]:
        """Publish changed inline files with optimistic concurrency and an idempotent request ID."""
        actor = self.actor(ctx)
        scope = self.scope(actor, artifact_id)
        with self.sharing.guard(actor, scope, "edit") as store:
            result = ScopedTools(store, "", True).revise_artifact(
                artifact_id,
                files_json,
                expected_revision,
                self.sharing.request_id(actor, request_id),
            )
            item = ArtifactStore(store).get_artifact(artifact_id)
            if not self.sharing.level(actor, Scope(scope.workspace, "project", item.project_id)):
                result.pop("project_id", None)
                result.pop("project_title", None)
            return result

    def create_artifact(
        self,
        ctx: Ctx,
        project_id: str,
        title: str,
        entrypoint: str,
        files_json: str,
        request_id: str,
        context: str = "",
    ) -> dict[str, object]:
        """Create an inline file bundle in an editable shared project; no host file paths."""
        actor = self.actor(ctx)
        with self.sharing.guard(actor, self.scope(actor, project=project_id), "edit") as store:
            return ScopedTools(store, "", True).create_artifact(
                project_id,
                title,
                entrypoint,
                files_json,
                self.sharing.request_id(actor, request_id),
                context,
            )

    def create_presentation(
        self,
        ctx: Ctx,
        deck_id: NewDeckID,
        title: str,
        design_id: str = "",
        brief: str = "",
        files: Annotated[list[FileInput] | None, Field(max_length=5)] = None,
        include_content: bool = False,
        request_id: str = "",
        project_id: str = "",
    ) -> dict[str, object]:
        """Create a native deck with an initial cover and brief in your owned workspace.

        A workspace connection can choose the project; a project connection defaults to its
        project. Use a stable request_id to safely retry and include_content to read the cover.
        """
        actor = self.actor(ctx)
        connection = self.scope(actor)
        if not self.sharing.owner(Actor(actor.person), connection.workspace):
            raise AccessDenied
        project = (
            ("" if project_id == LEGACY_PROJECT else project_id)
            if project_id
            else connection.resource
            if connection.kind == "project"
            else ""
        )
        scope = (
            Scope(connection.workspace, "project", project)
            if project
            else Scope(connection.workspace)
        )
        with self.sharing.guard(actor, scope, "edit") as store:
            if store.folder(deck_id).exists():
                self.sharing.require(actor, self.scope(actor, deck_id), "edit")
            reserve = IMPORT_DISK_RESERVE + 3 * sum(len(file.data_base64) for file in files or [])
            if disk_usage(store.root).free < reserve:
                raise ValueError("Not enough workspace storage for this presentation.")
            name = str(self.sharing.agent_record(actor)["name"])
            with revision_author(name + " · agent"):
                created = store.create(
                    deck_id,
                    title,
                    design_id,
                    brief,
                    files,
                    self.sharing.request_id(actor, request_id) if request_id else "",
                    brief_author="agent",
                    project_id=project,
                    scoped=True,
                    disk_reserve_bytes=reserve,
                )
        if include_content:
            return public_object(created.model_dump(mode="json"))
        root = self.public_url + workspace_editor_path(scope.workspace)
        return {
            **deck_receipt(created).model_dump(),
            "workspace_url": root + "?" + urlencode({"deck": deck_id}),
            "audience_url": root + f"decks/{deck_id}/presenter?audience=1",
        }

    def presentation_outline(self, store: Store, actor: Actor, scope: Scope) -> dict[str, object]:
        """Include attachment names only for the workspace owner's scoped agent."""
        result = ScopedTools(store, "", True).get_presentation_outline(scope.resource)
        if self.sharing.owner(Actor(actor.person), scope.workspace):
            files = store.brief_files(scope.resource)
            if files:
                result["brief_files"] = [
                    {"file_index": index, "name": file.name, "size": file.size}
                    for index, file in enumerate(files)
                ]
        return result

    def get_presentation_outline(self, ctx: Ctx, deck_id: str) -> dict[str, object]:
        """Read this presentation's brief, slide order and current concurrency hashes."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id) as store:
            return self.presentation_outline(store, actor, self.scope(actor, deck_id))

    def read_presentation_brief_file(
        self,
        ctx: Ctx,
        deck_id: str,
        file_index: int,
        offset: int = 0,
        max_bytes: int = 65536,
        encoding: Literal["utf-8", "base64"] = "utf-8",
    ) -> dict[str, object]:
        """Read a bounded page of an owner brief file listed by get_presentation_outline."""
        if not 4 <= max_bytes <= MAX_READ_BYTES:
            raise ValueError("Read 4–1048576 bytes per attachment page.")
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id) as store:
            if not self.sharing.owner(Actor(actor.person), self.scope(actor, deck_id).workspace):
                raise AccessDenied
            files = store.brief_files(deck_id)
            if not 0 <= file_index < len(files):
                raise AccessDenied
            file = files[file_index]
            return attachment_page(
                Path(file.path),
                store.folder(deck_id) / "brief-attachments",
                file,
                offset,
                max_bytes,
                encoding,
            )

    def get_slide(self, ctx: Ctx, deck_id: str, slide_id: str) -> dict[str, object]:
        """Read complete slide source and expected_hash without other people's chat."""
        actor = self.actor(ctx)
        scope = self.scope(actor, deck_id)
        with self.deck_store(actor, deck_id) as store:
            result = ScopedTools(store, "", True).get_slide(deck_id, slide_id)
            if not self.sharing.owner(actor, scope.workspace):
                result.pop("agent", None)
            return result

    def set_slide_activity(
        self,
        ctx: Ctx,
        deck_id: str,
        slide_id: Annotated[str, Field(min_length=1, max_length=100)],
        active: bool,
    ) -> dict[str, int]:
        """Show anonymous editing activity to deck readers. Refresh during work; clear when done.

        Use only when you may share that work on this slide is in progress. Reading slides and
        handling private feedback never publish this signal automatically.
        """
        actor = self.actor(ctx)
        self.presence.set_editing(actor, self.scope(actor, deck_id), slide_id, active)
        return {"expires_in": AGENT_ACTIVITY_TTL if active else 0}

    def revise_slide(
        self,
        ctx: Ctx,
        deck_id: str,
        slide_id: str,
        expected_hash: str,
        request_id: str,
        patch_json: str,
    ) -> dict[str, object]:
        """Edit one slide with the hash you read; conflicts must be re-read and reconciled."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id, "edit") as store:
            return ScopedTools(store, "", True).revise_slide(
                deck_id,
                slide_id,
                expected_hash,
                self.sharing.request_id(actor, request_id),
                patch_json,
            )

    def add_slide(
        self,
        ctx: Ctx,
        deck_id: str,
        slide_json: str,
        request_id: str,
        after_slide_id: str,
        expected_slide_ids: list[str],
        expected_sections_hash: str,
        section_id: str = "",
    ) -> dict[str, object]:
        """Add a slide to a shared presentation using the existing slide contract."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id, "edit") as store:
            return ScopedTools(store, "", True).add_slide(
                deck_id,
                slide_json,
                self.sharing.request_id(actor, request_id),
                after_slide_id,
                expected_slide_ids,
                expected_sections_hash,
                section_id,
            )

    def publish_structure_proposal(
        self,
        ctx: Ctx,
        deck_id: str,
        summary: str,
        base_slide_ids: list[str],
        base_sections_hash: str,
        proposed_slide_ids: list[str],
        request_id: str,
        proposed_titles: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """Request a human decision on a proposed presentation structure without editing it."""
        actor = self.actor(ctx)
        return StructureProposals(self.sharing).publish(
            actor,
            self.scope(actor, deck_id),
            summary,
            base_slide_ids,
            base_sections_hash,
            proposed_slide_ids,
            request_id,
            proposed_titles,
        )

    def reorder_slides(
        self,
        ctx: Ctx,
        deck_id: str,
        slide_ids: list[str],
        expected_slide_ids: list[str],
        include_content: bool = False,
        sections: list[DeckSection] | None = None,
        expected_sections_hash: str = "",
    ) -> dict[str, object]:
        """Move slides using the previous order; include sections for cross-group moves."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id, "edit") as store:
            deck = (
                Sections(store).replace(
                    deck_id, slide_ids, sections, expected_slide_ids, expected_sections_hash
                )
                if sections is not None
                else store.reorder(deck_id, slide_ids, expected_slide_ids=expected_slide_ids)
            )
            if include_content:
                return public_object(
                    deck.model_dump(mode="json", exclude={"slides": {"__all__": {"agent"}}})
                )
            return deck_receipt(deck).model_dump()

    def set_presentation_sections(
        self,
        ctx: Ctx,
        deck_id: str,
        sections: list[DeckSection],
        expected_slide_ids: list[str],
        expected_sections_hash: str,
    ) -> dict[str, object]:
        """Set named slide groups using the outline's slide order and sections hash."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id, "edit") as store:
            return section_layout(
                Sections(store).replace(
                    deck_id,
                    [slide_id for section in sections for slide_id in section.slide_ids]
                    if sections
                    else expected_slide_ids,
                    sections,
                    expected_slide_ids,
                    expected_sections_hash,
                )
            )

    def remove_slide(
        self, ctx: Ctx, deck_id: str, slide_id: str, expected_hash: str, request_id: str = ""
    ) -> dict[str, object]:
        """Remove a slide the human asked to delete, preserving it for recovery.

        Use expected_hash from get_slide. Reuse request_id to safely retry the same removal.
        """
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id, "edit") as store:
            operation = self.sharing.request_id(actor, request_id or uuid.uuid4().hex)
            return (
                SlideActions(store).remove(deck_id, slide_id, expected_hash, operation).model_dump()
            )

    def get_deleted_slides(self, ctx: Ctx, deck_id: str) -> list[dict[str, object]]:
        """List slide IDs and titles for recoverable deletions in an editable presentation."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id, "edit") as store:
            return SlideActions(store).deletion_summaries(deck_id)

    def restore_slides(
        self, ctx: Ctx, deck_id: str, deletion_id: str, request_id: str
    ) -> dict[str, object]:
        """Restore a recoverable deletion using its operation_id and a stable request_id."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id, "edit") as store:
            operation = self.sharing.request_id(actor, request_id)
            return SlideActions(store).restore(deck_id, deletion_id, operation).model_dump()

    def publish_slide_designs(
        self,
        ctx: Ctx,
        deck_id: str,
        slide_id: str,
        expected_content_hash: str,
        request_id: str,
        options: Annotated[list[DesignInput], Field(min_length=1, max_length=5)],
    ) -> dict[str, object]:
        """Publish named whole-slide designs against get_slide's content_hash for human review."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id, "edit") as store:
            result = SlideDesigns(store, scoped=True).publish(
                deck_id,
                slide_id,
                expected_content_hash,
                self.sharing.request_id(actor, request_id),
                options,
            )
            return result.model_dump(mode="json")

    def get_slide_designs(
        self,
        ctx: Ctx,
        deck_id: str,
        slide_id: str,
        cursor: int | None = None,
        decision_cursor: int | None = None,
        set_id: str | None = None,
    ) -> dict[str, object]:
        """Read published design sets and human decisions for this slide."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id) as store:
            return (
                SlideDesigns(store, scoped=True)
                .list(
                    deck_id, slide_id, cursor=cursor, decision_cursor=decision_cursor, set_id=set_id
                )
                .model_dump(mode="json")
            )

    def get_slide_design(
        self, ctx: Ctx, deck_id: str, slide_id: str, design_id: str
    ) -> dict[str, object]:
        """Read one frozen design's editable audience source."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id) as store:
            return public_object(
                SlideDesigns(store, scoped=True)
                .read(deck_id, slide_id, design_id)
                .model_dump(mode="json")
            )

    def capture_slide_design(
        self, ctx: Ctx, deck_id: str, slide_id: str, design_id: str
    ) -> CallToolResult:
        """Render a frozen slide design without changing the current slide."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id) as store:
            return ScopedTools(store, "", True).capture_slide_design(deck_id, slide_id, design_id)

    def publish_element_alternatives(
        self,
        ctx: Ctx,
        deck_id: str,
        slide_id: str,
        target_id: Annotated[str, Field(min_length=1, max_length=200)],
        kind: Literal["headline", "text", "graphic"],
        label: Annotated[str, Field(min_length=1, max_length=80)],
        expected_content_hash: str,
        request_id: str,
        options: Annotated[list[ElementInput], Field(min_length=1, max_length=5)],
    ) -> dict[str, object]:
        """Publish alternatives for an existing static element, retaining its unique HTML ID."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id, "edit") as store:
            result = ElementAlternatives(store, scoped=True).publish(
                deck_id,
                slide_id,
                target_id,
                kind,
                label,
                expected_content_hash,
                self.sharing.request_id(actor, request_id),
                options,
            )
            return result.model_dump(mode="json")

    def get_element_alternatives(
        self,
        ctx: Ctx,
        deck_id: str,
        slide_id: str,
        cursor: int | None = None,
        decision_cursor: int | None = None,
        set_id: str | None = None,
    ) -> dict[str, object]:
        """Read element option groups and human decisions for this slide."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id) as store:
            return (
                ElementAlternatives(store, scoped=True)
                .list(
                    deck_id, slide_id, cursor=cursor, decision_cursor=decision_cursor, set_id=set_id
                )
                .model_dump(mode="json")
            )

    def get_element_alternative(
        self,
        ctx: Ctx,
        deck_id: str,
        slide_id: str,
        element_option_id: str,
        context_hash: str | None = None,
    ) -> dict[str, object]:
        """Read an immutable element fragment in its retained full-slide context."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id) as store:
            return (
                ElementAlternatives(store, scoped=True)
                .read(deck_id, slide_id, element_option_id, context_hash)
                .model_dump(mode="json")
            )

    def capture_element_alternative(
        self,
        ctx: Ctx,
        deck_id: str,
        slide_id: str,
        element_option_id: str,
        context_hash: str | None = None,
    ) -> CallToolResult:
        """Render a frozen element proposal in its full-slide context."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id) as store:
            return ScopedTools(store, "", True).capture_element_alternative(
                deck_id, slide_id, element_option_id, context_hash or "", scoped=True
            )

    def update_presentation_context(
        self, ctx: Ctx, deck_id: str, markdown: str, expected_hash: str
    ) -> str:
        """Replace the deck brief using context_hash from get_presentation_outline."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id, "edit") as store:
            return store.update_context(deck_id, markdown, expected_hash)

    def append_presentation_context(
        self, ctx: Ctx, deck_id: str, markdown: str, expected_hash: str
    ) -> str:
        """Append a continuation note using context_hash from get_presentation_outline."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id, "edit") as store:
            return store.append_context(deck_id, markdown, expected_hash)

    def get_presentation_theme(self, ctx: Ctx, deck_id: str) -> dict[str, object]:
        """Read the presentation's author CSS and its current expected_hash."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id) as store:
            return PresentationFiles(store).theme(deck_id)

    def update_presentation_theme(
        self, ctx: Ctx, deck_id: str, css: str, expected_hash: str
    ) -> dict[str, object]:
        """Replace this presentation's author CSS using its current expected_hash."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id, "edit") as store:
            return PresentationFiles(store).update_theme(deck_id, css, expected_hash)

    def list_presentation_assets(
        self, ctx: Ctx, deck_id: str, prefix: str = "assets/", offset: int = 0, limit: int = 50
    ) -> dict[str, object]:
        """List this deck's asset paths, sizes and current replacement hashes."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id) as store:
            return PresentationFiles(store).assets(deck_id, prefix, offset, limit, scoped=True)

    def upload_presentation_asset(
        self, ctx: Ctx, deck_id: str, asset_path: str, data_base64: str, expected_sha256: str = ""
    ) -> dict[str, object]:
        """Upload an inline deck asset; use its current sha256 when replacing existing bytes."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id, "edit") as store:
            if not asset_path.startswith("assets/") or not public_deck_asset(
                asset_path, embedded=True
            ):
                raise ValueError("Use a supported deck asset path.")
            data = base64.b64decode(data_base64, validate=True)
            if not data or len(data) > 1024 * 1024:
                raise ValueError("Upload a non-empty deck asset no larger than 1 MiB.")
            if disk_usage(store.root).free < IMPORT_DISK_RESERVE + len(data):
                raise ValueError("Not enough workspace storage for this asset.")
            return PresentationFiles(store).write_asset(deck_id, asset_path, data, expected_sha256)

    def prepare_interactive_html(
        self, ctx: Ctx, deck_id: str, html: str, include_theme: bool = True
    ) -> str:
        """Inline this deck's assets into interactive HTML without executing the supplied code."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id) as store:
            return prepare_document(store, deck_id, html, include_theme=include_theme, scoped=True)

    def capture_slide(
        self, ctx: Ctx, deck_id: str, slide_id: str, stage: int = -1, elapsed_ms: int = 0
    ) -> CallToolResult:
        """Render actual audience pixels; excludes private discussion and speaker notes."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id) as store:
            return ScopedTools(store, "", True).capture_slide(deck_id, slide_id, stage, elapsed_ms)

    def capture_contact_sheet(
        self,
        ctx: Ctx,
        deck_id: str,
        offset: int = 0,
        limit: int = 8,
        slide_ids: tuple[str, ...] = (),
    ) -> CallToolResult:
        """Render a scoped draft overview; use capture_slide for full-size detail."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id) as store:
            return ScopedTools(store, "", True).capture_contact_sheet(
                deck_id, offset, limit, slide_ids
            )

    def capture_artifact(self, ctx: Ctx, artifact_id: str) -> CallToolResult:
        """Render this owned artifact using the existing isolated browser capture."""
        actor = self.actor(ctx)
        with self.sharing.guard(actor, self.scope(actor, artifact_id)) as store:
            return ScopedTools(store, "", True).capture_artifact(artifact_id)

    def read_chat(
        self, ctx: Ctx, artifact_id: str, channel: Channel = "shared", before: int = 0
    ) -> list[dict[str, object]]:
        """Read assigned Shared threads or private Agent chat.

        Owners can allow full Shared history on a presentation.
        """
        actor = self.actor(ctx)
        return self.chat.history(actor, self.scope(actor, artifact_id), channel, before)

    def read_chat_attachment(
        self,
        ctx: Ctx,
        artifact_id: str,
        event_id: int,
        file_index: int,
        offset: int = 0,
        max_bytes: int = 65536,
        encoding: Literal["utf-8", "base64"] = "utf-8",
    ) -> dict[str, object]:
        """Read a bounded page from an attachment visible to this scoped agent."""
        if not 4 <= max_bytes <= 1024 * 1024:
            raise ValueError("Read 4–1048576 bytes per attachment page.")
        actor = self.actor(ctx)
        file, data = self.chat.attachment(
            actor, self.scope(actor, artifact_id), event_id, file_index, offset, max_bytes
        )
        if encoding == "utf-8":
            try:
                text = codecs.getincrementaldecoder("utf-8")().decode(
                    data, final=offset + len(data) == file["size"]
                )
            except UnicodeDecodeError:
                raise ValueError("Use encoding=base64 for binary files.") from None
            body: dict[str, object] = {"text": text}
            end = offset + len(text.encode())
        else:
            body = {"data_base64": base64.b64encode(data).decode()}
            end = offset + len(data)
        return {
            **file,
            "encoding": encoding,
            "offset": offset,
            "next_offset": end if end < file["size"] else None,
            **body,
        }

    def read_feedback(
        self, ctx: Ctx, artifact_id: str = "", deck_id: str = "", after: int = 0
    ) -> dict[str, object]:
        """Read pending requests with optional excerpts of prior Shared discussion.

        Repeated excerpt IDs reference text included earlier in the response.
        If shared_context_on_claim is true, claim with include_message=true for excerpts.
        """
        actor = self.actor(ctx)
        if not (artifact_id or deck_id) or (artifact_id and deck_id) or after < 0:
            raise ValueError(
                "Select exactly one artifact_id or deck_id; after must be nonnegative."
            )
        return self.chat.tasks(actor, self.scope(actor, artifact_id or deck_id))

    def claim_feedback(
        self,
        ctx: Ctx,
        artifact_id: str,
        event_id: int,
        session_id: str,
        include_message: bool = True,
    ) -> dict[str, object]:
        """Claim or renew for 10 minutes with a stable session ID.

        Set include_message=false after reading the task.
        """
        if not 8 <= len(session_id) <= 200:
            raise ValueError("Use a session_id of 8–200 characters.")
        actor = self.actor(ctx)
        return self.chat.claim(
            actor,
            self.scope(actor, artifact_id),
            event_id,
            session_id,
            include_message=include_message,
        )

    def reply_to_feedback(
        self,
        ctx: Ctx,
        artifact_id: str,
        event_id: int,
        claim: str,
        message: str,
        request_id: RequestID,
    ) -> dict[str, object]:
        """Reply in the request's original channel and acknowledge the claimed task atomically."""
        if not message.strip() or len(message) > 10000:
            raise ValueError("Reply with 1–10000 characters.")
        actor = self.actor(ctx)
        return self.chat.reply(
            actor, self.scope(actor, artifact_id), event_id, claim, message, request_id
        )

    def get_live_review_status(
        self, ctx: Ctx, artifact_id: str = "", deck_id: str = ""
    ) -> dict[str, object]:
        """Distinguish a requested connection from a host hook that is actually listening."""
        actor = self.actor(ctx)
        scope = self.scope(actor, artifact_id or deck_id)
        agents = self.chat.agents(actor, scope)
        return {
            "agent_id": actor.agent,
            "status": next(
                (item["status"] for item in agents if item["id"] == actor.agent), "offline"
            ),
        }

    def start_live_review(
        self, ctx: Ctx, artifact_id: str = "", deck_id: str = ""
    ) -> dict[str, object]:
        """Request this session's trusted host hook; does not disconnect other collaborators."""
        if bool(artifact_id) == bool(deck_id):
            raise ValueError("Select exactly one artifact_id or deck_id.")
        status = self.get_live_review_status(ctx, artifact_id, deck_id)
        return {
            **status,
            "status": "awaiting_host_hook" if status["status"] != "listening" else "listening",
            "artifact_id": artifact_id or deck_id,
            "guide": "get_authoring_guide(topic='live_review')",
        }

    def register(self, server: MCPServer[None]) -> None:
        reads = (
            self.get_working_context,
            self.list_artifacts,
            self.list_design_contexts,
            self.get_design_context,
            self.read_design_reference,
            self.get_design_file,
            self.get_authoring_guide,
            self.get_agent_hook_client,
            self.get_artifact,
            self.read_artifact_files,
            self.get_presentation_outline,
            self.read_presentation_brief_file,
            self.get_slide,
            self.get_slide_designs,
            self.get_slide_design,
            self.capture_slide_design,
            self.get_element_alternatives,
            self.get_element_alternative,
            self.capture_element_alternative,
            self.get_deleted_slides,
            self.get_presentation_theme,
            self.list_presentation_assets,
            self.prepare_interactive_html,
            self.capture_slide,
            self.capture_contact_sheet,
            self.capture_artifact,
            self.read_chat,
            self.read_chat_attachment,
            self.read_feedback,
            self.get_live_review_status,
        )
        writes = (
            self.revise_artifact,
            self.create_artifact,
            self.create_design_pack,
            self.write_design_file,
            self.revise_slide,
            self.set_slide_activity,
            self.add_slide,
            self.create_presentation,
            self.reorder_slides,
            self.publish_structure_proposal,
            self.set_presentation_sections,
            self.restore_slides,
            self.publish_slide_designs,
            self.publish_element_alternatives,
            self.update_presentation_context,
            self.append_presentation_context,
            self.update_presentation_theme,
            self.upload_presentation_asset,
            self.claim_feedback,
            self.reply_to_feedback,
            self.start_live_review,
        )
        for functions, annotations in ((reads, READ), (writes, WRITE)):
            for function in functions:
                server.add_tool(
                    remote_tool(cast(Callable[..., object], function)), annotations=annotations
                )
        server.add_tool(remote_tool(self.remove_slide), annotations=DESTRUCTIVE)
