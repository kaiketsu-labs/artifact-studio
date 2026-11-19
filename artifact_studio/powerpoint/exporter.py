"""Build editable PowerPoint slides from rendered Studio slide descriptions."""

from __future__ import annotations

import base64
import binascii
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, cast
from urllib.parse import unquote_to_bytes

from lxml.etree import Element, _Element  # pyright: ignore[reportPrivateUsage]
from PIL import Image, ImageColor, UnidentifiedImageError
from pptx import Presentation
from pptx.chart.data import ChartData
from pptx.chart.plot import BarPlot, _BasePlot  # pyright: ignore[reportPrivateUsage]
from pptx.chart.series import _BaseSeries  # pyright: ignore[reportPrivateUsage]
from pptx.chart.xlsx import CategoryWorkbookWriter
from pptx.dml.color import ColorFormat, RGBColor
from pptx.dml.fill import FillFormat
from pptx.dml.line import LineFormat
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE, MSO_CONNECTOR
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.opc.constants import RELATIONSHIP_TYPE
from pptx.opc.package import Part
from pptx.oxml.ns import qn
from pptx.oxml.shapes.shared import CT_ShapeProperties
from pptx.oxml.xmlchemy import OxmlElement
from pptx.shapes.autoshape import Shape
from pptx.shapes.base import BaseShape
from pptx.shapes.graphfrm import GraphicFrame
from pptx.shapes.shapetree import GroupShapes, SlideShapes
from pptx.text.text import Font, TextFrame
from pptx.util import Emu, Pt, lazyproperty
from xlsxwriter import Workbook  # pyright: ignore[reportMissingTypeStubs]
from xlsxwriter.worksheet import Worksheet  # pyright: ignore[reportMissingTypeStubs]

type Description = Mapping[str, object]
type Shapes = SlideShapes | GroupShapes

_PX_TO_EMU = 9525
_NO_STYLE_NO_GRID = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"
_SCALE = ContextVar("powerpoint_export_scale", default=1.0)
_PRESET_SHAPES = {member.xml_value: member for member in MSO_AUTO_SHAPE_TYPE}
_ALIGNMENTS = {
    "left": PP_ALIGN.LEFT,
    "center": PP_ALIGN.CENTER,
    "right": PP_ALIGN.RIGHT,
    "justify": PP_ALIGN.JUSTIFY,
}
_ANCHORS = {
    "top": MSO_ANCHOR.TOP,
    "middle": MSO_ANCHOR.MIDDLE,
    "center": MSO_ANCHOR.MIDDLE,
    "bottom": MSO_ANCHOR.BOTTOM,
}
_CHART_TYPES = {
    "clustered-column": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "clustered-bar": XL_CHART_TYPE.BAR_CLUSTERED,
    "line": XL_CHART_TYPE.LINE,
    "pie": XL_CHART_TYPE.PIE,
}


def _description(value: object) -> Description:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in cast(Mapping[object, object], value)
    ):
        raise ValueError("Expected a PowerPoint object description")
    return cast(Description, value)


def _items(value: object) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("Expected a list in the PowerPoint description")
    return cast(Sequence[object], value)


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected text in the PowerPoint description")
    return value


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("Expected a finite number in the PowerPoint description")
    return float(value)


def _px(value: object) -> Emu:
    return Emu(round(_number(value) * _PX_TO_EMU * _SCALE.get()))


def _font_size(value: object) -> Pt:
    return Pt(max(1, min(_number(value) * 0.75 * _SCALE.get(), 4000)))


def _color(value: object) -> tuple[str, int]:
    color = _text(value).strip().lower()
    if color == "transparent":
        return "000000", 0
    rgba = re.fullmatch(
        r"rgba?\(\s*(\d+(?:\.\d+)?)\s*(?:,\s*|\s+)"
        r"(\d+(?:\.\d+)?)\s*(?:,\s*|\s+)(\d+(?:\.\d+)?)"
        r"(?:\s*[,/]\s*(\d*\.?\d+%?))?\s*\)",
        color,
    )
    if rgba:
        red, green, blue, opacity = rgba.groups()
        channels = [max(0, min(255, round(float(part)))) for part in (red, green, blue)]
        if opacity is None:
            alpha = 100000
        elif opacity.endswith("%"):
            alpha = round(float(opacity[:-1]) * 1000)
        else:
            alpha = round(float(opacity) * 100000)
        return bytes(channels).hex().upper(), max(0, min(100000, alpha))
    red, green, blue, alpha = cast(tuple[int, int, int, int], ImageColor.getcolor(color, "RGBA"))
    return f"{red:02X}{green:02X}{blue:02X}", round(alpha / 255 * 100000)


def _xml(tag: str) -> _Element:
    return cast(_Element, OxmlElement(tag))


def _color_element(tag: str, value: object) -> _Element:
    hex_color, alpha = _color(value)
    node = _xml(tag)
    node.set("val", hex_color)
    if alpha != 100000:
        opacity = _xml("a:alpha")
        opacity.set("val", str(alpha))
        node.append(opacity)
    return node


def _format_color(formatting: ColorFormat, value: object) -> None:
    color, alpha = _color(value)
    formatting.rgb = RGBColor(*bytes.fromhex(color))
    if alpha < 100000:
        fill = cast(_Element, formatting._xFill)  # pyright: ignore[reportPrivateUsage]
        rgb = fill.find(qn("a:srgbClr"))
        if rgb is not None:
            opacity = _xml("a:alpha")
            opacity.set("val", str(alpha))
            rgb.append(opacity)


def _fill(
    fill: FillFormat, color: object, gradient: object = None, *, background: bool = False
) -> None:
    if gradient is not None:
        details = _description(gradient)
        stops = [_description(item) for item in _items(details.get("stops"))]
        if len(stops) < 2:
            raise ValueError("A PowerPoint gradient requires two or more stops")
        fill.gradient()
        fill.gradient_angle = (90 - _number(details.get("angle", 180))) % 360
        properties = cast(_Element, fill._xPr)  # pyright: ignore[reportPrivateUsage]
        stop_list = properties.find(f"{qn('a:gradFill')}/{qn('a:gsLst')}")
        if stop_list is None:
            raise ValueError("Could not create a PowerPoint gradient")
        for stop in list(stop_list):
            stop_list.remove(stop)
        for item in stops:
            position = _number(item.get("position"))
            if not 0 <= position <= 1:
                raise ValueError("PowerPoint gradient positions must be between zero and one")
            stop = _xml("a:gs")
            stop.set("pos", str(round(position * 100000)))
            stop.append(_color_element("a:srgbClr", item.get("color")))
            stop_list.append(stop)
    elif color is not None and _color(color)[1]:
        fill.solid()
        _format_color(fill.fore_color, color)
    elif not background:
        fill.background()


def _line(line: LineFormat, element: Description) -> None:
    color = element.get("stroke")
    width = _number(element.get("strokeWidth", 1))
    if color is None or width <= 0 or _color(color)[1] == 0:
        line.fill.background()
        return
    _format_color(line.color, color)
    line.width = _px(width)
    style = element.get("strokeStyle")
    if style == "dotted":
        line.dash_style = MSO_LINE_DASH_STYLE.ROUND_DOT
    elif style == "dashed":
        line.dash_style = MSO_LINE_DASH_STYLE.DASH


def _font(font: Font, styles: Description) -> None:
    if "fontFamily" in styles:
        font.name = _text(styles["fontFamily"]).split(",")[0].strip(" \"'")
    if "fontSize" in styles:
        font.size = _font_size(styles["fontSize"])
    for name in ("bold", "italic", "underline"):
        if name in styles:
            setattr(font, name, bool(styles[name]))
    if "color" in styles:
        _format_color(font.color, styles["color"])
    properties: _Element = font._element  # pyright: ignore[reportPrivateUsage]
    if styles.get("strike"):
        properties.set("strike", "sngStrike")
    if "letterSpacing" in styles:
        properties.set("spc", str(round(_number(styles["letterSpacing"]) * 75 * _SCALE.get())))


def _write_text(frame: TextFrame, element: Description) -> None:
    padding = _description(element.get("padding", {}))
    frame.margin_top = _px(padding.get("top", 0))
    frame.margin_right = _px(padding.get("right", 0))
    frame.margin_bottom = _px(padding.get("bottom", 0))
    frame.margin_left = _px(padding.get("left", 0))
    frame.vertical_anchor = _ANCHORS.get(_text(element.get("verticalAlign", "top")), MSO_ANCHOR.TOP)
    frame.word_wrap = bool(element.get("wrap", False))
    frame.auto_size = MSO_AUTO_SIZE.NONE
    for index, item in enumerate(_items(element.get("paragraphs", []))):
        styles = _description(item)
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.alignment = _ALIGNMENTS.get(_text(styles.get("align", "left")), PP_ALIGN.LEFT)
        paragraph.space_before = _px(styles.get("spaceBefore", 0))
        paragraph.space_after = _px(styles.get("spaceAfter", 0))
        if "lineHeight" in styles:
            paragraph.line_spacing = _px(styles["lineHeight"])
        for item in _items(styles.get("runs", [])):
            details = _description(item)
            run = paragraph.add_run()
            run.text = _text(details.get("text", ""))
            _font(run.font, details)


def _geometry(element: Description) -> tuple[Emu, Emu, Emu, Emu]:
    return (
        _px(element.get("x")),
        _px(element.get("y")),
        _px(element.get("width")),
        _px(element.get("height")),
    )


def _image_data(value: object) -> tuple[str, bytes]:
    data = _text(value)
    prefix, separator, encoded = data.partition(",")
    match = re.fullmatch(
        r"data:(image/[\w.+-]+)(?:;charset=utf-8)?(;base64)?", prefix, re.IGNORECASE
    )
    if not separator or match is None:
        raise ValueError("PowerPoint images require an embedded image data URI")
    mime, is_base64 = match.groups()
    try:
        payload = unquote_to_bytes(encoded)
        return mime.lower(), base64.b64decode(payload, validate=True) if is_base64 else payload
    except binascii.Error as exc:
        raise ValueError("Invalid embedded PowerPoint image") from exc


_SVG_NAMESPACE = "{http://www.w3.org/2000/svg}"
_SVG_STATIC_ELEMENTS = {
    "svg",
    "g",
    "defs",
    "symbol",
    "use",
    "path",
    "rect",
    "circle",
    "ellipse",
    "polygon",
    "polyline",
    "line",
    "linearGradient",
    "radialGradient",
    "stop",
    "pattern",
    "clipPath",
    "mask",
    "marker",
    "text",
    "tspan",
    "textPath",
    "title",
    "desc",
}
_SVG_LOCAL_REFERENCE = re.compile(r"#[\w.:+-]+")
_SVG_LOCAL_PAINT = re.compile(r"""url\(\s*['"]?#[\w.:+-]+['"]?\s*\)""", re.IGNORECASE)


def _validate_svg(data: bytes) -> None:
    # Browser image loading disables external resources and script. Keep the same restriction
    # when handing the original vector picture to an Office application.
    try:
        source = data.decode("utf-8-sig")
        if re.search(r"<!(?:DOCTYPE|ENTITY)\b|<\?(?!xml\s)", source, re.IGNORECASE):
            raise ValueError("SVG pictures cannot contain declarations other than the XML header")
        root = ET.fromstring(source)
    except (UnicodeError, ET.ParseError) as exc:
        raise ValueError("PowerPoint SVG pictures must contain valid UTF-8 SVG XML") from exc
    if root.tag != _SVG_NAMESPACE + "svg":
        raise ValueError("PowerPoint SVG pictures require an SVG root element")
    for node in root.iter():
        if (
            not node.tag.startswith(_SVG_NAMESPACE)
            or node.tag[len(_SVG_NAMESPACE) :] not in _SVG_STATIC_ELEMENTS
        ):
            raise ValueError("PowerPoint SVG pictures support static, self-contained SVG elements")
        for attribute, value in node.attrib.items():
            name = attribute.rsplit("}", 1)[-1]
            if name.lower().startswith("on") or name in {"base", "src", "style"}:
                raise ValueError("PowerPoint SVG pictures require static presentation attributes")
            if name == "href" and not _SVG_LOCAL_REFERENCE.fullmatch(value):
                raise ValueError("PowerPoint SVG pictures require local fragment references")
            if (
                "\\" in value
                or "/*" in value
                or ("url" in value.lower() and not _SVG_LOCAL_PAINT.fullmatch(value.strip()))
            ):
                raise ValueError("PowerPoint SVG pictures require local paint references")


def _image(shapes: Shapes, element: Description, rectangle: tuple[Emu, Emu, Emu, Emu]):
    mime, content = _image_data(element.get("data"))
    source_data = content
    if mime == "image/svg+xml":
        _validate_svg(content)
        fallback_mime, source_data = _image_data(element.get("fallbackData"))
        if fallback_mime != "image/png":
            raise ValueError("PowerPoint SVG images require a PNG fallback")
    try:
        source = BytesIO(source_data)
        with Image.open(source) as image:
            if image.format not in {"BMP", "GIF", "JPEG", "PNG", "TIFF", "WMF"}:
                converted = BytesIO()
                image.convert("RGBA").save(converted, format="PNG")
                source = converted
        source.seek(0)
        picture = shapes.add_picture(source, *rectangle)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Unsupported or invalid embedded PowerPoint image") from exc
    if mime == "image/svg+xml":
        package = picture.part.package
        svg = Part(package.next_partname("/ppt/media/image%d.svg"), mime, package, content)
        reference = picture.part.relate_to(svg, RELATIONSHIP_TYPE.IMAGE)
        picture_xml = cast(_Element, picture.element)
        blip = picture_xml.find(f"{qn('p:blipFill')}/{qn('a:blip')}")
        if blip is None:
            raise ValueError("Could not embed PowerPoint SVG image")
        extensions = _xml("a:extLst")
        extension = _xml("a:ext")
        extension.set("uri", "{96DAC541-7B7A-43D3-8B79-37D633B846F1}")
        svg_namespace = "http://schemas.microsoft.com/office/drawing/2016/SVG/main"
        svg_reference = Element(f"{{{svg_namespace}}}svgBlip", nsmap={"asvg": svg_namespace})
        svg_reference.set(qn("r:embed"), reference)
        extension.append(svg_reference)
        extensions.append(extension)
        blip.append(extensions)
    crop = _description(element.get("crop", {}))
    for side in ("top", "right", "bottom", "left"):
        amount = _number(crop.get(side, 0))
        if not 0 <= amount < 1:
            raise ValueError("PowerPoint image crop must be between zero and one")
        setattr(picture, f"crop_{side}", amount)
    if picture.crop_top + picture.crop_bottom >= 1 or picture.crop_left + picture.crop_right >= 1:
        raise ValueError("PowerPoint image crop must leave a visible area")
    return picture


def _table(shapes: Shapes, element: Description, rectangle: tuple[Emu, Emu, Emu, Emu]):
    rows = [_items(row) for row in _items(element.get("rows"))]
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        raise ValueError("PowerPoint tables require a nonempty rectangular set of cells")
    if isinstance(shapes, SlideShapes):
        graphic = shapes.add_table(len(rows), len(rows[0]), *rectangle)
    else:
        shape_id = shapes._next_shape_id  # pyright: ignore[reportPrivateUsage]
        tree = shapes._grpSp  # pyright: ignore[reportPrivateUsage]
        frame_element = tree.add_table(
            shape_id, f"Table {shape_id - 1}", len(rows), len(rows[0]), *rectangle
        )
        graphic = GraphicFrame(frame_element, shapes)
    table = graphic.table
    table.first_row = False
    table.horz_banding = False
    # All native table cell styling comes from the authored slide.
    table_xml = cast(_Element, graphic.element)
    style_id = table_xml.find(f".//{qn('a:tblPr')}/{qn('a:tableStyleId')}")
    if style_id is not None:
        style_id.text = _NO_STYLE_NO_GRID
    if "rowHeights" in element:
        heights = _items(element["rowHeights"])
        if len(heights) != len(rows):
            raise ValueError("PowerPoint table row heights must match the rows")
        for i, height in enumerate(heights):
            table.rows[i].height = _px(height)
    if "columnWidths" in element:
        widths = _items(element["columnWidths"])
        if len(widths) != len(rows[0]):
            raise ValueError("PowerPoint table column widths must match the columns")
        for j, width in enumerate(widths):
            table.columns[j].width = _px(width)
    for i, row in enumerate(rows):
        for j, item in enumerate(row):
            details = _description(item)
            cell = table.cell(i, j)
            _fill(cell.fill, details.get("fill"))
            _write_text(cell.text_frame, details)
            cell.vertical_anchor = cell.text_frame.vertical_anchor
            cell_xml = cell._tc.get_or_add_tcPr()  # pyright: ignore[reportPrivateUsage]
            cell_properties = cast(_Element, cell_xml)
            padding = _description(details.get("padding", {}))
            borders = _description(details.get("borders", {}))
            edges = (("left", "lnL"), ("right", "lnR"), ("top", "lnT"), ("bottom", "lnB"))
            for index, (side, tag) in enumerate(edges):
                setattr(cell, f"margin_{side}", _px(padding.get(side, 0)))
                border = _description(borders.get(side, details))
                stroke = border.get("stroke")
                width = _px(border.get("strokeWidth", 1))
                line = _xml(f"a:{tag}")
                if stroke is None or not width:
                    line.append(_xml("a:noFill"))
                else:
                    line.set("w", str(width))
                    fill = _xml("a:solidFill")
                    fill.append(_color_element("a:srgbClr", stroke))
                    line.append(fill)
                cell_properties.insert(index, line)
    if isinstance(shapes, GroupShapes):
        shapes._recalculate_extents()  # pyright: ignore[reportPrivateUsage]
    return graphic


class _LiteralWorkbookWriter(CategoryWorkbookWriter):
    @contextmanager
    def _open_worksheet(self, xlsx_file: BytesIO) -> Generator[tuple[Workbook, Worksheet]]:
        # Category and series names belong in literal string cells, even if they resemble formulas.
        with Workbook(
            xlsx_file, {"in_memory": True, "strings_to_formulas": False, "strings_to_urls": False}
        ) as workbook:
            yield workbook, workbook.add_worksheet()  # pyright: ignore[reportUnknownMemberType]


class _LiteralChartData(ChartData):
    @lazyproperty
    def _workbook_writer(self) -> CategoryWorkbookWriter:
        return _LiteralWorkbookWriter(self)


def _chart(shapes: Shapes, element: Description, rectangle: tuple[Emu, Emu, Emu, Emu]):
    categories = [_text(value) for value in _items(element.get("categories"))]
    series = [_description(item) for item in _items(element.get("series"))]
    if not categories or not series:
        raise ValueError("PowerPoint charts require categories and series")
    chart_type = _CHART_TYPES.get(_text(element.get("chartType", "clustered-column")))
    if chart_type is None or (chart_type == XL_CHART_TYPE.PIE and len(series) > 1):
        raise ValueError("Unsupported PowerPoint chart type or series count")
    chart_data = _LiteralChartData()
    chart_data.categories = categories
    for item in series:
        values = [_number(value) for value in _items(item.get("values"))]
        if len(values) != len(categories):
            raise ValueError("PowerPoint chart series must match the categories")
        name = _text(item.get("name"))
        chart_data.add_series(name, values)  # pyright: ignore[reportUnknownMemberType]
    # python-pptx documents this as a GraphicFrame but annotates the method as Chart.
    frame = cast(GraphicFrame, shapes.add_chart(chart_type, *rectangle, chart_data))
    chart = frame.chart
    chart_space = cast(_Element, chart._chartSpace)  # pyright: ignore[reportPrivateUsage]
    chart_properties = cast(CT_ShapeProperties, _xml("c:spPr"))
    chart_contents = chart_space.find(qn("c:chart"))
    if chart_contents is None:
        raise ValueError("Could not create chart contents")
    chart_contents.addnext(chart_properties)
    _fill(FillFormat.from_fill_parent(chart_properties), element.get("fill"))
    LineFormat(chart_properties).fill.background()
    title = element.get("title")
    chart.has_title = title is not None
    if title is not None:
        title_frame = chart.chart_title.text_frame
        title_frame.text = _text(title)
        _font(title_frame.paragraphs[0].font, element)
    chart.has_legend = bool(element.get("legend", True))
    if chart.has_legend:
        legend = chart.legend
        if legend is not None:
            legend.position = XL_LEGEND_POSITION.BOTTOM
            legend.include_in_layout = False
            _font(legend.font, element)
    plot = cast(_BasePlot, chart.plots[0])
    plot.has_data_labels = bool(element.get("showValues", False))
    if plot.has_data_labels:
        labels = plot.data_labels
        labels.show_value = True
        _font(labels.font, element)
    if chart_type != XL_CHART_TYPE.PIE:
        category_axis = chart.category_axis
        value_axis = chart.value_axis
        for axis in (category_axis, value_axis):
            _font(axis.tick_labels.font, element)
            if "axisColor" in element:
                _format_color(axis.format.line.color, element["axisColor"])
        if "minimum" in element:
            value_axis.minimum_scale = _number(element["minimum"])
        if "maximum" in element:
            value_axis.maximum_scale = _number(element["maximum"])
        if "majorUnit" in element:
            value_axis.major_unit = _number(element["majorUnit"])
        if "gridColor" in element:
            value_axis.has_major_gridlines = True
            _format_color(value_axis.major_gridlines.format.line.color, element["gridColor"])
    if "gapWidth" in element and chart_type in (
        XL_CHART_TYPE.BAR_CLUSTERED,
        XL_CHART_TYPE.COLUMN_CLUSTERED,
    ):
        cast(BarPlot, plot).gap_width = round(_number(element["gapWidth"]))
    for details, chart_series in zip(
        series, cast(Sequence[_BaseSeries], chart.series), strict=True
    ):
        if "color" in details:
            if chart_type == XL_CHART_TYPE.LINE:
                _format_color(chart_series.format.line.color, details["color"])
            else:
                _fill(chart_series.format.fill, details["color"])
    return frame


def _freeform(shapes: Shapes, element: Description, rectangle: tuple[Emu, Emu, Emu, Emu]) -> Shape:
    shape = shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, *rectangle)
    properties = cast(_Element, shape.element.spPr)
    preset = properties.find(qn("a:prstGeom"))
    if preset is None:
        raise ValueError("Could not create an editable PowerPoint freeform shape")
    geometry = _xml("a:custGeom")
    properties.replace(preset, geometry)
    for name in ("avLst", "gdLst", "ahLst", "cxnLst"):
        geometry.append(_xml(f"a:{name}"))
    rect = _xml("a:rect")
    for edge in ("l", "t", "r", "b"):
        rect.set(edge, edge)
    geometry.append(rect)
    paths = _xml("a:pathLst")
    geometry.append(paths)
    path = _xml("a:path")
    path.set("w", str(max(1, rectangle[2])))
    path.set("h", str(max(1, rectangle[3])))
    paths.append(path)
    points = _items(element.get("path"))
    if not points:
        raise ValueError("PowerPoint freeforms require path points")
    for index, item in enumerate(points):
        point = _description(item)
        if point.get("close"):
            path.append(_xml("a:close"))
            continue
        if index == 0 or point.get("move"):
            command = _xml("a:moveTo")
            coordinates = (("x", "y"),)
        elif "x1" in point:
            command = _xml("a:cubicBezTo")
            coordinates = (("x1", "y1"), ("x2", "y2"), ("x", "y"))
        else:
            command = _xml("a:lnTo")
            coordinates = (("x", "y"),)
        for x, y in coordinates:
            coordinate = _xml("a:pt")
            coordinate.set("x", str(_px(point.get(x)) - rectangle[0]))
            coordinate.set("y", str(_px(point.get(y)) - rectangle[1]))
            command.append(coordinate)
        path.append(command)
    return shape


def _add(shapes: Shapes, element: Description) -> None:
    element_type = _text(element.get("type"))
    if element_type == "group":
        group = shapes.add_group_shape()
        for item in _items(element.get("elements")):
            _add(group.shapes, _description(item))
        if "rotation" in element:
            group.rotation = _number(element["rotation"])
        if "name" in element:
            group.name = _text(element["name"])
        return
    shape: BaseShape
    if element_type == "line":
        shape = shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT,
            _px(element.get("x")),
            _px(element.get("y")),
            _px(element.get("x2")),
            _px(element.get("y2")),
        )
        _line(shape.line, element)
    else:
        rectangle = _geometry(element)
        match element_type:
            case "text":
                shape = shapes.add_textbox(*rectangle)
                _fill(shape.fill, element.get("fill"), element.get("gradient"))
                _line(shape.line, element)
                _write_text(shape.text_frame, element)
            case "shape":
                preset_name = _text(element.get("shape", "rect"))
                if preset_name == "freeform":
                    shape = _freeform(shapes, element, rectangle)
                else:
                    preset = _PRESET_SHAPES.get(preset_name)
                    if preset is None:
                        raise ValueError("Unsupported PowerPoint shape preset")
                    shape = shapes.add_shape(preset, *rectangle)
                    if preset == MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE and "radius" in element:
                        shortest = min(rectangle[2:])
                        if shortest > 0:
                            radius = _px(element["radius"]) / shortest
                            shape.adjustments[0] = min(0.5, max(0, radius))
                _fill(shape.fill, element.get("fill"), element.get("gradient"))
                _line(shape.line, element)
            case "image":
                shape = _image(shapes, element, rectangle)
            case "table":
                shape = _table(shapes, element, rectangle)
            case "chart":
                shape = _chart(shapes, element, rectangle)
            case _:
                raise ValueError(f"Unsupported PowerPoint object type: {element_type}")
    if element_type in {"text", "shape", "line"}:
        # python-pptx's default presets inherit effects from the Office theme.
        shape.shadow.inherit = False
        shape_xml = cast(_Element, shape.element)
        effect_ref = shape_xml.find(f"{qn('p:style')}/{qn('a:effectRef')}")
        if effect_ref is not None:
            effect_ref.set("idx", "0")
    if "rotation" in element:
        rotation = _number(element["rotation"])
        if isinstance(shape, GraphicFrame):
            if rotation % 360:
                raise ValueError("PowerPoint does not support rotation of tables or charts")
        else:
            shape.rotation = rotation
    if "name" in element:
        shape.name = _text(element["name"])


def write_powerpoint(
    slides: Sequence[Description],
    destination: str | Path | BinaryIO,
    *,
    size_emu: tuple[int, int] | None = None,
    title: str = "",
) -> None:
    """Write render-model slides as editable OOXML to a file or a binary stream."""

    if not slides:
        raise ValueError("PowerPoint export requires at least one slide")
    css_width = _number(slides[0].get("width"))
    css_height = _number(slides[0].get("height"))
    if css_width <= 0 or css_height <= 0:
        raise ValueError("PowerPoint export requires positive slide dimensions")
    width, height = size_emu or (round(css_width * _PX_TO_EMU), round(css_height * _PX_TO_EMU))
    if not all(914400 <= side <= 51206400 for side in (width, height)):
        raise ValueError("PowerPoint slide dimensions must be between 1 and 56 inches")
    scale = width / css_width
    if abs(height / scale - css_height) > 0.01:
        raise ValueError("PowerPoint slide size does not match the rendered aspect ratio")
    with _SCALE.set(scale / _PX_TO_EMU):
        presentation = Presentation()
        package = presentation.part.package
        # The library template includes a stock preview and metadata from an unrelated deck.
        for relation in list(package._rels.values()):  # pyright: ignore[reportPrivateUsage]
            if relation.reltype in {
                RELATIONSHIP_TYPE.THUMBNAIL,
                RELATIONSHIP_TYPE.EXTENDED_PROPERTIES,
            }:
                package.drop_rel(relation.rId)
        properties = presentation.core_properties
        properties.title = title[:255]
        properties.comments = ""
        properties.last_modified_by = "Artifact Studio"
        properties.created = properties.modified = datetime.now(UTC)
        presentation.slide_width = Emu(width)
        presentation.slide_height = Emu(height)
        slide_size = cast(_Element, presentation.element).find(qn("p:sldSz"))
        if slide_size is not None:
            slide_size.set("type", "custom")
        blank = presentation.slide_layouts[6]
        for index, description in enumerate(slides, start=1):
            if (
                abs(_number(description.get("width")) - css_width) > 0.01
                or abs(_number(description.get("height")) - css_height) > 0.01
            ):
                raise ValueError("PowerPoint slides must use the same dimensions")
            slide = presentation.slides.add_slide(blank)
            if "name" in description:
                slide.name = _text(description["name"])
            _fill(
                slide.background.fill,
                description.get("background"),
                description.get("gradient"),
                background=True,
            )
            for item in _items(description.get("elements")):
                element = _description(item)
                try:
                    _add(slide.shapes, element)
                except ValueError as exc:
                    label = element.get("name") or element.get("type") or "object"
                    raise ValueError(f"Slide {index}, {label}: {exc}") from exc
        presentation.save(str(destination) if isinstance(destination, Path) else destination)
