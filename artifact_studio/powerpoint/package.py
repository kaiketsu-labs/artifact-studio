"""Read internal Office Open XML parts with bounded expansion and no external I/O."""

import io
import posixpath
import zipfile
from functools import cached_property
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
TYPES = "{http://schemas.openxmlformats.org/package/2006/content-types}"


class Package:
    def __init__(self, data: bytes):
        self.archive = zipfile.ZipFile(io.BytesIO(data))
        entries = self.archive.infolist()
        if len(entries) > 5000 or sum(e.file_size for e in entries) > 150_000_000:
            raise ValueError("PowerPoint package exceeds 5,000 parts or 150 MB expanded.")
        self.names = {e.filename for e in entries}
        if len(self.names) != len(entries) or any(
            e.file_size > 45_000_000 or e.flag_bits & 1 for e in entries
        ):
            raise ValueError("Duplicate, encrypted or oversized PowerPoint part.")
        self._xml: dict[str, ET.Element] = {}
        self._rels: dict[str, dict[str, tuple[str, str, str]]] = {}
        self._nodes = 0

    def read(self, part: str) -> bytes:
        if part not in self.names:
            raise ValueError(f"Missing PowerPoint package part {part}.")
        return self.archive.read(part)

    def xml(self, part: str) -> ET.Element:
        if part not in self._xml:
            if part in self.names and self.archive.getinfo(part).file_size > 8_000_000:
                raise ValueError(f"XML part {part} is too large.")
            data = self.read(part)
            if b"<!DOCTYPE" in data.upper() or b"\0" in data:
                raise ValueError(f"Unsupported XML declaration in {part}.")
            node = ET.fromstring(data)
            self._nodes += sum(1 for _ in node.iter())
            if self._nodes > 300_000:
                raise ValueError("PowerPoint XML exceeds the object limit.")
            self._xml[part] = node
        return self._xml[part]

    def relationships(self, part: str) -> dict[str, tuple[str, str, str]]:
        if part not in self._rels:
            folder, name = posixpath.split(part)
            rels = posixpath.join(folder, "_rels", name + ".rels")
            values: dict[str, tuple[str, str, str]] = {}
            if rels in self.names:
                for rel in self.xml(rels).findall(REL + "Relationship"):
                    rid = rel.attrib["Id"]
                    if rid in values:
                        raise ValueError(f"Duplicate relationship {rid} in {part}.")
                    values[rid] = (
                        rel.attrib["Type"],
                        rel.attrib["Target"],
                        rel.get("TargetMode", "Internal"),
                    )
            self._rels[part] = values
        return self._rels[part]

    def resolve(self, part: str, rid: str) -> str:
        rel = self.relationships(part).get(rid)
        if not rel:
            raise ValueError(f"Missing relationship {rid} in {part}.")
        _, path, mode = rel
        path = unquote(path)
        uri = urlsplit(path)
        if (
            mode != "Internal"
            or uri.scheme
            or uri.netloc
            or uri.query
            or uri.fragment
            or "\\" in path
        ):
            raise ValueError("Externally linked resources are unsupported. Embed the resource.")
        target = posixpath.normpath(posixpath.join(posixpath.dirname(part), path)).lstrip("/")
        if target not in self.names or target.startswith("../"):
            raise ValueError(f"Missing or invalid related part {target}.")
        return target

    def related(self, part: str, kind: str) -> str:
        typename = R[1:-1] + "/" + kind
        matches = [r for r, value in self.relationships(part).items() if value[0] == typename]
        if len(matches) != 1:
            raise ValueError(f"Expected one {kind} relationship in {part or 'the package'}.")
        return self.resolve(part, matches[0])

    @cached_property
    def presentation(self) -> str:
        part = self.related("", "officeDocument")
        types = self.xml("[Content_Types].xml")
        mime = next(
            (
                e.get("ContentType")
                for e in types.findall(TYPES + "Override")
                if e.get("PartName") == "/" + part
            ),
            "",
        )
        if (
            mime
            != "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
        ):
            raise ValueError("Choose a macro-free .pptx presentation.")
        return part
