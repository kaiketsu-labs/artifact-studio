"""Package reusable design files and install verified copies without replacing local work."""

import errno
import re
import shutil
import stat
import tempfile
import unicodedata
import uuid
import zipfile
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from .artifacts import MAX_BUNDLE_SIZE, MAX_FILE_SIZE, MAX_FILES, relative_path
from .design_authoring import write_viewer
from .design_library import ContextManifest, DesignDetail, DesignLibrary
from .libraries import allowed, inventory
from .models import Deck, DesignManifest
from .presentation_files import ordinary_file
from .storage import DATA, DESIGN_FILES, SAFE_ID, ConflictError, SlideMarkup, Store

MAX_DESIGN_ZIP = 90_000_000
DESIGN_ZIP_LIMIT = "Choose a design ZIP no larger than 90 MB."
_MANIFESTS = {"design.json", "design-context.json"}
_INTERNAL_DIRECTORIES = frozenset(
    {"node_modules", "__pycache__", "feedback-files", "brief-attachments"}
)
_PRIVATE_FILE = re.compile(r"\.(?:sqlite3?|db)(?:-wal|-shm|-journal)?$|\.(?:pem|key)$", re.I)


def _source_allowed(path: Path) -> bool:
    """Keep transfer files portable and exclude private runtime and credential state."""
    return (
        allowed(path, all_extensions=True, excluded=_INTERNAL_DIRECTORIES)
        and not _PRIVATE_FILE.search(path.name)
        and path.name.casefold() not in {"id_rsa", "id_ed25519", "credentials", "secrets"}
    )


def _check_size(count: int, total: int, largest: int) -> None:
    """Use the existing design asset budgets for the complete archive."""
    if count > MAX_FILES or total > MAX_BUNDLE_SIZE or largest > MAX_FILE_SIZE:
        raise ValueError("Use up to 5,000 files, 50 MB each and 300 MB expanded.")


def export_design(store: Store, design_id: str, destination: Path) -> None:
    """Compress the selected design's portable files without collecting them in memory."""
    library = DesignLibrary(store)
    item = library.item(design_id)
    source = store.folder(item.folder_id)
    files = [
        file
        for file in inventory(source, all_extensions=True, excluded=_INTERNAL_DIRECTORIES)
        if _source_allowed(Path(file.path))
    ]
    _check_size(
        len(files), sum(file.size for file in files), max((file.size for file in files), default=0)
    )
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            name = relative_path(file.path)
            path = ordinary_file(Path(file.local_path))
            before = path.stat()
            if before.st_size != file.size:
                raise ConflictError("The design changed during export. Try again.")
            with path.open("rb") as incoming, archive.open(name, "w") as outgoing:
                remaining = file.size
                while chunk := incoming.read(min(remaining, 1024 * 1024)):
                    _ = outgoing.write(chunk)
                    remaining -= len(chunk)
                after = path.stat()
                if remaining or incoming.read(1) or (
                    after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
                ) != (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns):
                    raise ConflictError("The design changed during export. Try again.")
    if destination.stat().st_size > MAX_DESIGN_ZIP:
        raise ValueError(DESIGN_ZIP_LIMIT)


def _extract(source: BinaryIO, folder: Path, disk_reserve: int) -> None:
    """Validate paths, types and declared expansion before extracting to private staging."""
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            total = sum(info.file_size for info in entries)
            _check_size(len(entries), total, max((info.file_size for info in entries), default=0))
            members: dict[str, bool] = {}
            for info in entries:
                name = relative_path(info.filename.removesuffix("/"))
                portable = unicodedata.normalize("NFC", name).casefold()
                mode = stat.S_IFMT(info.external_attr >> 16)
                if (
                    portable in members
                    or info.orig_filename != info.filename
                    or mode not in {0, stat.S_IFDIR if info.is_dir() else stat.S_IFREG}
                    or not _source_allowed(Path(name))
                    or (info.is_dir() and info.file_size)
                ):
                    raise ValueError("The ZIP contains unsafe or duplicate file paths.")
                members[portable] = info.is_dir()
            for name in members:
                if any(members.get(parent.as_posix()) is False for parent in Path(name).parents):
                    raise ValueError("A design file conflicts with a folder in the ZIP.")
            if disk_reserve and shutil.disk_usage(folder).free < total + disk_reserve:
                raise OSError(errno.ENOSPC, "Not enough workspace storage for this design.")
            for info in entries:
                target = folder / info.filename
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as incoming, target.open("xb") as outgoing:
                    remaining = info.file_size
                    while chunk := incoming.read(min(remaining, 1024 * 1024)):
                        _ = outgoing.write(chunk)
                        remaining -= len(chunk)
                    if remaining or incoming.read(1):
                        raise ValueError("A design file is damaged or incomplete.")
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error) as error:
        raise ValueError("Choose a valid Artifact Studio design ZIP.") from error


def _prepare(folder: Path, folder_id: str) -> tuple[str, bool]:
    """Validate a native design and install the current trusted viewer for slide packs."""
    manifests = {name for name in _MANIFESTS if (folder / name).is_file()}
    if manifests == {"design-context.json"} and not (folder / "index.html").exists():
        context = ContextManifest.model_validate_json((folder / "design-context.json").read_bytes())
        if not SAFE_ID.fullmatch(context.id) or not context.name.strip():
            raise ValueError("The design reference collection has invalid registration details.")
        return context.id, False
    if manifests != {"design.json"}:
        raise ValueError("The ZIP must contain one design manifest at its top level.")
    manifest = DesignManifest.model_validate_json((folder / "design.json").read_bytes())
    if not all((folder / name).is_file() for name in DESIGN_FILES if name != "assets"):
        raise ValueError("The design ZIP is missing examples, theme, viewer or guidance files.")
    if (folder / "slideshow.css").is_dir():
        raise ValueError("The design ZIP contains an invalid viewer file.")
    if not (folder / "assets").exists():
        (folder / "assets").mkdir()
    if not (folder / "assets").is_dir():
        raise ValueError("The design assets must be in a folder.")
    source = (folder / "index.html").read_text()
    matches = list(DATA.finditer(source))
    if len(matches) != 1:
        raise ValueError("The ZIP must include one editable example deck.")
    deck = Deck.model_validate_json(matches[0][2])
    if (
        deck.design_id != manifest.id
        or len({slide.id for slide in deck.slides}) != len(deck.slides)
    ):
        raise ValueError("The example deck must match the design and use unique slide IDs.")
    for slide in deck.slides:
        parser = SlideMarkup()
        parser.feed(slide.html)
        parser.close()
    deck.id = folder_id
    write_viewer(folder, deck)
    return manifest.id, True


def import_design(store: Store, source: BinaryIO, *, disk_reserve: int = 0) -> DesignDetail:
    """Install a validated ZIP once, preserving saved defaults and existing designs."""
    folder_id = datetime.now(UTC).strftime("%Y-%m-%d-") + "design-" + uuid.uuid4().hex
    library = DesignLibrary(store)
    target = store.folder(folder_id)
    with tempfile.TemporaryDirectory(prefix=".design-import-", dir=store.root) as temporary:
        folder = Path(temporary)
        _extract(source, folder, disk_reserve)
        design_id, slide_pack = _prepare(folder, folder_id)
        published = False
        try:
            with store.connect() as db:
                _ = db.execute("BEGIN IMMEDIATE")
                if library.has_id(design_id):
                    raise ConflictError("This design is already in this workspace.")
                if slide_pack and (previous := store.default_design_id()):
                    _ = db.execute(
                        "INSERT INTO workspace_settings VALUES ('default_design', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (previous,),
                    )
                folder.rename(target)
                published = True
                detail = library.get(design_id)
            return detail
        except BaseException:
            if published:
                shutil.rmtree(target)
            raise
