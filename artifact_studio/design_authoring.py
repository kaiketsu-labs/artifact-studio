"""Reusable design authoring and explicit, conflict-aware presentation updates."""

import hashlib
import json
import shutil
import tempfile
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from pydantic import TypeAdapter

from .artifacts import MAX_BUNDLE_SIZE, MAX_FILES, relative_path
from .design_library import TEXT_LIMIT, DesignLibrary, DesignTextEdit
from .libraries import allowed
from .models import Deck, DesignManifest, Slide
from .presentation_files import (
    BASE,
    MANAGED_BASE,
    MANAGED_PREFIX,
    MAX_THEME,
    PresentationFiles,
    bounded_read,
    ordinary_file,
)
from .storage import ConflictError, Store, digest, replace_text

PROJECT = Path(__file__).resolve().parents[1]
BASELINE = ".artifact-studio-design-baseline.json"
SCAFFOLD_GUIDE = (
    "# Design authoring notes\n\n"
    "Record this brand's typography, colours, composition principles and source provenance here. "
    "Keep user preferences in PREFERENCES.md. Example slides demonstrate the design language; "
    "compose freely for each brief.\n\n"
    'Read current tool workflow with get_authoring_guide(topic="workflow"); load other topics '
    "only as needed. Workspace rules and the selected presentation brief remain primary.\n"
)
MANAGED = {
    "index.html",
    "design.json",
    "design-context.json",
    "presentation.js",
    "icons.js",
    "slideshow.css",
}


def write_starter(folder: Path, deck: Deck, theme: str) -> None:
    """Write the shared viewer shell, local runtime and design guidance."""
    (folder / "assets").mkdir()
    for filename in ("presentation.js", "icons.js", "slideshow.css"):
        _ = shutil.copy2(PROJECT / "runtime" / filename, folder / filename)
    _ = (folder / "AGENT-GUIDE.md").write_text(SCAFFOLD_GUIDE)
    _ = (folder / "PREFERENCES.md").write_text("")
    _ = (folder / "theme.css").write_text(theme)
    _ = (folder / "index.html").write_text(
        (PROJECT / "runtime/viewer.html")
        .read_text()
        .replace("{{DECK_JSON}}", deck.model_dump_json().replace("<", r"\u003c"))
    )


def blank_presentation(folder: Path, deck_id: str, title: str) -> Deck:
    """Start an unbranded presentation without registering a design pack."""
    deck = Deck(
        id=deck_id,
        title=title,
        slides=[
            Slide(
                id="cover",
                title=title,
                html=(
                    '<article class="slide studio-blank-cover">'
                    '<h1 data-element-id="title"></h1></article>'
                ),
            )
        ],
    )
    css = (
        ".studio-blank-cover{display:grid;align-content:center;padding:7%}"
        ".studio-blank-cover h1{margin:0;font-size:6cqw;line-height:1.06;overflow-wrap:anywhere}"
    )
    write_starter(folder, deck, MANAGED_PREFIX + css + MANAGED_BASE + BASE.read_text() + "\n}\n")
    return deck


def design_files(folder: Path) -> dict[str, str]:
    """Hash bounded theme/assets inventories without loading binaries into responses."""
    assets = folder / "assets"
    if assets.is_symlink() or not assets.is_dir():
        raise ValueError("The design assets directory must be a real directory.")
    files: dict[str, str] = {}
    total = 0
    for path in [folder / "theme.css", *sorted(assets.rglob("*"))]:
        if path.is_symlink():
            raise ValueError("Design files cannot contain symlinks.")
        if path.is_dir():
            continue
        ordinary_file(path)
        total += path.stat().st_size
        if total > MAX_BUNDLE_SIZE or len(files) > MAX_FILES:
            raise ValueError("Keep theme/assets below 300 MB and 5,000 files.")
        name = relative_path(path.relative_to(folder).as_posix())
        files[name] = hashlib.sha256(bounded_read(path)).hexdigest()
    return files


def record_design_baseline(store: Store, deck_id: str) -> None:
    """Remember exactly the copied bytes so future syncs recognize local overrides."""
    folder = store.folder(deck_id)
    replace_text(folder / BASELINE, json.dumps(design_files(folder), sort_keys=True))


class DesignAuthoring:
    """Scaffold reusable packs, edit their sources and apply reviewed design changes."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.library = DesignLibrary(store)
        self.files = PresentationFiles(store)

    def _folder(self, design_id: str) -> Path:
        item = next((item for item in self.store.designs() if item.id == design_id), None)
        if item is None:
            raise ValueError(
                "Create a slide design pack first; reference collections remain sources."
            )
        if (self.store.root / item.folder_id).is_symlink():
            raise ValueError("Design directories cannot be symlinks.")
        return self.store.folder(item.folder_id)

    def create(
        self, design_id: str, name: str, description: str = "", css: str = ""
    ) -> dict[str, object]:
        """Create and register a complete neutral viewer with one editable starting slide."""
        manifest = DesignManifest(
            id=design_id,
            name=name.strip(),
            description=description.strip() or "Reusable slide design",
        )
        if MANAGED_BASE in css:
            raise ValueError("Send author CSS only; the viewer base is managed.")
        theme = MANAGED_PREFIX + css + MANAGED_BASE + BASE.read_text() + "\n}\n"
        if len(theme.encode()) > MAX_THEME:
            raise ValueError("Keep theme.css below 500 KB including viewer styles.")
        folder_id = datetime.now(UTC).strftime("%Y-%m-%d-design-") + design_id
        destination = self.store.folder(folder_id)
        with self.store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            if destination.exists() or any(
                item.id == design_id for item in self.library.list() + self.library.deleted()
            ):
                raise ConflictError(
                    "That design ID already exists. Read its context before editing."
                )
            with tempfile.TemporaryDirectory(
                prefix=".design-authoring-", dir=self.store.root
            ) as tmp:
                folder = Path(tmp)
                (folder / ".authoring-assets").mkdir()
                _ = (folder / "README.md").write_text(
                    f"# {name}\n\n{manifest.description}\n\n"
                    "Neutral starting canvas. Inspect the supplied sources, then document their "
                    "provenance and the design language here.\n"
                )
                deck = Deck(
                    id=folder_id,
                    title=name,
                    design_id=design_id,
                    slides=[
                        Slide(
                            id="cover",
                            title=name,
                            html=(
                                '<article class="slide"><h1 data-element-id="title">'
                                + escape(name)
                                + "</h1></article>"
                            ),
                        )
                    ],
                )
                write_starter(folder, deck, theme)
                _ = (folder / "design.json").write_text(manifest.model_dump_json(indent=2) + "\n")
                folder.rename(destination)
        return {
            "design_id": design_id,
            "folder_id": folder_id,
            "name": manifest.name,
            "import_directory": str(destination / ".authoring-assets"),
            "theme_hash": digest(theme),
        }

    def _target(self, folder: Path, path: str) -> Path:
        relative_path(path)
        if not allowed(Path(path)) or path in MANAGED:
            raise ValueError(
                "Use design text/assets files; example slides use add_slide/revise_slide "
                "with the pack's folder_id. The viewer shell and registration are managed."
            )
        target = folder / path
        if any(part.is_symlink() for part in (target, *target.parents)):
            raise ValueError("Design paths cannot contain symlinks.")
        return target

    def read_file(self, design_id: str, path: str) -> dict[str, object]:
        """Read one author text file and its concurrency token, omitting managed viewer CSS."""
        folder = self._folder(design_id)
        target = self._target(folder, path)
        if path == "theme.css":
            theme = self.files.theme(folder.name)
            return {
                "design_id": design_id,
                "path": path,
                "text": theme["css"],
                "expected_hash": theme["expected_hash"],
                "base_styles_managed": True,
            }
        if ordinary_file(target).stat().st_size > TEXT_LIMIT:
            raise ValueError("Read text files up to 1 MB; use the local file for larger sources.")
        data = bounded_read(target)
        encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        try:
            text = data.decode(encoding)
        except UnicodeError as error:
            raise ValueError("Use get_design_context for binary file pointers.") from error
        if any(ord(char) < 32 and char not in "\t\n\r" for char in text):
            raise ValueError("Use get_design_context for binary file pointers.")
        return {
            "design_id": design_id,
            "path": path,
            "text": text,
            "expected_hash": hashlib.sha256(data).hexdigest(),
        }

    def write_file(
        self, design_id: str, path: str, text: str, expected_hash: str = ""
    ) -> dict[str, object]:
        """Create text files or replace a previously read version, preserving existing encoding."""
        folder = self._folder(design_id)
        target = self._target(folder, path)
        if len(text.encode()) > TEXT_LIMIT or any(
            ord(char) < 32 and char not in "\t\n\r" for char in text
        ):
            raise ValueError("Use readable text no larger than 1 MB.")
        if path == "theme.css":
            saved = self.files.update_theme(folder.name, text, expected_hash)
            return {"design_id": design_id, "path": path, "expected_hash": saved["expected_hash"]}
        if path.startswith("assets/"):
            files = design_files(folder)
            total = sum((folder / name).stat().st_size for name in files)
            previous = target.stat().st_size if target.exists() else 0
            if total - previous + len(text.encode()) > MAX_BUNDLE_SIZE or (
                path not in files and len(files) > MAX_FILES
            ):
                raise ValueError("Keep theme/assets below 300 MB and 5,000 files.")
        if target.exists():
            _ = self.library.edit_text(
                design_id, DesignTextEdit(path=path, text=text, expected_hash=expected_hash)
            )
        else:
            with self.store.editing(folder.name):
                if expected_hash or target.exists():
                    raise ConflictError("This file changed. Read it again before writing.")
                target.parent.mkdir(parents=True, exist_ok=True)
                replace_text(target, text)
        data = bounded_read(target)
        return {
            "design_id": design_id,
            "path": path,
            "size": len(data),
            "expected_hash": hashlib.sha256(data).hexdigest(),
        }

    def import_asset(
        self, design_id: str, source_path: str, asset_path: str, expected_sha256: str = ""
    ) -> dict[str, object]:
        """Copy staged or registered-library assets without binary echoes."""
        folder = self._folder(design_id)
        result = self.files.import_asset(folder.name, source_path, asset_path, expected_sha256)
        return {key: value for key, value in result.items() if key != "deck_id"} | {
            "design_id": design_id
        }

    def _plan(self, deck_id: str) -> tuple[dict[str, object], dict[str, str]]:
        deck = self.store.read(deck_id)
        folder = self.store.folder(deck_id)
        source_folder = self._folder(deck.design_id)
        if folder == source_folder:
            raise ValueError("The design example deck is already the source; sync a presentation.")
        source, current = design_files(source_folder), design_files(folder)
        baseline_path = folder / BASELINE
        baseline = (
            TypeAdapter(dict[str, str]).validate_json(bounded_read(baseline_path))
            if baseline_path.exists()
            else {}
        )
        changes: list[dict[str, str]] = []
        preserved: list[str] = []
        conflicts: list[str] = []
        for path, sha in source.items():
            existing = current.get(path, "")
            if sha == existing:
                continue
            if sha == baseline.get(path):
                preserved.append(path)
                continue
            action = (
                "add"
                if not existing and path not in baseline
                else "update"
                if existing == baseline.get(path)
                else "conflict"
            )
            if action == "conflict":
                conflicts.append(path)
            changes.append(
                {"path": path, "action": action, "source_sha256": sha, "current_sha256": existing}
            )
        token = digest(
            json.dumps([deck_id, deck.design_id, source, current, baseline], sort_keys=True)
        )
        return {
            "deck_id": deck_id,
            "design_id": deck.design_id,
            "expected_hash": token,
            "changes": changes,
            "conflicts": conflicts,
            "preserved_overrides": preserved,
            "preserved_deck_files": sorted(set(current) - set(source)),
            "baseline_available": baseline_path.exists(),
        }, source

    def sync_preview(self, deck_id: str) -> dict[str, object]:
        """Preview source changes and local conflicts before applying theme/assets only."""
        return self._plan(deck_id)[0]

    def sync(self, deck_id: str, expected_hash: str, overwrite: bool = False) -> dict[str, object]:
        """Apply a current preview, preserving local-only files and unchanged-source overrides."""
        with self.store.editing(deck_id):
            plan, baseline = self._plan(deck_id)
            if plan["expected_hash"] != expected_hash:
                raise ConflictError("The deck or design changed. Preview the design sync again.")
            if plan["conflicts"] and not overwrite:
                raise ConflictError(
                    "Local overrides conflict with the design. Inspect the preview "
                    "and use overwrite=True to replace those files."
                )
            folder = self.store.folder(deck_id)
            source = self._folder(self.store.read(deck_id).design_id)
            changes = TypeAdapter(list[dict[str, str]]).validate_python(plan["changes"])
            sizes = {name: (folder / name).stat().st_size for name in design_files(folder)}
            sizes.update({item["path"]: (source / item["path"]).stat().st_size for item in changes})
            if sum(sizes.values()) > MAX_BUNDLE_SIZE or len(sizes) > MAX_FILES + 1:
                raise ValueError("The merged theme/assets would exceed 300 MB or 5,000 files.")
            # Stage every new file before touching the deck; retain originals for rollback.
            with tempfile.TemporaryDirectory(prefix=".design-sync-", dir=folder) as tmp:
                staged = Path(tmp)
                for item in changes:
                    path = item["path"]
                    destination = folder / path
                    if destination.exists():
                        ordinary_file(destination)
                    if any(
                        parent.exists() and not parent.is_dir() for parent in destination.parents
                    ):
                        raise ValueError("A destination file blocks the design asset directory.")
                    data = bounded_read(source / path)
                    if hashlib.sha256(data).hexdigest() != item["source_sha256"]:
                        raise ConflictError("The design changed during preparation. Preview again.")
                    target = staged / "new" / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _ = target.write_bytes(data)
                if self._plan(deck_id)[0]["expected_hash"] != expected_hash:
                    raise ConflictError(
                        "The deck or design changed during preparation. Preview again."
                    )
                written: list[str] = []
                try:
                    for item in changes:
                        path = item["path"]
                        target = folder / path
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if target.exists():
                            backup = staged / "original" / path
                            backup.parent.mkdir(parents=True, exist_ok=True)
                            target.replace(backup)
                        written.append(path)
                        (staged / "new" / path).replace(target)
                    replace_text(folder / BASELINE, json.dumps(baseline, sort_keys=True))
                except OSError:
                    for path in reversed(written):
                        original = staged / "original" / path
                        if original.exists():
                            original.replace(folder / path)
                        else:
                            (folder / path).unlink(missing_ok=True)
                    raise
            return {
                "deck_id": deck_id,
                "design_id": plan["design_id"],
                "updated_files": len(changes),
                "design_hash": self.store.visual_hash(deck_id),
                "preserved_overrides": plan["preserved_overrides"],
            }
