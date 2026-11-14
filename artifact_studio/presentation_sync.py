"""Portable, private-state-free representation of an in-place presentation.

This module is deliberately not connected to hosted sync until applying a bundle is
crash recoverable. Each slide is separate so independent edits can be compared.
"""

import json
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
    manifest = PortableDeck.model_validate_json(files["presentation.json"])
    slides: list[PortableSlide] = []
    for slide_id in manifest.slides:
        slide = PortableSlide.model_validate_json(files[f"slides/{slide_id}.json"])
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


def prepare_portable_deck(current: Deck, files: dict[str, bytes]) -> Deck:
    """Build a new deck while retaining local-only fields and revision anchors."""
    manifest, incoming = parse_portable(files)
    existing = {slide.id: slide for slide in current.slides}
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
