"""Build an audience package from the validated slide and drawing relationship graph."""

import hashlib
import posixpath
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET

from .graphics import C
from .importer import OriginalObject, local, placeholder, required
from .package import REL, TYPES, A, P, Package, R
from .picture_export import audience_picture
from .roundtrip_edits import Chart
from .workbook_export import chart_workbook, xml_bytes, zip_parts
from .xml_patch import XmlPatch

_REL_TYPE = R[1:-1] + "/"
_PRESENTATION = "application/vnd.openxmlformats-officedocument.presentationml."
_DRAWING = "application/vnd.openxmlformats-officedocument.drawingml."
_MIME = {
    "officeDocument": _PRESENTATION + "presentation.main+xml",
    "slide": _PRESENTATION + "slide+xml",
    "slideLayout": _PRESENTATION + "slideLayout+xml",
    "slideMaster": _PRESENTATION + "slideMaster+xml",
    "theme": "application/vnd.openxmlformats-officedocument.theme+xml",
    "chart": _DRAWING + "chart+xml",
    "package": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "core": "application/vnd.openxmlformats-package.core-properties+xml",
}
_ROOTS = {
    "officeDocument": P + "presentation",
    "slide": P + "sld",
    "slideLayout": P + "sldLayout",
    "slideMaster": P + "sldMaster",
    "theme": A + "theme",
    "chart": C + "chartSpace",
}
_PRIVATE = {
    "authorLst",
    "cmAuthorLst",
    "cmLst",
    "commentLst",
    "extLst",
    "custDataLst",
    "custShowLst",
    "notesMasterIdLst",
    "handoutMasterIdLst",
    "photoAlbum",
    "modifyVerifier",
    "notes",
    "tags",
    "tagLst",
}

type Relation = tuple[str, str, str]


def _rels_name(part: str) -> str:
    parent, name = posixpath.split(part)
    return posixpath.join(parent, "_rels", name + ".rels")


def _target(part: str, destination: str) -> str:
    return quote(posixpath.relpath(destination, posixpath.dirname(part) or "."), safe="/")


def _reference_ids(node: ET.Element) -> set[str]:
    return {
        value
        for element in node.iter()
        for name, value in element.attrib.items()
        if name.startswith(R)
    }


class AudiencePackage:
    """Copy reachable XML and verified images, replacing each chart workbook."""

    def __init__(
        self,
        package: Package,
        folder: Path,
        assets: dict[str, bytes],
        objects: dict[str, dict[str, OriginalObject]],
        charts: dict[str, Chart],
    ):
        self.source = package
        self.folder = folder
        self.assets = assets
        self.objects = {original.shape for slide in objects.values() for original in slide.values()}
        self.charts = charts
        self.parts: dict[str, bytes] = {}
        self.types: dict[str, str] = {}
        self.patches: dict[str, XmlPatch] = {}
        self.slide_layouts: dict[str, str] = {}
        self.layout_masters: dict[str, str] = {}

    def patch(self, part: str) -> XmlPatch:
        if part not in self.patches:
            self.patches[part] = XmlPatch(self.source.read(part), self.source.xml(part))
        return self.patches[part]

    def _put(self, part: str, data: bytes, mime: str) -> None:
        if (
            part in self.parts
            or part == "[Content_Types].xml"
            or not all(segment not in {"", ".", "..", "_rels"} for segment in part.split("/"))
        ):
            raise ValueError("PowerPoint export found colliding package parts.")
        self.parts[part] = data
        self.types[part] = mime

    def _write_relationships(self, part: str, values: list[Relation]) -> None:
        if values:
            root = ET.Element(REL + "Relationships")
            for rid, kind, target in values:
                _ = ET.SubElement(
                    root, REL + "Relationship", Id=rid, Type=kind, Target=_target(part, target)
                )
            path = _rels_name(part)
            if path in self.parts:
                raise ValueError("PowerPoint export found colliding relationships.")
            self.parts[path] = xml_bytes(root)

    def _edge(self, part: str, destination: str, kind: str) -> Relation:
        return self._source_edges(part, {destination}, kind)[0]

    def _source_edges(self, part: str, destinations: set[str], kind: str) -> list[Relation]:
        result: list[Relation] = []
        for rid, (rel_type, _, _) in self.source.relationships(part).items():
            if rel_type == _REL_TYPE + kind:
                target = self.source.resolve(part, rid)
                if target in destinations:
                    result.append((rid, rel_type, target))
        if len(result) != len(destinations):
            raise ValueError(f"PowerPoint export found ambiguous {kind} relationships.")
        return result

    def _sanitize(self, node: ET.Element, patch: XmlPatch) -> None:
        for child in node:
            if local(child) in _PRIVATE:
                patch.replace(child, b"")
            elif not child.tag.startswith((P, A, C)):
                raise ValueError("PowerPoint export found unsupported extended markup.")
            else:
                self._sanitize(child, patch)

    def _prepare_templates(self, part: str, kind: str, patch: XmlPatch) -> list[Relation]:
        if kind == "slide":
            return [self._edge(part, self.slide_layouts[part], "slideLayout")]
        if kind not in {"slideLayout", "slideMaster"}:
            return []
        root = self.source.xml(part)
        tree = required(root, f"{P}cSld/{P}spTree")
        for shape in tree:
            if shape.tag in {P + "nvGrpSpPr", P + "grpSpPr"} or shape in self.objects:
                continue
            if placeholder(shape) is not None:
                for text in shape.findall(f"{P}txBody/{A}p/{A}r/{A}t"):
                    patch.text(text, "")
            else:
                patch.replace(shape, b"")
        if kind == "slideLayout":
            return [self._edge(part, self.layout_masters[part], "slideMaster")]
        layouts = {layout for layout, master in self.layout_masters.items() if master == part}
        reverse = self._source_edges(part, layouts, "slideLayout")
        ids = {rid for rid, _, _ in reverse}
        for entry in root.findall(f"{P}sldLayoutIdLst/{P}sldLayoutId"):
            if entry.get(R + "id") not in ids:
                patch.replace(entry, b"")
        return reverse + [self._edge(part, self.source.related(part, "theme"), "theme")]

    def _workbook(self, part: str, patch: XmlPatch) -> list[Relation]:
        chart = self.charts.get(part)
        if chart is None:
            raise ValueError("PowerPoint export found an unmapped chart.")
        for node, value in chart.edits.items():
            patch.text(node, value)
        external = self.source.xml(part).find(C + "externalData")
        if external is None:
            return []
        rid = external.get(R + "id", "")
        if self.source.relationships(part).get(rid, ("", "", ""))[0] != _REL_TYPE + "package":
            raise ValueError("PowerPoint chart workbook has an unsupported relationship.")
        _ = self.source.resolve(part, rid)
        path = "artifact-studio-embeddings/" + hashlib.sha256(part.encode()).hexdigest() + ".xlsx"
        ranges: list[tuple[str, list[str], bool]] = []
        for column in chart.ranges:
            if column.formula:
                values = [chart.edits.get(node, node.text or "") for node in column.cells]
                ranges.append((column.formula, values, column.numeric))
            elif column.cells and any(node in chart.edits for node in column.cells):
                raise ValueError("PowerPoint chart edit has no embedded workbook range.")
        self._put(path, chart_workbook(ranges), _MIME["package"])
        return [(rid, _REL_TYPE + "package", path)]

    def _image(self, part: str) -> None:
        if part in self.types:
            if self.types[part] not in {"image/png", "image/jpeg"}:
                raise ValueError("PowerPoint picture has a conflicting package path.")
            return
        data = self.source.read(part)
        signature = hashlib.sha256(data).hexdigest()
        candidates = [
            name for name in (f"{signature}.png", f"{signature}.jpeg") if name in self.assets
        ]
        if len(candidates) != 1:
            raise ValueError(
                "PowerPoint export found a picture outside the imported slide content."
            )
        asset = self.folder / "assets" / candidates[0]
        if (
            asset.parent.is_symlink()
            or asset.is_symlink()
            or not asset.is_file()
            or asset.stat().st_size != len(data)
        ):
            raise ValueError("An imported PowerPoint picture has been replaced or removed.")
        if hashlib.sha256(asset.read_bytes()).hexdigest() != signature:
            raise ValueError("An imported PowerPoint picture has been replaced or removed.")
        kind = asset.suffix.removeprefix(".")
        self._put(part, audience_picture(data, kind), "image/" + kind)

    def _walk(self, part: str, kind: str, edges: list[Relation] | None = None) -> None:
        if part in self.types:
            if self.types[part] != _MIME[kind]:
                raise ValueError("PowerPoint export found a part with conflicting types.")
            return
        root = self.source.xml(part)
        if root.tag != _ROOTS[kind]:
            raise ValueError(f"PowerPoint export found an invalid {kind} part.")
        patch = self.patch(part)
        self._sanitize(root, patch)
        relations = list(edges or ()) + self._prepare_templates(part, kind, patch)
        if kind == "chart":
            relations.extend(self._workbook(part, patch))
        data = patch.render()
        referenced = _reference_ids(ET.fromstring(data))
        known = {rid for rid, _, _ in relations}
        for rid in sorted(referenced - known):
            relationship = self.source.relationships(part).get(rid)
            if relationship is None or relationship[0] not in (
                _REL_TYPE + "image",
                _REL_TYPE + "chart",
            ):
                raise ValueError("PowerPoint export found an unsupported referenced resource.")
            relations.append((rid, relationship[0], self.source.resolve(part, rid)))
        self._put(part, data, _MIME[kind])
        self._write_relationships(part, relations)
        for _, relation_type, target in relations:
            child_kind = relation_type.removeprefix(_REL_TYPE)
            if child_kind == "image":
                self._image(target)
            elif child_kind != "package":
                self._walk(target, child_kind)

    def build(self, slides: list[ET.Element], title: str) -> bytes:
        pres = self.source.presentation
        roots = self.source.xml(pres)
        slides_by_rid = {slide.attrib[R + "id"]: slide for slide in slides}
        slides_by_part = {self.source.resolve(pres, rid): rid for rid in slides_by_rid}
        if len(slides_by_rid) != len(slides) or len(slides_by_part) != len(slides):
            raise ValueError("PowerPoint export found multiple slides mapped to one slide part.")
        self.slide_layouts = {
            target: self.source.related(target, "slideLayout") for target in slides_by_part
        }
        self.layout_masters = {
            layout: self.source.related(layout, "slideMaster")
            for layout in set(self.slide_layouts.values())
        }
        masters = self._source_edges(pres, set(self.layout_masters.values()), "slideMaster")
        master_ids = {rid for rid, _, _ in masters}
        patch = self.patch(pres)
        for entry in roots.findall(f"{P}sldMasterIdLst/{P}sldMasterId"):
            if entry.get(R + "id") not in master_ids:
                patch.replace(entry, b"")
        slide_elements = required(roots, P + "sldIdLst").findall(P + "sldId")
        if slide_elements != slides:
            for index, element in enumerate(slide_elements):
                patch.replace(element, ET.tostring(slides[index]) if index < len(slides) else b"")
        relations = [
            (rid, _REL_TYPE + "slide", target) for target, rid in slides_by_part.items()
        ] + masters
        self._walk(pres, "officeDocument", relations)
        core_path = "docProps/artifact-studio-core.xml"
        core = ET.Element(
            "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}coreProperties"
        )
        ET.SubElement(core, "{http://purl.org/dc/elements/1.1/}title").text = title
        self._put(core_path, xml_bytes(core), _MIME["core"])
        self._write_relationships(
            "",
            [
                ("rId1", _REL_TYPE + "officeDocument", pres),
                (
                    "rId2",
                    "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
                    core_path,
                ),
            ],
        )
        content = ET.Element(TYPES + "Types")
        _ = ET.SubElement(
            content,
            TYPES + "Default",
            Extension="rels",
            ContentType="application/vnd.openxmlformats-package.relationships+xml",
        )
        for path, mime in self.types.items():
            _ = ET.SubElement(
                content,
                TYPES + "Override",
                PartName="/" + quote(path, safe="/"),
                ContentType=mime,
            )
        self.parts["[Content_Types].xml"] = xml_bytes(content)
        return zip_parts(self.parts)
