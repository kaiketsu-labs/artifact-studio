"""Narrow cloud-agent tools reusing local storage, revision and feedback contracts."""

import asyncio
import base64
import fcntl
import json
import shutil
from collections.abc import Callable, Coroutine
from contextlib import suppress
from functools import wraps
from pathlib import Path
from typing import Annotated, cast

from mcp.server.mcpserver import Image as MCPImage
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field, TypeAdapter

from .agent_io import deck_receipt, rule_fields, slide_receipt
from .artifact_capture import capture_artifact_image
from .artifact_models import ArtifactFeedbackInput, ArtifactFileInput, ArtifactView, ProjectView
from .artifacts import LEGACY_PROJECT, ArtifactStore, relative_path, write_tree
from .copilot_capture import capture_cloud_slide
from .models import Slide, SlidePatch
from .slide_designs import DesignInput, SlideDesigns
from .storage import SAFE_ID, ConflictError, Store, digest
from .visuals import CaptureOptions, capture_snapshot

PROJECT = Path(__file__).resolve().parents[1]
BOOTSTRAP = (PROJECT / "agent/copilot.md").read_text()
MAX_READ_BYTES = 1024 * 1024
MAX_RESULT_BYTES = 4 * 1024 * 1024
Text = Annotated[str, Field(max_length=1_500_000)]
Identifier = Annotated[str, Field(min_length=1, max_length=100)]
RequestID = Annotated[str, Field(min_length=8, max_length=100)]
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False)
FILE_INPUTS = TypeAdapter(list[ArtifactFileInput])


def public_value(value: object) -> object:
    """Remove local filesystem destinations from otherwise scoped storage responses."""
    if isinstance(value, dict):
        mapping = cast(dict[str, object], value)
        excluded = {"source_path", "original_source_path", "storage_project_id", "download_url"}
        return {
            key: public_value(item)
            for key, item in mapping.items()
            if key not in excluded and not (key == "path" and str(item).startswith("/"))
        }
    if isinstance(value, list):
        return [public_value(item) for item in cast(list[object], value)]
    return value


def public_object(value: dict[str, object]) -> dict[str, object]:
    """Keep a dictionary-shaped response while stripping local-only destinations."""
    return cast(dict[str, object], public_value(value))


def remote_tool[**P, T](function: Callable[P, T]) -> Callable[P, Coroutine[object, object, T]]:
    """Run synchronous storage off the event loop and suppress filesystem error details."""

    @wraps(function)
    async def call(*args: P.args, **kwargs: P.kwargs) -> T:
        """Translate expected failures without exposing credentials or local paths."""
        try:
            task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
            try:
                result = await asyncio.shield(task)
            except asyncio.CancelledError:
                with suppress(Exception):
                    await task
                raise
            if (
                not isinstance(result, CallToolResult)
                and len(json.dumps(result, ensure_ascii=False).encode()) > MAX_RESULT_BYTES
            ):
                raise ToolError("Result exceeds 4 MiB. Request fewer files or a smaller page.")
            return result
        except ConflictError as error:
            raise ToolError(
                f"Conflict: {error}. Read the current revision before retrying."
            ) from None
        except FileNotFoundError:
            raise ToolError("The requested scoped resource is unavailable.") from None
        except ValueError as error:
            message = str(error)
            if "/" in message or "\\" in message:
                message = "Invalid request. Check relative paths, IDs, fields and revision tokens."
            raise ToolError(message) from None
        except OSError:
            raise ToolError(
                "Local storage is unavailable; ask the workspace owner to inspect it."
            ) from None

    return call


class ScopedTools:
    """Authorize current project membership before every source read or mutation."""

    def __init__(self, store: Store, project_id: str, whole_workspace: bool) -> None:
        """Keep gateway scope independent from the local application's global store."""
        self.store = store
        self.artifacts = ArtifactStore(store)
        self.project_id = project_id
        self.whole_workspace = whole_workspace
        if not whole_workspace:
            self.project(project_id)

    def project(self, project_id: str) -> ProjectView:
        """Resolve only the explicitly permitted project and its shared brief."""
        if not SAFE_ID.fullmatch(project_id) or (
            not self.whole_workspace and project_id != self.project_id
        ):
            raise ValueError("Project is outside this gateway's scope.")
        project = next(
            (item for item in self.artifacts.list_projects() if item.id == project_id), None
        )
        if project is None:
            raise ValueError("Project is unavailable in this gateway's scope.")
        return project

    def artifact(self, artifact_id: str, *, owned: bool = False) -> ArtifactView:
        """Check registry ownership before loading source or potentially unrelated metadata."""
        if not SAFE_ID.fullmatch(artifact_id):
            raise ValueError("Artifact is unavailable in this gateway's scope.")
        with self.store.connect() as db:
            row = db.execute(
                "SELECT project_id FROM artifact_items WHERE id=? "
                "UNION ALL SELECT project_id FROM artifact_legacy_projects WHERE artifact_id=?",
                (artifact_id, artifact_id),
            ).fetchone()
            owned_row = db.execute(
                "SELECT id FROM artifact_items WHERE id=?", (artifact_id,)
            ).fetchone()
        if owned_row is None:
            designs = {item.folder_id for item in self.store.designs(include_archived=True)}
            available = {item["id"] for item in self.store.list_decks()} - designs
            if artifact_id not in available:
                raise ValueError("Artifact is unavailable in this gateway's scope.")
            folder = self.store.root / artifact_id
            if folder.is_symlink():
                raise ValueError(
                    "Legacy presentations with linked folders are unavailable remotely."
                )
            for index, path in enumerate(folder.rglob("*")):
                if index >= 5000 or path.is_symlink():
                    raise ValueError("Remote legacy sources require at most 5000 unlinked files.")
        project_id = cast(str, row[0]) if row else LEGACY_PROJECT
        _ = self.project(project_id)
        item = self.artifacts.get_artifact(artifact_id)
        if item.project_id != project_id:
            raise ValueError("Artifact membership changed; discover the project again.")
        if owned and item.legacy_deck_id:
            raise ValueError("Legacy presentation bundles are private; use the slide tools.")
        return item

    def deck(self, deck_id: str) -> ArtifactView:
        """Authorize a legacy deck through its current artifact project membership."""
        item = self.artifact(deck_id)
        if item.legacy_deck_id != deck_id:
            raise ValueError("Use the artifact tools for this item.")
        return item

    def metadata(self, item: ArtifactView, *, full: bool = False) -> dict[str, object]:
        """Return an owned manifest or a legacy slide adapter without its private file inventory."""
        if not self.whole_workspace and item.project_id != self.project_id:
            current = self.artifact(item.id)
            item = item.model_copy(
                update={"project_id": current.project_id, "project_title": current.project_title}
            )
        result = item.model_dump(exclude={"files", "context"} if not full else set())
        if item.legacy_deck_id:
            result["files"] = []
            result["file_access"] = (
                "Use get_slide; legacy file bundles and attachments are private."
            )
        return public_object(result)

    def catalog(self, project_id: str) -> list[ArtifactView]:
        """Filter identities before reading their manifests, including reassigned legacy decks."""
        _ = self.project(project_id)
        with self.store.connect() as db:
            ids = [
                cast(str, row[0])
                for row in db.execute(
                    "SELECT id FROM artifact_items WHERE project_id=?", (project_id,)
                )
            ]
            bindings = {
                cast(str, row[0]): cast(str, row[1])
                for row in db.execute("SELECT artifact_id,project_id FROM artifact_legacy_projects")
            }
        designs = {item.folder_id for item in self.store.designs(include_archived=True)}
        ids.extend(
            item["id"]
            for item in self.store.list_decks()
            if item["id"] not in designs and bindings.get(item["id"], LEGACY_PROJECT) == project_id
        )
        return [self.artifact(artifact_id) for artifact_id in ids]

    def list_projects(self) -> list[dict[str, object]]:
        """Discover only projects explicitly shared by the gateway owner."""
        projects = (
            self.artifacts.list_projects()
            if self.whole_workspace
            else [self.project(self.project_id)]
        )
        return [public_object(item.model_dump(exclude={"context"})) for item in projects]

    def list_artifacts(self, project_id: str = "") -> list[dict[str, object]]:
        """List scoped artifact IDs and current revisions without loading their source bodies."""
        project_ids = (
            [project_id or self.project_id]
            if project_id or self.project_id
            else [item.id for item in self.artifacts.list_projects()]
        )
        return [self.metadata(item) for selected in project_ids for item in self.catalog(selected)]

    def get_working_context(
        self, artifact_id: str = "", project_id: str = "", known_rules_hash: str = ""
    ) -> dict[str, object]:
        """Read first: applicable workspace rules, scoped brief, cloud workflow and related IDs."""
        item = self.artifact(artifact_id) if artifact_id else None
        selected_id = project_id or (item.project_id if item else self.project_id)
        if item and selected_id != item.project_id:
            raise ValueError("Artifact and project do not match.")
        project = self.project(selected_id) if selected_id else None
        return {
            **rule_fields(
                self.store.authoring_instructions(),
                self.store.learned_preferences(),
                known_rules_hash,
            ),
            "authoring_guide_tool": "get_authoring_guide",
            "scope": "whole-workspace" if self.whole_workspace else self.project_id,
            "project": public_object(project.model_dump()) if project else None,
            "artifact": self.metadata(item, full=True) if item else None,
            "related_artifacts": self.list_artifacts(selected_id) if selected_id else [],
            "capabilities": {
                "inline_artifact_creation": True,
                "arbitrary_html_css_svg_javascript": True,
                "legacy_slide_editing": True,
                "owned_artifact_capture": True,
                "legacy_slide_capture": True,
                "automatic_local_hooks": False,
                "host_filesystem_access": False,
                "feedback_attachments": False,
            },
        }

    def get_authoring_guide(self) -> dict[str, object]:
        """Read cloud workflow and exact JSON shapes for inline files and slide authoring."""
        return {
            "guide": BOOTSTRAP,
            "files_json_schema": FILE_INPUTS.json_schema(),
            "slide_json_schema": Slide.model_json_schema(),
            "patch_json_schema": SlidePatch.model_json_schema(),
            "file_rules": (
                "Send relative owned file paths. files_json is a JSON array; each item has "
                "path and exactly one of text, data_base64, delete. Up to 30 changes; "
                "total request below 2 MiB."
            ),
            "visual_review": (
                "capture_artifact returns actual image pixels for owned bundles. "
                "capture_slide renders a sanitized audience-only clone with theme.css/assets. "
                "Physical display and presenter controls require a local visual review."
            ),
        }

    def get_artifact(self, artifact_id: Identifier) -> dict[str, object]:
        """Read current source manifest, context and revision tokens without discussion drafts."""
        return self.metadata(self.artifact(artifact_id), full=True)

    def read_artifact_files(
        self, artifact_id: Identifier, paths: list[str], revision: int = 0
    ) -> list[dict[str, object]]:
        """Read up to 20 owned files and 1 MiB; revision=0 selects the current revision."""
        current = self.artifact(artifact_id, owned=True)
        selected = (
            self.artifacts.get_artifact_revision(artifact_id, revision) if revision else current
        )
        inventory = {item.path: item for item in selected.files}
        if not paths or len(paths) > 20 or any(path not in inventory for path in paths):
            raise ValueError("Request between one and 20 declared owned file paths.")
        if sum(inventory[path].size for path in paths) > MAX_READ_BYTES:
            raise ValueError("Read at most 1 MiB of source per call.")
        return [
            item.model_dump()
            for item in self.artifacts.read_artifact_files(artifact_id, paths, selected.revision)
        ]

    def list_artifact_revisions(self, artifact_id: Identifier) -> list[dict[str, object]]:
        """Read immutable revision numbers and hashes without historical project metadata."""
        _ = self.artifact(artifact_id, owned=True)
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT revision,json_extract(document,'$.content_hash') AS hash "
                "FROM artifact_revisions WHERE artifact_id=? ORDER BY revision DESC LIMIT 100",
                (artifact_id,),
            ).fetchall()
        return [{"revision": cast(int, row[0]), "content_hash": cast(str, row[1])} for row in rows]

    @staticmethod
    def changes(files_json: str) -> list[ArtifactFileInput]:
        """Parse a bounded changed-file list using the existing typed file contract."""
        changes = FILE_INPUTS.validate_json(files_json)
        if not 1 <= len(changes) <= 30:
            raise ValueError("Provide between one and 30 file changes.")
        if len({item.path for item in changes}) != len(changes):
            raise ValueError("Each file path must appear only once.")
        for item in changes:
            _ = relative_path(item.path)
        return changes

    def revise_artifact(
        self,
        artifact_id: Identifier,
        files_json: Text,
        expected_revision: int,
        request_id: RequestID,
    ) -> dict[str, object]:
        """Publish a changed-file JSON array atomically; exact retries retain their receipt."""
        _ = self.artifact(artifact_id, owned=True)
        item = self.artifacts.revise_artifact(
            artifact_id, self.changes(files_json), expected_revision, request_id
        )
        return self.metadata(item)

    def create_artifact(
        self,
        project_id: Identifier,
        title: str,
        entrypoint: str,
        files_json: Text,
        request_id: RequestID,
        context: str = "",
    ) -> dict[str, object]:
        """Create arbitrary inline files without host paths or builds; exact retries are safe."""
        _ = self.project(project_id)
        changes = self.changes(files_json)
        if any(item.delete for item in changes):
            raise ValueError("New artifacts cannot include deleted files.")
        files = {
            item.path: item.text.encode()
            if item.text is not None
            else base64.b64decode(item.data_base64 or "", validate=True)
            for item in changes
        }
        payload = digest(json.dumps([project_id, title, entrypoint, files_json, context]))
        operation = digest(json.dumps([project_id, request_id]))
        staging = self.store.database.absolute().parent / "copilot-staging" / operation
        staging.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (staging.parent / "creation.lock").open("a+b") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError(
                    "Another artifact creation is in progress; retry shortly."
                ) from None
            with self.store.connect() as db:
                _ = db.execute(
                    "CREATE TABLE IF NOT EXISTS copilot_creations "
                    "(operation TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
                prior = db.execute(
                    "SELECT payload FROM copilot_creations WHERE operation=?", (operation,)
                ).fetchone()
                if prior and prior[0] != payload:
                    raise ConflictError(
                        "The creation request_id already belongs to another payload"
                    )
                _ = db.execute(
                    "INSERT OR IGNORE INTO copilot_creations VALUES (?,?)", (operation, payload)
                )
                recovered = db.execute(
                    "SELECT id FROM artifact_items "
                    "WHERE json_extract(document,'$.original_source_path')=?",
                    (str(staging),),
                ).fetchone()
            if recovered:
                item = self.artifact(cast(str, recovered[0]), owned=True)
                return self.metadata(self.artifacts.get_artifact_revision(item.id, 1))
            if staging.exists():
                shutil.rmtree(staging)
            try:
                write_tree(staging, files)
                item = self.artifacts.register_artifact(
                    project_id, title, staging, entrypoint, context=context
                )
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
            return self.metadata(item)

    def read_artifact_feedback(
        self, artifact_id: Identifier, after_delivery_id: int = 0, known_rules_hash: str = ""
    ) -> dict[str, object]:
        """Read released feedback and retain next_cursor for polling; Batch drafts stay private."""
        item = self.artifact(artifact_id, owned=True)
        feed = self.artifacts.feedback_feed(artifact_id, after_delivery_id)
        for event in feed.events:
            event.project_id = item.project_id
        return public_object(
            {
                **feed.model_dump(),
                **rule_fields(
                    self.store.authoring_instructions(),
                    self.store.learned_preferences(),
                    known_rules_hash,
                ),
            }
        )

    def reply_to_artifact_feedback(
        self,
        artifact_id: Identifier,
        event_id: int,
        message: str,
        content_hash: str,
        request_id: RequestID,
    ) -> dict[str, object]:
        """Reply to a released artifact request, preserving its revision and retry identity."""
        artifact = self.artifact(artifact_id, owned=True)
        released = {item.event_id for item in self.artifacts.feedback(artifact_id)}
        if event_id not in released:
            raise ValueError("Reply must reference released feedback in this artifact.")
        result = self.artifacts.add_feedback(
            artifact_id,
            ArtifactFeedbackInput(
                message=message, content_hash=content_hash, request_id=request_id, reply_to=event_id
            ),
            author="agent",
        )
        result.project_id = artifact.project_id
        return public_object(result.model_dump())

    def get_presentation_outline(self, deck_id: Identifier) -> dict[str, object]:
        """Read a scoped deck's brief, slide order, sections and concurrency hashes."""
        _ = self.deck(deck_id)
        view = self.store.view(deck_id, include_pending=False)
        return {
            "id": view.id,
            "title": view.title,
            "slides": [slide_receipt(deck_id, item).model_dump() for item in view.slides],
            "sections": [item.model_dump() for item in view.sections],
            "sections_hash": view.sections_hash,
            "context_markdown": view.context_markdown,
            "context_hash": view.context_hash,
        }

    def get_slide(self, deck_id: Identifier, slide_id: Identifier) -> dict[str, object]:
        """Read complete slide source and expected_hash; released discussion uses read_feedback."""
        _ = self.deck(deck_id)
        slide = self.store.locate(self.store.read(deck_id), slide_id)
        return public_object(
            self.store.view_slide(deck_id, slide, include_pending=False, feedback=[]).model_dump()
        )

    def revise_slide(
        self,
        deck_id: Identifier,
        slide_id: Identifier,
        expected_hash: str,
        request_id: RequestID,
        patch_json: Text,
    ) -> dict[str, object]:
        """Apply a SlidePatch JSON object with optimistic concurrency; no local asset embedding."""
        _ = self.deck(deck_id)
        view = self.store.revise_view(
            deck_id, slide_id, expected_hash, request_id, SlidePatch.model_validate_json(patch_json)
        )
        return slide_receipt(deck_id, view).model_dump()

    def publish_slide_designs(
        self,
        deck_id: Identifier,
        slide_id: Identifier,
        expected_content_hash: str,
        request_id: RequestID,
        options_json: Text,
    ) -> dict[str, object]:
        """Publish a JSON array of 1–5 designs; only the human can choose or keep one."""
        _ = self.deck(deck_id)
        options = TypeAdapter(list[DesignInput]).validate_json(options_json)
        result = SlideDesigns(self.store).publish(
            deck_id, slide_id, expected_content_hash, request_id, options
        )
        return public_object(result.model_dump(mode="json"))

    def get_slide_designs(
        self,
        deck_id: Identifier,
        slide_id: Identifier,
        cursor: int = -1,
        decision_cursor: int = -1,
        set_id: str = "",
    ) -> dict[str, object]:
        """Read saved proposals and decisions. -1 starts; 0 skips a finished stream."""
        _ = self.deck(deck_id)
        result = SlideDesigns(self.store).list(
            deck_id,
            slide_id,
            cursor=None if cursor == -1 else cursor,
            decision_cursor=None if decision_cursor == -1 else decision_cursor,
            set_id=set_id or None,
        )
        return public_object(result.model_dump(mode="json"))

    def get_slide_design(
        self,
        deck_id: Identifier,
        slide_id: Identifier,
        design_id: Identifier,
    ) -> dict[str, object]:
        """Read one scoped proposal's immutable audience source and feedback hash."""
        _ = self.deck(deck_id)
        return public_object(
            SlideDesigns(self.store).read(deck_id, slide_id, design_id).model_dump(mode="json")
        )

    def capture_slide_design(
        self,
        deck_id: Identifier,
        slide_id: Identifier,
        design_id: Identifier,
    ) -> CallToolResult:
        """Return frozen design pixels through the offline renderer without local host paths."""
        _ = self.deck(deck_id)
        frozen = SlideDesigns(self.store).preview(deck_id, slide_id, design_id)
        data = capture_snapshot(frozen, CaptureOptions(reduced_motion=True))
        _ = self.deck(deck_id)
        if len(data) > 3 * 1024 * 1024:
            raise ValueError("The captured design exceeds the gateway's 3 MiB image limit.")
        folder = self.store.database.parent / "copilot-captures"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"design-{design_id}.png"
        _ = path.write_bytes(data)
        metadata = {"design_id": design_id, "frame": frozen.frame.model_dump(mode="json")}
        return CallToolResult(
            content=[
                TextContent(type="text", text=json.dumps(metadata)),
                MCPImage(path=path).to_image_content(),
            ],
            structured_content=metadata,
        )

    def add_slide(
        self,
        deck_id: Identifier,
        slide_json: Text,
        request_id: RequestID,
        after_slide_id: str,
        expected_slide_ids: list[str],
        expected_sections_hash: str,
        section_id: str = "",
    ) -> dict[str, object]:
        """Insert Slide JSON using fresh outline hashes; an empty anchor selects the start."""
        _ = self.deck(deck_id)
        view = self.store.add_slide_once(
            deck_id,
            Slide.model_validate_json(slide_json),
            request_id,
            after_slide_id=after_slide_id,
            expected_slide_ids=expected_slide_ids,
            expected_sections_hash=expected_sections_hash,
            section_id=section_id,
        )
        return slide_receipt(deck_id, view).model_dump()

    def reorder_slides(
        self, deck_id: Identifier, slide_ids: list[str], expected_slide_ids: list[str]
    ) -> dict[str, object]:
        """Reorder slides using their current complete order as the concurrency baseline."""
        _ = self.deck(deck_id)
        return deck_receipt(
            self.store.reorder(deck_id, slide_ids, expected_slide_ids=expected_slide_ids)
        ).model_dump()

    def read_feedback(self, deck_id: Identifier, after_delivery_id: int = 0) -> dict[str, object]:
        """Read released deck requests without the shared design inbox or withheld attachments."""
        _ = self.deck(deck_id)
        events = [
            item
            for item in self.store.comments(deck_id)
            if item.released
            and item.delivery_id is not None
            and item.delivery_id > after_delivery_id
        ]
        return public_object(
            {
                "events": [item.model_dump() for item in events],
                "next_cursor": max(
                    (item.delivery_id or 0 for item in events), default=after_delivery_id
                ),
            }
        )

    def reply_to_feedback(
        self,
        deck_id: Identifier,
        message: str,
        request_id: RequestID,
        slide_id: str = "",
        event_id: int = 0,
    ) -> str:
        """Post as the agent; omit event_id for an opening message or reply to released feedback."""
        _ = self.deck(deck_id)
        events = {
            item.event_id: item
            for item in self.store.comments(deck_id)
            if item.released and (item.slide_id or "") == slide_id
        }
        if event_id and event_id not in events:
            raise ValueError("Reply must reference released feedback in this presentation context.")
        slide = self.store.locate(self.store.read(deck_id), slide_id) if slide_id else None
        content_hash = self.store.content_hash(deck_id, slide) if slide else ""
        return self.store.add_feedback(
            deck_id,
            slide_id or None,
            message,
            content_hash,
            request_id,
            reply_to=event_id or None,
            author="agent",
        )

    def capture_artifact(
        self,
        artifact_id: Identifier,
        width: int = 1280,
        height: int = 900,
        elapsed_ms: int = 0,
        revision: int = 0,
    ) -> CallToolResult:
        """Return image pixels of an owned revision in an offline browser, without host paths."""
        _ = self.artifact(artifact_id, owned=True)
        if not 240 <= width <= 1920 or not 240 <= height <= 1200:
            raise ValueError("Remote capture dimensions must be 240–1920 by 240–1200 pixels.")
        if not 0 <= elapsed_ms <= 5000:
            raise ValueError("Remote captures allow elapsed_ms between zero and 5000.")
        result = capture_artifact_image(
            self.artifacts,
            artifact_id,
            self.store.database.parent / "copilot-captures",
            width=width,
            height=height,
            elapsed_ms=elapsed_ms,
            revision=revision or None,
        )
        _ = self.artifact(artifact_id, owned=True)
        if Path(result.path).stat().st_size > 3 * 1024 * 1024:
            raise ValueError("The captured image exceeds 3 MiB; use a smaller viewport.")
        metadata = public_object(result.model_dump())
        return CallToolResult(
            content=[
                TextContent(type="text", text=json.dumps(metadata)),
                MCPImage(path=result.path).to_image_content(),
            ],
            structured_content=metadata,
        )

    def capture_slide(
        self, deck_id: Identifier, slide_id: Identifier, stage: int = -1, elapsed_ms: int = 0
    ) -> CallToolResult:
        """Inspect a full-size audience clone; stage=-1 completes builds, 0–50 selects a build."""
        _ = self.deck(deck_id)
        result = capture_cloud_slide(self.store, deck_id, slide_id, stage, elapsed_ms)
        _ = self.deck(deck_id)
        return result


def register_tools(
    server: MCPServer[None], store: Store, project_id: str, whole_workspace: bool
) -> None:
    """Register an explicit allowlist; local MCP registrations are never imported or modified."""
    scoped = ScopedTools(store, project_id, whole_workspace)
    reads: tuple[Callable[..., object], ...] = (
        scoped.list_projects,
        scoped.list_artifacts,
        scoped.get_working_context,
        scoped.get_authoring_guide,
        scoped.get_artifact,
        scoped.read_artifact_files,
        scoped.list_artifact_revisions,
        scoped.read_artifact_feedback,
        scoped.get_presentation_outline,
        scoped.get_slide,
        scoped.get_slide_designs,
        scoped.get_slide_design,
        scoped.capture_slide_design,
        scoped.read_feedback,
        scoped.capture_artifact,
        scoped.capture_slide,
    )
    writes: tuple[Callable[..., object], ...] = (
        scoped.create_artifact,
        scoped.revise_artifact,
        scoped.reply_to_artifact_feedback,
        scoped.revise_slide,
        scoped.publish_slide_designs,
        scoped.add_slide,
        scoped.reorder_slides,
        scoped.reply_to_feedback,
    )
    for function in reads:
        server.add_tool(remote_tool(cast(Callable[..., object], function)), annotations=READ)
    for function in writes:
        server.add_tool(remote_tool(cast(Callable[..., object], function)), annotations=WRITE)
