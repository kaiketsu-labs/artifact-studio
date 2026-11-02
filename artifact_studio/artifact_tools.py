"""Stateless MCP entrypoints for unrestricted files and scoped artifact discussion."""

from collections.abc import Callable
from functools import wraps

from mcp.server.mcpserver import Image as MCPImage
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from .agent_io import rule_fields
from .artifact_capture import capture_artifact_image
from .artifact_models import ArtifactFeedbackInput, ArtifactFileInput, ArtifactView
from .artifacts import ArtifactStore
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
    ) -> CallToolResult:
        """Inspect an actual rendered artifact image with exact revision and viewport metadata.
        Authored JavaScript runs in a fresh offline sandbox. elapsed_ms samples animations;
        use the browser for user-driven interaction states. For decks use capture_slide.
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
        return CallToolResult(
            content=[
                TextContent(type="text", text=result.model_dump_json()),
                MCPImage(path=result.path).to_image_content(),
            ],
            structured_content=result.model_dump(),
        )

    @mcp.tool(annotations=read)
    @tool_errors
    def list_projects() -> list[dict[str, object]]:
        """Discover project identities; load the selected brief through get_working_context."""
        return [
            item.model_dump(exclude={"context"})
            for item in ArtifactStore(get_store()).list_projects()
        ]

    @mcp.tool(annotations=read)
    @tool_errors
    def list_artifacts(project_id: str = "") -> list[dict[str, object]]:
        """Discover work and review status. Use list_designs for reusable designs."""
        return [
            {**artifact_receipt(item), "status": item.status, "legacy_deck_id": item.legacy_deck_id}
            for item in ArtifactStore(get_store()).list_artifacts(project_id)
        ]

    @mcp.tool(annotations=write)
    @tool_errors
    def create_project(title: str, context: str = "") -> dict[str, object]:
        """Create a shared brief for related artifacts without imposing a brand or template."""
        return ArtifactStore(get_store()).create_project(title, context).model_dump()

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
    ) -> list[dict[str, object]]:
        """Load explicitly requested source files; binary files return base64 only on request."""
        return [
            item.model_dump()
            for item in ArtifactStore(get_store()).read_artifact_files(
                artifact_id,
                paths,
                revision,
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
