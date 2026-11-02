"""Reusable design contexts, independently of presentation example documents."""

import hashlib
import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import Field

from .design_references import (
    DesignReferences,
    ReferenceInput,
    RemoveReferenceInput,
    validate_powerpoint,
)
from .libraries import LibraryFile, allowed, file_hash, inventory, safe_file
from .models import FileInput, StrictModel
from .storage import ConflictError, Store, decode_attachments, digest

PREVIEW_TYPES = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".avif": "image/avif",
}


class DesignCreate(StrictModel):
    """A named source collection; files need not be a presentation."""

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    files: list[FileInput] = Field(default_factory=list, max_length=5)


class ContextManifest(StrictModel):
    """Portable registration without a synthetic slide deck."""

    id: str
    name: str
    description: str = ""


class DesignItem(ContextManifest):
    """Compact design discovery for the overview and agent."""

    folder_id: str
    kind: Literal["slide_pack", "references"]
    file_count: int = 0
    formats: list[str] = Field(default_factory=list)
    is_default: bool = False
    preview_url: str = ""


class ContextFile(StrictModel):
    """Inert file pointers with a clear role and guarded removal token."""

    path: str
    name: str
    local_path: str
    size: int
    sha256: str
    version: str
    url: str
    group: str
    removable: bool


class DesignDetail(DesignItem):
    """The exact source inventory available to the agent, not a read receipt."""

    files: list[ContextFile]
    version: str


class DesignDelete(StrictModel):
    """Remove only the design version the human reviewed."""

    expected_version: str = Field(pattern=r"^[a-f0-9]{64}$")


class DesignLibrary:
    """Keep source collections, reusable slide packs and their visibility distinct."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def _records(self) -> list[DesignItem]:
        items = [
            DesignItem(**item.model_dump(), kind="slide_pack")
            for item in self.store.designs(include_archived=True)
        ]
        for path in sorted(self.store.root.glob("*/design-context.json")):
            if path.parent.name.startswith(".") or path.is_symlink() or path.parent.is_symlink():
                continue
            record = ContextManifest.model_validate_json(path.read_text())
            if any(item.id == record.id for item in items):
                raise ValueError("Design IDs must be unique.")
            items.append(
                DesignItem(**record.model_dump(), folder_id=path.parent.name, kind="references")
            )
        return items

    def _archived(self) -> set[str]:
        with self.store.connect() as db:
            return {str(row[0]) for row in db.execute("SELECT folder FROM design_archives")}

    def _item(self, design_id: str, *, archived: bool = False) -> DesignItem:
        hidden = self._archived()
        for item in self._records():
            if item.id == design_id and (archived or item.folder_id not in hidden):
                if (self.store.root / item.folder_id).is_symlink():
                    raise ValueError("Design folders must not be symbolic links.")
                return item
        raise FileNotFoundError("Design not found in the library.")

    def _inventory(self, item: DesignItem) -> list[LibraryFile]:
        return [
            file
            for file in inventory(self.store.folder(item.folder_id))
            if file.path not in {"design.json", "design-context.json"}
        ]

    def list(self) -> list[DesignItem]:
        hidden, default = self._archived(), self.store.default_design_id()
        result: list[DesignItem] = []
        for item in self._records():
            if item.folder_id in hidden:
                continue
            files = self._inventory(item)
            item.file_count = len(files)
            item.formats = sorted(
                {Path(file.path).suffix.lstrip(".").upper() or "FILE" for file in files}
            )
            item.is_default = item.id == default
            item.preview_url = self._preview(item, files)
            result.append(item)
        return sorted(result, key=lambda item: (not item.is_default, item.name.casefold(), item.id))

    @staticmethod
    def _preview(item: DesignItem, files: list[LibraryFile]) -> str:
        identifier = quote(item.id, safe="")
        if item.kind == "slide_pack":
            return f"/api/designs/{identifier}/preview"
        images = [file for file in files if Path(file.path).suffix.lower() in PREVIEW_TYPES]
        if not images:
            return ""
        source = sorted(
            images,
            key=lambda file: (
                not any(
                    word in Path(file.path).name.lower()
                    for word in ("preview", "cover", "layout", "board")
                ),
                file.path,
            ),
        )[0]
        version = Path(source.local_path).stat().st_mtime_ns
        return f"/api/design-library/{identifier}/image/{quote(source.path, safe='/')}?v={version}"

    def deleted(self) -> list[DesignItem]:
        hidden = self._archived()
        return [item for item in self._records() if item.folder_id in hidden]

    def get(self, design_id: str) -> DesignDetail:
        item = self._item(design_id)
        references = (
            {file.path: file for file in DesignReferences(self.store).list(design_id)}
            if item.kind == "slide_pack"
            else {}
        )
        files: list[ContextFile] = []
        inventory_files = self._inventory(item)
        for file in inventory_files:
            path = Path(file.local_path)
            token, stat = file_hash(path), path.stat()
            reference = file.path in references or file.path.startswith("reference/uploads/")
            group = (
                "References"
                if reference
                else "Guidance"
                if path.name in {"README.md", "AGENT-GUIDE.md", "PREFERENCES.md"}
                else "Examples & player"
                if path.name in {"index.html", "presentation.js", "icons.js", "slideshow.css"}
                else "Assets & styling"
            )
            files.append(
                ContextFile(
                    **file.model_dump(),
                    name=path.name,
                    sha256=token,
                    version=digest(f"{file.path}:{token}:{stat.st_mtime_ns}:{stat.st_ino}"),
                    url=(
                        f"/api/design-library/{quote(design_id, safe='')}/files/"
                        f"{quote(file.path, safe='/')}"
                    ),
                    group=group,
                    removable=reference,
                )
            )
        item.file_count = len(files)
        item.formats = sorted(
            {Path(file.path).suffix.lstrip(".").upper() or "FILE" for file in files}
        )
        item.is_default = item.id == self.store.default_design_id()
        item.preview_url = self._preview(item, inventory_files)
        return DesignDetail(
            **item.model_dump(),
            files=files,
            version=digest(item.model_dump_json() + json.dumps([f.model_dump() for f in files])),
        )

    def file(self, design_id: str, name: str) -> Path:
        item = self._item(design_id)
        if name not in {file.path for file in self._inventory(item)}:
            raise FileNotFoundError("Choose a file from this design's context.")
        return safe_file(self.store.folder(item.folder_id), name)

    @staticmethod
    def _attach(folder: Path, files: list[FileInput]) -> None:
        decoded = decode_attachments(files)
        # Validate the entire batch before publishing any file.
        prepared: list[tuple[Path, bytes]] = []
        for name, data in decoded:
            if Path(name).suffix.lower() in {".pptx", ".potx"}:
                validate_powerpoint(name, data)
            relative = (
                Path("reference/uploads")
                / hashlib.sha256(data).hexdigest()
                / DesignReferences.filename(name)
            )
            if not allowed(relative):
                raise ValueError("Rename this attachment; its filename is reserved.")
            target = folder / relative
            if any(part.is_symlink() for part in (target, *target.parents) if part != folder):
                raise ValueError("Design references must not contain symbolic links.")
            if target.exists() and file_hash(target) != hashlib.sha256(data).hexdigest():
                raise ConflictError("The reference path contains different content.")
            prepared.append((target, data))
        for target, data in prepared:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".upload-") as stream:
                    _ = stream.write(data)
                    stream.flush()
                    os.link(stream.name, target)

    def create(self, body: DesignCreate) -> DesignDetail:
        name = body.name.strip()
        if not name:
            raise ValueError("Give the design a name.")
        identifier = "design-" + uuid.uuid4().hex
        folder_id = datetime.now(UTC).strftime("%Y-%m-%d-") + identifier
        with tempfile.TemporaryDirectory(prefix=".design-", dir=self.store.root) as temporary:
            folder = Path(temporary)
            self._attach(folder, body.files)
            _ = (folder / "design-context.json").write_text(
                ContextManifest(
                    id=identifier, name=name, description=body.description.strip()
                ).model_dump_json(indent=2)
            )
            folder.rename(self.store.folder(folder_id))
        return self.get(identifier)

    def attach(self, design_id: str, body: ReferenceInput) -> DesignDetail:
        item = self._item(design_id)
        if item.kind == "slide_pack":
            _ = DesignReferences(self.store).attach(design_id, body)
        else:
            with self.store.connect() as db:
                _ = db.execute("BEGIN IMMEDIATE")
                item = self._item(design_id)
                self._attach(self.store.folder(item.folder_id), body.files)
        return self.get(design_id)

    def remove_file(self, design_id: str, body: RemoveReferenceInput) -> DesignDetail:
        item = self._item(design_id)
        if item.kind == "slide_pack":
            _ = DesignReferences(self.store).remove(design_id, body)
        else:
            with self.store.connect() as db:
                _ = db.execute("BEGIN IMMEDIATE")
                file = next((f for f in self.get(design_id).files if f.path == body.path), None)
                if file is None or not file.removable:
                    raise FileNotFoundError("Reference not found.")
                if file.version != body.expected_version:
                    raise ConflictError("This file changed. Reload before removing it.")
                self.file(design_id, body.path).unlink()
        return self.get(design_id)

    def delete(self, design_id: str, body: DesignDelete) -> None:
        with self.store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            item = self.get(design_id)
            if item.version != body.expected_version:
                raise ConflictError("This design changed. Reload before deleting it.")
            _ = db.execute("INSERT OR IGNORE INTO design_archives VALUES (?)", (item.folder_id,))

    def restore(self, design_id: str) -> DesignDetail:
        item = self._item(design_id, archived=True)
        with self.store.connect() as db:
            _ = db.execute("DELETE FROM design_archives WHERE folder=?", (item.folder_id,))
        return self.get(design_id)
