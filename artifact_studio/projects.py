"""Portable local projects and audience exports with embedded presentation assets."""

import base64
import hashlib
import io
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Literal, cast
from urllib.parse import unquote, urlsplit

from pydantic import Field, TypeAdapter, field_validator, model_validator

from .css_assets import embed_css
from .libraries import LibraryFile, inventory
from .models import Deck, EditIntent, Feedback, FeedbackFile, SlideTarget, StrictModel
from .slide_actions import SlideActionRecord, SlideActionResult, recover_pending
from .storage import ConflictError, SlideMarkup, Store

PROJECT = Path(__file__).resolve().parent.parent
MAX_ARCHIVE = 100 * 1024 * 1024
MAX_EXPANDED = 300 * 1024 * 1024
MAX_FILES = 3000


class NoteDraft(StrictModel):
    """An unfinished note and its original concurrency baseline."""

    notes: str = Field(max_length=20_000)
    expected_notes: str = Field(max_length=20_000)
    expected_revision: int | None = Field(default=None, ge=0)
    pending_notes: str | None = Field(default=None, max_length=20_000)


class _PortableFeedbackDraft(StrictModel):
    """Portable files exclude the private proposal archive needed by an unsent comment."""

    @model_validator(mode="before")
    @classmethod
    def require_retained_proposal_context(cls, value: object) -> object:
        if isinstance(value, dict):
            document = cast(dict[str, object], value)
            if any(
                document.get(key)
                for key in (
                    "element_set_id",
                    "element_option_id",
                    "element_option_name",
                    "element_context_hash",
                )
            ):
                raise ValueError("Finish or clear proposal feedback before saving this project.")
            return document
        return value


class VoiceFeedbackDraft(_PortableFeedbackDraft):
    """One unsent dictated comment pinned to its original reviewed selection."""

    request_id: str = Field(min_length=8, max_length=100)
    message: str = Field(max_length=10_000)
    slide_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    context_slide_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    content_hash: str = Field(max_length=128)
    target: SlideTarget | None = None


class FeedbackDraft(_PortableFeedbackDraft):
    """An unsent comment with its original slide and element context."""

    feedback: str = Field(max_length=10_000)
    target: SlideTarget | None = None
    slide_id: str
    context_slide_id: str | None = None
    content_hash: str
    request_id: str
    edit_intent: EditIntent | None = None
    voice_segments: list[VoiceFeedbackDraft] = Field(default_factory=list, max_length=100)

    @field_validator("voice_segments")
    @classmethod
    def distinct_voice_requests(cls, values: list[VoiceFeedbackDraft]) -> list[VoiceFeedbackDraft]:
        """Keep a portable draft's retry identities unambiguous."""
        if len({item.request_id for item in values}) != len(values):
            raise ValueError("Dictated comments must have distinct request IDs.")
        return values


class QuestionDraft(StrictModel):
    """An unsent answer kept with its original slide and private project."""

    slide_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    text: str = Field(default="", max_length=10_000)
    choice: int | None = Field(default=None, ge=0, le=5)


class ClientState(StrictModel):
    """Browser-only unfinished work scoped to the exported presentation."""

    active_slide_id: str = ""
    view_zoom: int = Field(default=100, ge=10, le=400)
    editor_zoom: int = Field(default=100, ge=55, le=400)
    notes: dict[str, NoteDraft] = Field(default_factory=dict, max_length=100)
    feedback: dict[str, FeedbackDraft] = Field(default_factory=dict, max_length=100)
    open_panels: list[Literal["notes", "sources", "agent"]] = Field(
        default_factory=lambda: ["notes"]
    )
    presenter_notes: dict[str, dict[str, str]] = Field(default_factory=dict, max_length=100)
    question_answers: dict[str, QuestionDraft] = Field(default_factory=dict, max_length=500)
    collapsed_sections: list[str] = Field(default_factory=list, max_length=100)


class ProjectFile(StrictModel):
    """An exact bundled file and the asset timestamp used by review hashes."""

    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mtime_ns: int = Field(ge=0)


class RevisionRecord(StrictModel):
    """A retained historical slide document."""

    slide: str
    hash: str
    document: str


class OperationRecord(StrictModel):
    """A retained idempotency token for unfinished client retries."""

    request_id: str
    payload_hash: str
    result: str


class ProjectBundle(StrictModel):
    """Versioned project metadata independent of a particular SQLite database."""

    format: Literal["artifact-studio-project"] = "artifact-studio-project"
    version: Literal[1] = 1
    deck: Deck
    files: dict[str, ProjectFile]
    feedback: list[Feedback] = Field(default_factory=list)
    approvals: dict[str, str] = Field(default_factory=dict)
    seen: dict[str, str] = Field(default_factory=dict)
    revisions: list[RevisionRecord] = Field(default_factory=list)
    operations: list[OperationRecord] = Field(default_factory=list)
    slide_actions: list[SlideActionRecord] = Field(default_factory=list)
    mode: Literal["live", "batch"] = "live"
    client: ClientState = Field(default_factory=ClientState)
    workspace_rules: str = ""
    learned_preferences: str = ""


def _hash(data: bytes) -> str:
    """Hash bundled bytes without relying on ZIP metadata."""
    return hashlib.sha256(data).hexdigest()


def _safe_member(name: str) -> bool:
    """Accept only normalized relative archive paths with ordinary file components."""
    path = PurePosixPath(name)
    parts = path.parts[1:] if path.parts[:1] == ("files",) else path.parts
    asset = parts[:1] in {("assets",), ("brief-attachments",)}
    return (
        bool(name)
        and not path.is_absolute()
        and path.as_posix() == name
        and not (
            "\\" in name
            or any(ord(char) < 32 for char in name)
            or any(part in {".", ".."} for part in path.parts)
            or (not asset and any(part.startswith(".") for part in path.parts))
        )
    )


def _project_inventory(folder: Path) -> list[LibraryFile]:
    """Keep complete asset trees, including hidden files that participate in review hashes."""
    files = {
        item.path: item
        for item in inventory(
            folder, all_extensions=True, excluded=frozenset({"node_modules", "__pycache__"})
        )
    }
    for name in ("assets", "brief-attachments"):
        tree = folder / name
        if tree.is_symlink():
            raise ValueError("Copy linked assets into the project before saving.")
        for directory, subdirectories, names in tree.walk(follow_symlinks=False):
            if any((directory / entry).is_symlink() for entry in [*subdirectories, *names]):
                raise ValueError("Copy linked assets into the project before saving.")
            for entry in names:
                path = directory / entry
                relative = path.relative_to(folder).as_posix()
                files[relative] = LibraryFile(
                    path=relative, local_path=str(path), size=path.stat().st_size
                )
    return sorted(files.values(), key=lambda item: item.path)


class RootSettings(HTMLParser):
    """Keep inert design settings while rebuilding the trusted viewer shell."""

    def __init__(self) -> None:
        """Collect only attributes that affect the design language."""
        super().__init__()
        self.attributes: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Preserve presentation classes, direction, language and progress preference."""
        if tag == "html":
            self.attributes = [
                (name, value)
                for name, value in attrs
                if name in {"lang", "dir", "class", "data-progress"} and value is not None
            ]


def root_tag(original: str) -> str:
    """Preserve inert root settings shared by interactive and printed audience exports."""
    settings = RootSettings()
    settings.feed(original)
    attributes = "".join(
        f' {name}="{escape(value, quote=True)}"' for name, value in settings.attributes
    )
    return "<html" + attributes + ">"


def _shell(original: str) -> str:
    """Build a current viewer with only inert settings from the original document."""
    return (
        (PROJECT / "runtime/viewer.html")
        .read_text()
        .replace('<html lang="en">', root_tag(original))
    )


def _review_tokens(
    db: sqlite3.Connection, table: Literal["approvals", "slide_views"], deck_id: str
) -> dict[str, str]:
    """Read only the selected presentation's review tokens."""
    return {
        cast(str, row[0]): cast(str, row[1])
        for row in db.execute(f"SELECT slide,content_hash FROM {table} WHERE deck=?", (deck_id,))
    }


def save_project(store: Store, deck_id: str, client: ClientState) -> bytes:
    """Bundle a consistent presentation snapshot, private history and browser drafts."""
    output = io.BytesIO()
    with store.connect() as db, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        _ = db.execute("BEGIN IMMEDIATE")
        recover_pending(store, db, deck_id)
        deck = store.read(deck_id)
        files: dict[str, ProjectFile] = {}
        total = 0
        for item in _project_inventory(store.folder(deck_id)):
            path = Path(item.local_path)
            before = path.stat()
            if total + before.st_size > MAX_EXPANDED or len(files) >= MAX_FILES:
                raise ValueError("This project exceeds the 300 MB or 3000-file limit.")
            data = path.read_bytes()
            after = path.stat()
            if before.st_mtime_ns != after.st_mtime_ns or before.st_size != after.st_size:
                raise ConflictError("A project file changed while saving. Save again.")
            total += len(data)
            files[item.path] = ProjectFile(sha256=_hash(data), mtime_ns=before.st_mtime_ns)
            archive.writestr("files/" + item.path, data)
        if store.read(deck_id) != deck:
            raise ConflictError("The presentation changed while saving. Save again.")
        current = _project_inventory(store.folder(deck_id))
        if set(files) != {item.path for item in current} or any(
            Path(item.local_path).stat().st_mtime_ns != files[item.path].mtime_ns
            for item in current
        ):
            raise ConflictError("Project assets changed while saving. Save again.")
        comments = store.comments(deck_id)
        contexts = {
            cast(int, row[0]): cast(str | None, row[1])
            for row in db.execute(
                "SELECT id,context_slide_id FROM feedback WHERE deck=?", (deck_id,)
            )
        }
        for comment in comments:
            comment.context_slide_id = contexts[comment.event_id]
            for index, attachment in enumerate(comment.files):
                path = Path(attachment.path)
                data = path.read_bytes()
                if len(data) != attachment.size or _hash(data) != attachment.sha256:
                    raise ValueError("A review attachment is damaged or incomplete.")
                name = f"review-attachments/{comment.event_id}/{index}.attachment"
                if total + len(data) > MAX_EXPANDED or len(files) >= MAX_FILES:
                    raise ValueError("This project exceeds the 300 MB or 3000-file limit.")
                total += len(data)
                files[name] = ProjectFile(
                    sha256=attachment.sha256, mtime_ns=path.stat().st_mtime_ns
                )
                archive.writestr("files/" + name, data)
                attachment.path = name
                attachment.download_url = ""
        bundle = ProjectBundle(
            deck=deck,
            files=files,
            feedback=comments,
            approvals=_review_tokens(db, "approvals", deck_id),
            seen=_review_tokens(db, "slide_views", deck_id),
            revisions=[
                RevisionRecord.model_validate(dict(row))
                for row in db.execute(
                    "SELECT slide,hash,document FROM revisions WHERE deck=?", (deck_id,)
                )
            ],
            operations=[
                OperationRecord.model_validate(dict(row))
                for row in db.execute(
                    "SELECT request_id,payload_hash,result FROM operations WHERE deck=?", (deck_id,)
                )
            ],
            slide_actions=[
                SlideActionRecord.model_validate(dict(row))
                for row in db.execute(
                    "SELECT request_id,payload_hash,before_json,after_json,result_json,completed "
                    "FROM slide_actions WHERE deck=? AND completed=1 ORDER BY rowid",
                    (deck_id,),
                )
            ],
            mode=store.workflow(deck_id).mode,
            client=client,
            workspace_rules=store.authoring_instructions(),
            learned_preferences=store.learned_preferences(),
        )
        manifest = bundle.model_dump_json()
        if total + len(manifest.encode()) > MAX_EXPANDED:
            raise ValueError("The complete project exceeds 300 MB.")
        archive.writestr("project.json", manifest)
    data = output.getvalue()
    if len(data) > MAX_ARCHIVE:
        raise ValueError("Keep project files below 100 MB compressed.")
    return data


def _read_bundle(data: bytes) -> tuple[ProjectBundle, dict[str, bytes]]:
    """Validate every archive entry before writing any imported project files."""
    if len(data) > MAX_ARCHIVE:
        raise ValueError("Choose a project file below 100 MB.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [item.filename for item in entries]
            if len(entries) > MAX_FILES + 1 or len(set(names)) != len(names):
                raise ValueError("The project has too many or duplicate files.")
            if sum(item.file_size for item in entries) > MAX_EXPANDED:
                raise ValueError("The expanded project exceeds 300 MB.")
            for item in entries:
                if (
                    not _safe_member(item.filename)
                    or stat.S_ISLNK(item.external_attr >> 16)
                    or item.is_dir()
                ):
                    raise ValueError("The project contains an invalid file path.")
            bundle = ProjectBundle.model_validate_json(archive.read("project.json"))
            if not all(_safe_member(name) for name in bundle.files):
                raise ValueError("The project contains an invalid file path.")
            if set(names) != {"project.json", *("files/" + name for name in bundle.files)}:
                raise ValueError("The project file inventory does not match its contents.")
            files = {name: archive.read("files/" + name) for name in bundle.files}
            if any(_hash(content) != bundle.files[name].sha256 for name, content in files.items()):
                raise ValueError("A project file is damaged or incomplete.")
    except (zipfile.BadZipFile, KeyError, RuntimeError, EOFError) as error:
        raise ValueError("Choose a valid Artifact Studio project file.") from error
    if not {"index.html", "theme.css"}.issubset(files):
        raise ValueError("The project is missing its presentation or theme.")
    if len({slide.id for slide in bundle.deck.slides}) != len(bundle.deck.slides):
        raise ValueError("Project slide IDs must be unique.")
    if len({item.event_id for item in bundle.feedback}) != len(bundle.feedback):
        raise ValueError("Project message IDs must be unique.")
    attachment_paths: set[str] = set()
    for item in bundle.feedback:
        for index, attachment in enumerate(item.files):
            name = f"review-attachments/{item.event_id}/{index}.attachment"
            if attachment.path != name or name not in files:
                raise ValueError("A project review attachment is missing or outside its message.")
            if len(files[name]) != attachment.size or _hash(files[name]) != attachment.sha256:
                raise ValueError("A project review attachment is damaged or incomplete.")
            attachment_paths.add(name)
    if attachment_paths != {name for name in files if name.startswith("review-attachments/")}:
        raise ValueError("The project contains unreferenced private review files.")
    for slide in bundle.deck.slides:
        SlideMarkup().feed(slide.html)
    if len({item.request_id for item in bundle.slide_actions}) != len(bundle.slide_actions):
        raise ValueError("Project slide action request IDs must be unique.")
    for item in bundle.slide_actions:
        result = SlideActionResult.model_validate_json(item.result_json)
        if (
            item.completed != 1
            or result.operation_id != item.request_id
            or not 8 <= len(item.request_id) <= 100
        ):
            raise ValueError("Project slide action records are inconsistent.")
        for document in (item.before_json, item.after_json):
            historical = Deck.model_validate_json(document)
            if historical.id != bundle.deck.id:
                raise ValueError("Project slide action belongs to another presentation.")
            if len({slide.id for slide in historical.slides}) != len(historical.slides):
                raise ValueError("Project slide IDs must be unique.")
            for slide in historical.slides:
                SlideMarkup().feed(slide.html)
    return bundle, files


def open_project(store: Store, data: bytes) -> tuple[Deck, ClientState]:
    """Restore a local copy, remapping private history without replacing existing work."""
    bundle, files = _read_bundle(data)
    deck = bundle.deck.model_copy(deep=True)
    original_id = deck.id
    _ = store.folder(original_id)
    with store.connect() as db:
        _ = db.execute("BEGIN IMMEDIATE")
        if store.folder(deck.id).exists():
            deck.id = original_id[:80] + "-copy-" + uuid.uuid4().hex[:8]
            deck.title += " (copy)"
        target = store.folder(deck.id)
        with tempfile.TemporaryDirectory(prefix=".project-", dir=store.root) as temporary:
            staging = Path(temporary) / "deck"
            staging.mkdir()
            for name, content in files.items():
                if name.startswith("review-attachments/"):
                    continue
                path = staging / name
                path.parent.mkdir(parents=True, exist_ok=True)
                _ = path.write_bytes(content)
                os.utime(path, ns=(bundle.files[name].mtime_ns, bundle.files[name].mtime_ns))
            # The imported document supplies data; executable viewer code comes from this install.
            shell = _shell(files["index.html"].decode())
            _ = (staging / "index.html").write_text(
                shell.replace("{{DECK_JSON}}", deck.model_dump_json().replace("<", r"\u003c"))
            )
            for name in ("presentation.js", "icons.js", "slideshow.css"):
                _ = shutil.copy2(PROJECT / "runtime" / name, staging / name)
            # Rule snapshots remain references; primary workspace rules retain their authority.
            context = staging / "project-context"
            context.mkdir(exist_ok=True)
            _ = (context / "workspace-rules.md").write_text(bundle.workspace_rules)
            _ = (context / "learned-preferences.md").write_text(bundle.learned_preferences)
            # Opening a deck does not register or replace a shared design pack.
            (staging / "design.json").unlink(missing_ok=True)
            (staging / "assets").mkdir(exist_ok=True)
            staging.rename(target)
        try:
            _restore_review(store, db, deck, bundle, files)
        except Exception:
            shutil.rmtree(target)
            raise
    with (store.root / "README.md").open("a") as index:
        _ = index.write(
            f"\n- [{deck.title}]({deck.id}/index.html) · [Context]({deck.id}/README.md)\n"
        )
    return deck, bundle.client


def _restore_review(
    store: Store,
    db: sqlite3.Connection,
    deck: Deck,
    bundle: ProjectBundle,
    files: dict[str, bytes],
) -> None:
    """Restore only this deck's messages and review state, excluding active agent leases."""
    event_ids: dict[int, int] = {}
    question_keys = [
        item.question.key for item in bundle.feedback if item.question and item.question.key
    ]
    if len(question_keys) != len(set(question_keys)):
        raise ValueError("Project questions must have distinct identities.")
    for item in bundle.feedback:
        attachments = store.save_feedback_files(
            deck.id, [(file.name, files[file.path]) for file in item.files]
        )
        cursor = db.execute(
            "INSERT INTO feedback(deck,slide,author,message,created_at,content_hash,reply_to,"
            "target,released,batch_id,context_slide_id,files,edit_intent,question,"
            "element_set_id,element_option_id,element_context_hash,element_option_name) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                deck.id,
                item.slide_id or "",
                item.author,
                item.message,
                item.created_at,
                item.content_hash,
                None,
                item.target.model_dump_json() if item.target else None,
                item.released,
                item.batch_id,
                item.context_slide_id,
                TypeAdapter(list[FeedbackFile]).dump_json(attachments).decode(),
                item.edit_intent.model_dump_json() if item.edit_intent else None,
                item.question.model_dump_json() if item.question else None,
                item.element_set_id,
                item.element_option_id,
                item.element_context_hash,
                item.element_option_name,
            ),
        )
        event_ids[item.event_id] = cast(int, cursor.lastrowid)
    for item in bundle.feedback:
        if item.reply_to is not None:
            if item.reply_to not in event_ids:
                raise ValueError("A project reply is missing its original message.")
            _ = db.execute(
                "UPDATE feedback SET reply_to=? WHERE id=?",
                (event_ids[item.reply_to], event_ids[item.event_id]),
            )
        if item.released and item.author == "user":
            _ = db.execute("INSERT INTO delivery(event_id) VALUES (?)", (event_ids[item.event_id],))
    question_ids = {
        item.event_id: item
        for item in bundle.feedback
        if item.question is not None and item.author == "agent"
    }
    restored_drafts: dict[str, QuestionDraft] = {}
    for old_id, draft in bundle.client.question_answers.items():
        question = question_ids.get(int(old_id)) if old_id.isdecimal() else None
        if question is None or question.slide_id != draft.slide_id:
            raise ValueError("An answer draft does not belong to a question on its slide.")
        if draft.choice is not None and (
            question.question is None or draft.choice >= len(question.question.options)
        ):
            raise ValueError("An answer draft selects an unavailable option.")
        restored_drafts[str(event_ids[int(old_id)])] = draft
    bundle.client.question_answers = restored_drafts
    for table, tokens in (("approvals", bundle.approvals), ("slide_views", bundle.seen)):
        _ = db.executemany(
            f"INSERT INTO {table} VALUES (?,?,?)",
            [(deck.id, slide, token) for slide, token in tokens.items()],
        )
    _ = db.executemany(
        "INSERT INTO revisions VALUES (?,?,?,?)",
        [(deck.id, item.slide, item.hash, item.document) for item in bundle.revisions],
    )
    _ = db.executemany(
        "INSERT INTO operations VALUES (?,?,?,?)",
        [
            (
                deck.id,
                item.request_id,
                item.payload_hash,
                str(event_ids.get(int(item.result), int(item.result)))
                if item.result.isdecimal()
                else item.result,
            )
            for item in bundle.operations
        ],
    )
    for item in bundle.slide_actions:
        before = Deck.model_validate_json(item.before_json)
        after = Deck.model_validate_json(item.after_json)
        before.id = deck.id
        after.id = deck.id
        _ = db.execute(
            "INSERT INTO slide_actions "
            "(deck,request_id,payload_hash,before_json,after_json,result_json,completed) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                deck.id,
                item.request_id,
                item.payload_hash,
                before.model_dump_json(),
                after.model_dump_json(),
                item.result_json,
                item.completed,
            ),
        )
    _ = db.execute("INSERT INTO workflow(deck,mode) VALUES (?,?)", (deck.id, bundle.mode))
    store.seed_slide_views(db, deck)


class AssetEmbedder:
    """Resolve only bundled local assets and inline them as portable data URLs."""

    def __init__(self, folder: Path) -> None:
        """Remember the export boundary and deduplicate repeated file reads."""
        self.folder = folder.resolve()
        self.cache: dict[Path, str] = {}
        self.visiting: set[Path] = set()
        self.css_bytes = 0
        self.css_limit = 64 * 1024 * 1024

    def asset(self, url: str, directory: Path | None = None) -> str:
        """Embed a file relative to its declaring CSS or HTML document."""
        if url.startswith(("data:", "#")):
            return url
        path = self.local_path(url, directory)
        parsed = urlsplit(url)
        if path in self.visiting:
            raise ValueError("An export asset contains a circular dependency.")
        if path not in self.cache:
            self.visiting.add(path)
            content = path.read_bytes()
            if path.suffix.lower() == ".svg":
                content = self.markup(content.decode(), path.parent).encode()
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.cache[path] = f"data:{mime};base64," + base64.b64encode(content).decode()
            self.visiting.remove(path)
        return self.cache[path] + ("#" + parsed.fragment if parsed.fragment else "")

    def local_path(self, url: str, directory: Path | None = None) -> Path:
        """Resolve a dependency within the presentation folder boundary."""
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc:
            raise ValueError("Standalone export needs local assets. Download remote assets first.")
        path = ((directory or self.folder) / unquote(parsed.path)).resolve()
        if not path.is_relative_to(self.folder) or not path.is_file():
            raise ValueError(f"An export asset is missing or outside the project: {url}")
        return path

    def css(self, text: str, directory: Path | None = None) -> str:
        """Inline local font and image dependencies without changing animations."""
        self.css_bytes += len(text.encode())
        if self.css_bytes > self.css_limit:
            raise ValueError("CSS expansion exceeds the portable document size limit.")

        def import_css(url: str) -> str:
            path = self.local_path(url, directory)
            if path in self.visiting:
                raise ValueError("CSS imports contain a circular dependency.")
            if len(self.visiting) >= 64:
                raise ValueError("CSS imports exceed the 64-level nesting limit.")
            self.visiting.add(path)
            try:
                return self.css(path.read_text(), path.parent)
            finally:
                self.visiting.remove(path)

        return embed_css(text, lambda url: self.asset(url, directory), import_css)

    def markup(self, text: str, directory: Path | None = None) -> str:
        """Inline HTML/SVG dependencies while preserving ordinary hyperlinks and markup."""
        parser = EmbeddedMarkup(self, directory)
        parser.feed(text)
        parser.close()
        return "".join(parser.parts)

    def srcset(self, text: str, directory: Path | None) -> str:
        """Embed responsive candidates while preserving commas within data URLs."""
        result: list[str] = []
        position = 0
        while position < len(text):
            while position < len(text) and (text[position].isspace() or text[position] == ","):
                position += 1
            if position == len(text):
                break
            start = position
            data_url = text.startswith("data:", position)
            while (
                position < len(text)
                and not text[position].isspace()
                and (data_url or text[position] != ",")
            ):
                position += 1
            url = text[start:position]
            descriptor = ""
            if not url.endswith(","):
                start = position
                while position < len(text) and text[position] != ",":
                    position += 1
                descriptor = text[start:position].strip()
            result.append(
                self.asset(url.rstrip(","), directory) + (" " + descriptor if descriptor else "")
            )
        return ", ".join(result)


class EmbeddedMarkup(HTMLParser):
    """Rewrite asset-bearing attributes without normalizing presentation markup."""

    def __init__(self, embedder: AssetEmbedder, directory: Path | None) -> None:
        """Collect the original token stream and inline dependencies in place."""
        super().__init__(convert_charrefs=False)
        self.embedder = embedder
        self.directory = directory
        self.parts: list[str] = []
        self.in_style = False
        self.open_tags: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Preserve links while embedding images, responsive sources and inline backgrounds."""

        def rewrite(match: re.Match[str]) -> str:
            name = match[2].lower()
            if name not in {"src", "href", "xlink:href", "srcset", "style", "poster", "srcdoc"}:
                return match[0]
            value = unescape(next(value for value in match.group(4, 5, 6) if value is not None))
            if name == "srcdoc":
                result = self.embedded_document(value)
            elif name == "style":
                result = self.embedder.css(value, self.directory)
            elif name == "srcset":
                result = self.embedder.srcset(value, self.directory)
            elif (
                tag == "a"
                and name == "href"
                and urlsplit(value).scheme in {"https", "http", "mailto", "tel"}
            ):
                result = value
            else:
                result = self.embedder.asset(value, self.directory)
            quote = '"' if match[4] is not None else "'" if match[5] is not None else '"'
            return match[1] + match[2] + match[3] + quote + escape(result, quote=True) + quote

        raw = self.get_starttag_text() or ""
        name = re.match(r"<\s*([^\s/>]+)", raw)
        if name and tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.open_tags.append((tag, name[1]))
        self.parts.append(
            re.sub(
                r"(\s+)([^\s=/>]+)(\s*=\s*)(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))",
                rewrite,
                raw,
                flags=re.I | re.S,
            )
        )
        self.in_style = tag == "style"

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Preserve self-closing SVG elements and their original token syntax."""
        self.handle_starttag(tag, attrs)
        if self.open_tags and self.open_tags[-1][0] == tag:
            _ = self.open_tags.pop()
        self.in_style = False

    def embedded_document(self, html: str) -> str:
        """Leave embedded documents untouched unless the custom-document preparer opts in."""
        return html

    def handle_endtag(self, tag: str) -> None:
        """End a preserved markup element or inline CSS block."""
        name = tag
        for index in range(len(self.open_tags) - 1, -1, -1):
            if self.open_tags[index][0] == tag:
                name = self.open_tags[index][1]
                del self.open_tags[index:]
                break
        self.parts.append(f"</{name}>")
        self.in_style = False

    def handle_data(self, data: str) -> None:
        """Preserve text or embed assets inside slide-local style blocks."""
        self.parts.append(self.embedder.css(data, self.directory) if self.in_style else data)

    def handle_entityref(self, name: str) -> None:
        """Retain named text entities."""
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        """Retain numeric text entities."""
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        """Retain non-executable markup comments."""
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        """Retain document declarations in embedded SVG files."""
        self.parts.append(f"<!{decl}>")


def audience_html(
    store: Store, deck_id: str, *, printing: bool = False, include_hidden: bool = True
) -> str:
    """Build one offline audience document, excluding private notes and conversations."""
    deck = store.read(deck_id).model_copy(deep=True)
    deck.sections = []
    if printing and not include_hidden:
        deck.slides = [slide for slide in deck.slides if not slide.hidden]
        if not deck.slides:
            raise ValueError(
                "No visible slides to export. Include hidden slides or unhide a slide."
            )
    folder = store.folder(deck_id)
    embedder = AssetEmbedder(folder)
    theme = embedder.css((folder / "theme.css").read_text()).replace("<", r"\3c ")
    for slide in deck.slides:
        slide.html = embedder.markup(slide.html)
        slide.notes = ""
        slide.agent.summary = ""
        slide.agent.questions = []
        slide.agent.alternatives = []
        for source in slide.sources:
            if not urlsplit(source.url).scheme:
                source.url = ""
    if printing:
        return _print_document(deck, theme, (folder / "index.html").read_text())
    deck.id = "export-" + uuid.uuid4().hex
    shell = _shell((folder / "index.html").read_text())
    shell = shell.replace("<html", '<html data-export="audience"', 1)
    shell = shell.replace(
        '<link rel="stylesheet" href="theme.css">',
        "<style>"
        + theme
        + '</style><style id="slideshow-styles">'
        + (PROJECT / "runtime/slideshow.css").read_text()
        + "</style>",
    )
    shell = shell.replace("{{DECK_JSON}}", deck.model_dump_json().replace("<", r"\u003c"))
    for name in ("icons.js", "presentation.js"):
        script = (PROJECT / "runtime" / name).read_text().replace("</script", r"<\/script")
        shell = shell.replace(f'<script src="{name}"></script>', f"<script>{script}</script>")
    return shell


def _print_document(deck: Deck, theme: str, original: str) -> str:
    """Lay out every slide on its own widescreen page with all builds visible."""
    slides = "".join(
        f'<section class="print-page" data-hidden="{str(slide.hidden).lower()}">'
        + slide.html
        + "</section>"
        for slide in deck.slides
    )
    styles = (PROJECT / "runtime/print.css").read_text()
    script = (PROJECT / "runtime/print.js").read_text()
    interactive = [slide.interactive_html for slide in deck.slides]
    runtime = (PROJECT / "runtime/presentation.js").read_text().replace("</script", r"<\/script")
    return (
        "<!doctype html>"
        + root_tag(original)
        + '<head><meta charset="utf-8"><title>'
        + escape(deck.title)
        + "</title><style>"
        + theme
        + "</style><style>"
        + styles
        + '</style></head><body><div class="print-actions">'
        + '<label><input id="print-include-hidden" type="checkbox" checked> '
        + 'Include hidden slides</label><button id="print" disabled>Save as PDF</button></div>'
        + '<p id="print-empty" class="print-empty" role="status" hidden>'
        + "No visible slides. Include hidden slides or unhide a slide before exporting.</p>"
        + slides
        + '<script id="interactive-data" type="application/json">'
        + json.dumps(interactive).replace("<", r"\u003c")
        + "</script><script data-builds-only>"
        + runtime
        + "</script><script>"
        + script
        + "</script></body></html>"
    )
