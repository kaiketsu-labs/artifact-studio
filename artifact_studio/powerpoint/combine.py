"""Join native authored slides to a sanitized source deck in the current Studio order."""

import io
import posixpath
import zipfile
from collections.abc import Sequence
from typing import cast
from xml.dom import minidom

from .package import REL, TYPES, P, Package, R

_P, _R, _REL = (name[1:-1] for name in (P, R, REL))


def _document(package: Package, part: str) -> minidom.Document:
    return minidom.parseString(package.read(part))


def _items(root: minidom.Element, ns: str, name: str) -> list[minidom.Element]:
    return list(root.getElementsByTagNameNS(ns, name))


def _part_relationships(part: str) -> str:
    folder, name = posixpath.split(part)
    return posixpath.join(folder, "_rels", name + ".rels")


def _child(document: minidom.Document, parent: minidom.Element, name: str) -> minidom.Element:
    prefix = parent.prefix
    child = document.createElementNS(parent.namespaceURI, f"{prefix}:{name}" if prefix else name)
    _ = parent.appendChild(child)
    return child


def combine_slides(source: bytes, authored: bytes, order: Sequence[bool]) -> bytes:
    """A true entry selects the next source slide. Both inputs are server-produced packages."""
    original, generated = Package(source), Package(authored)
    try:
        if not any(order) or all(order):
            raise ValueError("Combining decks requires both original and authored slides.")
        expanded = sum(
            entry.file_size for deck in (original, generated) for entry in deck.archive.infolist()
        )
        if expanded > 150_000_000:
            raise ValueError("The combined PowerPoint exceeds the 150 MB package budget.")
        main = original.presentation
        pp = _document(original, main)
        gp = _document(generated, generated.presentation)
        main_root = pp.documentElement
        assert main_root is not None and gp.documentElement is not None
        (main_size,) = _items(main_root, _P, "sldSz")
        (new_size,) = _items(gp.documentElement, _P, "sldSz")
        if any(main_size.getAttribute(key) != new_size.getAttribute(key) for key in ("cx", "cy")):
            raise ValueError("All slides in one PowerPoint file must have the same canvas size.")
        (slide_list,) = _items(main_root, _P, "sldIdLst")
        (masters,) = _items(main_root, _P, "sldMasterIdLst")
        slides = _items(slide_list, _P, "sldId")
        added = _items(gp.documentElement, _P, "sldId")
        if order.count(True) != len(slides) or order.count(False) != len(added):
            raise ValueError("The exported slides no longer match the Studio slide order.")

        # Keep all original names and relationships; a new namespace holds generated parts.
        prefix = "ppt/studio/"
        while any(part.startswith(prefix) for part in original.names):
            prefix += "added/"
        main_rels = _part_relationships(main)
        rels = _document(original, main_rels)
        types = _document(original, "[Content_Types].xml")
        assert rels.documentElement is not None and types.documentElement is not None
        known_ids = set(original.relationships(main))
        copied: dict[str, bytes] = {}
        source_types = generated.xml("[Content_Types].xml")
        overrides = {
            elem.attrib["PartName"].lstrip("/"): elem.attrib["ContentType"]
            for elem in source_types.findall(TYPES + "Override")
        }
        defaults = {
            elem.attrib["Extension"]: elem.attrib["ContentType"]
            for elem in source_types.findall(TYPES + "Default")
        }

        # Layout IDs and master IDs share a number range across all included masters.
        master_ids = {int(node.getAttribute("id")) for node in _items(masters, _P, "sldMasterId")}
        for node in _items(masters, _P, "sldMasterId"):
            part = original.resolve(main, node.getAttributeNS(_R, "id"))
            master_ids.update(
                int(child.attrib["id"]) for child in original.xml(part).iter(P + "sldLayoutId")
            )

        def master_id() -> str:
            value = max(master_ids, default=2147483647) + 1
            if value > 4294967295:
                value = next(i for i in range(2147483648, 4294967296) if i not in master_ids)
            master_ids.add(value)
            return str(value)

        def copy_part(part: str) -> str:
            target = prefix + part
            if target in copied:
                return target
            data = generated.read(part)
            mime = overrides.get(part) or defaults[part.rsplit(".", 1)[-1]]
            entry = _child(types, cast(minidom.Element, types.documentElement), "Override")
            entry.setAttribute("PartName", "/" + target)
            entry.setAttribute("ContentType", mime)
            if mime.endswith("presentationml.slideMaster+xml"):
                document = _document(generated, part)
                assert document.documentElement is not None
                for layout in _items(document.documentElement, _P, "sldLayoutId"):
                    layout.setAttribute("id", master_id())
                data = document.toxml(encoding="utf-8")
            copied[target] = data
            relpart = _part_relationships(part)
            if relpart in generated.names:
                document = _document(generated, relpart)
                assert document.documentElement is not None
                for link in _items(document.documentElement, _REL, "Relationship"):
                    target_part = generated.resolve(part, link.getAttribute("Id"))
                    destination = copy_part(target_part)
                    link.setAttribute(
                        "Target", posixpath.relpath(destination, posixpath.dirname(target))
                    )
                copied[_part_relationships(target)] = document.toxml(encoding="utf-8")
            return target

        def attach(node: minidom.Element) -> str:
            rid = node.getAttributeNS(_R, "id")
            dest = copy_part(generated.resolve(generated.presentation, rid))
            candidate = "rStudio" + str(len(known_ids) + 1)
            while candidate in known_ids:
                candidate += "a"
            known_ids.add(candidate)
            entry = _child(rels, cast(minidom.Element, rels.documentElement), "Relationship")
            entry.setAttribute("Id", candidate)
            entry.setAttribute("Type", generated.relationships(generated.presentation)[rid][0])
            entry.setAttribute("Target", posixpath.relpath(dest, posixpath.dirname(main)))
            return candidate

        def set_relation(node: minidom.Element, rid: str) -> None:
            node.setAttribute("xmlns:r", _R)
            node.setAttributeNS(_R, "r:id", rid)

        for master in _items(gp.documentElement, _P, "sldMasterId"):
            rid = attach(master)
            new = _child(pp, masters, "sldMasterId")
            new.setAttribute("id", master_id())
            set_relation(new, rid)
        ids = {int(node.getAttribute("id")) for node in slides}
        for node in slides:
            _ = slide_list.removeChild(node)
        existing, fresh = iter(slides), iter(added)
        slide_id = 256
        for retained in order:
            if retained:
                _ = slide_list.appendChild(next(existing))
            else:
                while slide_id in ids:
                    slide_id += 1
                new = _child(pp, slide_list, "sldId")
                new.setAttribute("id", str(slide_id))
                set_relation(new, attach(next(fresh)))
                slide_id += 1
        copied.update(
            {
                main: pp.toxml(encoding="utf-8"),
                main_rels: rels.toxml(encoding="utf-8"),
                "[Content_Types].xml": types.toxml(encoding="utf-8"),
            }
        )
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for part in sorted(original.names - copied.keys()):
                archive.writestr(part, original.read(part))
            for part, content in copied.items():
                archive.writestr(part, content)
        return stream.getvalue()
    finally:
        original.archive.close()
        generated.archive.close()
