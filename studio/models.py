"""Validated presentation documents and review messages."""

from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    """Reject accidental fields in agent and browser writes."""

    model_config = ConfigDict(extra="forbid")


class FileInput(StrictModel):
    """A local reference file attached to a brief or design request."""

    name: str = Field(min_length=1, max_length=250)
    data_base64: str = Field(max_length=35_000_000)


class DesignFile(StrictModel):
    """A durable attachment available to local authoring agents."""

    name: str
    path: str
    size: int
    sha256: str


class FeedbackFile(DesignFile):
    """A private review reference with a local path and inert download endpoint."""

    download_url: str = ""


class Source(StrictModel):
    """A source attached to a specific slide."""

    label: str
    url: str = ""

    @field_validator("url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        """Permit source documents and web links without executable URL schemes."""
        if urlsplit(value).scheme not in {"", "http", "https"} or value.startswith("//"):
            raise ValueError("Sources must use relative paths or HTTP(S) links.")
        if any(ord(char) < 32 for char in value) or "\\" in value:
            raise ValueError("Source URLs must not contain control characters or backslashes.")
        return value


class AgentContext(StrictModel):
    """Audience-independent context shown beside the slide."""

    summary: str = ""
    questions: list[str] = Field(
        default_factory=list,
        description="Legacy notes; use ask_slide_question for answerable slide questions.",
    )
    alternatives: list[str] = Field(default_factory=list)


class Slide(StrictModel):
    """Canonical editable slide stored inside index.html."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    title: str = Field(min_length=1, max_length=160)
    html: str = Field(min_length=1, max_length=150_000)
    interactive_html: str = Field(
        default="",
        max_length=2_000_000,
        description=(
            "Optional arbitrary HTML/CSS/JavaScript in an offline sandbox; "
            "keep html as a meaningful static fallback."
        ),
    )
    notes: str = Field(default="", max_length=20_000)
    sources: list[Source] = Field(default_factory=list)
    revision: int = Field(default=1, ge=1)
    hidden: bool = Field(default=False, strict=True)
    agent: AgentContext = Field(default_factory=AgentContext)


class DeckSection(StrictModel):
    """A named, contiguous group of slides in the editing overview."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    title: str = Field(min_length=1, max_length=100)
    slide_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value: str) -> str:
        """Keep section names useful without changing user capitalization."""
        if not value.strip():
            raise ValueError("Give the section a name.")
        return value.strip()


class Deck(StrictModel):
    """A presentation whose authoritative source is index.html."""

    schema_version: Literal[1] = 1
    id: str
    title: str
    design_id: str = ""
    slides: list[Slide] = Field(min_length=1, max_length=100)
    sections: list[DeckSection] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def section_order(self) -> Self:
        """Section membership partitions the canonical slide order exactly once."""
        if len({section.id for section in self.sections}) != len(self.sections):
            raise ValueError("Section IDs must be unique.")
        if self.sections and [item for group in self.sections for item in group.slide_ids] != [
            slide.id for slide in self.slides
        ]:
            raise ValueError("Sections must contain every slide once in presentation order.")
        return self


class DeckReceipt(StrictModel):
    """Confirm creation or ordering without echoing every slide's source."""

    id: str
    title: str
    design_id: str
    slide_ids: list[str]


class CreatedDeckReceipt(DeckReceipt):
    """Give a newly created deck explicit review and sanitized audience destinations."""

    workspace_url: str
    audience_url: str


class ElementTarget(StrictModel):
    """An element snapshot anchored to the slide version the human reviewed."""

    selector: str = Field(max_length=2000)
    label: str = Field(max_length=200)
    quote: str = Field(default="", max_length=2000)
    html: str = Field(default="", max_length=15000)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(ge=0, le=1)
    height: float = Field(ge=0, le=1)


class EditIntent(StrictModel):
    """A pending element edit whose target and revision live in its feedback envelope."""

    action: Literal["move", "rotate", "transform", "delete", "replace_text"]
    values: dict[str, float | str] = Field(default_factory=dict, max_length=20)


class QuestionPrompt(StrictModel):
    """An immutable set of suggested answers; a written answer is always available."""

    options: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(
        default_factory=list, max_length=6
    )
    cancelled: bool = Field(default=False, strict=True)
    key: str = Field(default="", pattern=r"^(?:[a-f0-9]{32})?$")

    @field_validator("options")
    @classmethod
    def distinct_options(cls, values: list[str]) -> list[str]:
        """Keep options readable and unambiguous without changing their meaning."""
        values = [value.strip() for value in values]
        if any(not value for value in values) or len({value.casefold() for value in values}) != len(
            values
        ):
            raise ValueError("Use nonempty, distinct answer options.")
        return values


class QuestionAnswerInput(StrictModel):
    """Submit one selected or written answer exactly once."""

    answer: str = Field(min_length=1, max_length=10_000)
    request_id: str = Field(min_length=8, max_length=100)


class SlideQuestionView(StrictModel):
    """A compact question lifecycle with only answers visible to this reader."""

    event_id: int
    slide_id: str
    question: str
    options: list[str]
    content_hash: str
    status: Literal["open", "answered", "cancelled"]
    answer: str = ""
    answer_event_id: int | None = None


class Feedback(StrictModel):
    """A durable human comment or agent reply with immutable revision context."""

    event_id: int
    slide_id: str | None
    author: Literal["user", "agent"]
    message: str
    created_at: str
    content_hash: str
    reply_to: int | None = None
    target: ElementTarget | None = None
    released: bool = True
    delivery_id: int | None = None
    batch_id: str | None = None
    scope: Literal["slide", "presentation"] = "slide"
    context_slide_id: str | None = None
    files: list[FeedbackFile] = Field(default_factory=list)
    edit_intent: EditIntent | None = None
    question: QuestionPrompt | None = None

    @model_validator(mode="after")
    def question_context(self) -> Self:
        """Imported question metadata must retain a valid agent-owned slide thread."""
        if self.question and (
            self.author != "agent" or not self.slide_id or self.reply_to is not None
        ):
            raise ValueError("A question must be an agent message on its own slide thread.")
        return self


class FeedbackWorkflow(StrictModel):
    """Human delivery preference and the observable agent-listening state."""

    mode: Literal["live", "batch"] = "live"
    pending: int = 0
    agent_waiting: bool = False
    last_agent_check: str | None = None
    review_connected: bool = False
    review_harness: str | None = None
    review_session: str | None = None


class FeedbackFeed(StrictModel):
    """Released feedback in delivery order, including batches released after newer comments."""

    events: list[Feedback]
    next_cursor: int
    mode: Literal["live", "batch"]
    authoring_instructions: str = ""
    learned_preferences: str = ""
    design_requests: list[DesignMessage] = Field(default_factory=list)
    rules_hash: str = ""
    rules_included: bool = True
    design_requests_hash: str = ""
    design_requests_included: bool = True


class SlideView(Slide):
    """A slide with current concurrency tokens and human review status."""

    expected_hash: str
    content_hash: str
    approved: bool
    has_unseen_changes: bool = False
    feedback: list[Feedback]


class AgentSlideView(SlideView):
    """Complete editable source with an explicit discussion-history inclusion marker."""

    feedback_included: bool


class SlideReceipt(StrictModel):
    """A small mutation acknowledgement retaining concurrency and human review state."""

    deck_id: str
    id: str
    title: str
    revision: int
    expected_hash: str
    content_hash: str
    approved: bool
    hidden: bool
    has_unseen_changes: bool


class DeckView(StrictModel):
    """A deck snapshot for the review workspace."""

    id: str
    title: str
    design_id: str = ""
    design_hash: str = ""
    slides: list[SlideView]
    sections: list[DeckSection] = Field(default_factory=list)
    sections_hash: str = ""
    context_markdown: str = ""
    context_hash: str = ""
    authoring_instructions: str = ""
    learned_preferences: str = ""
    workflow: FeedbackWorkflow = Field(default_factory=FeedbackWorkflow)
    feedback: list[Feedback] = Field(default_factory=list)
    brief_files: list[DesignFile] = Field(default_factory=list)


class AgentDeckView(DeckView):
    """An agent snapshot with explicit retention metadata for workspace guidance."""

    rules_hash: str
    rules_included: bool


class SlidePatch(StrictModel):
    """Fields an agent may revise without granting approval."""

    title: str | None = Field(default=None, max_length=160)
    html: str | None = Field(default=None, max_length=150_000)
    interactive_html: str | None = Field(
        default=None,
        max_length=2_000_000,
        description=(
            "Revise custom HTML/CSS/JavaScript freely; set an empty string to disable. "
            "Keep the static html fallback aligned."
        ),
    )
    notes: str | None = Field(default=None, max_length=20_000)
    sources: list[Source] | None = None
    agent: AgentContext | None = None
    hidden: bool | None = Field(default=None, strict=True)


class SlideVisibilityInput(StrictModel):
    """A slideshow visibility change independent of ongoing content and note edits."""

    hidden: bool = Field(strict=True)
    expected_hidden: bool = Field(strict=True)


class SlideOrderInput(StrictModel):
    """A complete slide order guarded against concurrent structural changes."""

    slide_ids: list[str] = Field(min_length=1, max_length=100)
    expected_slide_ids: list[str] = Field(min_length=1, max_length=100)


class SlideActionInput(SlideOrderInput):
    """A human slide lifecycle action anchored to the selection the user reviewed."""

    action: Literal["add", "duplicate", "delete"]
    expected_hashes: dict[str, str] = Field(default_factory=dict)
    request_id: str = Field(min_length=8, max_length=100)


class SlideOrderUpdateInput(SlideOrderInput):
    """An optional explicit section layout for moves between named groups."""

    sections: list[DeckSection] | None = Field(default=None, max_length=100)
    expected_sections_hash: str = Field(default="", pattern=r"^(?:[a-f0-9]{64})?$")


class SectionLayoutInput(SlideOrderInput):
    """Replace named groups with guards against concurrent structure changes."""

    sections: list[DeckSection] = Field(max_length=100)
    expected_sections_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class SlideRestoreInput(StrictModel):
    """Restore a durable deletion without overwriting later slide content."""

    deletion_id: str = Field(min_length=8, max_length=100)
    request_id: str = Field(min_length=8, max_length=100)


class SlidesVisibilityInput(StrictModel):
    """A visibility command for a whole selection with per-slide expected states."""

    slide_ids: list[str] = Field(min_length=1, max_length=100)
    hidden: bool = Field(strict=True)
    expected_hidden: dict[str, Annotated[bool, Field(strict=True)]]


class CommentInput(StrictModel):
    """Human feedback bound to the slide version visible when submitted."""

    message: str = Field(default="", max_length=10_000)
    content_hash: str
    request_id: str = Field(min_length=8, max_length=100)
    target: ElementTarget | None = None
    files: list[FileInput] = Field(default_factory=list, max_length=5)
    edit_intent: EditIntent | None = None


class PresentationCommentInput(StrictModel):
    """A general instruction with the viewed slide retained only as context."""

    message: str = Field(default="", max_length=10_000)
    request_id: str = Field(min_length=8, max_length=100)
    context_slide_id: str | None = None
    files: list[FileInput] = Field(default_factory=list, max_length=5)


class ApprovalInput(StrictModel):
    """A human approval of an exact rendered content version."""

    content_hash: str
    approved: bool


class SlideSeenInput(StrictModel):
    """The exact visible version opened by the human reviewer."""

    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class InstructionsInput(StrictModel):
    """A human Markdown edit with a concurrency baseline."""

    markdown: str = Field(max_length=20_000)
    expected_hash: str


class WorkspaceInstructionsInput(InstructionsInput):
    """Save workspace rules and optional secondary guidance with separate baselines."""

    learned_preferences: InstructionsInput | None = None


class NotesInput(StrictModel):
    """A note edit that detects another editor's changes."""

    notes: str = Field(max_length=20_000)
    expected_notes: str


class ModeInput(StrictModel):
    """Select how newly submitted feedback is released to agents."""

    mode: Literal["live", "batch"]


class DesignManifest(StrictModel):
    """A reusable local visual language discovered from a design folder."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=250)


class DesignView(DesignManifest):
    """A validated design with its canonical collection folder."""

    folder_id: str


class CreateInput(StrictModel):
    """A new presentation created from a chosen design language."""

    deck_id: str = Field(max_length=100)
    title: str = Field(min_length=1, max_length=160)
    design_id: str = ""
    brief: str = Field(default="", max_length=10_000)
    files: list[FileInput] = Field(default_factory=list, max_length=5)


class DesignRequestInput(StrictModel):
    """A short message, link or file handoff from the design-library chat."""

    request_id: str = Field(pattern=r"^[a-zA-Z0-9-]{8,100}$")
    message: str = Field(default="", max_length=10_000)
    files: list[FileInput] = Field(default_factory=list, max_length=5)


class DesignMessage(StrictModel):
    """A design request or agent response in the shared design-library conversation."""

    event_id: int
    author: Literal["user", "agent"]
    message: str
    created_at: str
    files: list[DesignFile] = Field(default_factory=list)
    reply_to: int | None = None
    status: Literal["queued", "working", "needs_input", "ready"] = "queued"
    design_id: str | None = None


class DesignInbox(StrictModel):
    """Design chat history and actual agent availability."""

    messages: list[DesignMessage]
    next_cursor: int
    agent_waiting: bool


FeedbackFeed.model_rebuild()
