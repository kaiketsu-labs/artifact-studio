import base64
import xml.etree.ElementTree as ET
from io import BytesIO
from urllib.parse import quote_from_bytes
from zipfile import ZipFile

import pytest
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.shapes.autoshape import Shape
from pptx.shapes.connector import Connector
from pptx.shapes.graphfrm import GraphicFrame
from pptx.shapes.group import GroupShape
from pptx.shapes.picture import Picture

from artifact_studio.powerpoint.exporter import Description, write_powerpoint

_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}
_RECTANGLE = {"x": 20, "y": 30, "width": 200, "height": 100}
_PARAGRAPHS = [{"runs": [{"text": "Editable", "fontSize": 16, "fontFamily": "Arial"}]}]


def _picture(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _png() -> str:
    buffer = BytesIO()
    Image.new("RGB", (8, 4), (220, 84, 30)).save(buffer, format="PNG")
    return _picture("image/png", buffer.getvalue())


def _slide(*elements: Description) -> Description:
    return {"width": 800, "height": 600, "background": "#EEEEEE", "elements": list(elements)}


def _saved(slide: Description, *, size_emu: tuple[int, int] | None = None) -> BytesIO:
    output = BytesIO()
    write_powerpoint([slide], output, size_emu=size_emu)
    output.seek(0)
    return output


def test_writes_native_text_group_and_rotated_picture_and_line() -> None:
    slide = _slide(
        {
            "type": "group",
            "elements": [
                {
                    "type": "shape",
                    **_RECTANGLE,
                    "shape": "roundRect",
                    "radius": 12,
                    "fill": "#FF9933",
                },
                {
                    "type": "text",
                    **_RECTANGLE,
                    "paragraphs": [
                        {
                            "align": "center",
                            "lineHeight": 20,
                            "runs": [
                                {"text": "A ", "fontSize": 16, "bold": True},
                                {
                                    "text": "line",
                                    "fontSize": 16,
                                    "italic": True,
                                    "letterSpacing": 1,
                                },
                            ],
                        },
                        {"runs": [{"text": "Second paragraph", "color": "rgba(10, 20, 30, 0.5)"}]},
                    ],
                },
            ],
        },
        {
            "type": "image",
            **_RECTANGLE,
            "data": _png(),
            "crop": {"left": 0.1, "right": 0.2},
            "rotation": 30,
        },
        {
            "type": "line",
            "x": 50,
            "y": 120,
            "x2": 210,
            "y2": 40,
            "stroke": "#123456",
            "strokeWidth": 2,
            "rotation": 15,
        },
    )
    output = _saved(slide)
    presentation = Presentation(output)
    assert presentation.slide_width == 800 * 9525
    assert presentation.slide_height == 600 * 9525
    group, picture, line = presentation.slides[0].shapes
    assert isinstance(group, GroupShape)
    assert group.left == 20 * 9525
    shape, text = group.shapes
    assert shape.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE
    assert isinstance(text, Shape)
    assert text.text == "A line\nSecond paragraph"
    runs = text.text_frame.paragraphs[0].runs
    assert runs[0].font.bold
    assert runs[1].font.italic
    assert runs[0].font.size is not None and runs[0].font.size.pt == 12
    assert isinstance(picture, Picture)
    assert picture.rotation == 30
    assert picture.crop_left == pytest.approx(0.1)
    assert picture.crop_right == pytest.approx(0.2)
    assert isinstance(line, Connector)
    assert line.rotation == 15
    with ZipFile(output) as package:
        xml = ET.fromstring(package.read("ppt/slides/slide1.xml"))
        background = xml.find(".//p:bgPr/a:solidFill/a:srgbClr", _NS)
        assert background is not None and background.get("val") == "EEEEEE"
        line_style = xml.find(".//p:cxnSp/p:spPr/a:ln", _NS)
        assert line_style is not None and line_style.get("w") == str(2 * 9525)
        assert [effect.get("idx") for effect in xml.findall(".//p:style/a:effectRef", _NS)] == [
            "0",
            "0",
        ]
        assert len(xml.findall(".//p:spPr/a:effectLst", _NS)) == 3


def test_editable_table_cell_properties_and_chart_workbook() -> None:
    table = {
        "type": "table",
        **_RECTANGLE,
        "columnWidths": [90, 110],
        "rows": [
            [
                {
                    "paragraphs": _PARAGRAPHS,
                    "padding": {"top": 4, "left": 8},
                    "verticalAlign": "center",
                    "fill": "#DDEEFF",
                    "stroke": "#112233",
                    "strokeWidth": 1,
                },
                {"paragraphs": _PARAGRAPHS},
            ]
        ],
    }
    chart = {
        "type": "chart",
        **_RECTANGLE,
        "categories": ["April", "=1+1"],
        "series": [
            {"name": "First series", "values": [1, 2], "color": "#A33422"},
            {"name": "=2+2", "values": [2, 3], "color": "#3344AA"},
        ],
        "minimum": 0,
        "maximum": 4,
        "majorUnit": 1,
        "fontSize": 13,
        "gridColor": "#EEEEEE",
        "axisColor": "#222222",
        "showValues": True,
    }
    output = _saved(_slide(table, chart))
    presentation = Presentation(output)
    table_shape, chart_shape = presentation.slides[0].shapes
    assert isinstance(table_shape, GraphicFrame) and table_shape.has_table
    assert table_shape.table.cell(0, 0).text == "Editable"
    assert table_shape.table.cell(0, 0).margin_left == 8 * 9525
    assert table_shape.table.columns[1].width == 110 * 9525
    assert isinstance(chart_shape, GraphicFrame) and chart_shape.has_chart
    with ZipFile(output) as package:
        slide_xml = ET.fromstring(package.read("ppt/slides/slide1.xml"))
        style_id = slide_xml.find(".//a:tblPr/a:tableStyleId", _NS)
        assert style_id is not None and style_id.text == "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"
        cell = slide_xml.find(".//a:tcPr", _NS)
        assert cell is not None
        assert cell.get("anchor") == "ctr"
        assert [child.tag.split("}")[-1] for child in cell][:5] == [
            "lnL",
            "lnR",
            "lnT",
            "lnB",
            "solidFill",
        ]
        chart_xml = ET.fromstring(package.read("ppt/charts/chart1.xml"))
        values_visible = chart_xml.find(".//c:showVal", _NS)
        assert values_visible is not None and values_visible.get("val") == "1"
        chart_maximum = chart_xml.find(".//c:valAx/c:scaling/c:max", _NS)
        assert chart_maximum is not None and float(chart_maximum.get("val", "0")) == 4
        workbook = next(name for name in package.namelist() if name.endswith(".xlsx"))
        with ZipFile(BytesIO(package.read(workbook))) as embedded:
            worksheet = ET.fromstring(embedded.read("xl/worksheets/sheet1.xml"))
            strings = ET.fromstring(embedded.read("xl/sharedStrings.xml"))
            assert worksheet.find(".//s:f", _NS) is None
            assert [cell.text for cell in strings.findall(".//s:t", _NS)] == [
                "April",
                "=1+1",
                "First series",
                "=2+2",
            ]


def test_writes_gradient_freeform_and_embeds_safe_vector_picture() -> None:
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><circle cx="10" cy="10" r="9"/></svg>'
    gradient = {
        "angle": 180,
        "stops": [{"color": "#440055", "position": 0}, {"color": "#CC2266", "position": 1}],
    }
    slide = {
        **_slide(
            {
                "type": "shape",
                **_RECTANGLE,
                "shape": "freeform",
                "stroke": "#009900",
                "gradient": gradient,
                "path": [
                    {"x": 20, "y": 30, "move": True},
                    {"x": 220, "y": 30, "x1": 80, "y1": 130, "x2": 150, "y2": -20},
                    {"x": 220, "y": 130},
                    {"close": True},
                ],
            },
            {
                "type": "image",
                **_RECTANGLE,
                "data": "data:image/svg+xml;charset=utf-8," + quote_from_bytes(svg),
                "fallbackData": _png(),
            },
        ),
        "gradient": gradient,
    }
    with ZipFile(_saved(slide)) as package:
        xml = ET.fromstring(package.read("ppt/slides/slide1.xml"))
        assert xml.find(".//a:custGeom/a:pathLst/a:path/a:cubicBezTo", _NS) is not None
        stops = xml.findall(".//a:gradFill/a:gsLst/a:gs", _NS)
        assert [stop.get("pos") for stop in stops] == ["0", "100000"] * 2
        assert (
            package.read(next(name for name in package.namelist() if name.endswith(".svg"))) == svg
        )
        assert "ppt/media/image1.png" in package.namelist()
        assert b"svgBlip" in package.read("ppt/slides/slide1.xml")


def test_size_override_scales_text_and_geometry_without_affecting_later_exports() -> None:
    slide = _slide({"type": "text", **_RECTANGLE, "paragraphs": _PARAGRAPHS})
    size = (800 * 9525 * 2, 600 * 9525 * 2)
    presentation = Presentation(_saved(slide, size_emu=size))
    assert (presentation.slide_width, presentation.slide_height) == size
    text = presentation.slides[0].shapes[0]
    assert isinstance(text, Shape)
    assert text.left == 20 * 9525 * 2
    font_size = text.text_frame.paragraphs[0].runs[0].font.size
    assert font_size is not None and font_size.pt == 24
    regular = Presentation(_saved(slide))
    assert regular.slides[0].shapes[0].left == 20 * 9525
    with pytest.raises(ValueError, match="aspect ratio"):
        _saved(slide, size_emu=(size[0], size[1] + 9525))


def test_rejects_svg_external_references_before_embedding() -> None:
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><use href="https://example.test/a.svg"/></svg>'
    slide = _slide(
        {
            "type": "image",
            **_RECTANGLE,
            "data": _picture("image/svg+xml", svg),
            "fallbackData": _png(),
        }
    )
    with pytest.raises(ValueError, match="local fragment"):
        _saved(slide)


def test_audience_file_uses_current_metadata_and_omits_the_library_thumbnail() -> None:
    output = BytesIO()
    write_powerpoint([{"width": 1600, "height": 900, "elements": []}], output, title="Map")
    saved = Presentation(BytesIO(output.getvalue()))
    assert saved.core_properties.title == "Map"
    assert saved.core_properties.last_modified_by == "Artifact Studio"
    with ZipFile(BytesIO(output.getvalue())) as archive:
        assert not any(name.startswith("docProps/thumbnail") for name in archive.namelist())
        assert "docProps/app.xml" not in archive.namelist()
