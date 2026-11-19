"""Export current imported slides as native editable PowerPoint objects."""

import hashlib
import sqlite3
import zipfile
from pathlib import Path
from typing import cast
from xml.etree import ElementTree as ET
from xml.parsers.expat import ExpatError

from ..models import Deck, Slide
from ..slide_actions import SlideActionResult
from ..storage import ConflictError, Store
from .combine import combine_slides
from .importer import OriginalObject, compile_slides, required
from .package import P, Package, R
from .roundtrip_edits import Chart, patch_slide
from .roundtrip_package import AudiencePackage

_REIMPORT = "Import the original PowerPoint again to restore its editable export source."
_SOURCE_JOIN = (
    "FROM powerpoint_sources AS source JOIN creation_receipts AS receipt "
    "ON source.deck=receipt.deck AND source.marker=receipt.marker WHERE source.deck=?"
)


def _matches_marker(folder: Path, marker: str) -> bool:
    identity = folder / ".artifact-studio-creation"
    try:
        return (
            not identity.is_symlink()
            and identity.is_file()
            and identity.stat().st_size == 32
            and identity.read_bytes() == marker.encode()
        )
    except OSError:
        return False


def has_powerpoint_source(store: Store, deck_id: str) -> bool:
    """Check source capability without loading the original upload."""
    with store.connect() as db:
        row = db.execute("SELECT source.marker " + _SOURCE_JOIN, (deck_id,)).fetchone()
    return row is not None and _matches_marker(store.folder(deck_id), cast(str, row[0]))


def source_slide_ids(store: Store, deck_id: str) -> set[str]:
    """Return the original slide IDs for the currently installed import."""
    with store.editing(deck_id) as db:
        row = db.execute(
            "SELECT source.marker,receipt.document " + _SOURCE_JOIN, (deck_id,)
        ).fetchone()
        if row is None or not _matches_marker(store.folder(deck_id), cast(str, row[0])):
            raise ValueError(_REIMPORT)
        return set(_slide_origins(db, Deck.model_validate_json(row[1])))


def _slide_origins(db: sqlite3.Connection, deck: Deck) -> dict[str, str]:
    origins = {slide.id: slide.id for slide in deck.slides}
    for row in db.execute(
        "SELECT result_json FROM slide_actions WHERE deck=? AND completed=1 "
        "AND json_extract(result_json,'$.action')='duplicate' ORDER BY rowid",
        (deck.id,),
    ):
        operation = SlideActionResult.model_validate_json(cast(str, row[0]))
        for source_id, clone_id in zip(operation.selected_ids, operation.added_ids, strict=True):
            if source_id in origins:
                origins[clone_id] = origins[source_id]
    return origins


def _verify_theme(folder: Path, theme_hash: str) -> None:
    theme = folder / "theme.css"
    if theme.is_symlink() or not theme.is_file() or theme.stat().st_size > 8_000_000:
        raise ValueError("PowerPoint export requires the original imported theme.")
    if hashlib.sha256(theme.read_bytes()).hexdigest() != theme_hash:
        raise ValueError("PowerPoint export cannot apply changes to the imported theme.")


def export_powerpoint(store: Store, deck_id: str, *, slide_ids: set[str] | None = None) -> bytes:
    """Export visible imported slides in canonical order using a private source snapshot."""
    folder = store.folder(deck_id)
    with store.editing(deck_id) as db:
        row = db.execute(
            "SELECT source.marker,source.source,source.theme_hash,receipt.document " + _SOURCE_JOIN,
            (deck_id,),
        ).fetchone()
        if row is None or not _matches_marker(folder, cast(str, row[0])):
            raise ValueError(_REIMPORT)
        deck = store.read(deck_id)
        marker, data, theme, document = cast(tuple[str, bytes, str, str], row)
        baseline = Deck.model_validate_json(document)
        origins = _slide_origins(db, baseline)
    if baseline.id != deck_id or deck.id != deck_id:
        raise ValueError(_REIMPORT)
    _verify_theme(folder, theme)
    try:
        source = Package(data)
        with source.archive:
            result = _export(source, folder, baseline, deck, origins, slide_ids)
    except (
        ET.ParseError,
        ExpatError,
        zipfile.BadZipFile,
        KeyError,
        IndexError,
        OverflowError,
    ) as exc:
        raise ValueError("The retained PowerPoint source could not be mapped.") from exc
    with store.editing(deck_id):
        if not _matches_marker(folder, marker) or deck != store.read(deck_id):
            raise ConflictError("The presentation changed during PowerPoint export. Try again.")
        _verify_theme(folder, theme)
    return result


def _export(
    source: Package,
    folder: Path,
    baseline: Deck,
    deck: Deck,
    origins: dict[str, str],
    slide_ids: set[str] | None,
) -> bytes:
    slides = [
        slide
        for slide in deck.slides
        if not slide.hidden and (slide_ids is None or slide.id in slide_ids)
    ]
    if not slides:
        raise ValueError("PowerPoint export needs at least one visible imported slide.")
    originals: dict[str, dict[str, OriginalObject]] = {}
    compiled, assets = compile_slides(source, originals=originals)
    if [slide.id for slide in compiled] != [slide.id for slide in baseline.slides]:
        raise ValueError("The retained PowerPoint source does not match this presentation.")
    source_ids = required(source.xml(source.presentation), P + "sldIdLst").findall(P + "sldId")
    native = {
        slide.id: (slide, element)
        for slide, element in zip(baseline.slides, source_ids, strict=True)
    }
    unknown = next((slide for slide in slides if slide.id not in origins), None)
    if unknown is not None:
        raise ValueError(f'Slide "{unknown.title}" has no original PowerPoint object mapping.')
    groups: list[list[Slide]] = []
    positions: list[int] = []
    occurrence: dict[str, int] = {}
    for slide in slides:
        origin = origins[slide.id]
        batch = occurrence.get(origin, 0)
        occurrence[origin] = batch + 1
        if batch == len(groups):
            groups.append([])
        groups[batch].append(slide)
        positions.append(batch)

    def assemble(start: int, end: int) -> bytes:
        if end - start == 1:
            return _component(
                source, folder, deck.title, groups[start], native, origins, originals, assets
            )
        middle = (start + end) // 2
        return combine_slides(
            assemble(start, middle),
            assemble(middle, end),
            [n < middle for n in positions if start <= n < end],
        )

    output = assemble(0, len(groups))
    final = Package(output)
    with final.archive:
        verified, _ = compile_slides(final)
    if len(verified) != len(slides) or any(slide.hidden for slide in verified):
        raise ValueError("PowerPoint export could not verify the visible slide order.")
    return output


def _component(
    source: Package,
    folder: Path,
    title: str,
    slides: list[Slide],
    native: dict[str, tuple[Slide, ET.Element]],
    origins: dict[str, str],
    originals: dict[str, dict[str, OriginalObject]],
    assets: dict[str, bytes],
) -> bytes:
    charts: dict[str, Chart] = {}
    parts = AudiencePackage(
        source, folder, assets, {slide.id: originals[origins[slide.id]] for slide in slides}, charts
    )
    ordered: list[ET.Element] = []
    for slide in slides:
        origin = origins[slide.id]
        original, element = native[origin]
        ordered.append(element)
        part = source.resolve(source.presentation, element.attrib[R + "id"])
        patch = parts.patch(part)
        if original.hidden:
            patch.attribute(source.xml(part), "show", "1")
        try:
            for chart in patch_slide(source, original, slide, part, originals[origin], patch):
                previous = charts.setdefault(chart.part, chart)
                if previous.edits != chart.edits:
                    raise ValueError(
                        "PowerPoint export cannot apply conflicting shared chart edits."
                    )
        except ValueError as exc:
            raise ValueError(f'Slide "{slide.title}": {exc}') from exc
    return parts.build(ordered, title)
