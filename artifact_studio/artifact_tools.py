"""Stateless MCP entrypoints for unrestricted files and scoped artifact discussion."""

from collections.abc import Callable
from functools import wraps
from typing import Literal

from mcp.server.mcpserver import Image as MCPImage
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from .agent_io import rule_fields
from .artifact_capture import capture_artifact_image
from .artifact_models import ArtifactFeedbackInput, ArtifactFileInput, ArtifactView
from .artifacts import ArtifactStore
from .discovery import DiscoveryLimit, DiscoveryOffset, DiscoveryQuery, matches, page
from .project_tree import ProjectTree
from .storage import Store


def artifact_receipt(view: ArtifactView) -> dict[str, object]:
    """Return revision and destinations without echoing source or asset inventories."""
    return {
        "id": view.id,
        "project_id": view.project_id,
        "title": view.title,
        "kind": view.kind,
        "revision": view.revision,
        "content_hash": view.content_hash,
        "source_path": view.source_path,
        "entrypoint": view.entrypoint,
        "workspace_url": f"/?artifact={view.id}",
    }


def tool_errors[**P, T](function: Callable[P, T]) -> Callable[P, T]:
    """Expose anticipated validation and conflict messages through the MCP contract."""

    @wraps(function)
    def call(*args: P.args, **kwargs: P.kwargs) -> T:
        """Preserve the handler schema while converting expected storage failures."""
        try:
            return function(*args, **kwargs)
        except (ValueError, FileNotFoundError) as error:
            raise ToolError(str(error)) from error

    return call


def register_artifact_tools(mcp: MCPServer[None], get_store: Callable[[], Store]) -> None:
    """Register generic operations while preserving all existing presentation tools."""
    read = ToolAnnotations(read_only_hint=True, destructive_hint=False)
    write = ToolAnnotations(read_only_hint=False, destructive_hint=False)

    @mcp.tool(annotations=read)
    @tool_errors
    def capture_artifact(
        artifact_id: str,
        width: int = 1280,
        height: int = 900,
        elapsed_ms: int = 0,
        full_page: bool = False,
        revision: int | None = None,
        return_image: bool = True,
    ) -> CallToolResult:
        """Inspect an actual rendered artifact image with exact revision and viewport metadata.
        Authored JavaScript runs in a fresh offline sandbox. elapsed_ms samples animations;
        use the browser for user-driven interaction states. For decks use capture_slide.
        return_image=False returns saved paths/metadata without inline pixels for local automation.
        Inspect the saved image separately before claiming a visual review.
        """
        store = get_store()
        result = capture_artifact_image(
            ArtifactStore(store),
            artifact_id,
            store.database.parent / "artifact-captures",
            width=width,
            height=height,
            elapsed_ms=elapsed_ms,
            full_page=full_page,
            revision=revision,
        )
        response = CallToolResult(
            content=[TextContent(type="text", text=result.model_dump_json())],
            structured_content=result.model_dump(),
        )
        if return_image:
            response.content.append(MCPImage(path=result.path).to_image_content())
        return response

    @mcp.tool(annotations=read)
    @tool_errors
    def list_projects(
        query: DiscoveryQuery = "", offset: DiscoveryOffset = 0, limit: DiscoveryLimit = 0
    ) -> list[dict[str, object]]:
        """Find projects by title or ID. Page matches with offset/limit (0 limit returns all)."""
        return [
            item.model_dump(exclude={"context"})
            for item in ArtifactStore(get_store()).list_projects(
                query=query, offset=offset, limit=limit
            )
        ]

    @mcp.tool(annotations=read)
    @tool_errors
    def list_artifacts(
        project_id: str = "",
        kind: str = "",
        query: DiscoveryQuery = "",
        offset: DiscoveryOffset = 0,
        limit: DiscoveryLimit = 0,
    ) -> list[dict[str, object]]:
        """Find work by title or ID; filter by kind/project. Page matches with offset/limit."""
        if kind == "presentation" and not project_id:
            decks = (
                item
                for item in get_store().list_decks()
                if matches(query, item["id"], item["title"])
            )
            return [
                {**item, "kind": "presentation", "legacy_deck_id": item["id"]}
                for item in page(decks, offset, limit)
            ]
        return [
            {**artifact_receipt(item), "status": item.status, "legacy_deck_id": item.legacy_deck_id}
            for item in ArtifactStore(get_store()).list_artifacts(
                project_id, query=query, kind=kind, offset=offset, limit=limit
            )
        ]

    @mcp.tool(annotations=write)
    @tool_errors
    def create_project(title: str, context: str = "") -> dict[str, object]:
        """Create a shared brief for related artifacts without imposing a brand or template."""
        store = get_store()
        project = ArtifactStore(store).create_project(title, context)
        return {**project.model_dump(), "tree_hash": ProjectTree(store).view().content_hash}

    @mcp.tool(annotations=read)
    @tool_errors
    def get_project_tree() -> dict[str, object]:
        """Read folder IDs, project references and the current tree_hash without project briefs."""
        return ProjectTree(get_store()).agent_view()

    @mcp.tool(annotations=write)
    @tool_errors
    def create_project_folder(
        title: str, expected_tree_hash: str, parent_id: str = ""
    ) -> dict[str, object]:
        """Create a navigation folder using current tree_hash; an empty parent_id selects root."""
        return ProjectTree(get_store()).create_folder(title, expected_tree_hash, parent_id)

    @mcp.tool(annotations=write)
    @tool_errors
    def move_project_to_folder(
        project_id: str, expected_tree_hash: str, folder_id: str = ""
    ) -> dict[str, object]:
        """Place a project in a folder using current tree_hash; an empty folder_id selects root."""
        return ProjectTree(get_store()).move_project(project_id, expected_tree_hash, folder_id)

    @mcp.tool(annotations=read)
    @tool_errors
    def get_artifact(artifact_id: str, include_feedback: bool = False) -> dict[str, object]:
        """Read a file manifest and current revision; discussion excludes withheld Batch drafts."""
        service = ArtifactStore(get_store())
        result = service.get_artifact(artifact_id).model_dump()
        if include_feedback:
            result["feedback"] = [item.model_dump() for item in service.feedback(artifact_id)]
        return result

    @mcp.tool(annotations=read)
    @tool_errors
    def read_artifact_files(
        artifact_id: str,
        paths: list[str],
        revision: int | None = None,
        known_hashes: dict[str, str] | None = None,
        offset: int = 0,
        max_bytes: int = 0,
        encoding: Literal["utf-8", "base64"] = "utf-8",
    ) -> list[dict[str, object]]:
        """Read owned files. known_hashes maps paths to cached SHA-256; a match omits content.
        Page one file with max_bytes, revision and next_offset; use base64 for binary pages.
        """
        return [
            item.model_dump()
            for item in ArtifactStore(get_store()).read_artifact_files(
                artifact_id,
                paths,
                revision,
                known_hashes=known_hashes,
                offset=offset,
                max_bytes=max_bytes,
                encoding=encoding,
            )
        ]

    @mcp.tool(annotations=write)
    @tool_errors
    def register_artifact(
        project_id: str,
        title: str,
        source_path: str,
        entrypoint: str,
        kind: str = "auto",
        context: str = "",
    ) -> dict[str, object]:
        """Copy a local authored file/tree into the project; keep original files intact.
        Compose arbitrary HTML/CSS/SVG/JavaScript or other formats with any authoring tools.
        kind selects a viewer, never allowed components. No design pack is needed.
        Registration never runs build/install commands. Bundle dependencies locally.
        """
        return artifact_receipt(
            ArtifactStore(get_store()).register_artifact(
                project_id,
                title,
                source_path,
                entrypoint,
                kind,
                context,
            )
        )

    @mcp.tool(annotations=write)
    @tool_errors
    def revise_artifact(
        artifact_id: str,
        files: list[ArtifactFileInput],
        expected_revision: int | str,
        request_id: str,
    ) -> dict[str, object]:
        """Publish a changed-file set atomically; keep unrelated files and prior snapshots.
        Pass the current revision or hash and a unique request_id; exact retries are safe.
        File replacements accept text or base64. Deletion requires delete=true.
        """
        return artifact_receipt(
            ArtifactStore(get_store()).revise_artifact(
                artifact_id,
                files,
                expected_revision,
                request_id,
            )
        )

    @mcp.tool(annotations=read)
    @tool_errors
    def read_artifact_feedback(
        artifact_id: str,
        after_delivery_id: int = 0,
        known_rules_hash: str = "",
    ) -> dict[str, object]:
        """Read released requests with original revision and targets, including group selections.
        Retain next_cursor as after_delivery_id. Withheld Batch feedback and drafts are private.
        Reuse known_rules_hash only while retaining that exact guidance.
        """
        store = get_store()
        return {
            **ArtifactStore(store).feedback_feed(artifact_id, after_delivery_id).model_dump(),
            **rule_fields(
                store.authoring_instructions(), store.learned_preferences(), known_rules_hash
            ),
        }

    @mcp.tool(annotations=write)
    @tool_errors
    def reply_to_artifact_feedback(
        artifact_id: str,
        message: str,
        content_hash: str,
        request_id: str,
        reply_to: int | None = None,
    ) -> dict[str, object]:
        """Reply briefly to released artifact feedback without approving or exposing drafts."""
        return (
            ArtifactStore(get_store())
            .add_feedback(
                artifact_id,
                ArtifactFeedbackInput(
                    message=message,
                    content_hash=content_hash,
                    request_id=request_id,
                    reply_to=reply_to,
                ),
                author="agent",
            )
            .model_dump()
        )
