"""Conflict-checked deck styling and bounded asset copies without binary MCP echoes."""

import hashlib
import tempfile
from pathlib import Path

from .artifacts import MAX_BUNDLE_SIZE, MAX_FILE_SIZE, MAX_FILES, relative_path
from .libraries import Libraries
from .storage import ConflictError, Store, digest, replace_text

BASE = Path(__file__).resolve().parents[1] / "runtime/viewer.css"
MAX_THEME = 500_000
MANAGED_PREFIX = "@layer artifact-studio-base;\n"
MANAGED_BASE = "\n/* Artifact Studio viewer base */\n@layer artifact-studio-base {\n"


def ordinary_file(path: Path) -> Path:
    """Reject symbolic links and special files before a bounded local read."""
    if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
        raise ValueError("Use an ordinary local file without symlinks.")
    return path


def bounded_read(path: Path) -> bytes:
    """Bound allocation and detect a source changed during the copy."""
    ordinary_file(path)
    before = path.stat()
    if before.st_size > MAX_FILE_SIZE:
        raise ValueError("Keep presentation assets at or below 50 MB each.")
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_SIZE + 1)
    after = path.stat()
    unchanged = all(
        getattr(before, field) == getattr(after, field)
        for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    )
    if len(data) > MAX_FILE_SIZE or not unchanged or len(data) != after.st_size:
        raise ConflictError("The source changed during reading. Retry from a stable file.")
    return data


class PresentationFiles:
    """Edit a selected deck, never its reusable design or another presentation."""

    def __init__(self, store: Store) -> None:
        """Reuse canonical deck locking and approval dependency hashes."""
        self.store = store

    def theme(self, deck_id: str) -> dict[str, object]:
        """Return author CSS without retransmitting the common viewer stylesheet."""
        _ = self.store.read(deck_id)
        path = ordinary_file(self.store.folder(deck_id) / "theme.css")
        if path.stat().st_size > MAX_THEME:
            raise ValueError("Keep theme.css below 500 KB.")
        current = path.read_text()
        base = BASE.read_text()
        managed = current.startswith(base)
        css = current[len(base) :].removeprefix("\n") if managed else current
        if (
            current.startswith(MANAGED_PREFIX)
            and MANAGED_BASE in current
            and current.endswith("\n}\n")
        ):
            css = current[len(MANAGED_PREFIX) : current.index(MANAGED_BASE)]
            managed = True
        return {
            "deck_id": deck_id,
            "css": css,
            "expected_hash": digest(current),
            "design_hash": self.store.visual_hash(deck_id),
            "base_styles_included": managed,
            "base_styles_managed_on_update": True,
        }

    def update_theme(self, deck_id: str, css: str, expected_hash: str) -> dict[str, object]:
        """Keep viewer geometry, save author CSS atomically and reject stale overwrites."""
        if MANAGED_BASE in css:
            raise ValueError("The viewer base marker is reserved; send author CSS only.")
        # Layer declaration may precede @import; base rules remain lower priority than author CSS.
        combined = MANAGED_PREFIX + css + MANAGED_BASE + BASE.read_text() + "\n}\n"
        if len(combined.encode()) > MAX_THEME:
            raise ValueError("Keep theme.css below 500 KB including viewer styles.")
        with self.store.editing(deck_id):
            current = self.theme(deck_id)
            path = ordinary_file(self.store.folder(deck_id) / "theme.css")
            if path.read_text() != combined:
                if current["expected_hash"] != expected_hash:
                    raise ConflictError(
                        "The presentation theme changed. Read it again before editing."
                    )
                replace_text(path, combined)
            return {
                "deck_id": deck_id,
                "expected_hash": digest(combined),
                "design_hash": self.store.visual_hash(deck_id),
            }

    def asset_path(self, deck_id: str, asset_path: str) -> Path:
        """Confine destination paths to real directories under this deck's assets folder."""
        relative_path(asset_path)
        if not asset_path.startswith("assets/"):
            raise ValueError("Use a deck-local assets/ destination.")
        folder = self.store.folder(deck_id)
        path = folder / asset_path
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("Presentation asset paths cannot contain symlinks.")
        if not path.resolve().is_relative_to(folder):
            raise ValueError("Keep presentation assets inside the selected deck.")
        return path

    def source_path(self, deck_id: str, source_path: str) -> Path:
        """Read only this deck's staged/public sources or explicitly registered references."""
        source = ordinary_file(Path(source_path).expanduser().absolute()).resolve()
        folder = self.store.folder(deck_id)
        roots = [folder / ".authoring-assets", folder / "assets", folder / "brief-attachments"]
        deck = self.store.read(deck_id)
        design = next((item for item in self.store.designs() if item.id == deck.design_id), None)
        if design is not None:
            roots.append(self.store.folder(design.folder_id) / "assets")
        if any(source.is_relative_to(root) for root in roots):
            return source
        libraries = Libraries(self.store)
        for library in libraries.list():
            if library.id == "collection":
                continue
            if source.is_relative_to(Path(library.path).resolve()) and any(
                source == Path(item.local_path).resolve() for item in libraries.files(library.id)
            ):
                return source
        raise ValueError(
            "Asset sources must be staged in "
            + str(folder / ".authoring-assets")
            + ", selected-deck brief/assets, selected-design assets, or a registered library."
        )

    def assets(
        self, deck_id: str, prefix: str = "assets/", offset: int = 0, limit: int = 50
    ) -> dict[str, object]:
        """Page actual deck assets and hashes without loading binaries into model context."""
        _ = self.store.read(deck_id)
        if not offset >= 0 or not 1 <= limit <= 100 or not prefix.startswith("assets/"):
            raise ValueError("Use an assets/ prefix, nonnegative offset and limit 1–100.")
        folder = self.store.folder(deck_id)
        assets = folder / "assets"
        if assets.is_symlink():
            raise ValueError("The assets folder cannot be a symlink.")
        paths = sorted(
            path
            for path in assets.rglob("*")
            if path.is_file() and path.relative_to(folder).as_posix().startswith(prefix)
        )
        page = paths[offset : offset + limit]
        return {
            "deck_id": deck_id,
            "import_directory": str(folder / ".authoring-assets"),
            "assets": [
                {
                    "path": path.relative_to(folder).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": hashlib.sha256(bounded_read(path)).hexdigest(),
                }
                for path in page
            ],
            "next_offset": offset + limit if offset + limit < len(paths) else None,
        }

    def import_asset(
        self, deck_id: str, source_path: str, asset_path: str, expected_sha256: str = ""
    ) -> dict[str, object]:
        """Copy an explicit local source; only a matching hash permits replacing an asset."""
        source = self.source_path(deck_id, source_path)
        data = bounded_read(source)
        sha = hashlib.sha256(data).hexdigest()
        with self.store.editing(deck_id):
            _ = self.store.read(deck_id)
            path = self.asset_path(deck_id, asset_path)
            existing = hashlib.sha256(bounded_read(path)).hexdigest() if path.exists() else ""
            if existing != sha:
                if existing != expected_sha256:
                    raise ConflictError("The asset changed or already exists. Read its hash first.")
                assets = self.store.folder(deck_id) / "assets"
                files = [item for item in assets.rglob("*") if item.is_file()]
                total = sum(item.stat().st_size for item in files)
                previous_size = path.stat().st_size if path.exists() else 0
                if total - previous_size + len(data) > MAX_BUNDLE_SIZE or (
                    not path.exists() and len(files) >= MAX_FILES
                ):
                    raise ValueError("Keep presentation assets below 300 MB and 5,000 files.")
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                    _ = stream.write(data)
                    temporary = Path(stream.name)
                temporary.replace(path)
            return {
                "deck_id": deck_id,
                "path": asset_path,
                "size": len(data),
                "sha256": sha,
                "design_hash": self.store.visual_hash(deck_id),
            }
