"""Build a chart workbook using just the chart's referenced cells."""

import io
import zipfile
from copy import deepcopy
from xml.etree import ElementTree as ET

from .graphics import workbook_range
from .package import REL, TYPES, R

S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
XML = "{http://www.w3.org/XML/1998/namespace}"


def xml_bytes(node: ET.Element) -> bytes:
    # LibreOffice requires the default namespace on OPC content types and relationships.
    # Declare it on this document only; ElementTree's namespace registry is process-wide.
    if node.tag in {TYPES + "Types", REL + "Relationships"}:
        node = deepcopy(node)
        namespace = TYPES if node.tag == TYPES + "Types" else REL
        node.set("xmlns", namespace[1:-1])
        for element in node.iter():
            element.tag = element.tag.removeprefix(namespace)
    return ET.tostring(node, encoding="utf-8", xml_declaration=True)


def zip_parts(parts: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return output.getvalue()


def chart_workbook(ranges: list[tuple[str, list[str], bool]]) -> bytes:
    """Copy current chart data into sheets under their original chart formula addresses."""
    sheets: dict[str, dict[int, dict[str, tuple[str, bool]]]] = {}
    for formula, values, numeric in ranges:
        sheet, column, first, last = workbook_range(formula)
        if len(values) != last - first + 1:
            raise ValueError("PowerPoint chart workbook range has the wrong size.")
        rows = sheets.setdefault(sheet, {})
        for row, value in enumerate(values, first):
            cells = rows.setdefault(row, {})
            if cells.setdefault(column, (value, numeric)) != (value, numeric):
                raise ValueError("PowerPoint chart edits conflict in a shared workbook cell.")
    parts: dict[str, bytes] = {}
    workbook = ET.Element(S + "workbook")
    entries = ET.SubElement(workbook, S + "sheets")
    relations = ET.Element(REL + "Relationships")
    root_relations = ET.Element(REL + "Relationships")
    _ = ET.SubElement(
        root_relations,
        REL + "Relationship",
        Id="rId1",
        Type=R[1:-1] + "/officeDocument",
        Target="xl/workbook.xml",
    )
    contents = ET.Element(TYPES + "Types")
    _ = ET.SubElement(
        contents,
        TYPES + "Default",
        Extension="rels",
        ContentType="application/vnd.openxmlformats-package.relationships+xml",
    )
    _ = ET.SubElement(
        contents,
        TYPES + "Override",
        PartName="/xl/workbook.xml",
        ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    )
    for index, (name, rows) in enumerate(sheets.items(), 1):
        rid = f"rId{index}"
        path = f"worksheets/sheet{index}.xml"
        _ = ET.SubElement(
            entries, S + "sheet", {"name": name, "sheetId": str(index), R + "id": rid}
        )
        _ = ET.SubElement(
            relations, REL + "Relationship", Id=rid, Type=R[1:-1] + "/worksheet", Target=path
        )
        _ = ET.SubElement(
            contents,
            TYPES + "Override",
            PartName="/xl/" + path,
            ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
        )
        worksheet = ET.Element(S + "worksheet")
        sheet_data = ET.SubElement(worksheet, S + "sheetData")
        for row, cells in sorted(rows.items()):
            row_element = ET.SubElement(sheet_data, S + "row", r=str(row))
            for column, (value, numeric) in sorted(
                cells.items(), key=lambda pair: (len(pair[0]), pair[0])
            ):
                cell = ET.SubElement(row_element, S + "c", r=f"{column}{row}")
                if numeric:
                    ET.SubElement(cell, S + "v").text = value
                else:
                    cell.set("t", "inlineStr")
                    inline = ET.SubElement(cell, S + "is")
                    ET.SubElement(inline, S + "t", {XML + "space": "preserve"}).text = value
        parts["xl/" + path] = xml_bytes(worksheet)
    parts["xl/workbook.xml"] = xml_bytes(workbook)
    parts["xl/_rels/workbook.xml.rels"] = xml_bytes(relations)
    parts["_rels/.rels"] = xml_bytes(root_relations)
    parts["[Content_Types].xml"] = xml_bytes(contents)
    return zip_parts(parts)
