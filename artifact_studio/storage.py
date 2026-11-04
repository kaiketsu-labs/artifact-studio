"""File-backed slide content with transactional review and idempotency records."""

import base64
import binascii
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal, cast

from pydantic import TypeAdapter

from .models import (
    AgentContext,
    Deck,
    DeckView,
    DesignFile,
    DesignInbox,
    DesignManifest,
    DesignMessage,
    DesignRequestInput,
    DesignView,
    EditIntent,
    ElementTarget,
    Feedback,
    FeedbackFeed,
    FeedbackFile,
    FeedbackWorkflow,
    FileInput,
    QuestionPrompt,
    Slide,
    SlidePatch,
    SlideTarget,
    SlideView,
)
from .slide_css import declarations, stylesheet

DATA = re.compile(r'(<script id="deck-data" type="application/json">)(.*?)(</script>)', re.S)
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")
SLIDE_TARGET = TypeAdapter(SlideTarget)
DESIGN_FILES = (
    "index.html",
    "theme.css",
    "presentation.js",
    "icons.js",
    "AGENT-GUIDE.md",
    "PREFERENCES.md",
    "README.md",
    "assets",
)
FEEDBACK_FILES = TypeAdapter(list[FeedbackFile])


class ConflictError(ValueError):
    """The caller edited an outdated revision or reused an operation ID."""


class SlideMarkup(HTMLParser):
    """Reject executable markup and external asset loads in agent-authored slides."""

    def __init__(self) -> None:
        """Track stylesheet bodies as well as ordinary markup attributes."""
        super().__init__()
        self.in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Allow presentation markup while keeping code in the trusted runtime."""
        if tag.lower() in {
            "script",
            "iframe",
            "object",
            "embed",
            "form",
            "input",
            "button",
            "textarea",
            "select",
            "base",
            "link",
            "meta",
            "foreignobject",
            "animate",
            "set",
            "audio",
            "video",
        }:
            raise ValueError(
                f"Keep <{tag}> in interactive_html; html is its script-free static fallback."
            )
        for name, value in attrs:
            text = (value or "").strip()
            if name.lower().startswith("on") or name.lower() in {"srcdoc", "srcset"}:
                raise ValueError(
                    "Use interactive_html for executable or alternate-source attributes."
                )
            if name.lower() in {"src", "href", "xlink:href"} and (
                not (text.startswith("assets/") or text.startswith("#")) or ".." in text
            ):
                raise ValueError("Slide assets must use local assets/ paths or SVG anchors.")
            if name.lower() == "style":
                declarations(text)
        if tag.lower() == "style":
            self.in_style = True

    def handle_data(self, data: str) -> None:
        """Validate parsed stylesheet tokens, including escaped resource URLs."""
        if self.in_style:
            _ = stylesheet(data)

    def handle_endtag(self, tag: str) -> None:
        """Finish the current local stylesheet."""
        if tag.lower() == "style":
            self.in_style = False

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Apply the same rules to self-closing SVG and image elements."""
        self.handle_starttag(tag, attrs)


def replace_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace a document without exposing partially written content."""
    with tempfile.NamedTemporaryFile(
        mode="w", encoding=encoding, dir=path.parent, delete=False
    ) as file:
        _ = file.write(content)
        temporary = Path(file.name)
    temporary.replace(path)


def digest(value: str) -> str:
    """Return a stable SHA-256 token for concurrency and approval checks."""
    return hashlib.sha256(value.encode()).hexdigest()


def decode_attachments(files: list[FileInput]) -> list[tuple[str, bytes]]:
    """Validate shared attachment limits before writing any local files."""
    if len(files) > 5:
        raise ValueError("Choose up to 5 attachments.")
    decoded: list[tuple[str, bytes]] = []
    for file in files:
        name = Path(file.name.replace("\\", "/")).name
        if name in {"", ".", ".."} or any(ord(char) < 32 for char in name):
            raise ValueError("Choose a file with a valid filename.")
        try:
            data = base64.b64decode(file.data_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("The attachment could not be read. Attach it again.") from error
        if len(data) > 25_000_000:
            raise ValueError("Keep each attachment at or below 25 MB.")
        decoded.append((name, data))
    if sum(len(data) for _, data in decoded) > 50_000_000:
        raise ValueError("Keep the combined attachments at or below 50 MB.")
    return decoded


class Store:
    """Read canonical HTML files and persist review state independently of MCP sessions."""

    def __init__(self, root: Path, database: Path) -> None:
        """Initialize the local review database without changing existing decks."""
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS design_archives (folder TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS workspace_settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS archived_decks (
                    deck TEXT PRIMARY KEY, archived_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS review_sessions (
                    harness TEXT NOT NULL, session_id TEXT NOT NULL, deck TEXT NOT NULL,
                    feedback_cursor INTEGER NOT NULL DEFAULT 0,
                    design_cursor INTEGER NOT NULL DEFAULT 0,
                    expires_at REAL NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(harness, session_id));
                CREATE TABLE IF NOT EXISTS approvals (
                    deck TEXT, slide TEXT, content_hash TEXT,
                    PRIMARY KEY(deck, slide));
                CREATE TABLE IF NOT EXISTS slide_views (
                    deck TEXT NOT NULL, slide TEXT NOT NULL, content_hash TEXT NOT NULL,
                    PRIMARY KEY(deck, slide));
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY, deck TEXT NOT NULL, slide TEXT NOT NULL,
                    author TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL,
                    content_hash TEXT NOT NULL, reply_to INTEGER);
                CREATE TABLE IF NOT EXISTS operations (
                    deck TEXT, request_id TEXT, payload_hash TEXT, result TEXT,
                    PRIMARY KEY(deck, request_id));
                CREATE TABLE IF NOT EXISTS creation_receipts (
                    deck TEXT PRIMARY KEY, request_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL, document TEXT NOT NULL, marker TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS delivery (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow (
                    deck TEXT PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'live',
                    waiting_until REAL NOT NULL DEFAULT 0, last_check TEXT);
                CREATE TABLE IF NOT EXISTS revisions (
                    deck TEXT, slide TEXT, hash TEXT, document TEXT,
                    PRIMARY KEY(deck, slide, hash));
                CREATE TABLE IF NOT EXISTS slide_actions (
                    deck TEXT NOT NULL, request_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    before_json TEXT NOT NULL, after_json TEXT NOT NULL, result_json TEXT NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(deck, request_id));
                CREATE TABLE IF NOT EXISTS design_chat (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT UNIQUE NOT NULL,
                    payload_hash TEXT NOT NULL, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS design_presence (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), waiting_until REAL NOT NULL);
            """)
            from .version_history import install_slide_history

            install_slide_history(db)
            from .slide_designs import install_designs

            install_designs(db)
            columns = {row[1] for row in db.execute("PRAGMA table_info(feedback)")}
            if "target" not in columns:
                _ = db.execute("ALTER TABLE feedback ADD COLUMN target TEXT")
            if "released" not in columns:
                _ = db.execute("ALTER TABLE feedback ADD COLUMN released INTEGER DEFAULT 1")
            if "batch_id" not in columns:
                _ = db.execute("ALTER TABLE feedback ADD COLUMN batch_id TEXT")
            if "context_slide_id" not in columns:
                _ = db.execute("ALTER TABLE feedback ADD COLUMN context_slide_id TEXT")
            if "files" not in columns:
                _ = db.execute("ALTER TABLE feedback ADD COLUMN files TEXT NOT NULL DEFAULT '[]'")
            if "edit_intent" not in columns:
                _ = db.execute("ALTER TABLE feedback ADD COLUMN edit_intent TEXT")
            if "question" not in columns:
                _ = db.execute("ALTER TABLE feedback ADD COLUMN question TEXT")
            if "design_set_id" not in columns:
                _ = db.execute("ALTER TABLE feedback ADD COLUMN design_set_id TEXT")
            if "design_option_id" not in columns:
                _ = db.execute("ALTER TABLE feedback ADD COLUMN design_option_id TEXT")
            _ = db.execute(
                "CREATE INDEX IF NOT EXISTS feedback_replies ON feedback(reply_to,author)"
            )
            _ = db.execute(
                "INSERT OR IGNORE INTO delivery(event_id) SELECT id FROM feedback "
                "WHERE released=1 AND author='user' ORDER BY id"
            )
            for row in db.execute(
                "SELECT DISTINCT deck FROM slide_actions WHERE completed=0 UNION "
                "SELECT DISTINCT deck FROM slide_design_decisions WHERE completed=0"
            ).fetchall():
                with suppress(ConflictError, ValueError, FileNotFoundError):
                    self.recover_slides(db, cast(str, row[0]))
            for file in self.root.glob("*/index.html"):
                if 'id="deck-data"' not in file.read_text():
                    continue
                with suppress(ValueError, FileNotFoundError):
                    self.seed_slide_views(db, self.read(file.parent.name))

    def seed_slide_views(self, db: sqlite3.Connection, deck: Deck) -> None:
        """Establish initial visual baselines without acknowledging any later revision."""
        for slide in deck.slides:
            _ = db.execute(
                "INSERT OR IGNORE INTO slide_views VALUES (?,?,?)",
                (deck.id, slide.id, self.content_hash(deck.id, slide)),
            )

    @staticmethod
    def default_instructions() -> str:
        """Read the human-maintained default authoring rules shipped with the app."""
        return (
            Path(__file__).resolve().parent.parent / "docs/AUTHORING-INSTRUCTIONS.md"
        ).read_text()

    def authoring_instructions(self) -> str:
        """Return persistent workspace rules independently of presentation design or chat."""
        path = self.root / "INSTRUCTIONS.md"
        if path.exists():
            return path.read_text()
        with self.connect() as db:
            row = db.execute(
                "SELECT value FROM workspace_settings WHERE key='authoring_instructions'"
            ).fetchone()
        return cast(str, row[0]) if row is not None else self.default_instructions()

    def save_instructions(
        self,
        markdown: str,
        expected_hash: str,
        *,
        learned_preferences: tuple[str, str] | None = None,
    ) -> None:
        """Check both document baselines before saving human edits to workspace files."""
        if len(markdown) > 20_000:
            raise ValueError("Keep instructions under 20,000 characters.")
        if learned_preferences is not None and len(learned_preferences[0]) > 20_000:
            raise ValueError("Keep learned preferences under 20,000 characters.")
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            path = self.root / "INSTRUCTIONS.md"
            row = db.execute(
                "SELECT value FROM workspace_settings WHERE key='authoring_instructions'"
            ).fetchone()
            fallback = cast(str, row[0]) if row is not None else self.default_instructions()
            current = path.read_text() if path.exists() else fallback
            if markdown != current and digest(current) != expected_hash:
                raise ConflictError("Instructions changed in another window. Reopen before saving.")
            preferences_path = self.root / "LEARNED-PREFERENCES.md"
            preferences_current = self.learned_preferences()
            preferences = preferences_current
            if learned_preferences is not None:
                preferences, preferences_hash = learned_preferences
                if (
                    preferences != preferences_current
                    and digest(preferences_current) != preferences_hash
                ):
                    raise ConflictError(
                        "Learned preferences changed in another window. Reload before saving."
                    )
            if markdown != current or not path.exists():
                replace_text(path, markdown)
            if learned_preferences is not None and preferences != preferences_current:
                replace_text(preferences_path, preferences)

    def learned_preferences(self) -> str:
        """Read secondary learned guidance without changing explicit workspace rules."""
        path = self.root / "LEARNED-PREFERENCES.md"
        return path.read_text() if path.exists() else ""

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection]:
        """Open a transaction-capable connection with a bounded busy timeout."""
        db = sqlite3.connect(self.database, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover_slides(self, db: sqlite3.Connection, deck_id: str) -> None:
        """Finish durable structural intents while the caller holds the write transaction."""
        from .slide_actions import recover_pending

        recover_pending(self, db, deck_id)
        from .slide_designs import recover_design_decisions

        recover_design_decisions(self, db, deck_id)

    @contextmanager
    def editing(self, deck_id: str) -> Generator[sqlite3.Connection]:
        """Serialize content changes after recovering earlier structural operations."""
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            self.recover_slides(db, deck_id)
            yield db

    def folder(self, deck_id: str) -> Path:
        """Resolve a presentation ID without permitting directory traversal."""
        if not SAFE_ID.fullmatch(deck_id):
            raise ValueError("Invalid presentation ID.")
        folder = (self.root / deck_id).resolve()
        if not folder.is_relative_to(self.root):
            raise ValueError("Presentation must be inside the collection.")
        return folder

    def read(self, deck_id: str) -> Deck:
        """Read the authoritative embedded document from index.html."""
        source = (self.folder(deck_id) / "index.html").read_text()
        match = DATA.search(source)
        if match is None:
            raise ValueError("This presentation has no Artifact Studio document yet.")
        deck = Deck.model_validate_json(match[2])
        if len({slide.id for slide in deck.slides}) != len(deck.slides):
            raise ValueError("Slide IDs must be unique.")
        return deck

    def write(self, deck_id: str, deck: Deck) -> None:
        """Replace the embedded source atomically while retaining the HTML shell."""
        from .sections import reconcile

        deck.sections = reconcile(deck.sections, [slide.id for slide in deck.slides])
        target = self.folder(deck_id) / "index.html"
        source = target.read_text()
        document = deck.model_dump_json(indent=2).replace("<", r"\u003c")
        updated, count = DATA.subn(lambda match: match[1] + document + match[3], source)
        if count != 1:
            raise ValueError("Expected one canonical deck document.")
        replace_text(target, updated)

    def visual_hash(self, deck_id: str) -> str:
        """Invalidate approvals when shared styling or bundled assets change."""
        folder = self.folder(deck_id)
        assets = [
            (str(path.relative_to(folder)), path.stat().st_size, path.stat().st_mtime_ns)
            for path in sorted((folder / "assets").rglob("*"))
            if path.is_file()
        ]
        return digest((folder / "theme.css").read_text() + json.dumps(assets))

    def content_hash(self, deck_id: str, slide: Slide, *, design_hash: str | None = None) -> str:
        """Hash audience-visible content independently of private notes and discussion."""
        return digest(
            slide.model_dump_json(
                include={"title", "html", "sources"}
                | ({"interactive_html"} if slide.interactive_html else set())
            )
            + (self.visual_hash(deck_id) if design_hash is None else design_hash)
        )

    @staticmethod
    def locate(deck: Deck, slide_id: str) -> Slide:
        """Find a stable slide ID regardless of its position in the deck."""
        slide = next((slide for slide in deck.slides if slide.id == slide_id), None)
        if slide is None:
            raise ValueError("Slide ID does not exist.")
        return slide

    def comments(self, deck_id: str, after_event_id: int = 0) -> list[Feedback]:
        """Read a replayable feedback queue using a durable event cursor."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT feedback.*,delivery.id AS delivery_id,o.name AS design_option_name "
                "FROM feedback LEFT JOIN delivery ON feedback.id=delivery.event_id "
                "LEFT JOIN slide_design_options o ON o.id=feedback.design_option_id "
                "WHERE deck=? AND feedback.id>? "
                "ORDER BY feedback.id",
                (deck_id, after_event_id),
            ).fetchall()
        return [
            Feedback(
                event_id=cast(int, row["id"]),
                slide_id=cast(str, row["slide"]) or None,
                scope="slide" if row["slide"] else "presentation",
                context_slide_id=cast("str | None", row["context_slide_id"]),
                author=cast(Literal["user", "agent"], row["author"]),
                message=cast(str, row["message"]),
                created_at=cast(str, row["created_at"]),
                content_hash=cast(str, row["content_hash"]),
                reply_to=cast("int | None", row["reply_to"]),
                released=bool(row["released"]),
                delivery_id=cast("int | None", row["delivery_id"]),
                batch_id=cast("str | None", row["batch_id"]),
                target=SLIDE_TARGET.validate_json(cast(str, row["target"]))
                if row["target"]
                else None,
                files=self.feedback_files(deck_id, cast(int, row["id"]), cast(str, row["files"])),
                edit_intent=EditIntent.model_validate_json(cast(str, row["edit_intent"]))
                if row["edit_intent"]
                else None,
                question=QuestionPrompt.model_validate_json(cast(str, row["question"]))
                if row["question"]
                else None,
                design_set_id=cast(str | None, row["design_set_id"]),
                design_option_id=cast(str | None, row["design_option_id"]),
                design_option_name=cast(str | None, row["design_option_name"]),
            )
            for row in rows
        ]

    def feedback_files(self, deck_id: str, event_id: int, document: str) -> list[FeedbackFile]:
        """Resolve private references without allowing saved paths outside review storage."""
        files = FEEDBACK_FILES.validate_json(document)
        if not files:
            return files
        boundary = (self.database.parent / "feedback-files" / deck_id).resolve()
        for index, file in enumerate(files):
            path = (boundary / file.path).resolve()
            if not path.is_relative_to(boundary):
                raise ValueError("Feedback attachment path is outside its private storage.")
            file.path = str(path)
            file.download_url = f"/api/decks/{deck_id}/feedback/{event_id}/files/{index}"
        return files

    def save_feedback_files(
        self, deck_id: str, files: list[tuple[str, bytes]]
    ) -> list[FeedbackFile]:
        """Save references beside the private database, outside presentation sync trees."""
        folder = self.database.parent / "feedback-files" / deck_id
        request = uuid.uuid4().hex
        result: list[FeedbackFile] = []
        for index, (name, data) in enumerate(files):
            relative = Path(request) / f"{index}.attachment"
            path = folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            _ = path.write_bytes(data)
            result.append(
                FeedbackFile(
                    name=name,
                    path=relative.as_posix(),
                    size=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                )
            )
        return result

    def review_hashes(
        self, deck_id: str, slide_id: str | None = None
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Read review hashes once per snapshot or only for a requested standalone slide."""
        selected = " AND slide=?" if slide_id is not None else ""
        parameters = (deck_id, slide_id) if slide_id is not None else (deck_id,)
        with self.connect() as db:
            approved = db.execute(
                "SELECT slide,content_hash FROM approvals WHERE deck=?" + selected, parameters
            ).fetchall()
            seen = db.execute(
                "SELECT slide,content_hash FROM slide_views WHERE deck=?" + selected, parameters
            ).fetchall()
        return (
            {cast(str, row[0]): cast(str, row[1]) for row in approved},
            {cast(str, row[0]): cast(str, row[1]) for row in seen},
        )

    def view_slide(
        self,
        deck_id: str,
        slide: Slide,
        *,
        include_pending: bool = True,
        design_hash: str | None = None,
        feedback: list[Feedback] | None = None,
        review_hashes: tuple[dict[str, str], dict[str, str]] | None = None,
    ) -> SlideView:
        """Combine canonical content with approval and discussion state."""
        content_hash = self.content_hash(deck_id, slide, design_hash=design_hash)
        approved, seen = (
            self.review_hashes(deck_id, slide.id) if review_hashes is None else review_hashes
        )
        return SlideView(
            **slide.model_dump(),
            expected_hash=digest(slide.model_dump_json()),
            content_hash=content_hash,
            approved=approved.get(slide.id) == content_hash,
            has_unseen_changes=seen.get(slide.id) != content_hash,
            feedback=[
                item
                for item in (self.comments(deck_id) if feedback is None else feedback)
                if item.slide_id == slide.id and (include_pending or item.released)
            ],
        )

    def view(self, deck_id: str, *, include_pending: bool = True) -> DeckView:
        """Return a review-ready snapshot of a presentation."""
        from .sections import sections_hash
        from .slide_designs import design_badges

        deck = self.read(deck_id)
        context_file = self.folder(deck_id) / "README.md"
        context = context_file.read_text() if context_file.exists() else ""
        design_hash = self.visual_hash(deck_id)
        feedback = self.comments(deck_id)
        by_slide: defaultdict[str | None, list[Feedback]] = defaultdict(list)
        for comment in feedback:
            by_slide[comment.slide_id].append(comment)
        review_hashes = self.review_hashes(deck_id)
        return DeckView(
            id=deck_id,
            title=deck.title,
            design_id=deck.design_id,
            design_hash=design_hash,
            slides=[
                self.view_slide(
                    deck_id,
                    slide,
                    include_pending=include_pending,
                    design_hash=design_hash,
                    feedback=by_slide[slide.id],
                    review_hashes=review_hashes,
                )
                for slide in deck.slides
            ],
            sections=deck.sections,
            sections_hash=sections_hash(deck.sections),
            context_markdown=context,
            context_hash=digest(context),
            authoring_instructions=self.authoring_instructions(),
            learned_preferences=self.learned_preferences(),
            brief_files=self.brief_files(deck_id),
            slide_designs=design_badges(self, deck),
            workflow=self.workflow(deck_id),
            feedback=[
                item
                for item in feedback
                if item.scope == "presentation" and (include_pending or item.released)
            ],
        )

    def update_context(self, deck_id: str, markdown: str, expected_hash: str) -> str:
        """Update the deck's context document with conflict protection for remote agents."""
        if not 1 <= len(markdown) <= 50_000:
            raise ValueError("Context must contain 1–50,000 characters.")
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            _ = self.read(deck_id)
            path = self.folder(deck_id) / "README.md"
            previous = path.read_text() if path.exists() else ""
            if previous == markdown:
                return digest(previous)
            if digest(previous) != expected_hash:
                raise ConflictError("Presentation context changed. Read it again before editing.")
            replace_text(path, markdown)
        return digest(markdown)

    def append_context(self, deck_id: str, markdown: str, expected_hash: str) -> str:
        """Append context with optimistic concurrency and exact retry recognition."""
        if not markdown.strip():
            raise ValueError("Add a nonempty continuation note.")
        path = self.folder(deck_id) / "README.md"
        previous = path.read_text() if path.exists() else ""
        addition = "\n\n" + markdown.strip() + "\n"
        if digest(previous) == expected_hash:
            updated = previous + addition
        elif previous.endswith(addition) and digest(previous[: -len(addition)]) == expected_hash:
            updated = previous
        else:
            raise ConflictError("Presentation context changed. Read it again before editing.")
        return self.update_context(deck_id, updated, expected_hash)

    def list_decks(self, *, archived: bool = False) -> list[dict[str, str]]:
        """List compatible presentations by their recoverable workspace visibility."""
        with self.connect() as db:
            removed = {cast(str, row[0]) for row in db.execute("SELECT deck FROM archived_decks")}
        result: list[dict[str, str]] = []
        for file in sorted(self.root.glob("*/index.html")):
            if (file.parent.name in removed) != archived:
                continue
            if 'id="deck-data"' not in file.read_text():
                continue
            deck = self.read(file.parent.name)
            result.append({"id": file.parent.name, "title": deck.title})
        return result

    def set_archived(self, deck_id: str, *, archived: bool) -> None:
        """Remove or restore a selector entry while preserving its files and review state."""
        _ = self.read(deck_id)
        with self.connect() as db:
            if archived:
                _ = db.execute(
                    "INSERT OR IGNORE INTO archived_decks VALUES (?,?)",
                    (deck_id, datetime.now(UTC).isoformat()),
                )
                _ = db.execute(
                    "UPDATE review_sessions SET enabled=0,expires_at=0 WHERE deck=?", (deck_id,)
                )
            else:
                _ = db.execute("DELETE FROM archived_decks WHERE deck=?", (deck_id,))

    def revise(
        self,
        deck_id: str,
        slide_id: str,
        expected_hash: str,
        request_id: str,
        patch: SlidePatch,
        *,
        prepare_interactive: Callable[[str], str] | None = None,
    ) -> Slide:
        """Apply an idempotent revision with optimistic concurrency protection."""
        result = self._revise(
            deck_id,
            slide_id,
            expected_hash,
            request_id,
            patch,
            prepare_interactive=prepare_interactive,
        )
        return Slide.model_validate(result.model_dump(include=set(Slide.model_fields)))

    def revise_view(
        self,
        deck_id: str,
        slide_id: str,
        expected_hash: str,
        request_id: str,
        patch: SlidePatch,
        *,
        prepare_interactive: Callable[[str], str] | None = None,
    ) -> SlideView:
        """Return the revision's recorded write-time hashes, never infer a legacy baseline."""
        result = self._revise(
            deck_id,
            slide_id,
            expected_hash,
            request_id,
            patch,
            prepare_interactive=prepare_interactive,
        )
        if not isinstance(result, SlideView):
            raise ConflictError(
                "This earlier revision has no recorded visual receipt. "
                "Read the current slide and reconcile before proceeding."
            )
        return result

    def _revise(
        self,
        deck_id: str,
        slide_id: str,
        expected_hash: str,
        request_id: str,
        patch: SlidePatch,
        *,
        prepare_interactive: Callable[[str], str] | None = None,
    ) -> Slide:
        """Share the locked write/replay path with source-only and receipt-aware callers."""
        from .version_history import capture_slide_version

        if not 8 <= len(request_id) <= 100:
            raise ValueError("Use a unique request_id of 8–100 characters.")
        payload = slide_id + expected_hash + patch.model_dump_json()
        if prepare_interactive is not None:
            payload += ":embed-interactive-assets"
        payload_hash = digest(payload)
        with self.editing(deck_id) as db:
            prior = db.execute(
                "SELECT payload_hash,result FROM operations WHERE deck=? AND request_id=?",
                (deck_id, request_id),
            ).fetchone()
            if prior is not None:
                if prior[0] != payload_hash:
                    raise ConflictError("This request_id was already used with another payload.")
                document = json.loads(cast(str, prior[1]))
                if "content_hash" in document:
                    return SlideView.model_validate(document)
                return Slide.model_validate(document)
            deck = self.read(deck_id)
            slide = self.locate(deck, slide_id)
            if digest(slide.model_dump_json()) != expected_hash:
                raise ConflictError("Slide changed. Read it again and merge your revision.")
            if prepare_interactive is not None and patch.interactive_html:
                patch = patch.model_copy(
                    update={"interactive_html": prepare_interactive(patch.interactive_html)}
                )
            _ = db.execute(
                "INSERT OR IGNORE INTO slide_views VALUES (?,?,?)",
                (
                    deck_id,
                    slide_id,
                    self.content_hash(deck_id, slide) if slide.revision == 1 else "",
                ),
            )
            changes = patch.model_dump(exclude_none=True)
            revised = Slide.model_validate(slide.model_dump() | changes)
            if revised.notes != slide.notes:
                revised.notes_revision = slide.notes_revision + 1
            SlideMarkup().feed(revised.html)
            if (
                revised.html != slide.html
                or revised.interactive_html != slide.interactive_html
                or revised.title != slide.title
                or revised.sources != slide.sources
            ):
                revised.revision = slide.revision + 1
            capture_slide_version(db, deck_id, slide)
            deck.slides[deck.slides.index(slide)] = revised
            self.write(deck_id, deck)
            if revised.revision != slide.revision:
                capture_slide_version(db, deck_id, revised, saved_at=datetime.now(UTC).isoformat())
            result = self._write_view(db, deck_id, revised)
            _ = db.execute(
                "INSERT INTO operations VALUES (?,?,?,?)",
                (deck_id, request_id, payload_hash, result.model_dump_json()),
            )
        return result

    def _write_view(self, db: sqlite3.Connection, deck_id: str, slide: Slide) -> SlideView:
        """Freeze source, visual hashes and review state without caching private discussion."""
        approved = db.execute(
            "SELECT content_hash FROM approvals WHERE deck=? AND slide=?", (deck_id, slide.id)
        ).fetchone()
        seen = db.execute(
            "SELECT content_hash FROM slide_views WHERE deck=? AND slide=?", (deck_id, slide.id)
        ).fetchone()
        return self.view_slide(
            deck_id,
            slide,
            feedback=[],
            review_hashes=(
                {slide.id: cast(str, approved[0])} if approved else {},
                {slide.id: cast(str, seen[0])} if seen else {},
            ),
        )

    def set_slide_hidden(
        self, deck_id: str, slide_id: str, *, hidden: bool, expected_hidden: bool
    ) -> None:
        """Update only slideshow visibility while preserving content, notes and approval."""
        self.set_slides_hidden(
            deck_id, [slide_id], hidden=hidden, expected_hidden={slide_id: expected_hidden}
        )

    def set_slides_hidden(
        self,
        deck_id: str,
        slide_ids: list[str],
        *,
        hidden: bool,
        expected_hidden: dict[str, bool],
    ) -> None:
        """Change visibility for a selection atomically while preserving independent edits."""
        if (
            not slide_ids
            or len(set(slide_ids)) != len(slide_ids)
            or set(slide_ids) != set(expected_hidden)
        ):
            raise ValueError("Provide each selected slide and its visibility exactly once.")
        with self.editing(deck_id):
            deck = self.read(deck_id)
            selected = [self.locate(deck, slide_id) for slide_id in slide_ids]
            if any(
                slide.hidden != hidden and slide.hidden != expected_hidden[slide.id]
                for slide in selected
            ):
                raise ConflictError("Slide visibility changed. Refresh and try again.")
            if all(slide.hidden == hidden for slide in selected):
                return
            for slide in selected:
                slide.hidden = hidden
            self.write(deck_id, deck)

    def mark_slide_seen(self, deck_id: str, slide_id: str, content_hash: str) -> bool:
        """Acknowledge only the opened version, without hiding a newer concurrent edit."""
        with self.editing(deck_id) as db:
            slide = self.locate(self.read(deck_id), slide_id)
            if self.content_hash(deck_id, slide) != content_hash:
                return False
            _ = db.execute(
                "INSERT INTO slide_views VALUES (?,?,?) ON CONFLICT(deck,slide) "
                "DO UPDATE SET content_hash=excluded.content_hash",
                (deck_id, slide_id, content_hash),
            )
        return True

    def approve(self, deck_id: str, slide_id: str, content_hash: str, approved: bool) -> None:
        """Record only the exact slide version approved through the human UI."""
        with self.editing(deck_id) as db:
            slide = self.locate(self.read(deck_id), slide_id)
            if self.content_hash(deck_id, slide) != content_hash:
                raise ConflictError("The slide changed before approval. Review the new version.")
            _ = db.execute(
                "INSERT OR REPLACE INTO approvals VALUES (?,?,?)",
                (deck_id, slide_id, content_hash if approved else ""),
            )
            if approved:
                _ = db.execute(
                    "INSERT OR REPLACE INTO slide_views VALUES (?,?,?)",
                    (deck_id, slide_id, content_hash),
                )

    def add_feedback(
        self,
        deck_id: str,
        slide_id: str | None,
        message: str,
        content_hash: str,
        request_id: str,
        *,
        reply_to: int | None = None,
        target: SlideTarget | None = None,
        context_slide_id: str | None = None,
        files: list[FileInput] | None = None,
        edit_intent: EditIntent | None = None,
        expected_mode: Literal["live", "batch"] | None = None,
        author: Literal["user", "agent"] | None = None,
        design_option_id: str | None = None,
    ) -> str:
        """Save a comment or reply exactly once, retaining its original slide context."""
        decoded = decode_attachments(files or [])
        if not message.strip() and not decoded:
            raise ValueError("Add a message or attach a file.")
        author = author or ("agent" if reply_to is not None else "user")
        target_json = target.model_dump_json() if target else None
        intent_json = edit_intent.model_dump_json() if edit_intent else None
        if edit_intent is not None and not isinstance(target, ElementTarget):
            raise ValueError("Element edit requests need exactly one element target.")
        payload_hash = digest(
            json.dumps(
                [slide_id, message, content_hash, reply_to, target_json]
                + ([context_slide_id] if context_slide_id is not None else [])
                + (
                    [[name, hashlib.sha256(data).hexdigest()] for name, data in decoded]
                    if decoded
                    else []
                )
                + ([intent_json] if intent_json is not None else [])
                + (["agent"] if author == "agent" and reply_to is None else [])
                + (["slide-design", design_option_id] if design_option_id is not None else [])
            )
        )
        stored_slide_id = slide_id or ""
        with self.editing(deck_id) as db:
            prior = db.execute(
                "SELECT payload_hash,result FROM operations WHERE deck=? AND request_id=?",
                (deck_id, request_id),
            ).fetchone()
            if prior is not None:
                if prior[0] != payload_hash:
                    raise ConflictError("Request ID already belongs to another action.")
                return cast(str, prior[1])
            deck = self.read(deck_id)
            if design_option_id is not None:
                option = db.execute(
                    "SELECT o.source_hash FROM slide_design_options o JOIN slide_design_sets s "
                    "ON s.id=o.set_id WHERE o.id=? AND s.deck=? AND s.slide=?",
                    (design_option_id, deck_id, slide_id),
                ).fetchone()
                if option is None or option[0] != content_hash or target is not None:
                    raise ConflictError(
                        "Design feedback must reference its original proposal and source hash."
                    )
            if slide_id is not None:
                _ = self.locate(deck, slide_id)
            elif target is not None:
                raise ValueError("Element targets need a slide ID.")
            if (
                reply_to is not None
                and db.execute(
                    "SELECT id FROM feedback WHERE id=? AND deck=? AND slide=?",
                    (reply_to, deck_id, stored_slide_id),
                ).fetchone()
                is None
            ):
                raise ValueError("Reply must reference feedback in the same presentation context.")
            mode_row = db.execute("SELECT mode FROM workflow WHERE deck=?", (deck_id,)).fetchone()
            mode = cast(Literal["live", "batch"], mode_row["mode"]) if mode_row else "live"
            if expected_mode is not None and mode != expected_mode:
                raise ConflictError(
                    "Feedback mode changed. "
                    "Review the current Live or Batch setting before sending."
                )
            released = author == "agent" or mode == "live"
            attachments = self.save_feedback_files(deck_id, decoded)
            result = self.append_feedback(
                db,
                deck_id,
                stored_slide_id,
                author,
                message,
                content_hash,
                released=released,
                reply_to=reply_to,
                target_json=target_json,
                context_slide_id=context_slide_id or slide_id,
                files_json=TypeAdapter(list[FeedbackFile]).dump_json(attachments).decode(),
                intent_json=intent_json,
                design_option_id=design_option_id,
            )
            _ = db.execute(
                "INSERT INTO operations VALUES (?,?,?,?)",
                (deck_id, request_id, payload_hash, result),
            )
        return result

    @staticmethod
    def append_feedback(
        db: sqlite3.Connection,
        deck_id: str,
        slide_id: str,
        author: str,
        message: str,
        content_hash: str,
        *,
        released: bool,
        reply_to: int | None = None,
        target_json: str | None = None,
        context_slide_id: str | None = None,
        files_json: str = "[]",
        intent_json: str | None = None,
        question_json: str | None = None,
        design_set_id: str | None = None,
        design_option_id: str | None = None,
    ) -> str:
        """Append a message and its delivery record within the caller's transaction."""
        cursor = db.execute(
            "INSERT INTO feedback(deck,slide,author,message,created_at,content_hash,"
            "reply_to,target,context_slide_id,released,files,edit_intent,question,"
            "design_set_id,design_option_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                deck_id,
                slide_id,
                author,
                message.strip(),
                datetime.now(UTC).isoformat(),
                content_hash,
                reply_to,
                target_json,
                context_slide_id,
                released,
                files_json,
                intent_json,
                question_json,
                design_set_id,
                design_option_id,
            ),
        )
        if released and author == "user":
            _ = db.execute("INSERT INTO delivery(event_id) VALUES (?)", (cursor.lastrowid,))
        return str(cursor.lastrowid)

    def save_notes(
        self,
        deck_id: str,
        slide_id: str,
        notes: str,
        expected_notes: str,
        expected_revision: int | None = None,
    ) -> int:
        """Preserve simultaneous content edits while saving human speaker notes."""
        with self.editing(deck_id):
            deck = self.read(deck_id)
            slide = self.locate(deck, slide_id)
            if expected_revision is not None and slide.notes_revision != expected_revision:
                if slide.notes == notes and slide.notes_revision > expected_revision:
                    return slide.notes_revision
                raise ConflictError("Notes changed in another window. Review the saved notes.")
            if slide.notes == notes and expected_revision is None:
                return slide.notes_revision
            if slide.notes != expected_notes:
                raise ConflictError("Notes changed in another window. Copy your edit and refresh.")
            slide.notes = notes
            slide.notes_revision += 1
            self.write(deck_id, deck)
            return slide.notes_revision

    def designs(self, *, include_archived: bool = False) -> list[DesignView]:
        """Discover reusable designs and validate the files needed for deck creation."""
        result: list[DesignView] = []
        with self.connect() as db:
            archived = {str(row[0]) for row in db.execute("SELECT folder FROM design_archives")}
        for manifest in sorted(self.root.glob("*/design.json")):
            if not include_archived and manifest.parent.name in archived:
                continue
            folder = self.folder(manifest.parent.name)
            design = DesignManifest.model_validate_json(manifest.read_text())
            if any(existing.id == design.id for existing in result):
                raise ValueError(f"Duplicate design ID: {design.id}. Use a unique design ID.")
            for name in DESIGN_FILES:
                if not (folder / name).exists():
                    raise ValueError(f"Design {design.name} is missing {name}.")
            if self.read(folder.name).design_id != design.id:
                raise ValueError(f"Design ID in {folder.name}/index.html must match design.json.")
            result.append(DesignView(**design.model_dump(), folder_id=folder.name))
        return result

    def default_design_id(self) -> str:
        """Use a saved workspace preference, falling back to the first installed design."""
        designs = self.designs()
        with self.connect() as db:
            row = db.execute(
                "SELECT value FROM workspace_settings WHERE key='default_design'"
            ).fetchone()
        preferred = str(row[0]) if row else ""
        return (
            preferred
            if any(item.id == preferred for item in designs)
            else (designs[0].id if designs else "")
        )

    def set_default_design(self, design_id: str) -> str:
        """Save a user-selected brand without changing any existing presentation."""
        _ = self.design(design_id)
        if not any(item.id == design_id for item in self.designs()):
            raise ValueError("Restore the deleted design before making it the default.")
        with self.connect() as db:
            _ = db.execute(
                "INSERT OR REPLACE INTO workspace_settings VALUES ('default_design', ?)",
                (design_id,),
            )
        return design_id

    def design(self, design_id: str) -> DesignView:
        """Resolve an explicitly selected design without hidden session state."""
        for design in self.designs(include_archived=True):
            if design.id == design_id:
                return design
        raise ValueError(f"Unknown design '{design_id}'. Call list_designs to choose a design.")

    def register_design(self, folder_id: str, manifest: DesignManifest) -> DesignView:
        """Validate and register an agent-prepared design without replacing an existing brand."""
        folder = self.folder(folder_id)
        if any(
            not path.is_symlink() and json.loads(path.read_text()).get("id") == manifest.id
            for path in self.root.glob("*/design-context.json")
            if not path.parent.name.startswith(".")
        ):
            raise ConflictError(
                "This ID belongs to a reference collection. Choose a new slide design ID."
            )
        if not folder.is_dir():
            raise ValueError("Prepare the reference-based design folder before registering it.")
        for name in DESIGN_FILES:
            if not (folder / name).exists():
                raise ValueError(f"The prepared design is missing {name}.")
        if self.read(folder_id).design_id != manifest.id:
            raise ValueError("Set the example deck's design_id to the chosen design ID first.")
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            existing = next(
                (item for item in self.designs(include_archived=True) if item.id == manifest.id),
                None,
            )
            result = DesignView(**manifest.model_dump(), folder_id=folder_id)
            if existing == result:
                return result
            if existing is not None or (folder / "design.json").exists():
                raise ConflictError("That design is already registered. Choose a unique design ID.")
            _ = (folder / "design.json").write_text(manifest.model_dump_json(indent=2) + "\n")
            with (self.root / "README.md").open("a") as file:
                _ = file.write(
                    f"\n- [{manifest.name} · reusable design]({folder_id}/index.html) "
                    f"· [Context]({folder_id}/README.md)\n"
                )
        return result

    def create(
        self,
        deck_id: str,
        title: str,
        design_id: str = "",
        brief: str = "",
        files: list[FileInput] | None = None,
        request_id: str = "",
        *,
        brief_author: Literal["user", "agent"] = "user",
    ) -> Deck:
        """Create a branded starting canvas with a durable brief for the authoring agent."""
        if request_id and not 8 <= len(request_id) <= 100:
            raise ValueError("Use a unique request_id of 8–100 characters.")
        payload_hash = digest(
            json.dumps(
                [deck_id, title, design_id, brief, [item.model_dump() for item in files or []]]
                + (["agent"] if brief_author == "agent" else []),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*", deck_id):
            raise ValueError("Use a dated presentation ID: YYYY-MM-DD-short-title.")
        if not title.strip() or len(title) > 160:
            raise ValueError("Use a presentation title of 1–160 characters.")
        brief = brief.strip()
        if len(brief) > 10_000:
            raise ValueError("Keep the presentation brief under 10,000 characters.")
        target = self.folder(deck_id)
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT request_id,payload_hash,document,marker "
                "FROM creation_receipts WHERE deck=?",
                (deck_id,),
            ).fetchone()
            if prior and request_id and prior[0] == request_id:
                if prior[1] != payload_hash:
                    raise ConflictError(
                        "This creation request_id was already used with different content."
                    )
                if not target.is_dir():
                    raise ConflictError(
                        "The recorded presentation is missing. Read and reconcile before "
                        "continuing; it was not recreated."
                    )
                marker_path = target / ".artifact-studio-creation"
                if (
                    marker_path.is_symlink()
                    or not marker_path.is_file()
                    or marker_path.stat().st_size != 32
                    or marker_path.read_bytes() != str(prior[3]).encode()
                ):
                    raise ConflictError(
                        "The presentation's creation identity is unavailable or changed. "
                        "Read and reconcile ownership before continuing."
                    )
                try:
                    current = self.read(deck_id)
                except (OSError, ValueError) as error:
                    raise ConflictError(
                        "The recorded presentation is missing or unreadable. "
                        "Inspect and reconcile it before continuing; it was not recreated."
                    ) from error
                if current.id != deck_id:
                    raise ConflictError(
                        "The recorded presentation has a different identity. "
                        "Inspect and reconcile it before continuing."
                    )
                return Deck.model_validate_json(cast(str, prior[2]))
            if target.exists():
                raise ConflictError(
                    "That presentation ID already exists. Read and reconcile its ownership "
                    "and contents before choosing another ID."
                )
            if prior:
                raise ConflictError(
                    "This presentation ID has a recorded creation but its folder is missing. "
                    "Read and reconcile before continuing."
                )
            decoded = decode_attachments(files or [])
            design_id = design_id or self.default_design_id()
            if not design_id:
                raise ValueError(
                    "Add a design from a PPTX, URL or reference before creating slides."
                )
            design = self.design(design_id)
            template = self.folder(design.folder_id)
            from .design_authoring import design_files, record_design_baseline

            _ = design_files(template)
            target.mkdir()
            for name in (
                "index.html",
                "theme.css",
                "presentation.js",
                "icons.js",
                "PREFERENCES.md",
                "AGENT-GUIDE.md",
            ):
                _ = shutil.copy2(template / name, target / name)
            _ = shutil.copytree(template / "assets", target / "assets")
            _ = shutil.copy2(
                Path(__file__).resolve().parent.parent / "runtime/slideshow.css",
                target / "slideshow.css",
            )
            deck = self.read(deck_id)
            deck.id, deck.title = deck_id, title
            deck.design_id = design_id
            deck.slides = deck.slides[:1]
            deck.sections = []
            for slide in deck.slides:
                slide.hidden = False
                slide.title = title
                slide.html = re.sub(
                    r"(<h1\b[^>]*>).*?(</h1>)",
                    lambda match: match[1] + escape(title) + match[2],
                    slide.html,
                    count=1,
                    flags=re.S,
                )
                slide.notes = ""
                slide.agent = AgentContext(
                    summary=(
                        "Your brief is saved. Ready for the first draft."
                        if brief
                        else "Tell your agent who this is for and what it should say."
                    )
                )
                for source in slide.sources:
                    if source.url.lower().endswith(".pptx") and not source.url.startswith(
                        ("..", "http")
                    ):
                        source.url = f"../{design.folder_id}/{source.url}"
            self.write(deck_id, deck)
            try:
                record_design_baseline(self, deck_id)
            except OSError, ValueError:
                shutil.rmtree(target)
                raise
            self.seed_slide_views(db, deck)
            attachments: list[DesignFile] = []
            for file_index, (name, data) in enumerate(decoded):
                relative = Path("brief-attachments") / f"{file_index}.attachment"
                path = target / relative
                path.parent.mkdir(exist_ok=True)
                _ = path.write_bytes(data)
                attachments.append(
                    DesignFile(
                        name=name,
                        path=relative.as_posix(),
                        size=len(data),
                        sha256=hashlib.sha256(data).hexdigest(),
                    )
                )
            if attachments:
                _ = (target / "brief-files.json").write_bytes(
                    TypeAdapter(list[DesignFile]).dump_json(attachments, indent=2)
                )
            attachment_context = ""
            if attachments:
                attachment_context = (
                    "## Brief attachments\n\n"
                    + "".join(
                        f"- {item.name}: `{item.path}` "
                        f"(download: /api/decks/{deck_id}/brief-files/{file_index})\n"
                        for file_index, item in enumerate(attachments)
                    )
                    + "\n"
                )
            _ = (target / "README.md").write_text(
                f"# {title}\n\nCreated from the {design.name} design language. "
                "Canonical file: index.html.\n\n"
                f"## Purpose and context\n\n{brief or 'Awaiting the human’s brief.'}\n\n"
                f"{attachment_context}"
                f"## Sources and data date\n\nCreated {deck_id[:10]}. "
                "Brand reference: the selected design bundle. Content sources and factual "
                "data dates still need to be established from the brief.\n\n"
                "## Preferences and continuation\n\nRead PREFERENCES.md and AGENT-GUIDE.md. "
                "Use concise, human-readable text and meaningful visuals. Layout examples "
                "are flexible. Read presentation feedback before drafting; ask about missing "
                "context. Local files remain canonical; private sync is opt-in. "
                "All slides start unreviewed.\n"
            )
            if brief:
                _ = self.append_feedback(db, deck_id, "", brief_author, brief, "", released=True)
            index = self.root / "README.md"
            with index.open("a") as file:
                _ = file.write(
                    f"\n- [{title}]({deck_id}/index.html) · [Context]({deck_id}/README.md)\n"
                )
            if request_id:
                marker = uuid.uuid4().hex
                _ = (target / ".artifact-studio-creation").write_text(marker)
                _ = db.execute(
                    "INSERT INTO creation_receipts VALUES (?,?,?,?,?)",
                    (deck_id, request_id, payload_hash, deck.model_dump_json(), marker),
                )
        return deck

    def brief_files(self, deck_id: str) -> list[DesignFile]:
        """Resolve saved brief attachments inside their canonical presentation folder."""
        folder = self.folder(deck_id)
        manifest = folder / "brief-files.json"
        if not manifest.exists():
            return []
        attachments = TypeAdapter(list[DesignFile]).validate_json(manifest.read_bytes())
        for attachment in attachments:
            path = (folder / attachment.path).resolve()
            if not path.is_relative_to((folder / "brief-attachments").resolve()):
                raise ValueError("Brief attachment path is outside its presentation.")
            attachment.path = str(path)
        return attachments

    def add_slide(self, deck_id: str, slide: Slide) -> Slide:
        """Append any freely composed HTML/SVG slide without imposing a layout catalogue."""
        with self.editing(deck_id) as db:
            return self._append_slide(db, deck_id, slide)

    def _append_slide(
        self,
        db: sqlite3.Connection,
        deck_id: str,
        slide: Slide,
        prepare_interactive: Callable[[str], str] | None = None,
        *,
        after_slide_id: str | None = None,
        section_id: str = "",
        expected_slide_ids: list[str] | None = None,
        expected_sections_hash: str = "",
    ) -> Slide:
        """Append under the caller's write transaction without accepting an existing ID."""
        deck = self.read(deck_id)
        ids = [item.id for item in deck.slides]
        guarded = (
            after_slide_id is not None
            or bool(section_id)
            or expected_slide_ids is not None
            or bool(expected_sections_hash)
        )
        if guarded:
            from .sections import sections_hash

            if expected_slide_ids is None or not expected_sections_hash:
                raise ValueError(
                    "Targeted additions need expected_slide_ids and expected_sections_hash "
                    "from a fresh outline."
                )
            if ids != expected_slide_ids or sections_hash(deck.sections) != expected_sections_hash:
                raise ConflictError(
                    "Slide order or sections changed. Read the outline before inserting."
                )
        if after_slide_id and after_slide_id not in ids:
            raise ValueError("The insertion anchor does not exist in this presentation.")
        destination = None
        if deck.sections:
            if section_id:
                destination = next(
                    (group for group in deck.sections if group.id == section_id), None
                )
            elif after_slide_id == "":
                destination = deck.sections[0]
            else:
                anchor = after_slide_id or ids[-1]
                destination = next(
                    (group for group in deck.sections if anchor in group.slide_ids), None
                )
            if destination is None:
                raise ValueError("The destination section does not exist.")
            if after_slide_id and after_slide_id not in destination.slide_ids:
                raise ValueError("The insertion anchor belongs to a different section.")
            local = (
                destination.slide_ids.index(after_slide_id) + 1
                if after_slide_id
                else 0
                if after_slide_id == ""
                else len(destination.slide_ids)
            )
            position = (
                sum(
                    len(group.slide_ids)
                    for group in deck.sections[: deck.sections.index(destination)]
                )
                + local
            )
        else:
            if section_id:
                raise ValueError("The destination section does not exist.")
            local = None
            position = (
                ids.index(after_slide_id) + 1
                if after_slide_id
                else 0
                if after_slide_id == ""
                else len(ids)
            )
        if any(existing.id == slide.id for existing in deck.slides):
            raise ConflictError("Slide ID already exists. Read it rather than adding it twice.")
        if len(deck.slides) >= 100:
            raise ValueError("A presentation supports at most 100 slides.")
        slide = slide.model_copy(update={"revision": 1})
        if prepare_interactive is not None and slide.interactive_html:
            slide = slide.model_copy(
                update={"interactive_html": prepare_interactive(slide.interactive_html)}
            )
        SlideMarkup().feed(slide.html)
        if position == 0:
            _ = db.execute(
                "UPDATE feedback SET context_slide_id=? WHERE deck=? "
                "AND COALESCE(slide,'')='' AND context_slide_id IS NULL AND reply_to IS NULL",
                (ids[0], deck_id),
            )
        if destination is not None and local is not None:
            destination.slide_ids.insert(local, slide.id)
        deck.slides.insert(position, slide)
        self.write(deck_id, deck)
        self.seed_slide_views(db, deck)
        return slide

    def add_slide_once(
        self,
        deck_id: str,
        slide: Slide,
        request_id: str,
        *,
        prepare_interactive: Callable[[str], str] | None = None,
        after_slide_id: str | None = None,
        section_id: str = "",
        expected_slide_ids: list[str] | None = None,
        expected_sections_hash: str = "",
    ) -> SlideView:
        """Record the original write-time acknowledgement for exact-payload add retries.

        An unrecorded existing ID always conflicts; it is never inferred to belong to this request.
        """
        if not 8 <= len(request_id) <= 100:
            raise ValueError("Use a unique request_id of 8–100 characters.")
        payload: list[object] = [
            "add_slide",
            slide.model_dump(mode="json"),
            prepare_interactive is not None,
        ]
        if (
            after_slide_id is not None
            or section_id
            or expected_slide_ids is not None
            or expected_sections_hash
        ):
            payload.append([after_slide_id, section_id, expected_slide_ids, expected_sections_hash])
        payload_hash = digest(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        with self.editing(deck_id) as db:
            prior = db.execute(
                "SELECT payload_hash,result FROM operations WHERE deck=? AND request_id=?",
                (deck_id, request_id),
            ).fetchone()
            if prior is not None:
                if prior["payload_hash"] != payload_hash:
                    raise ConflictError("This request_id was already used with another payload.")
                return SlideView.model_validate_json(cast(str, prior["result"]))
            added = self._append_slide(
                db,
                deck_id,
                slide,
                prepare_interactive,
                after_slide_id=after_slide_id,
                section_id=section_id,
                expected_slide_ids=expected_slide_ids,
                expected_sections_hash=expected_sections_hash,
            )
            result = self._write_view(db, deck_id, added)
            _ = db.execute(
                "INSERT INTO operations VALUES (?,?,?,?)",
                (deck_id, request_id, payload_hash, result.model_dump_json()),
            )
            return result

    def reorder(
        self, deck_id: str, slide_ids: list[str], *, expected_slide_ids: list[str] | None = None
    ) -> Deck:
        """Reorder exactly the current slides while preserving their stable IDs."""
        with self.editing(deck_id) as db:
            deck = self.read(deck_id)
            lookup = {slide.id: slide for slide in deck.slides}
            if len(slide_ids) != len(lookup) or set(slide_ids) != set(lookup):
                raise ConflictError("Provide every current slide ID exactly once.")
            current_ids = [slide.id for slide in deck.slides]
            if current_ids == slide_ids:
                return deck
            if expected_slide_ids is not None and current_ids != expected_slide_ids:
                raise ConflictError("Slide order changed. Try moving the slides again.")
            _ = db.execute(
                "UPDATE feedback SET context_slide_id=? WHERE deck=? "
                "AND COALESCE(slide,'')='' AND context_slide_id IS NULL AND reply_to IS NULL",
                (current_ids[0], deck_id),
            )
            deck.slides = [lookup[slide_id] for slide_id in slide_ids]
            self.write(deck_id, deck)
        return deck

    def remove_slide(self, deck_id: str, slide_id: str, expected_hash: str) -> None:
        """Remove a requested slide while retaining its source in revision history."""
        from .slide_actions import SlideActions

        deck = self.read(deck_id)
        _ = SlideActions(self).delete(
            deck_id,
            [slide_id],
            [slide.id for slide in deck.slides],
            {slide_id: expected_hash},
            "agent-delete-" + uuid.uuid4().hex,
        )

    def workflow(self, deck_id: str) -> FeedbackWorkflow:
        """Read delivery mode and bounded listening leases; never claim an idle agent is active."""
        with self.connect() as db:
            row = db.execute("SELECT * FROM workflow WHERE deck=?", (deck_id,)).fetchone()
            pending = db.execute(
                "SELECT COUNT(*) FROM feedback WHERE deck=? AND author='user' AND released=0",
                (deck_id,),
            ).fetchone()[0]
            connected = db.execute(
                "SELECT harness,session_id FROM review_sessions "
                "WHERE deck=? AND enabled=1 AND expires_at>? ORDER BY expires_at DESC LIMIT 1",
                (deck_id, time.time()),
            ).fetchone()
        return FeedbackWorkflow(
            mode=cast(Literal["live", "batch"], row["mode"]) if row else "live",
            pending=cast(int, pending),
            agent_waiting=bool(connected or (row and row["waiting_until"] > time.time())),
            last_agent_check=cast("str | None", row["last_check"]) if row else None,
            review_connected=bool(connected),
            review_harness=cast(str, connected[0]) if connected else None,
            review_session=cast(str, connected[1]) if connected else None,
        )

    def set_mode(self, deck_id: str, mode: Literal["live", "batch"]) -> FeedbackWorkflow:
        """Change future comment delivery; queued comments still require an explicit send."""
        _ = self.read(deck_id)
        with self.connect() as db:
            _ = db.execute(
                "INSERT INTO workflow(deck,mode) VALUES (?,?) "
                "ON CONFLICT(deck) DO UPDATE SET mode=excluded.mode",
                (deck_id, mode),
            )
        return self.workflow(deck_id)

    def release_batch(self, deck_id: str) -> FeedbackWorkflow:
        """Release all pending comments together in a durable delivery sequence."""
        _ = self.read(deck_id)
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT id FROM feedback WHERE deck=? AND author='user' AND released=0 ORDER BY id",
                (deck_id,),
            ).fetchall()
            batch_id = str(uuid.uuid4())
            for row in rows:
                _ = db.execute(
                    "UPDATE feedback SET released=1,batch_id=? WHERE id=?", (batch_id, row[0])
                )
                _ = db.execute("INSERT INTO delivery(event_id) VALUES (?)", (row[0],))
        return self.workflow(deck_id)

    def mark_agent_check(self, deck_id: str, wait_seconds: float = 0) -> None:
        """Advertise listening only while an actual bounded MCP wait request is active."""
        _ = self.read(deck_id)
        with self.connect() as db:
            _ = db.execute(
                "INSERT INTO workflow(deck,waiting_until,last_check) VALUES (?,?,?) "
                "ON CONFLICT(deck) DO UPDATE SET waiting_until=excluded.waiting_until,"
                "last_check=excluded.last_check",
                (deck_id, time.time() + wait_seconds, datetime.now(UTC).isoformat()),
            )

    def feedback_feed(self, deck_id: str, after_delivery_id: int = 0) -> FeedbackFeed:
        """Return only released human feedback with a cursor that cannot skip late batches."""
        events = sorted(
            (
                item
                for item in self.comments(deck_id)
                if item.delivery_id is not None and item.delivery_id > after_delivery_id
            ),
            key=lambda item: item.delivery_id or 0,
        )
        return FeedbackFeed(
            events=events,
            next_cursor=max((item.delivery_id or 0 for item in events), default=after_delivery_id),
            mode=self.workflow(deck_id).mode,
            authoring_instructions=self.authoring_instructions(),
            learned_preferences=self.learned_preferences(),
            design_requests=[
                message
                for message in self.design_inbox().messages
                if message.author == "user" and message.status == "queued"
            ],
        )

    def design_inbox(self, after_event_id: int = 0) -> DesignInbox:
        """Read the shared design conversation and a bounded listening presence."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT document FROM design_chat WHERE id>? ORDER BY id", (after_event_id,)
            ).fetchall()
            deck_wait = db.execute("SELECT MAX(waiting_until) FROM workflow").fetchone()[0]
            design_wait = db.execute(
                "SELECT waiting_until FROM design_presence WHERE singleton=1"
            ).fetchone()
            review_wait = db.execute(
                "SELECT MAX(expires_at) FROM review_sessions WHERE enabled=1"
            ).fetchone()[0]
        messages = [DesignMessage.model_validate_json(cast(str, row[0])) for row in rows]
        return DesignInbox(
            messages=messages,
            next_cursor=max((item.event_id for item in messages), default=after_event_id),
            agent_waiting=max(
                cast(float, deck_wait or 0),
                cast(float, design_wait[0] if design_wait else 0),
                cast(float, review_wait or 0),
            )
            > time.time(),
        )

    def mark_design_wait(self, seconds: float = 0) -> None:
        """Expose availability only during a real design-inbox wait."""
        with self.connect() as db:
            _ = db.execute(
                "INSERT OR REPLACE INTO design_presence VALUES (1,?)", (time.time() + seconds,)
            )

    def post_design_request(self, body: DesignRequestInput) -> DesignMessage:
        """Save a reference handoff and attachments exactly once in the local inbox."""
        if not body.message.strip() and not body.files:
            raise ValueError("Add a message, link or file for your agent.")
        payload_hash = digest(body.model_dump_json())
        decoded = decode_attachments(body.files)
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT payload_hash,document FROM design_chat WHERE request_id=?",
                (body.request_id,),
            ).fetchone()
            if existing:
                if existing[0] != payload_hash:
                    raise ConflictError("That request was already sent with different content.")
                return DesignMessage.model_validate_json(cast(str, existing[1]))
            cursor = db.execute(
                "INSERT INTO design_chat(request_id,payload_hash,document) VALUES (?,?,?)",
                (body.request_id, payload_hash, "{}"),
            )
            event_id = cast(int, cursor.lastrowid)
            attachment_folder = self.database.parent / "design-inbox" / body.request_id
            attachments: list[DesignFile] = []
            for index, (name, data) in enumerate(decoded):
                attachment_folder.mkdir(parents=True, exist_ok=True)
                path = attachment_folder / f"{index}.attachment"
                _ = path.write_bytes(data)
                attachments.append(
                    DesignFile(
                        name=name,
                        path=str(path.resolve()),
                        size=len(data),
                        sha256=hashlib.sha256(data).hexdigest(),
                    )
                )
            message = DesignMessage(
                event_id=event_id,
                author="user",
                message=body.message.strip(),
                created_at=datetime.now(UTC).isoformat(),
                files=attachments,
            )
            _ = db.execute(
                "UPDATE design_chat SET document=? WHERE id=?",
                (message.model_dump_json(), event_id),
            )
        return message

    def reply_design(
        self,
        event_id: int,
        message: str,
        request_id: str,
        status: Literal["working", "needs_input", "ready"],
        design_id: str | None = None,
    ) -> DesignMessage:
        """Respond to a design handoff and link a registered design when it is ready."""
        if not message.strip() or len(message) > 10_000 or not 8 <= len(request_id) <= 100:
            raise ValueError("Use a message of 1–10,000 characters and a unique request_id.")
        if status == "ready":
            if design_id is None:
                raise ValueError("Register the finished design and include its design_id.")
            _ = self.design(design_id)
        payload_hash = digest(json.dumps([event_id, message, status, design_id]))
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT payload_hash,document FROM design_chat WHERE request_id=?", (request_id,)
            ).fetchone()
            if previous:
                if previous[0] != payload_hash:
                    raise ConflictError(
                        "This reply request_id was already used with another payload."
                    )
                return DesignMessage.model_validate_json(cast(str, previous[1]))
            row = db.execute("SELECT document FROM design_chat WHERE id=?", (event_id,)).fetchone()
            if row is None:
                raise ValueError("Read the design inbox and reply to an existing user request.")
            original = DesignMessage.model_validate_json(cast(str, row[0]))
            if original.author != "user":
                raise ValueError("Reply to a user design request.")
            original.status, original.design_id = status, design_id
            _ = db.execute(
                "UPDATE design_chat SET document=? WHERE id=?",
                (original.model_dump_json(), event_id),
            )
            cursor = db.execute(
                "INSERT INTO design_chat(request_id,payload_hash,document) VALUES (?,?,?)",
                (request_id, payload_hash, "{}"),
            )
            reply = DesignMessage(
                event_id=cast(int, cursor.lastrowid),
                author="agent",
                message=message,
                created_at=datetime.now(UTC).isoformat(),
                reply_to=event_id,
                status=status,
                design_id=design_id,
            )
            _ = db.execute(
                "UPDATE design_chat SET document=? WHERE id=?",
                (reply.model_dump_json(), reply.event_id),
            )
        return reply
