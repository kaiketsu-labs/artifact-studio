"""Explicitly scoped MCP surface; no legacy global routes or feedback are exposed."""

import base64
import codecs
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, cast

from fastapi import Request
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult

from .artifacts import ArtifactStore
from .copilot_tools import MAX_READ_BYTES, READ, WRITE, ScopedTools, attachment_page, remote_tool
from .multiplayer_access import AccessDenied, Actor, Kind, Role, Scope, Sharing
from .multiplayer_chat import Channel, Conversations
from .storage import Store
from .version_authors import revision_author

Ctx = Context[None, Request]


class MultiplayerTools:
    def __init__(self, sharing: Sharing, chat: Conversations, public_url: str) -> None:
        self.sharing, self.chat, self.public_url = sharing, chat, public_url

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

    def get_working_context(self, ctx: Ctx, artifact_id: str = "") -> dict[str, object]:
        """Read first: exact workspace/scope, selected artifact, and collaboration workflow."""
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
                "Get_authoring_guide loads details on demand.",
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
                result["authoring_instructions"] = store.authoring_instructions()
            return result

    def list_artifacts(self, ctx: Ctx) -> dict[str, object]:
        """List only artifacts and projects available to this particular agent connection."""
        actor = self.actor(ctx)
        return self.catalog(actor, self.scope(actor))

    def get_authoring_guide(
        self, ctx: Ctx, topic: Literal["workflow", "live_review"] = "workflow"
    ) -> dict[str, object]:
        """Load detailed authoring or host-hook guidance only when needed."""
        actor = self.actor(ctx)
        with self.sharing.guard(actor, self.scope(actor)) as store:
            if topic == "live_review":
                return {
                    "guide": (
                        "Install get_agent_hook_client's client using normal native host trust. "
                        "Set ARTIFACT_STUDIO_AGENT_TOKEN in the host environment, never in chat. "
                        "Configure hooks with this HTTPS origin and the same MCP server name. "
                        "start_live_review requests a connection; get_live_review_status reports "
                        "whether hooks are actually listening. Other agents remain connected. "
                        "A hook signals pending assigned requests. read_feedback and "
                        "claim_feedback retrieve/claim them. Polling works without hooks."
                    ),
                    "origin": self.public_url,
                }
            guide = ScopedTools(store, "", True).get_authoring_guide()
            guide["guide"] = (
                "Use get_working_context for the selected scope. For presentations, read "
                "get_presentation_outline (brief, order, hashes), then get_slide only for slides "
                "you need. revise_slide takes SlidePatch JSON and the expected_hash you read. "
                "add_slide uses fresh outline order and sections hashes. For owned artifacts, "
                "get_artifact returns the manifest; read_artifact_files loads selected files. "
                "revise_artifact takes files_json, expected_revision and request_id. "
                "create_artifact needs an editable project and inline file bundle. "
                "capture_slide/capture_artifact return real pixels for visual review. "
                "Use arbitrary HTML/CSS/SVG/JavaScript inside the existing file contract. "
                "Read only assigned requests with read_feedback. Claim with claim_feedback "
                "before acting. Reply with reply_to_feedback using the claim and a stable "
                "request_id. Reply visibility follows the original chat; never summarize "
                "private chat publicly without permission. Read_chat provides shared context "
                "or your owner's private conversation. Read_chat_attachment retrieves one "
                "visible file by event ID and index. Withheld drafts stay private. "
                "On conflicts re-read and reconcile; on lost responses retry the same request_id."
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
        self, ctx: Ctx, artifact_id: str, files_json: str, expected_revision: int, request_id: str
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

    def capture_slide(
        self, ctx: Ctx, deck_id: str, slide_id: str, stage: int = -1, elapsed_ms: int = 0
    ) -> CallToolResult:
        """Render actual audience pixels; excludes private discussion and speaker notes."""
        actor = self.actor(ctx)
        with self.deck_store(actor, deck_id) as store:
            return ScopedTools(store, "", True).capture_slide(deck_id, slide_id, stage, elapsed_ms)

    def capture_artifact(self, ctx: Ctx, artifact_id: str) -> CallToolResult:
        """Render this owned artifact using the existing isolated browser capture."""
        actor = self.actor(ctx)
        with self.sharing.guard(actor, self.scope(actor, artifact_id)) as store:
            return ScopedTools(store, "", True).capture_artifact(artifact_id)

    def read_chat(
        self, ctx: Ctx, artifact_id: str, channel: Channel = "shared", before: int = 0
    ) -> list[dict[str, object]]:
        """Read shared conversation or your owner's private conversation; never withheld drafts."""
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
        """Read pending requests assigned to you; after is not an acknowledgement."""
        actor = self.actor(ctx)
        if not (artifact_id or deck_id) or (artifact_id and deck_id) or after < 0:
            raise ValueError(
                "Select exactly one artifact_id or deck_id; after must be nonnegative."
            )
        return self.chat.tasks(actor, self.scope(actor, artifact_id or deck_id))

    def claim_feedback(
        self, ctx: Ctx, artifact_id: str, event_id: int, session_id: str
    ) -> dict[str, object]:
        """Atomically claim/renew a task for 10 minutes; use a unique stable native session ID."""
        if not 8 <= len(session_id) <= 200:
            raise ValueError("Use a session_id of 8–200 characters.")
        actor = self.actor(ctx)
        return self.chat.claim(actor, self.scope(actor, artifact_id), event_id, session_id)

    def reply_to_feedback(
        self, ctx: Ctx, artifact_id: str, event_id: int, claim: str, message: str, request_id: str
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
            self.get_authoring_guide,
            self.get_agent_hook_client,
            self.get_artifact,
            self.read_artifact_files,
            self.get_presentation_outline,
            self.read_presentation_brief_file,
            self.get_slide,
            self.capture_slide,
            self.capture_artifact,
            self.read_chat,
            self.read_chat_attachment,
            self.read_feedback,
            self.get_live_review_status,
        )
        writes = (
            self.revise_artifact,
            self.create_artifact,
            self.revise_slide,
            self.add_slide,
            self.claim_feedback,
            self.reply_to_feedback,
            self.start_live_review,
        )
        for functions, annotations in ((reads, READ), (writes, WRITE)):
            for function in functions:
                server.add_tool(
                    remote_tool(cast(Callable[..., object], function)), annotations=annotations
                )
