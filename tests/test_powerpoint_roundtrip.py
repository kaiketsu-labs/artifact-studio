"""Keep supported imported edits native and exclude private package parts."""

import base64
import io
from pathlib import Path
from xml.dom import minidom
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from PIL import Image, PngImagePlugin

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.powerpoint.edit import Node, Tree, edit_pptx_text, transform_pptx_object
from artifact_studio.powerpoint.graphics import C, _workbook_values  # pyright: ignore[reportPrivateUsage]
from artifact_studio.powerpoint.importer import compile_slides, import_powerpoint
from artifact_studio.powerpoint.package import REL, A, P, Package, R
from artifact_studio.powerpoint.picture_export import audience_picture
from artifact_studio.powerpoint.roundtrip import (
    export_powerpoint,
    has_powerpoint_source,
    source_slide_ids,
)
from artifact_studio.powerpoint.workbook_export import chart_workbook, xml_bytes
from artifact_studio.slide_actions import SlideActions
from artifact_studio.storage import Store, digest
from tests.pptx_fixture import fictional_layers_powerpoint, fictional_powerpoint
from tests.test_powerpoint_clustered_chart import fictional_chart


def _path(node: Node) -> list[int]:
    result: list[int] = []
    while node.parent is not None and node.parent.tag:
        result.insert(0, next(i for i, child in enumerate(node.parent.children) if child is node))
        node = node.parent
    return result


def _nodes(node: Node) -> list[Node]:
    return [node] + [item for child in node.children for item in _nodes(child)]


def _set_text(source: str, old: str, new: str) -> str:
    tree = Tree(source)
    selected = next(
        node
        for node in _nodes(tree.root)
        if node.end and not node.children and source[node.content : node.end] == old
    )
    return edit_pptx_text(source, _path(selected), new)


def _store(tmp_path: Path, source: bytes, *, name: str = "A round-trip test") -> tuple[Store, str]:
    store = Store(tmp_path / "collection", tmp_path / "review.sqlite3")
    project = ArtifactStore(store).create_project("Source tests")
    deck = import_powerpoint(store, project.id, name, base64.b64encode(source).decode())
    return store, deck.id


def _rewrite(source: bytes, changes: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with ZipFile(io.BytesIO(source)) as old, ZipFile(output, "w") as new:
        for name in old.namelist():
            new.writestr(name, changes.get(name, old.read(name)))
        for name, value in changes.items():
            if name not in old.namelist():
                new.writestr(name, value)
    return output.getvalue()


def test_roundtrip_preserves_unchanged_slide_and_applies_geometry(tmp_path: Path) -> None:
    source = fictional_layers_powerpoint()
    store, deck_id = _store(tmp_path, source)
    assert has_powerpoint_source(store, deck_id)
    assert source_slide_ids(store, deck_id) == {"slide-1"}
    assert not list(store.folder(deck_id).rglob("*.pptx"))
    unchanged = export_powerpoint(store, deck_id)
    part = "ppt/slides/slide1.xml"
    with ZipFile(io.BytesIO(source)) as uploaded, ZipFile(io.BytesIO(unchanged)) as output:
        assert uploaded.read(part) == output.read(part)
    deck = store.read(deck_id)
    slide = deck.slides[0]
    objects = Tree(slide.html).root.children[0].children
    image = next(node for node in objects if node.children and node.children[0].tag == "img")
    slide.html = transform_pptx_object(slide.html, _path(image)[0], 0.125, -0.05, 30)
    slide.title = "A renamed slide"
    store.write(deck_id, deck)
    out = Package(export_powerpoint(store, deck_id))
    with out.archive:
        assert out.xml(part).find(P + "cSld").get("name") == "A renamed slide"  # type: ignore[union-attr]
        picture = next(out.xml(part).iter(P + "pic"))
        assert next(picture.iter(A + "xfrm")).get("rot") == "1800000"
        rendered, _ = compile_slides(out)
        assert "rotate(30deg)" in rendered[0].html


def test_roundtrip_updates_chart_table_and_text_in_reordered_native_package(tmp_path: Path) -> None:
    store, deck_id = _store(tmp_path, fictional_powerpoint())
    deck = store.read(deck_id)
    first, second = deck.slides
    second.html = _set_text(second.html, "Herbs", "Basil")
    second.html = _set_text(second.html, "24", "34")
    second.html = _set_text(second.html, "Spring", "Station")
    first.html = _set_text(first.html, first.title, "Revised slide title")
    first.title = "Revised slide title"
    deck.slides = [second, first]
    store.write(deck_id, deck)
    output = Package(export_powerpoint(store, deck_id))
    with output.archive:
        slides, _ = compile_slides(output)
        assert [slide.title for slide in slides] == [second.title, first.title]
        assert "Basil" in slides[0].html and "Station" in slides[0].html
        chart_part = "ppt/charts/chart1.xml"
        chart = output.xml(chart_part)
        assert {element.text for element in chart.iter(C + "v")} >= {"Station", "34"}
        workbook = output.read(output.related(chart_part, "package"))
        assert "Station" in _workbook_values(workbook, "Sheet1!$A$2:$A$3")
        assert "34" in _workbook_values(workbook, "Sheet1!$B$2:$B$3")


def test_roundtrip_prunes_private_metadata_hidden_slides_and_unrelated_parts(
    tmp_path: Path,
) -> None:
    archive = fictional_powerpoint()
    with ZipFile(io.BytesIO(archive)) as original:
        slide_part = "ppt/slides/slide1.xml"
        slide = ET.fromstring(original.read(slide_part))
        _ = ET.SubElement(ET.SubElement(slide, P + "extLst"), "private", value="HiddenSecret")
        workbook_path = next(name for name in original.namelist() if name.endswith(".xlsx"))
        workbook = original.read(workbook_path)
        with ZipFile(io.BytesIO(workbook)) as inner:
            original_worksheet = inner.read("xl/worksheets/sheet1.xml")
        workbook = _rewrite(
            workbook,
            {
                "xl/hidden.txt": b"HiddenSecret",
                "xl/worksheets/sheet1.xml": original_worksheet.replace(
                    b"</sheetData>",
                    b'<row r="99"><c r="A99" t="inlineStr"><is><t>HiddenSecret</t></is></c></row>'
                    b"</sheetData>",
                ),
            },
        )
    source = _rewrite(
        archive,
        {
            slide_part: xml_bytes(slide).replace(b"</ns0:sld>", b"<!--HiddenSecret--></ns0:sld>"),
            "private.bin": b"HiddenSecret",
            "docProps/core.xml": b"<private>HiddenSecret</private>",
            workbook_path: workbook,
        },
    )
    store, deck_id = _store(tmp_path, source)
    deck = store.read(deck_id)
    deck.slides[0].hidden = True
    store.write(deck_id, deck)
    out = Package(export_powerpoint(store, deck_id))
    with out.archive:
        assert slide_part not in out.names
        assert "private.bin" not in out.names and "docProps/core.xml" not in out.names
        assert all(b"HiddenSecret" not in out.read(name) for name in out.names)
        inner_workbook = out.read(out.related("ppt/charts/chart1.xml", "package"))
        with ZipFile(io.BytesIO(inner_workbook)) as zipfile:
            assert "xl/hidden.txt" not in zipfile.namelist()
            assert all(b"HiddenSecret" not in zipfile.read(name) for name in zipfile.namelist())
    deck.slides[0].hidden = False
    store.write(deck_id, deck)
    out = Package(export_powerpoint(store, deck_id))
    with out.archive:
        assert slide_part in out.names and b"HiddenSecret" not in out.read(slide_part)


def test_roundtrip_updates_every_clustered_cache_and_workbook(tmp_path: Path) -> None:
    source = fictional_powerpoint()
    chart_relationships = ET.Element(REL + "Relationships")
    _ = ET.SubElement(
        chart_relationships,
        REL + "Relationship",
        Id="rId1",
        Type=R[1:-1] + "/package",
        Target="../embeddings/clustered.xlsx",
    )
    source = _rewrite(
        source,
        {
            "ppt/charts/chart1.xml": fictional_chart(),
            "ppt/charts/_rels/chart1.xml.rels": xml_bytes(chart_relationships),
            "ppt/embeddings/clustered.xlsx": chart_workbook(
                [
                    ("S!$A$2:$A$3", ["Orchard", "Harbor"], False),
                    ("S!$B$1", ["Old"], False),
                    ("S!$C$1", ["New"], False),
                    ("S!$B$2:$B$3", ["3", "5"], True),
                    ("S!$C$2:$C$3", ["7", "9"], True),
                ]
            ),
        },
    )
    store, deck_id = _store(tmp_path, source)
    deck = store.read(deck_id)
    slide = deck.slides[1]
    slide.html = _set_text(slide.html, "Orchard", "Avenue")
    slide.html = _set_text(slide.html, "7", "8")
    store.write(deck_id, deck)
    package = Package(export_powerpoint(store, deck_id))
    with package.archive:
        part = "ppt/charts/chart1.xml"
        assert [n.text for n in package.xml(part).iter(C + "v")].count("Avenue") == 2
        data = package.read(package.related(part, "package"))
        assert _workbook_values(data, "S!$A$2:$A$3") == ["Avenue", "Harbor"]
        assert _workbook_values(data, "S!$C$2:$C$3") == ["8", "9"]


def test_roundtrip_rejects_unmapped_markup_theme_and_replaced_assets(tmp_path: Path) -> None:
    store, deck_id = _store(tmp_path, fictional_layers_powerpoint())
    deck = store.read(deck_id)
    slide = deck.slides[0]
    original = slide.html
    slide.html = slide.html.replace("</article>", "<p>Unmapped addition</p></article>")
    store.write(deck_id, deck)
    with pytest.raises(ValueError, match="markup or styling"):
        _ = export_powerpoint(store, deck_id)
    slide.html = original
    store.write(deck_id, deck)
    theme = store.folder(deck_id) / "theme.css"
    baseline = theme.read_bytes()
    _ = theme.write_bytes(baseline + b".slide{opacity:0}")
    with pytest.raises(ValueError, match="imported theme"):
        _ = export_powerpoint(store, deck_id)
    _ = theme.write_bytes(baseline)
    asset = next((store.folder(deck_id) / "assets").glob("*.png"))
    _ = asset.write_bytes(b"Replacement")
    with pytest.raises(ValueError, match="picture has been replaced"):
        _ = export_powerpoint(store, deck_id)


def test_roundtrip_duplicate_action_keeps_chart_edits_independent(tmp_path: Path) -> None:
    store, deck_id = _store(tmp_path, fictional_powerpoint())
    deck = store.read(deck_id)
    first, original = deck.slides
    result = SlideActions(store).duplicate(
        deck_id,
        [original.id],
        [first.id, original.id],
        {original.id: digest(original.model_dump_json())},
        "duplicate-chart",
    )
    (clone_id,) = result.added_ids
    deck = store.read(deck_id)
    clone = deck.slides[2]
    assert clone.id == clone_id
    clone.html = _set_text(clone.html, "24", "34")
    clone.title = "Fictional duplicate chart"
    # Put the cloned chart before its original to exercise order across the two components.
    deck.slides = [clone, first, original]
    store.write(deck_id, deck)
    store = Store(store.root, store.database)
    assert source_slide_ids(store, deck_id) == {first.id, original.id, clone_id}
    package = Package(export_powerpoint(store, deck_id))
    with package.archive:
        slide_refs = package.xml(package.presentation).findall(f"{P}sldIdLst/{P}sldId")
        clone_part, _, original_part = [
            package.resolve(package.presentation, element.attrib[R + "id"])
            for element in slide_refs
        ]
        cloned_chart = package.related(clone_part, "chart")
        original_chart = package.related(original_part, "chart")
        assert cloned_chart != original_chart
        assert any(node.text == "34" for node in package.xml(cloned_chart).iter(C + "v"))
        assert any(node.text == "24" for node in package.xml(original_chart).iter(C + "v"))
        assert _workbook_values(
            package.read(package.related(cloned_chart, "package")), "Sheet1!$B$2:$B$3"
        ) == ["12", "34"]
        assert _workbook_values(
            package.read(package.related(original_chart, "package")), "Sheet1!$B$2:$B$3"
        ) == ["12", "24"]


@pytest.mark.parametrize("image_type", ["png", "jpeg"])
def test_audience_picture_preserves_pixels_without_private_image_metadata(image_type: str) -> None:
    image = Image.new("RGB", (17, 12), (10, 20, 30))
    stream = io.BytesIO()
    private = b"PrivateImageMetadata"
    if image_type == "png":
        info = PngImagePlugin.PngInfo()
        info.add_text("Comment", private.decode())
        image.save(stream, "PNG", pnginfo=info)
    else:
        image.save(stream, "JPEG", comment=private, progressive=True)
    source = stream.getvalue()
    assert private in source
    public = audience_picture(source + private, image_type)
    assert private not in public
    with Image.open(io.BytesIO(source)) as before, Image.open(io.BytesIO(public)) as after:
        assert before.tobytes() == after.tobytes()


def test_package_metadata_opens_in_office_consumers(tmp_path: Path) -> None:
    store, deck_id = _store(tmp_path, fictional_powerpoint())
    with ZipFile(io.BytesIO(export_powerpoint(store, deck_id))) as exported:
        workbook_name = next(name for name in exported.namelist() if name.endswith(".xlsx"))
        with ZipFile(io.BytesIO(exported.read(workbook_name))) as workbook:
            for archive in (exported, workbook):
                for name in archive.namelist():
                    if name == "[Content_Types].xml" or name.endswith(".rels"):
                        root = minidom.parseString(archive.read(name)).documentElement
                        assert root is not None and root.prefix is None
                        assert root.getAttribute("xmlns") == root.namespaceURI
        types = ET.fromstring(exported.read("[Content_Types].xml"))
        theme = next(item for item in types if item.get("PartName", "").startswith("/ppt/theme/"))
        assert theme.get("ContentType") == "application/vnd.openxmlformats-officedocument.theme+xml"
