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
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal, cast

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
    ElementTarget,
    Feedback,
    FeedbackFeed,
    FeedbackWorkflow,
    Slide,
    SlidePatch,
    SlideView,
)

DATA = re.compile(r'(<script id="deck-data" type="application/json">)(.*?)(</script>)', re.S)
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")
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


class ConflictError(ValueError):
    """The caller edited an outdated revision or reused an operation ID."""


class SlideMarkup(HTMLParser):
    """Reject executable markup and external asset loads in agent-authored slides."""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Allow presentation markup while keeping code in the trusted runtime."""
        if tag.lower() in {
            "script",
            "style",
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
            raise ValueError(f"Use presentation markup, not <{tag}>.")
        for name, value in attrs:
            text = (value or "").strip()
            if name.lower().startswith("on") or name.lower() in {"srcdoc", "srcset"}:
                raise ValueError("Executable or alternate-source attributes are not allowed.")
            if name.lower() in {"src", "href", "xlink:href"} and (
                not (text.startswith("assets/") or text.startswith("#")) or ".." in text
            ):
                raise ValueError("Slide assets must use local assets/ paths or SVG anchors.")
            if name.lower() == "style" and re.search(r"url\s*\(|expression|@import", text, re.I):
                raise ValueError("Use shared theme classes for image backgrounds.")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Apply the same rules to self-closing SVG and image elements."""
        self.handle_starttag(tag, attrs)


def digest(value: str) -> str:
    """Return a stable SHA-256 token for concurrency and approval checks."""
    return hashlib.sha256(value.encode()).hexdigest()


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
                CREATE TABLE IF NOT EXISTS workspace_settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS approvals (
                    deck TEXT, slide TEXT, content_hash TEXT,
                    PRIMARY KEY(deck, slide));
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY, deck TEXT NOT NULL, slide TEXT NOT NULL,
                    author TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL,
                    content_hash TEXT NOT NULL, reply_to INTEGER);
                CREATE TABLE IF NOT EXISTS operations (
                    deck TEXT, request_id TEXT, payload_hash TEXT, result TEXT,
                    PRIMARY KEY(deck, request_id));
                CREATE TABLE IF NOT EXISTS delivery (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow (
                    deck TEXT PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'live',
                    waiting_until REAL NOT NULL DEFAULT 0, last_check TEXT);
                CREATE TABLE IF NOT EXISTS revisions (
                    deck TEXT, slide TEXT, hash TEXT, document TEXT,
                    PRIMARY KEY(deck, slide, hash));
                CREATE TABLE IF NOT EXISTS design_chat (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT UNIQUE NOT NULL,
                    payload_hash TEXT NOT NULL, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS design_presence (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), waiting_until REAL NOT NULL);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(feedback)")}
            if "target" not in columns:
                _ = db.execute("ALTER TABLE feedback ADD COLUMN target TEXT")
            if "released" not in columns:
                _ = db.execute("ALTER TABLE feedback ADD COLUMN released INTEGER DEFAULT 1")
            if "batch_id" not in columns:
                _ = db.execute("ALTER TABLE feedback ADD COLUMN batch_id TEXT")
            if "context_slide_id" not in columns:
                _ = db.execute("ALTER TABLE feedback ADD COLUMN context_slide_id TEXT")
            _ = db.execute(
                "INSERT OR IGNORE INTO delivery(event_id) SELECT id FROM feedback "
                "WHERE released=1 AND author='user' ORDER BY id"
            )

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
            raise ValueError("This presentation has no Slide Studio document yet.")
        deck = Deck.model_validate_json(match[2])
        if len({slide.id for slide in deck.slides}) != len(deck.slides):
            raise ValueError("Slide IDs must be unique.")
        return deck

    def write(self, deck_id: str, deck: Deck) -> None:
        """Replace the embedded source atomically while retaining the HTML shell."""
        target = self.folder(deck_id) / "index.html"
        source = target.read_text()
        document = deck.model_dump_json(indent=2).replace("<", r"\u003c")
        updated, count = DATA.subn(lambda match: match[1] + document + match[3], source)
        if count != 1:
            raise ValueError("Expected one canonical deck document.")
        with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, delete=False) as file:
            _ = file.write(updated)
            temporary = Path(file.name)
        temporary.replace(target)

    def visual_hash(self, deck_id: str) -> str:
        """Invalidate approvals when shared styling or bundled assets change."""
        folder = self.folder(deck_id)
        assets = [
            (str(path.relative_to(folder)), path.stat().st_size, path.stat().st_mtime_ns)
            for path in sorted((folder / "assets").rglob("*"))
            if path.is_file()
        ]
        return digest((folder / "theme.css").read_text() + json.dumps(assets))

    def content_hash(self, deck_id: str, slide: Slide) -> str:
        """Hash audience-visible content independently of private notes and discussion."""
        return digest(
            slide.model_dump_json(include={"title", "html", "sources"}) + self.visual_hash(deck_id)
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
                "SELECT feedback.*,delivery.id AS delivery_id FROM feedback LEFT JOIN delivery "
                "ON feedback.id=delivery.event_id WHERE deck=? AND feedback.id>? "
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
                target=ElementTarget.model_validate_json(cast(str, row["target"]))
                if row["target"]
                else None,
            )
            for row in rows
        ]

    def view_slide(self, deck_id: str, slide: Slide, *, include_pending: bool = True) -> SlideView:
        """Combine canonical content with approval and discussion state."""
        content_hash = self.content_hash(deck_id, slide)
        with self.connect() as db:
            row = db.execute(
                "SELECT content_hash FROM approvals WHERE deck=? AND slide=?", (deck_id, slide.id)
            ).fetchone()
        return SlideView(
            **slide.model_dump(),
            expected_hash=digest(slide.model_dump_json()),
            content_hash=content_hash,
            approved=row is not None and row[0] == content_hash,
            feedback=[
                item
                for item in self.comments(deck_id)
                if item.slide_id == slide.id and (include_pending or item.released)
            ],
        )

    def view(self, deck_id: str, *, include_pending: bool = True) -> DeckView:
        """Return a review-ready snapshot of a presentation."""
        deck = self.read(deck_id)
        context_file = self.folder(deck_id) / "README.md"
        context = context_file.read_text() if context_file.exists() else ""
        return DeckView(
            id=deck_id,
            title=deck.title,
            design_id=deck.design_id,
            slides=[
                self.view_slide(deck_id, slide, include_pending=include_pending)
                for slide in deck.slides
            ],
            context_markdown=context,
            context_hash=digest(context),
            workflow=self.workflow(deck_id),
            feedback=[
                item
                for item in self.comments(deck_id)
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
            with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
                _ = file.write(markdown)
                temporary = Path(file.name)
            temporary.replace(path)
        return digest(markdown)

    def list_decks(self) -> list[dict[str, str]]:
        """List compatible presentations without changing older example decks."""
        result: list[dict[str, str]] = []
        for file in sorted(self.root.glob("*/index.html")):
            if 'id="deck-data"' not in file.read_text():
                continue
            deck = self.read(file.parent.name)
            result.append({"id": file.parent.name, "title": deck.title})
        return result

    def revise(
        self, deck_id: str, slide_id: str, expected_hash: str, request_id: str, patch: SlidePatch
    ) -> Slide:
        """Apply an idempotent revision with optimistic concurrency protection."""
        if not 8 <= len(request_id) <= 100:
            raise ValueError("Use a unique request_id of 8–100 characters.")
        payload_hash = digest(slide_id + expected_hash + patch.model_dump_json())
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT payload_hash,result FROM operations WHERE deck=? AND request_id=?",
                (deck_id, request_id),
            ).fetchone()
            if prior is not None:
                if prior[0] != payload_hash:
                    raise ConflictError("This request_id was already used with another payload.")
                return Slide.model_validate_json(cast(str, prior[1]))
            deck = self.read(deck_id)
            slide = self.locate(deck, slide_id)
            if digest(slide.model_dump_json()) != expected_hash:
                raise ConflictError("Slide changed. Read it again and merge your revision.")
            changes = patch.model_dump(exclude_none=True)
            revised = Slide.model_validate(slide.model_dump() | changes)
            SlideMarkup().feed(revised.html)
            if (
                revised.html != slide.html
                or revised.title != slide.title
                or revised.sources != slide.sources
            ):
                revised.revision = slide.revision + 1
            _ = db.execute(
                "INSERT OR IGNORE INTO revisions VALUES (?,?,?,?)",
                (deck_id, slide_id, expected_hash, slide.model_dump_json()),
            )
            deck.slides[deck.slides.index(slide)] = revised
            self.write(deck_id, deck)
            _ = db.execute(
                "INSERT INTO operations VALUES (?,?,?,?)",
                (deck_id, request_id, payload_hash, revised.model_dump_json()),
            )
        return revised

    def approve(self, deck_id: str, slide_id: str, content_hash: str, approved: bool) -> None:
        """Record only the exact slide version approved through the human UI."""
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            slide = self.locate(self.read(deck_id), slide_id)
            if self.content_hash(deck_id, slide) != content_hash:
                raise ConflictError("The slide changed before approval. Review the new version.")
            _ = db.execute(
                "INSERT OR REPLACE INTO approvals VALUES (?,?,?)",
                (deck_id, slide_id, content_hash if approved else ""),
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
        target: ElementTarget | None = None,
        context_slide_id: str | None = None,
    ) -> str:
        """Save a comment or reply exactly once, retaining its original slide context."""
        if not message.strip():
            raise ValueError("Feedback must not be empty.")
        author = "agent" if reply_to is not None else "user"
        target_json = target.model_dump_json() if target else None
        payload_hash = digest(
            json.dumps(
                [slide_id, message, content_hash, reply_to, target_json]
                + ([context_slide_id] if context_slide_id is not None else [])
            )
        )
        stored_slide_id = slide_id or ""
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT payload_hash,result FROM operations WHERE deck=? AND request_id=?",
                (deck_id, request_id),
            ).fetchone()
            if prior is not None:
                if prior[0] != payload_hash:
                    raise ConflictError("Request ID already belongs to another action.")
                return cast(str, prior[1])
            deck = self.read(deck_id)
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
            released = author == "agent" or self.workflow(deck_id).mode == "live"
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
                context_slide_id=context_slide_id,
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
    ) -> str:
        """Append a message and its delivery record within the caller's transaction."""
        cursor = db.execute(
            "INSERT INTO feedback(deck,slide,author,message,created_at,content_hash,"
            "reply_to,target,context_slide_id,released) VALUES (?,?,?,?,?,?,?,?,?,?)",
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
            ),
        )
        if released and author == "user":
            _ = db.execute("INSERT INTO delivery(event_id) VALUES (?)", (cursor.lastrowid,))
        return str(cursor.lastrowid)

    def save_notes(self, deck_id: str, slide_id: str, notes: str, expected_notes: str) -> None:
        """Preserve simultaneous content edits while saving human speaker notes."""
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            deck = self.read(deck_id)
            slide = self.locate(deck, slide_id)
            if slide.notes != expected_notes:
                raise ConflictError("Notes changed in another window. Copy your edit and refresh.")
            slide.notes = notes
            self.write(deck_id, deck)

    def designs(self) -> list[DesignView]:
        """Discover reusable designs and validate the files needed for deck creation."""
        result: list[DesignView] = []
        for manifest in sorted(self.root.glob("*/design.json")):
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
        with self.connect() as db:
            _ = db.execute(
                "INSERT OR REPLACE INTO workspace_settings VALUES ('default_design', ?)",
                (design_id,),
            )
        return design_id

    def design(self, design_id: str) -> DesignView:
        """Resolve an explicitly selected design without hidden session state."""
        for design in self.designs():
            if design.id == design_id:
                return design
        raise ValueError(f"Unknown design '{design_id}'. Call list_designs to choose a design.")

    def register_design(self, folder_id: str, manifest: DesignManifest) -> DesignView:
        """Validate and register an agent-prepared design without replacing an existing brand."""
        folder = self.folder(folder_id)
        if not folder.is_dir():
            raise ValueError("Prepare the reference-based design folder before registering it.")
        for name in DESIGN_FILES:
            if not (folder / name).exists():
                raise ValueError(f"The prepared design is missing {name}.")
        if self.read(folder_id).design_id != manifest.id:
            raise ValueError("Set the example deck's design_id to the chosen design ID first.")
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            existing = next((item for item in self.designs() if item.id == manifest.id), None)
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

    def create(self, deck_id: str, title: str, design_id: str = "", brief: str = "") -> Deck:
        """Create a branded starting canvas with a durable brief for the authoring agent."""
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*", deck_id):
            raise ValueError("Use a dated presentation ID: YYYY-MM-DD-short-title.")
        if not title.strip() or len(title) > 160:
            raise ValueError("Use a presentation title of 1–160 characters.")
        brief = brief.strip()
        if len(brief) > 10_000:
            raise ValueError("Keep the presentation brief under 10,000 characters.")
        design_id = design_id or self.default_design_id()
        if not design_id:
            raise ValueError("Add a design from a PPTX, URL or reference before creating slides.")
        design = self.design(design_id)
        target = self.folder(deck_id)
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            if target.exists():
                raise ConflictError("That folder already exists. Choose another presentation ID.")
            template = self.folder(design.folder_id)
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
            deck = self.read(deck_id)
            deck.id, deck.title = deck_id, title
            deck.design_id = design_id
            deck.slides = deck.slides[:1]
            for slide in deck.slides:
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
            _ = (target / "README.md").write_text(
                f"# {title}\n\nCreated from the {design.name} design language. "
                "Canonical file: index.html.\n\n"
                f"## Purpose and context\n\n{brief or 'Awaiting the human’s brief.'}\n\n"
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
                _ = self.append_feedback(db, deck_id, "", "user", brief, "", released=True)
            index = self.root / "README.md"
            with index.open("a") as file:
                _ = file.write(
                    f"\n- [{title}]({deck_id}/index.html) · [Context]({deck_id}/README.md)\n"
                )
        return deck

    def add_slide(self, deck_id: str, slide: Slide) -> Slide:
        """Append any freely composed HTML/SVG slide without imposing a layout catalogue."""
        SlideMarkup().feed(slide.html)
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            deck = self.read(deck_id)
            if any(existing.id == slide.id for existing in deck.slides):
                raise ConflictError("Slide ID already exists. Read it rather than adding it twice.")
            if len(deck.slides) >= 100:
                raise ValueError("A presentation supports at most 100 slides.")
            slide.revision = 1
            deck.slides.append(slide)
            self.write(deck_id, deck)
        return slide

    def reorder(self, deck_id: str, slide_ids: list[str]) -> Deck:
        """Reorder exactly the current slides while preserving their stable IDs."""
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            deck = self.read(deck_id)
            lookup = {slide.id: slide for slide in deck.slides}
            if len(slide_ids) != len(lookup) or set(slide_ids) != set(lookup):
                raise ConflictError("Provide every current slide ID exactly once.")
            deck.slides = [lookup[slide_id] for slide_id in slide_ids]
            self.write(deck_id, deck)
        return deck

    def remove_slide(self, deck_id: str, slide_id: str, expected_hash: str) -> None:
        """Remove a requested slide while retaining its source in revision history."""
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            deck = self.read(deck_id)
            slide = self.locate(deck, slide_id)
            if len(deck.slides) == 1:
                raise ValueError("Keep at least one slide in the presentation.")
            if digest(slide.model_dump_json()) != expected_hash:
                raise ConflictError("Slide changed. Read it before removing it.")
            _ = db.execute(
                "INSERT OR IGNORE INTO revisions VALUES (?,?,?,?)",
                (deck_id, slide_id, expected_hash, slide.model_dump_json()),
            )
            deck.slides.remove(slide)
            self.write(deck_id, deck)

    def workflow(self, deck_id: str) -> FeedbackWorkflow:
        """Read delivery mode and bounded listening leases; never claim an idle agent is active."""
        with self.connect() as db:
            row = db.execute("SELECT * FROM workflow WHERE deck=?", (deck_id,)).fetchone()
            pending = db.execute(
                "SELECT COUNT(*) FROM feedback WHERE deck=? AND author='user' AND released=0",
                (deck_id,),
            ).fetchone()[0]
        return FeedbackWorkflow(
            mode=cast(Literal["live", "batch"], row["mode"]) if row else "live",
            pending=cast(int, pending),
            agent_waiting=bool(row and row["waiting_until"] > time.time()),
            last_agent_check=cast("str | None", row["last_check"]) if row else None,
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
        messages = [DesignMessage.model_validate_json(cast(str, row[0])) for row in rows]
        return DesignInbox(
            messages=messages,
            next_cursor=max((item.event_id for item in messages), default=after_event_id),
            agent_waiting=max(
                cast(float, deck_wait or 0), cast(float, design_wait[0] if design_wait else 0)
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
        decoded: list[tuple[str, bytes]] = []
        for file in body.files:
            name = Path(file.name.replace("\\", "/")).name
            if name in {"", ".", ".."}:
                raise ValueError("Choose a file with a valid filename.")
            try:
                data = base64.b64decode(file.data_base64, validate=True)
            except binascii.Error as error:
                raise ValueError("The attachment could not be read. Attach it again.") from error
            if len(data) > 25_000_000:
                raise ValueError("Keep each attachment under 25 MB.")
            decoded.append((name, data))
        if sum(len(data) for _, data in decoded) > 50_000_000:
            raise ValueError("Keep the combined attachments under 50 MB.")
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
                path = attachment_folder / f"{index}-{name}"
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
