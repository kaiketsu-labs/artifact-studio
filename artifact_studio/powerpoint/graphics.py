"""Convert supported DrawingML tables and charts to editable HTML objects."""

from __future__ import annotations

import html
import io
import math
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from .text import color, font

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _text(node: ET.Element) -> str:
    return "".join(item.text or "" for item in node.iter(f"{A}t"))


def _table(table: ET.Element, width: int, fonts: dict[str, str], colors: dict[str, str]) -> str:
    columns = table.findall(f"{A}tblGrid/{A}gridCol")
    rows = table.findall(f"{A}tr")
    if not columns or not rows:
        raise ValueError("empty table")
    widths = [int(col.get("w", "0")) for col in columns]
    if any(width <= 0 for width in widths):
        raise ValueError("invalid table column widths")
    out = [
        '<div style="width:100%;height:100%;container-type:inline-size">'
        '<table style="width:100%;height:100%;border-collapse:collapse;'
        'table-layout:fixed"><colgroup>'
    ]
    out.extend(f'<col style="width:{width / sum(widths) * 100:.4f}%">' for width in widths)
    out.append("</colgroup><tbody>")
    heights = [int(row.get("h", "0")) for row in rows]
    if any(height <= 0 for height in heights):
        raise ValueError("invalid table row heights")
    table_props = table.find(f"{A}tblPr")
    if table_props is not None and (
        len(table_props)
        or any(value not in ("0", "false") for value in table_props.attrib.values())
    ):
        raise ValueError("theme-backed table styles and banding are unsupported")
    for row, height in zip(rows, heights, strict=True):
        cells = row.findall(f"{A}tc")
        if len(cells) != len(columns):
            raise ValueError("invalid table row")
        out.append(f'<tr style="height:{height / sum(heights) * 100:.4f}%">')
        for cell in cells:
            if any(
                cell.get(attr) is not None for attr in ("gridSpan", "rowSpan", "hMerge", "vMerge")
            ):
                raise ValueError("merged table cells are unsupported")
            text = "<br>".join(html.escape(_text(p)) for p in cell.findall(f"{A}txBody/{A}p"))
            runs = cell.findall(f"{A}txBody/{A}p/{A}r")
            if len(runs) > 1 or len(cell.findall(f"{A}txBody/{A}p")) > 1:
                raise ValueError("mixed formatting or multiple table paragraphs are unsupported")
            props = cell.find(f"{A}tcPr")
            style = "border:1px solid #000;padding:0.5cqw 1cqw;vertical-align:top"
            if props is not None:
                if set(props.attrib) - {"marL", "marR", "marT", "marB", "anchor"} or any(
                    child.tag not in {f"{A}solidFill", f"{A}lnL", f"{A}lnR", f"{A}lnT", f"{A}lnB"}
                    for child in props
                ):
                    raise ValueError("unsupported table cell properties")
                for attr, side in (
                    ("marL", "left"),
                    ("marR", "right"),
                    ("marT", "top"),
                    ("marB", "bottom"),
                ):
                    if (margin := props.get(attr)) is not None:
                        if not margin.isdecimal():
                            raise ValueError("invalid table cell margin")
                        style += f";padding-{side}:{int(margin) / width * 100:.6f}cqw"
                anchor = props.get("anchor", "t")
                if anchor not in {"t", "ctr", "b"}:
                    raise ValueError("unsupported table cell alignment")
                style += ";vertical-align:" + {"t": "top", "ctr": "middle", "b": "bottom"}[anchor]
                for tag, side in (
                    ("lnL", "left"),
                    ("lnR", "right"),
                    ("lnT", "top"),
                    ("lnB", "bottom"),
                ):
                    line = props.find(f"{A}{tag}")
                    if line is None:
                        continue
                    dash = line.find(f"{A}prstDash")
                    if (
                        set(line.attrib) - {"w", "cap", "cmpd", "algn"}
                        or line.get("cap", "flat") != "flat"
                        or line.get("cmpd", "sng") != "sng"
                        or line.get("algn", "ctr") != "ctr"
                        or any(
                            child.tag
                            not in {
                                f"{A}solidFill",
                                f"{A}prstDash",
                                f"{A}round",
                                f"{A}headEnd",
                                f"{A}tailEnd",
                            }
                            for child in line
                        )
                        or any(
                            end.get("type", "none") != "none"
                            for end in (line.findall(f"{A}headEnd") + line.findall(f"{A}tailEnd"))
                        )
                        or (dash is not None and dash.get("val") != "solid")
                    ):
                        raise ValueError("unsupported table cell border")
                    line_width = line.get("w", "")
                    fill = line.find(f"{A}solidFill")
                    if not line_width.isdecimal() or fill is None:
                        raise ValueError("invalid table cell border")
                    style += (
                        f";border-{side}:{int(line_width) / width * 100:.6f}cqw "
                        f"solid {color(fill, colors)}"
                    )
            paragraph = cell.find(f"{A}txBody/{A}p/{A}pPr")
            if paragraph is not None:
                if (
                    set(paragraph.attrib) - {"indent", "marL", "algn"}
                    or paragraph.get("indent", "0") != "0"
                    or paragraph.get("marL", "0") != "0"
                    or any(child.tag != f"{A}buNone" for child in paragraph)
                ):
                    raise ValueError("unsupported table paragraph styling")
                align = paragraph.get("algn", "l")
                if align not in {"l", "ctr", "r"}:
                    raise ValueError("unsupported table paragraph alignment")
                style += ";text-align:" + {"l": "left", "ctr": "center", "r": "right"}[align]
            run = cell.find(f"{A}txBody/{A}p/{A}r/{A}rPr")
            if text and run is None:
                raise ValueError("table text requires explicit font styling")
            if run is not None:
                if set(run.attrib) - {"sz", "b", "i", "lang", "dirty"} or any(
                    child.tag not in {f"{A}solidFill", f"{A}latin", f"{A}ea", f"{A}cs"}
                    for child in run
                ):
                    raise ValueError("unsupported table text styling")
                size = run.get("sz", "")
                if not size.isdecimal() or not 0 < int(size) <= 40000:
                    raise ValueError("table text requires an explicit valid font size")
                style += f";font-size:{int(size) * 127 / width * 100:.6f}cqw"
                font_node = run.find(f"{A}latin")
                if font_node is None or not font_node.get("typeface"):
                    raise ValueError("table text requires an explicit font family")
                family = font_node.get("typeface", "")
                if any(
                    node.get("typeface") != family
                    for node in (run.findall(f"{A}ea") + run.findall(f"{A}cs"))
                ):
                    raise ValueError("mixed script table fonts are unsupported")
                style += f";font-family:{html.escape(font(family, fonts), quote=True)}"
                if run.get("b") == "1":
                    style += ";font-weight:bold"
                if run.get("i") == "1":
                    style += ";font-style:italic"
                fill = run.find(f"{A}solidFill")
                if fill is None:
                    raise ValueError("table text requires an explicit color")
                style += f";color:{color(fill, colors)}"
            if props is not None:
                fill = props.find(f"{A}solidFill")
                if fill is not None and (
                    len(fill) != 1 or fill[0].tag != f"{A}srgbClr" or len(fill[0])
                ):
                    raise ValueError("unsupported table cell fill")
                if fill is not None:
                    style += f";background:{color(fill, colors)}"
            out.append(f'<td style="{style}">{text}</td>')
        out.append("</tr>")
    return "".join(out) + "</tbody></table></div>"


def _points(node: ET.Element | None) -> list[str]:
    if node is None:
        return []
    cache = next(
        (
            child
            for child in node.iter()
            if child.tag in (f"{C}strCache", f"{C}numCache", f"{C}strLit", f"{C}numLit")
        ),
        None,
    )
    if cache is None:
        return []
    points: dict[int, str] = {}
    for point in cache.findall(f"{C}pt"):
        try:
            index = int(point.get("idx", "-1"))
        except ValueError as exc:
            raise ValueError("invalid chart point index") from exc
        value = point.findtext(f"{C}v")
        if index < 0 or index in points or value is None:
            raise ValueError("invalid chart point")
        points[index] = value
    if sorted(points) != list(range(len(points))):
        raise ValueError("sparse chart data is unsupported")
    return list(points.values())


def _xml(data: bytes) -> ET.Element:
    if len(data) > 8_000_000 or b"<!DOCTYPE" in data.upper() or b"\0" in data:
        raise ValueError("unsupported or oversized chart XML")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError("invalid chart XML") from exc
    if sum(1 for _ in root.iter()) > 300_000:
        raise ValueError("chart XML exceeds object limit")
    return root


def workbook_range(formula: str) -> tuple[str, str, int, int]:
    """Parse a simple contiguous chart reference in an embedded XLSX workbook."""
    match = re.fullmatch(
        r"(?:'([^']+)'|([^'!]+))!\$?([A-Z]+)\$?(\d+)(?::\$?([A-Z]+)\$?(\d+))?", formula
    )
    if not match:
        raise ValueError("unsupported chart workbook range")
    sheet_name = match.group(1) or match.group(2)
    col1, row1, col2, row2 = (
        match.group(3),
        int(match.group(4)),
        match.group(5) or match.group(3),
        int(match.group(6) or match.group(4)),
    )
    if col1 != col2 or row1 < 1 or row2 < row1 or row2 - row1 > 10000:
        raise ValueError("unsupported chart workbook range")
    return sheet_name, col1, row1, row2


def _workbook_values(data: bytes, formula: str) -> list[str]:
    """Read a simple contiguous chart range from an embedded XLSX workbook."""
    sheet_name, col1, row1, row2 = workbook_range(formula)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as book:
            entries = book.infolist()
            if (
                len(entries) > 5000
                or sum(info.file_size for info in entries) > 16_000_000
                or len({info.filename for info in entries}) != len(entries)
                or any(info.flag_bits & 1 or info.file_size > 8_000_000 for info in entries)
            ):
                raise ValueError("chart workbook is oversized, encrypted or has duplicate parts")

            def xml(name: str) -> ET.Element:
                return _xml(book.read(name))

            ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            pkg = "{http://schemas.openxmlformats.org/package/2006/relationships}"
            workbook = xml("xl/workbook.xml")
            sheets = [
                sheet
                for sheet in workbook.findall(f"{ns}sheets/{ns}sheet")
                if sheet.get("name") == sheet_name
            ]
            if len(sheets) != 1:
                raise ValueError("chart workbook sheet is missing")
            rels = xml("xl/_rels/workbook.xml.rels")
            relationships = [
                rel
                for rel in rels.findall(f"{pkg}Relationship")
                if rel.get("Id") == sheets[0].get(f"{R}id")
            ]
            if len(relationships) != 1 or relationships[0].get("TargetMode") == "External":
                raise ValueError("external chart workbook sheets are unsupported")
            target = relationships[0].get("Target", "")
            path = (
                posixpath.normpath(posixpath.join("xl", target.lstrip("/")))
                if not target.startswith("/")
                else target.lstrip("/")
            )
            if not path.startswith("xl/"):
                raise ValueError("invalid chart workbook sheet path")
            sheet = xml(path)
            cells = {cell.get("r", ""): cell for cell in sheet.findall(f".//{ns}c")}
            shared: list[str] = []
            if "xl/sharedStrings.xml" in book.namelist():
                shared = [
                    "".join(item.itertext())
                    for item in xml("xl/sharedStrings.xml").findall(f"{ns}si")
                ]
            result: list[str] = []
            for row in range(row1, row2 + 1):
                cell = cells.get(f"{col1}{row}")
                if cell is None or cell.find(f"{ns}f") is not None:
                    raise ValueError("missing or calculated chart workbook cell")
                value = cell.findtext(f"{ns}v")
                kind = cell.get("t", "n")
                if kind == "inlineStr":
                    inline = cell.find(f"{ns}is")
                    value = "".join(inline.itertext()) if inline is not None else None
                elif kind == "s" and value is not None:
                    value = shared[int(value)]
                elif kind not in ("n", "str"):
                    raise ValueError("unsupported chart workbook cell type")
                if value is None:
                    raise ValueError("missing chart workbook value")
                result.append(value)
            return result
    except (zipfile.BadZipFile, KeyError, ET.ParseError, IndexError, OverflowError) as exc:
        raise ValueError("invalid embedded chart workbook") from exc


def _verify_workbook(
    data: bytes, series: ET.Element, labels: list[str], values: list[float]
) -> None:
    category_formula = series.findtext(f"{C}cat/{C}strRef/{C}f")
    value_formula = series.findtext(f"{C}val/{C}numRef/{C}f")
    if not category_formula or not value_formula:
        raise ValueError("unsupported workbook-backed chart references")
    workbook_labels = _workbook_values(data, category_formula)
    try:
        workbook_values = [float(value) for value in _workbook_values(data, value_formula)]
    except ValueError as exc:
        raise ValueError("invalid chart workbook values") from exc
    if workbook_labels != labels or workbook_values != values:
        raise ValueError("chart workbook data does not match chart cache")


def _chart(
    data: bytes,
    part: str,
    read_part: Callable[[str], bytes],
    resolve: Callable[[str, str], str],
    colors: dict[str, str],
    fonts: dict[str, str],
    width: int,
    height: int | None = None,
) -> str:
    root = _xml(data)
    plot = root.find(f".//{C}plotArea")
    if plot is None:
        raise ValueError("missing chart plot")
    kinds = [child for child in plot if child.tag.endswith("Chart")]
    if len(kinds) != 1 or kinds[0].tag != f"{C}barChart":
        raise ValueError("unsupported chart type")
    chart = kinds[0]
    direction = chart.find(f"{C}barDir")
    if direction is None or direction.get("val") != "col":
        grouping = chart.find(f"{C}grouping")
        if direction is not None and direction.get("val") == "bar":
            if grouping is not None and grouping.get("val") == "percentStacked":
                raise ValueError("100% stacked horizontal bar charts are unsupported")
            raise ValueError("horizontal bar charts are unsupported")
        raise ValueError("unsupported chart direction")
    grouping = chart.find(f"{C}grouping")
    if grouping is not None and grouping.get("val") not in ("clustered", "standard"):
        raise ValueError("unsupported chart grouping")
    series = chart.findall(f"{C}ser")
    if len(series) != 1:
        from artifact_studio.powerpoint.clustered_chart import clustered_chart

        return clustered_chart(root, part, read_part, resolve, width, height or width, fonts)
    labels = _points(series[0].find(f"{C}cat"))
    raw = _points(series[0].find(f"{C}val"))
    if not labels or len(labels) != len(raw):
        raise ValueError("chart categories and values must match")
    try:
        numbers = [Decimal(value) for value in raw]
        values = [float(value) for value in numbers]
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise ValueError("invalid chart values") from exc
    if any(
        not math.isfinite(value) or number < 0 or (number != 0 and not 1e-300 <= value <= 1e300)
        for number, value in zip(numbers, values, strict=True)
    ):
        raise ValueError("negative or non-finite chart values are unsupported")
    external = root.find(f"{C}externalData")
    if external is not None:
        relationship = external.get(f"{R}id")
        if not relationship:
            raise ValueError("missing chart workbook relationship")
        _verify_workbook(read_part(resolve(part, relationship)), series[0], labels, values)
    chart_root = root.find(f"{C}chart")
    if chart_root is not None and any(
        chart_root.find(f"{C}{name}") is not None for name in ("title", "legend")
    ):
        raise ValueError("chart titles and legends are unsupported")
    if (
        root.find(f"{C}spPr") is not None
        or root.find(f"{C}style") is not None
        or plot.find(f"{C}layout") is not None
    ):
        raise ValueError("custom chart styles and plot layout are unsupported")
    if series[0].find(f"{C}dLbls") is not None or chart.find(f"{C}dLbls") is not None:
        raise ValueError("custom chart data labels are unsupported")
    if any(
        series[0].find(f"{C}{name}") is not None
        for name in ("dPt", "invertIfNegative", "errBars", "trendline")
    ):
        raise ValueError("custom chart points and series effects are unsupported")
    fill_props = series[0].find(f"{C}spPr")
    if fill_props is None:
        accent = colors.get("accent1", "")
        if not re.fullmatch(r"[0-9a-fA-F]{6}", accent):
            raise ValueError("chart series theme color is unresolved")
        series_color = "#" + accent
    elif len(fill_props) == 1 and fill_props[0].tag == f"{A}solidFill":
        series_color = color(fill_props[0], colors)
    else:
        raise ValueError("unsupported chart series fill or outline")
    categories = plot.findall(f"{C}catAx")
    axes = plot.findall(f"{C}valAx")
    if len(categories) != 1 or len(axes) != 1 or len(plot.findall(f"{C}serAx")):
        raise ValueError("secondary or missing chart axes are unsupported")
    category, value_axis = categories[0], axes[0]
    axis_ids = [item.get("val") for item in chart.findall(f"{C}axId")]
    category_id = category.find(f"{C}axId")
    value_id = value_axis.find(f"{C}axId")
    if (
        category_id is None
        or value_id is None
        or len(axis_ids) != 2
        or set(axis_ids) != {category_id.get("val"), value_id.get("val")}
    ):
        raise ValueError("chart axis IDs do not match")
    for current, other in ((category, value_id), (value_axis, category_id)):
        cross = current.find(f"{C}crossAx")
        if cross is None or cross.get("val") != other.get("val"):
            raise ValueError("chart axis cross references do not match")
    category_scale = category.find(f"{C}scaling")
    if category_scale is not None and any(
        child.tag != f"{C}orientation" or child.get("val") != "minMax" for child in category_scale
    ):
        raise ValueError("category axis orientation or scaling is unsupported")
    if any(
        category.find(f"{C}{name}") is not None
        for name in ("numFmt", "title", "spPr", "txPr", "majorGridlines", "minorGridlines")
    ):
        raise ValueError("custom category axis formatting is unsupported")
    axis = plot.find(f"{C}valAx")
    scale = axis.find(f"{C}scaling") if axis is not None else None
    if scale is not None and any(
        scale.find(f"{C}{name}") is not None for name in ("min", "max", "logBase")
    ):
        raise ValueError("custom chart axis scales are unsupported")
    if axis is not None and axis.find(f"{C}majorUnit") is not None:
        raise ValueError("custom chart axis tick intervals are unsupported")
    if axis is not None and any(
        axis.find(f"{C}{name}") is not None
        for name in ("numFmt", "title", "spPr", "txPr", "minorGridlines")
    ):
        raise ValueError("custom chart axis formatting is unsupported")
    gridlines = axis is not None and axis.find(f"{C}majorGridlines") is not None
    chart_text = root.find(f"{C}txPr/{A}p/{A}pPr/{A}defRPr")
    chart_size = chart_text.get("sz", "1800") if chart_text is not None else "1800"
    if not chart_size.isdecimal() or not 0 < int(chart_size) <= 40000:
        raise ValueError("invalid chart text size")
    if chart_text is not None and (set(chart_text.attrib) - {"sz"} or list(chart_text)):
        raise ValueError("custom chart text formatting is unsupported")
    family = html.escape(font("+mn-lt", fonts), quote=True)
    text_size = int(chart_size) * 127 / width * 100
    maximum = max(values) or 1
    magnitude = 10 ** math.floor(math.log10(maximum))
    maximum = math.ceil(maximum / magnitude) * magnitude
    result = [
        f'<div data-pptx-chart="column" data-chart-max="{maximum:g}" '
        'style="position:relative;container-type:inline-size;display:flex;align-items:end;gap:4%;'
        f'padding-left:8%;box-sizing:border-box;width:100%;height:100%;font-family:{family}">'
    ]
    title = _points(series[0].find(f"{C}tx"))
    auto_title_deleted = chart_root.find(f"{C}autoTitleDeleted") if chart_root is not None else None
    if title and (auto_title_deleted is None or auto_title_deleted.get("val") != "1"):
        result.append(
            '<span data-chart-title style="position:absolute;top:0;left:8%;width:92%;'
            f'text-align:center;font-size:{text_size * 1.1:.6f}cqw;font-weight:bold">'
            f"{html.escape(title[0])}</span>"
        )
    for index in range(7):
        ratio = index / 6
        result.append(
            f'<span data-chart-tick="{ratio:.12g}" style="position:absolute;left:8%;'
            f"bottom:{10 + ratio * 70:.4f}%;width:92%;height:0;"
            f'{"border-top:1px solid #ddd;" if gridlines else ""}">'
            '<span style="position:absolute;right:100%;top:0;transform:translate(-4px,-50%);'
            f'font-size:{text_size:.6f}cqw">{maximum * ratio:g}</span></span>'
        )
    for label, raw_value, value in zip(labels, raw, values, strict=True):
        result.append(
            '<div style="flex:1;min-width:0;height:100%;position:relative;'
            f"font-size:{text_size:.6f}cqw;"
            'display:flex;justify-content:center">'
        )
        result.append(
            f'<span data-chart-value="{html.escape(raw_value, quote=True)}" '
            f'aria-label="Chart value {html.escape(raw_value, quote=True)}" '
            f'style="position:absolute;z-index:1;bottom:10%;width:40%;'
            f'height:{value / maximum * 70:.4f}%;min-height:8px;opacity:0">'
            f"{html.escape(raw_value)}</span>"
        )
        result.append(
            f'<div data-chart-bar style="position:absolute;bottom:10%;background:{series_color};'
            "width:40%;"
            f'height:{value / maximum * 70:.4f}%"></div>'
        )
        result.append(
            '<span style="position:absolute;bottom:0;text-align:center">'
            f"{html.escape(label)}</span></div>"
        )
    return "".join(result) + "</div>"


def graphic(
    frame: ET.Element,
    part: str,
    read_part: Callable[[str], bytes],
    resolve: Callable[[str, str], str],
    width: int,
    height: int,
    *,
    colors: dict[str, str] | None = None,
    fonts: dict[str, str] | None = None,
) -> str:
    """Return editable markup for a supported DrawingML graphic frame."""
    if width <= 0 or height <= 0:
        raise ValueError("invalid graphic size")
    data = frame.find(f".//{A}graphicData")
    if data is None:
        raise ValueError("missing graphic data")
    table = data.find(f"{A}tbl")
    if table is not None:
        return _table(table, width, fonts or {}, colors or {})
    chart = data.find(f"{C}chart")
    if chart is not None:
        relationship = chart.get(f"{R}id")
        if not relationship:
            raise ValueError("missing chart relationship")
        chart_part = resolve(part, relationship)
        return _chart(
            read_part(chart_part),
            chart_part,
            read_part,
            resolve,
            colors or {},
            fonts or {},
            width,
            height,
        )
    raise ValueError("unsupported graphic frame")
