"""Typed file-bundle manifests and revision-bound review envelopes."""

from typing import Literal, Self

from pydantic import Field, model_validator

from .models import EditIntent, FeedbackFile, FileInput, StrictModel


class ProjectView(StrictModel):
    """A compact project brief without loading related artifact source."""

    id: str
    title: str
    context: str = ""
    context_hash: str = ""
    artifact_count: int = 0
    legacy: bool = False
    source_path: str = ""


class ArtifactFile(StrictModel):
    """File metadata; an empty SHA-256 marks an unhashed legacy discovery entry."""

    path: str
    size: int
    sha256: str
    mime_type: str


class ArtifactFileInput(StrictModel):
    """An exact text or binary replacement, or an explicit file deletion."""

    path: str = Field(min_length=1, max_length=1000)
    text: str | None = Field(default=None, max_length=50_000_000)
    data_base64: str | None = Field(default=None, max_length=70_000_000)
    delete: bool = False

    @model_validator(mode="after")
    def one_operation(self) -> Self:
        """Prevent ambiguous file writes and preserve empty text files."""
        if sum((self.text is not None, self.data_base64 is not None, self.delete)) != 1:
            raise ValueError("Provide exactly one of text, data_base64, or delete.")
        return self


class ArtifactFileContent(ArtifactFile):
    """Requested source bytes represented without silently changing their encoding."""

    text: str | None = None
    data_base64: str | None = None


class ArtifactManifest(StrictModel):
    """An immutable owned source snapshot and its entrypoint."""

    schema_version: Literal[1] = 1
    id: str
    project_id: str
    title: str
    kind: str
    entrypoint: str
    revision: int = Field(ge=1)
    content_hash: str
    files: list[ArtifactFile]
    context: str = ""
    context_hash: str = ""
    original_source_path: str = ""
    storage_project_id: str = ""
    external_dependencies: list[str] = Field(default_factory=list)
    created_at: str
    viewer_version: str = "artifact-viewer-v1"


class ArtifactView(ArtifactManifest):
    """Compact artifact metadata with review status for the exact source snapshot."""

    project_title: str
    status: Literal["draft", "updated", "approved"] = "draft"
    approved: bool = False
    has_unseen_changes: bool = False
    legacy_deck_id: str | None = None
    source_path: str
    snapshot_mode: Literal["immutable", "legacy-in-place"] = "immutable"
    files_complete: bool = True


class ArtifactRegion(StrictModel):
    """A normalized visual selection with its original rendered dimensions."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(ge=0, le=1)
    height: float = Field(ge=0, le=1)
    viewport_width: float | None = Field(default=None, gt=0)
    viewport_height: float | None = Field(default=None, gt=0)
    image_width: float | None = Field(default=None, gt=0)
    image_height: float | None = Field(default=None, gt=0)


class ArtifactTarget(StrictModel):
    """A best-effort anchor retained against the original reviewed revision."""

    kind: Literal["artifact", "element", "text", "region", "slide", "section", "group", "image"] = (
        "artifact"
    )
    selector: str = Field(default="", max_length=2000)
    label: str = Field(default="", max_length=500)
    quote: str = Field(default="", max_length=10_000)
    section: str = Field(default="", max_length=500)
    slide_id: str | None = None
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    region: ArtifactRegion | None = None
    viewport: dict[str, float] = Field(default_factory=dict, max_length=8)
    x: float | None = Field(default=None, ge=0, le=1)
    y: float | None = Field(default=None, ge=0, le=1)
    width: float | None = Field(default=None, ge=0, le=1)
    height: float | None = Field(default=None, ge=0, le=1)
    elements: list[ArtifactTarget] = Field(default_factory=list, max_length=100)
    state: str = Field(default="", max_length=10_000)

    @model_validator(mode="after")
    def bounded_group(self) -> Self:
        """Keep multiselections flat and bounded for portable review anchors."""
        if self.elements and (self.kind != "group" or any(item.elements for item in self.elements)):
            raise ValueError("Use a flat group of at most 100 individual targets.")
        if self.kind == "group" and not self.elements:
            raise ValueError("A target group must contain at least one target.")
        return self


class ArtifactFeedbackInput(StrictModel):
    """A comment or reply carrying explicit version, target and retry context."""

    message: str = Field(default="", max_length=10_000)
    request_id: str = Field(min_length=8, max_length=100)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    target: ArtifactTarget | None = None
    edit_intent: EditIntent | None = None
    reply_to: int | None = None
    files: list[FileInput] = Field(default_factory=list, max_length=5)


class ArtifactFeedback(StrictModel):
    """Durable discussion with immutable original anchors and private draft delivery."""

    event_id: int
    artifact_id: str
    project_id: str
    revision: int
    content_hash: str
    author: Literal["user", "agent"]
    message: str
    created_at: str
    target: ArtifactTarget | None = None
    edit_intent: EditIntent | None = None
    reply_to: int | None = None
    released: bool = True
    delivery_id: int | None = None
    batch_id: str | None = None
    stale: bool = False
    files: list[FeedbackFile] = Field(default_factory=list)


class ArtifactFeedbackFeed(StrictModel):
    """Released human requests in delivery order, including late-released batches."""

    events: list[ArtifactFeedback]
    next_cursor: int
    mode: Literal["live", "batch"]
