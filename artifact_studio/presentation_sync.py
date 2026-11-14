"""Portable, private-state-free representation of an in-place presentation.

Each slide is separate so independent edits can be compared. Incoming changes
are staged privately and recovered before later presentation edits.
"""

import json
import sqlite3
from pathlib import Path
from typing import Annotated, Self

from pydantic import Field, model_validator

from .artifacts import MAX_BUNDLE_SIZE, MAX_FILES
from .models import Deck, DeckSection, Slide, StrictModel
from .presentation_files import bounded_read
from .project_sync import _checked_bundle, _file_path  # pyright: ignore[reportPrivateUsage]
from .storage import Store


class PortableSlide(StrictModel):
    """Audience source only; local notes, agent context and revision anchors stay local."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    title: str = Field(min_length=1, max_length=160)
    html: str = Field(min_length=1, max_length=150_000)
    interactive_html: str = Field(default="", max_length=2_000_000)
    hidden: bool = Field(default=False, strict=True)

    @classmethod
    def from_slide(cls, slide: Slide) -> PortableSlide:
        return cls(
            id=slide.id,
            title=slide.title,
            html=slide.html,
            interactive_html=slide.interactive_html,
            hidden=slide.hidden,
        )


class PortableDeck(StrictModel):
    """Portable identity and ordering; no local review or authoring context."""

    format: int = Field(default=1, strict=True, ge=1, le=1)
    title: str = Field(min_length=1, max_length=160)
    slides: list[Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")]] = Field(
        min_length=1, max_length=100
    )
    sections: list[DeckSection] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if len(set(self.slides)) != len(self.slides):
            raise ValueError("Slide IDs must be unique.")
        if (
            self.sections
            and [item for section in self.sections for item in section.slide_ids] != self.slides
        ):
            raise ValueError("Sections must partition slides in order.")
        if len({section.id for section in self.sections}) != len(self.sections):
            raise ValueError("Section IDs must be unique.")
        return self


def _json_bytes(value: StrictModel) -> bytes:
    return (json.dumps(value.model_dump(), ensure_ascii=False, indent=2) + "\n").encode()


def portable_snapshot(store: Store, deck_id: str) -> dict[str, bytes]:
    """Read only portable source from a stable, caller-locked presentation."""
    folder = store.folder(deck_id)
    if (store.root / deck_id).is_symlink():
        raise ValueError("Presentation directories cannot be symlinks.")
    deck = store.read(deck_id)
    manifest = PortableDeck(
        title=deck.title, slides=[slide.id for slide in deck.slides], sections=deck.sections
    )
    files = {"presentation.json": _json_bytes(manifest)}
    for slide in deck.slides:
        files[f"slides/{slide.id}.json"] = _json_bytes(PortableSlide.from_slide(slide))
    files["theme.css"] = bounded_read(folder / "theme.css")
    assets = folder / "assets"
    if assets.is_symlink() or not assets.is_dir():
        raise ValueError("Presentation assets must be a real directory.")
    for path in sorted(assets.rglob("*")):
        if path.is_symlink():
            raise ValueError("Presentation assets cannot contain symlinks.")
        if path.is_dir():
            continue
        name = path.relative_to(folder).as_posix()
        _ = _file_path(name)
        files[name] = bounded_read(path)
        if len(files) > MAX_FILES or sum(map(len, files.values())) > MAX_BUNDLE_SIZE:
            raise ValueError("Presentation source exceeds the synchronization limits.")
    _checked_bundle(files)
    return files


def parse_portable(files: dict[str, bytes]) -> tuple[PortableDeck, list[PortableSlide]]:
    """Validate a complete portable presentation without touching local state."""
    _checked_bundle(files)
    try:
        manifest = PortableDeck.model_validate_json(files["presentation.json"])
    except KeyError as exc:
        raise ValueError("Presentation manifest is missing.") from exc
    slides: list[PortableSlide] = []
    for slide_id in manifest.slides:
        try:
            slide = PortableSlide.model_validate_json(files[f"slides/{slide_id}.json"])
        except KeyError as exc:
            raise ValueError("Presentation slide source is missing.") from exc
        if slide.id != slide_id:
            raise ValueError("Slide identity does not match its source path.")
        slides.append(slide)
    expected = {"presentation.json", "theme.css"} | {
        f"slides/{item}.json" for item in manifest.slides
    }
    if "theme.css" not in files or any(
        name not in expected and not name.startswith("assets/") for name in files
    ):
        raise ValueError("Unexpected or missing presentation source file.")
    # Validate the resulting canonical deck constraints before any local write.
    _ = Deck(
        id="portable-validation",
        title=manifest.title,
        sections=manifest.sections,
        slides=[
            Slide(
                id=slide.id,
                title=slide.title,
                html=slide.html,
                interactive_html=slide.interactive_html,
                hidden=slide.hidden,
            )
            for slide in slides
        ],
    )
    return manifest, slides


def canonical_portable(files: dict[str, bytes]) -> dict[str, bytes]:
    """Compare JSON by validated content so formatting does not create sync loops."""
    manifest, slides = parse_portable(files)
    normalized = files.copy()
    normalized["presentation.json"] = _json_bytes(manifest)
    for slide in slides:
        normalized[f"slides/{slide.id}.json"] = _json_bytes(slide)
    return normalized


def prepare_portable_deck(
    current: Deck, files: dict[str, bytes], *, retained: dict[str, Slide] | None = None
) -> Deck:
    """Build a new deck while retaining local-only fields and revision anchors."""
    manifest, incoming = parse_portable(files)
    existing = dict(retained or {})
    existing.update({slide.id: slide for slide in current.slides})
    result: list[Slide] = []
    for item in incoming:
        old = existing.get(item.id)
        if old is None:
            slide = Slide(
                id=item.id,
                title=item.title,
                html=item.html,
                interactive_html=item.interactive_html,
                hidden=item.hidden,
            )
        else:
            changed = (
                item.title != old.title
                or item.html != old.html
                or item.interactive_html != old.interactive_html
            )
            slide = old.model_copy(
                update={
                    "title": item.title,
                    "html": item.html,
                    "interactive_html": item.interactive_html,
                    "hidden": item.hidden,
                    "revision": old.revision + int(changed),
                },
                deep=True,
            )
        result.append(slide)
    return Deck.model_validate(
        current.model_dump()
        | {"title": manifest.title, "sections": manifest.sections, "slides": result}
    )


def _journal_path(store: Store, deck_id: str, name: str) -> Path:
    """Resolve only a real in-deck source path without following symlinks."""
    if name != "index.html" and name != "theme.css" and not name.startswith("assets/"):
        raise ValueError("Invalid presentation sync journal path.")
    _ = _file_path(name)
    folder = store.folder(deck_id)
    path = folder / name
    if (store.root / deck_id).is_symlink() or any(
        part.is_symlink() for part in (path, *path.parents) if part != store.root.parent
    ):
        raise ValueError("Presentation source paths cannot contain symlinks.")
    if not path.resolve().is_relative_to(folder):
        raise ValueError("Presentation source escaped its folder.")
    return Path(path)


def _journal_read(path: Path) -> bytes | None:
    if not path.exists():
        return None
    return bounded_read(path)


def _journal_write(path: Path, value: bytes | None) -> None:
    """Replace one path durably; repeated recovery accepts either version."""
    import os
    import tempfile

    if value is None:
        if path.exists():
            path.unlink()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                temporary = stream.name
                _ = stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)
    if path.parent.exists():
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def recover_presentation_sync(store: Store, db: sqlite3.Connection, deck_id: str) -> None:
    """Finish a prepared update, refusing any file changed outside the journal."""
    from datetime import UTC, datetime

    from .storage import ConflictError
    from .version_history import capture_slide_version

    row = db.execute(
        "SELECT before_json,after_json FROM presentation_sync_journal WHERE deck=?", (deck_id,)
    ).fetchone()
    if row is None:
        return
    before = Deck.model_validate_json(row["before_json"])
    after = Deck.model_validate_json(row["after_json"])
    rows = db.execute(
        "SELECT path,before,after FROM presentation_sync_files WHERE deck=? "
        "ORDER BY CASE WHEN path='index.html' THEN 1 ELSE 0 END,path",
        (deck_id,),
    ).fetchall()
    # Check the entire write set before making another change; never overwrite divergence.
    for item in rows:
        current = _journal_read(_journal_path(store, deck_id, item["path"]))
        if current != item["before"] and current != item["after"]:
            raise ConflictError("Presentation source changed during sync recovery.")
    for item in rows:
        path = _journal_path(store, deck_id, item["path"])
        current = _journal_read(path)
        if current != item["before"] and current != item["after"]:
            raise ConflictError("Presentation source changed during sync recovery.")
        if current != item["after"]:
            _journal_write(path, item["after"])
    # Persist every ancestor entry as well as the leaf. A previous interrupted
    # attempt may already have made a directory without syncing its parent.
    import os

    root = store.folder(deck_id)
    directories = {root}
    for item in rows:
        parent = _journal_path(store, deck_id, item["path"]).parent
        while parent != root:
            if parent.exists():
                directories.add(parent)
            parent = parent.parent
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        handle = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(handle)
        finally:
            os.close(handle)
    previous = {slide.id: slide for slide in before.slides}
    for slide in before.slides:
        capture_slide_version(db, deck_id, slide)
    for slide in after.slides:
        old = previous.get(slide.id)
        if old is None or slide.revision != old.revision:
            capture_slide_version(
                db, deck_id, slide, saved_at=datetime.now(UTC).isoformat(), authored=True
            )
    _ = db.execute(
        "UPDATE feedback SET context_slide_id=? WHERE deck=? "
        "AND COALESCE(slide,'')='' AND context_slide_id IS NULL AND reply_to IS NULL",
        (before.slides[0].id, deck_id),
    )
    store.seed_slide_views(db, after)
    _ = db.execute("DELETE FROM presentation_sync_files WHERE deck=?", (deck_id,))
    _ = db.execute("DELETE FROM presentation_sync_journal WHERE deck=?", (deck_id,))


def apply_portable(
    store: Store, deck_id: str, expected: dict[str, bytes], source: dict[str, bytes]
) -> None:
    """Stage an exact, guarded update in private SQLite, then finish it recoverably."""
    from .storage import DATA, ConflictError

    _ = parse_portable(source)
    with store.editing(deck_id) as db:
        if portable_snapshot(store, deck_id) != expected:
            raise ConflictError("Presentation changed during sync. Check sync again.")
        before = store.read(deck_id)
        manifest, _ = parse_portable(source)
        old_ids = {slide.id for slide in before.slides}
        retained: dict[str, Slide] = {}
        for slide_id in set(manifest.slides) - old_ids:
            row = db.execute(
                "SELECT document FROM revisions WHERE deck=? AND slide=? "
                "ORDER BY rowid DESC LIMIT 1",
                (deck_id, slide_id),
            ).fetchone()
            if row is not None:
                retained[slide_id] = Slide.model_validate_json(row[0])
        after = prepare_portable_deck(before, source, retained=retained)
        index = _journal_read(_journal_path(store, deck_id, "index.html"))
        if index is None:
            raise ValueError("Presentation index is missing.")
        html = index.decode("utf-8")
        document = after.model_dump_json(indent=2).replace("<", r"\u003c")
        updated, count = DATA.subn(lambda match: match[1] + document + match[3], html)
        if count != 1:
            raise ValueError("Expected one canonical deck document.")
        changes: dict[str, tuple[bytes | None, bytes | None]] = {
            "index.html": (index, updated.encode()),
            "theme.css": (expected["theme.css"], source["theme.css"]),
        }
        assets = {name for name in expected.keys() | source.keys() if name.startswith("assets/")}
        for name in assets:
            changes[name] = (expected.get(name), source.get(name))
        for name, (old, _) in changes.items():
            if _journal_read(_journal_path(store, deck_id, name)) != old:
                raise ConflictError("Presentation changed during sync. Check sync again.")
        _ = db.execute(
            "INSERT INTO presentation_sync_journal VALUES (?,?,?)",
            (deck_id, before.model_dump_json(), after.model_dump_json()),
        )
        _ = db.executemany(
            "INSERT INTO presentation_sync_files VALUES (?,?,?,?)",
            [(deck_id, name, old, new) for name, (old, new) in changes.items() if old != new],
        )
    # The intent commits before any source write. Recovery is also run before every edit.
    with store.editing(deck_id):
        pass
