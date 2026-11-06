"""Human-selected workspace paths captured as immutable, read-only chat context."""

import base64
import hashlib
import json
import re
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter
from pydantic import Field

from .artifact_models import ArtifactView, ProjectView
from .artifacts import ArtifactStore
from .favorites import Favorites
from .interactive import InteractiveMarkup, prepare_document
from .models import FileInput, StrictModel
from .projects import audience_html, root_tag
from .sharing import audience_files
from .storage import ConflictError, Store, digest
from .visuals import ReviewEmbedder

ReferenceKind = Literal["project", "artifact", "presentation", "slide"]


class ReferenceTarget(StrictModel):
    """Stable IDs identify a selection; display paths are never parsed as identity."""

    kind: ReferenceKind
    project_id: str = Field(min_length=1, max_length=160)
    artifact_id: str = Field(default="", max_length=160)
    slide_id: str = Field(default="", max_length=80)


class ReferenceRequest(ReferenceTarget):
    """Reject source or membership changes since the displayed suggestion."""

    token: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReferenceEntry(ReferenceRequest):
    """A small discoverable path without authored source, attachments or review history."""

    title: str
    path: str
    description: str = ""
    starred: bool = False
    preview_url: str = ""
    preview_kind: Literal["image", "slide", "project"] = "image"
    detail: str = ""
    reference_error: str = ""


class PreparedReference(StrictModel):
    """One immutable reference ready for the existing feedback attachment pipeline."""

    entry: ReferenceEntry
    file: FileInput


class ChatReferences:
    """Discover workspace paths on demand; freeze only the explicit human selection."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.artifacts = ArtifactStore(store)
        self.favorites = Favorites(store)
        self.visual_errors: dict[str, str] = {}

    @staticmethod
    def _summary(item: ArtifactView) -> dict[str, object]:
        return {
            "artifact_id": item.id,
            "title": item.title,
            "kind": item.kind,
            "revision": item.revision,
            "content_hash": item.content_hash,
            "context_hash": item.context_hash,
            "entrypoint": item.entrypoint,
        }

    def _project(self, project: ProjectView, items: list[ArtifactView]) -> ReferenceEntry:
        return ReferenceEntry(
            kind="project",
            project_id=project.id,
            title=project.title,
            path="/" + project.title,
            preview_kind="project",
            detail=f"Project brief · {len(items)} artifacts",
            token=digest(
                json.dumps(
                    [
                        project.id,
                        project.title,
                        project.context_hash,
                        [self._summary(item) for item in items],
                    ],
                    sort_keys=True,
                )
            ),
        )

    def _artifact(self, item: ArtifactView) -> ReferenceEntry:
        kind = "presentation" if item.legacy_deck_id else "artifact"
        path = "/" + item.project_title + "/" + item.title
        return ReferenceEntry(
            kind=kind,
            project_id=item.project_id,
            artifact_id=item.id,
            title=item.title,
            path=path,
            token=digest(json.dumps([path, item.project_id, self._summary(item)], sort_keys=True)),
            preview_url=f"/api/hub/previews/{item.id}?v={item.content_hash}",
            detail="Presentation" if item.legacy_deck_id else item.kind.replace("_", " ").title(),
        )

    def _visual_tokens(
        self, item: ArtifactView, slide_id: str = "", *, strict: bool = True
    ) -> dict[str, str]:
        """Hash actual embedded dependencies once per deck, sharing the asset read cache."""
        folder = self.store.folder(item.id)
        embedder = ReviewEmbedder(folder)
        theme = embedder.css(embedder.local_path("theme.css").read_text())
        deck = self.store.read(item.id)
        shared = digest(
            theme
            + root_tag((folder / "index.html").read_text())
            + deck.model_dump_json(exclude={"slides", "sections"})
        )
        self.visual_errors = {}
        result: dict[str, str] = {}
        for slide in deck.slides:
            if slide_id and slide.id != slide_id:
                continue
            embedder.expanded_bytes = 0
            try:
                markup = embedder.markup(slide.html)
                interactive = InteractiveMarkup(embedder)
                interactive.feed(slide.interactive_html)
                interactive.close()
                result[slide.id] = digest(
                    shared
                    + markup
                    + "".join(interactive.parts)
                    + slide.model_dump_json(exclude={"notes", "agent"})
                )
            except (ValueError, FileNotFoundError) as error:
                if strict:
                    raise
                self.visual_errors[slide.id] = str(error)
                result[slide.id] = ""
                embedder.visiting.clear()
        return result

    def _slides(
        self,
        item: ArtifactView,
        parent: ReferenceEntry,
        visuals: dict[str, str],
        descriptions: dict[str, str],
        slide_id: str = "",
    ) -> list[ReferenceEntry]:
        result: list[ReferenceEntry] = []
        for index, slide in enumerate(self.store.read(item.id).slides, 1):
            if slide_id and slide.id != slide_id:
                continue
            description = descriptions.get(item.id + "/" + slide.id, "")
            path = parent.path + f"/{index:02d} " + slide.title
            result.append(
                ReferenceEntry(
                    kind="slide",
                    project_id=item.project_id,
                    artifact_id=item.id,
                    slide_id=slide.id,
                    title=slide.title,
                    path=path,
                    description=description,
                    starred=item.id + "/" + slide.id in descriptions,
                    token=digest(
                        json.dumps(
                            [
                                parent.token,
                                path,
                                slide.id,
                                visuals[slide.id],
                                description,
                            ]
                        )
                    ),
                    preview_url=f"/api/decks/{item.id}/slides/{slide.id}/preview",
                    preview_kind="slide",
                    reference_error=self.visual_errors.get(slide.id, ""),
                    detail="Slide"
                    + (" · Favorite" if item.id + "/" + slide.id in descriptions else ""),
                )
            )
        return result

    def catalog(self) -> list[ReferenceEntry]:
        """One UI discovery request per picker opening, never part of agent bootstrap."""
        items = self.artifacts.list_artifacts()
        stars = set(self.favorites.index()["artifacts"])
        descriptions = self.favorites.descriptions()
        result = [
            self._project(project, [item for item in items if item.project_id == project.id])
            for project in self.artifacts.list_projects()
        ]
        for item in items:
            entry = self._artifact(item)
            entry.starred = item.id in stars
            if item.legacy_deck_id:
                try:
                    visuals = self._visual_tokens(item, strict=False)
                    result.extend(self._slides(item, entry, visuals, descriptions))
                    entry.token = digest(entry.token + json.dumps(visuals, sort_keys=True))
                    if self.visual_errors:
                        entry.reference_error = (
                            "A slide has unavailable dependencies. Choose an available slide."
                        )
                except (ValueError, FileNotFoundError) as error:
                    entry.reference_error = str(error)
            result.append(entry)
        return result

    def _current(self, target: ReferenceTarget) -> ReferenceEntry:
        if target.kind == "project":
            if target.artifact_id or target.slide_id:
                raise ValueError("Choose a project without an artifact or slide ID.")
            project = next(
                (p for p in self.artifacts.list_projects() if p.id == target.project_id), None
            )
            if project is None:
                raise ValueError("This project is no longer available.")
            return self._project(project, self.artifacts.list_artifacts(project.id))
        item = self.artifacts.get_artifact(target.artifact_id)
        if item.project_id != target.project_id:
            raise ConflictError("This reference moved. Type @ to select it again.")
        entry = self._artifact(item)
        if target.kind == "slide" and item.legacy_deck_id:
            visuals = self._visual_tokens(item, target.slide_id)
            slide = next(
                (
                    s
                    for s in self._slides(
                        item, entry, visuals, self.favorites.descriptions(), target.slide_id
                    )
                ),
                None,
            )
            if slide is not None:
                return slide
        elif target.kind == entry.kind and not target.slide_id:
            if item.legacy_deck_id:
                entry.token = digest(
                    entry.token + json.dumps(self._visual_tokens(item), sort_keys=True)
                )
            return entry
        raise ValueError("This reference is no longer available.")

    def prepare(self, target: ReferenceRequest) -> PreparedReference:
        """Freeze selected source and recheck its path/version before attaching anything."""
        entry = self._current(target)
        if entry.token != target.token:
            raise ConflictError("This reference changed. Type @ to select it again.")
        metadata: dict[str, object] = {
            "format": "artifact-studio-reference/v1",
            "role": "read-only context reference",
            **entry.model_dump(
                exclude={"preview_url", "preview_kind", "starred", "detail", "reference_error"}
            ),
        }
        if entry.kind == "slide":
            token = self.favorites.selection_token(entry.artifact_id, entry.slide_id)
            file = self.favorites.reference(
                entry.artifact_id, entry.slide_id, token, require_favorite=False
            )
        else:
            if entry.kind == "project":
                project = next(
                    p for p in self.artifacts.list_projects() if p.id == entry.project_id
                )
                metadata.update(
                    {
                        "scope": "Project brief and artifact index only; no artifact bodies.",
                        "context": project.context,
                        "artifacts": [
                            self._summary(item)
                            for item in self.artifacts.list_artifacts(project.id)
                        ],
                    }
                )
                document, suffix = json.dumps(metadata, ensure_ascii=False, indent=2), "json"
            elif entry.kind == "presentation":
                document = audience_html(self.store, entry.artifact_id)
                for slide in self.store.read(entry.artifact_id).slides:
                    if slide.interactive_html:
                        embedded = prepare_document(
                            self.store, entry.artifact_id, slide.interactive_html
                        )
                        original = json.dumps(slide.interactive_html, ensure_ascii=False).replace(
                            "<", r"\u003c"
                        )
                        replacement = json.dumps(embedded, ensure_ascii=False).replace(
                            "<", r"\u003c"
                        )
                        document = document.replace(original, replacement, 1)
                provenance = json.dumps(metadata).replace("<", r"\u003c")
                document = document.replace(
                    "<head>",
                    '<head><script type="application/json" '
                    'id="artifact-studio-workspace-reference">' + provenance + "</script>",
                    1,
                )
                suffix = "html"
            else:
                item = self.artifacts.get_artifact(entry.artifact_id)
                files, excluded = audience_files(
                    self.artifacts.snapshot_files(item.id, item.revision)
                )
                if item.entrypoint not in files:
                    raise ValueError("This artifact's entrypoint is not shareable.")
                contents: list[dict[str, object]] = []
                for path, data in files.items():
                    part: dict[str, object] = {
                        "path": path,
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                    try:
                        part["text"] = data.decode("utf-8")
                    except UnicodeDecodeError:
                        part["data_base64"] = base64.b64encode(data).decode()
                    contents.append(part)
                contents.sort(
                    key=lambda part: (
                        "data_base64" in part,
                        part["path"] != item.entrypoint,
                        str(part["path"]),
                    )
                )
                metadata.update(
                    {
                        "revision": item.revision,
                        "entrypoint": item.entrypoint,
                        "context": item.context,
                        "external_dependencies": item.external_dependencies,
                        "excluded_paths": excluded,
                        "files": contents,
                    }
                )
                document, suffix = json.dumps(metadata, ensure_ascii=False, indent=2), "json"
            encoded = document.encode()
            if len(encoded) > 25_000_000:
                raise ValueError(
                    "This reference exceeds 25 MB. Select a smaller artifact or individual slide."
                )
            file = FileInput(
                name=f"Reference - {entry.artifact_id or entry.project_id}.{suffix}",
                data_base64=base64.b64encode(encoded).decode(),
            )
        if self._current(target).token != entry.token:
            raise ConflictError("This reference changed while attaching. Select it again.")
        title = re.sub(r"[/\\:\x00-\x1f]", "-", entry.title).strip()[:160] or entry.kind
        suffix = "html" if file.name.endswith(".html") else "json"
        file.name = f"{title} - {entry.kind} reference.{suffix}"
        return PreparedReference(entry=entry, file=file)


def chat_reference_routes(get_store: Callable[[], Store]) -> APIRouter:
    router = APIRouter(tags=["Chat references"])

    @router.get("/api/chat-references")
    def catalog() -> list[ReferenceEntry]:
        return ChatReferences(get_store()).catalog()

    @router.post("/api/chat-references")
    def prepare(body: ReferenceRequest) -> PreparedReference:
        return ChatReferences(get_store()).prepare(body)

    return router
