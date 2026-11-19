"""Validate complete native imports, unchanged projects on failure, and safe retries."""

import base64
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from artifact_studio.artifacts import ArtifactStore, write_legacy_project
from artifact_studio.powerpoint import import_powerpoint
from artifact_studio.powerpoint.package import A, P
from artifact_studio.storage import ConflictError, Store
from tests.pptx_fixture import (
    corrupt_powerpoint,
    fictional_layers_powerpoint,
    fictional_powerpoint,
    unsupported_powerpoint,
)


def modified_slide(feature: str) -> bytes:
    output = io.BytesIO()
    with (
        ZipFile(io.BytesIO(fictional_powerpoint())) as source,
        ZipFile(output, "w", ZIP_DEFLATED) as target,
    ):
        for entry in source.infolist():
            data = source.read(entry)
            if entry.filename == "ppt/slides/slide2.xml":
                slide = ET.fromstring(data)
                if feature == "transition":
                    _ = ET.SubElement(ET.SubElement(slide, P + "transition"), P + "fade")
                elif feature == "animation":
                    _ = ET.SubElement(ET.SubElement(slide, P + "timing"), P + "spTgt", spid="2")
                else:
                    font = next(slide.iter(A + "latin"))
                    font.set("typeface", "Fictional Missing Typeface")
                data = ET.tostring(slide)
            target.writestr(entry, data)
    return output.getvalue()


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (corrupt_powerpoint(), "Re-save as .pptx"),
        (unsupported_powerpoint(), "Slide 2, Chart 3: unsupported chart type"),
        (modified_slide("transition"), "Slide 2, Slide transitions"),
        (modified_slide("animation"), "Slide 2, TextBox 1: animations"),
        (modified_slide("font"), "Slide 2, TextBox 1: PowerPoint font is unavailable"),
    ],
    ids=["corrupt", "unsupported-chart", "transition", "object-animation", "missing-font"],
)
def test_rejection_leaves_existing_project_intact(
    tmp_path: Path, source: bytes, message: str
) -> None:
    store = Store(tmp_path / "collection", tmp_path / "review.sqlite3")
    project = ArtifactStore(store).create_project("Preserve me")
    before = set(store.root.iterdir())
    with pytest.raises(ValueError) as caught:
        _ = import_powerpoint(store, project.id, "Fictional", base64.b64encode(source).decode())
    assert message in str(caught.value)
    assert set(store.root.iterdir()) == before
    with store.connect() as db:
        assert not db.execute("SELECT * FROM artifact_legacy_projects").fetchall()
        assert not db.execute("SELECT * FROM creation_receipts").fetchall()


def test_simultaneous_import_retries_and_original_media(tmp_path: Path) -> None:
    store = Store(tmp_path / "collection", tmp_path / "review.sqlite3")
    hub = ArtifactStore(store)
    project = hub.create_project("Fictional shapes")
    source = fictional_layers_powerpoint()
    encoded = base64.b64encode(source).decode()
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [
            pool.submit(import_powerpoint, store, project.id, "Layers", encoded, "shared-key")
            for _ in range(2)
        ]
        first, second = [request.result() for request in requests]
    assert first == second
    assert hub.get_artifact(first.id).project_id == project.id
    with ZipFile(io.BytesIO(source)) as archive:
        original = next(name for name in archive.namelist() if name.startswith("ppt/media/"))
        assert [
            asset.read_bytes() for asset in (store.folder(first.id) / "assets").glob("*.png")
        ] == [archive.read(original)]
    with pytest.raises(ConflictError):
        _ = import_powerpoint(store, project.id, "Different", encoded, "shared-key")
    with store.connect() as db:
        assert len(db.execute("SELECT * FROM creation_receipts").fetchall()) == 1
    other = hub.create_project("Another team")
    with store.connect() as db:
        write_legacy_project(db, first.id, other.id)
    with pytest.raises(ConflictError):
        _ = import_powerpoint(store, project.id, "Layers", encoded, "shared-key")
    assert hub.get_artifact(first.id).project_id == other.id


def test_visible_objects_survive_empty_outline_duplicate_id_and_default_stretch() -> None:
    from artifact_studio.powerpoint.importer import compile_slides
    from artifact_studio.powerpoint.package import Package

    package = Package(fictional_layers_powerpoint())
    with package.archive:
        slide = package.xml("ppt/slides/slide1.xml")
        picture = next(slide.iter(P + "pic"))
        stretch = next(picture.iter(A + "stretch"))
        stretch.clear()
        shape = next(slide.iter(P + "sp"))
        props = next(shape.iter(P + "spPr"))
        for line in props.findall(A + "ln"):
            props.remove(line)
        _ = ET.SubElement(props, A + "ln")
        shared_id = next(shape.iter(P + "cNvPr")).attrib["id"]
        next(picture.iter(P + "cNvPr")).set("id", shared_id)
        slides, images = compile_slides(package)
    assert f'id="pptx-slide-{shared_id}"' in slides[0].html
    assert f'id="pptx-slide-{shared_id}-2"' in slides[0].html
    assert len(images) == 1


def test_distinct_unsupported_objects_are_reported_together() -> None:
    from artifact_studio.powerpoint.importer import compile_slides
    from artifact_studio.powerpoint.package import Package

    package = Package(fictional_powerpoint())
    with package.archive:
        for name, shape in enumerate(package.xml("ppt/slides/slide2.xml").iter(P + "graphicFrame")):
            props = next(shape.iter(P + "cNvPr"))
            props.set("name", f"Fictional object {name}")
            props.set("hidden", "1")
        with pytest.raises(ValueError) as result:
            compile_slides(package)
    assert all(
        f"Slide 2, Fictional object {i}: Hidden objects" in str(result.value) for i in (0, 1)
    )
