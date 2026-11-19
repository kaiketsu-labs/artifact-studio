import xml.etree.ElementTree as ET
from io import BytesIO
from zipfile import ZipFile

import pytest

from artifact_studio.powerpoint.graphics import graphic
from tests.pptx_fixture import fictional_powerpoint


def test_graphics_from_fictional_presentation() -> None:
    with ZipFile(BytesIO(fictional_powerpoint())) as archive:
        slide = ET.fromstring(archive.read("ppt/slides/slide2.xml"))
        frames = slide.findall(
            ".//{http://schemas.openxmlformats.org/presentationml/2006/main}graphicFrame"
        )

        def resolve(part: str, relationship: str) -> str:
            if part == "ppt/slides/slide2.xml" and relationship == "rId2":
                return "ppt/charts/chart1.xml"
            assert part == "ppt/charts/chart1.xml" and relationship == "rId1"
            return "ppt/embeddings/Microsoft_Excel_Sheet1.xlsx"

        table = graphic(frames[0], "ppt/slides/slide2.xml", archive.read, resolve, 400, 100)
        chart = graphic(
            frames[1],
            "ppt/slides/slide2.xml",
            archive.read,
            resolve,
            400,
            300,
            colors={"accent1": "4F81BD"},
            fonts={"+mn-lt": "Liberation Sans"},
        )
    assert table.count("<td ") == 4
    assert "24 baskets" in table and "#D9EDEB" in table
    assert 'data-pptx-chart="column"' in chart
    assert "Spring" in chart and "Summer" in chart
    assert 'data-chart-value="12"' in chart and "height:28.0000%" in chart
    assert 'data-chart-value="24"' in chart and "height:56.0000%" in chart


def test_rejects_unsupported_graphic() -> None:
    frame = ET.fromstring(
        '<frame xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData/></frame>'
    )
    with pytest.raises(ValueError, match="unsupported"):
        graphic(frame, "slide", lambda _: b"", lambda _part, rel: rel, 1, 1)


def test_chart_value_edit_rescales_bars_and_ticks() -> None:
    from artifact_studio.powerpoint.edit import edit_pptx_text

    with ZipFile(BytesIO(fictional_powerpoint())) as archive:
        slide = ET.fromstring(archive.read("ppt/slides/slide2.xml"))
        frames = slide.findall(
            ".//{http://schemas.openxmlformats.org/presentationml/2006/main}graphicFrame"
        )
        chart = graphic(
            frames[1],
            "slide",
            archive.read,
            lambda p, _r: (
                "ppt/charts/chart1.xml"
                if p == "slide"
                else "ppt/embeddings/Microsoft_Excel_Sheet1.xlsx"
            ),
            400,
            300,
            colors={"accent1": "4F81BD"},
            fonts={"+mn-lt": "Liberation Sans"},
        )
    source = (
        '<article data-pptx-width="400" data-pptx-height="300"><div>' + chart + "</div></article>"
    )
    result = edit_pptx_text(source, [0, 0, 8, 0], "60")
    assert 'data-chart-max="60"' in result
    assert 'data-chart-tick="0.5"' in result
    assert 'font-size:57150.000000cqw">30</span></span>' in result
    assert 'data-chart-value="60" aria-label="Chart value 60"' in result
    assert "height:70.0000%" in result
    assert "height:28.0000%" in result
    for invalid in ("-1", "1e-999", "1e999"):
        with pytest.raises(ValueError, match="Chart values"):
            edit_pptx_text(source, [0, 0, 8, 0], invalid)
    for boundary in ("1e-300", "1e300"):
        assert f'data-chart-value="{boundary}"' in edit_pptx_text(source, [0, 0, 8, 0], boundary)


def test_chart_rejects_workbook_disagreeing_with_cache() -> None:
    with ZipFile(BytesIO(fictional_powerpoint())) as archive:
        slide = ET.fromstring(archive.read("ppt/slides/slide2.xml"))
        frame = slide.findall(
            ".//{http://schemas.openxmlformats.org/presentationml/2006/main}graphicFrame"
        )[1]

        def read(part: str) -> bytes:
            data = archive.read(part)
            return (
                data.replace(b">12</ns0:v>", b">13</ns0:v>")
                if part.endswith("chart1.xml")
                else data
            )

        def resolve(part: str, _rel: str) -> str:
            return (
                "ppt/charts/chart1.xml"
                if part == "slide"
                else "ppt/embeddings/Microsoft_Excel_Sheet1.xlsx"
            )

        with pytest.raises(ValueError, match="does not match"):
            graphic(
                frame,
                "slide",
                read,
                resolve,
                400,
                300,
                colors={"accent1": "4F81BD"},
                fonts={"+mn-lt": "Liberation Sans"},
            )


def test_graphics_reject_unsupported_table_styles_and_unsafe_chart_xml() -> None:
    with ZipFile(BytesIO(fictional_powerpoint())) as archive:
        slide = ET.fromstring(archive.read("ppt/slides/slide2.xml"))
        table, chart = slide.findall(
            ".//{http://schemas.openxmlformats.org/presentationml/2006/main}graphicFrame"
        )
        run = table.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}rPr")
        assert run is not None
        run.set("u", "sng")
        with pytest.raises(ValueError, match="text styling"):
            graphic(table, "slide", archive.read, lambda _p, _r: "", 400, 300)
        with pytest.raises(ValueError, match="chart XML"):
            graphic(
                chart,
                "slide",
                lambda _p: b'<!DOCTYPE a [<!ENTITY x "y">]><a/>',
                lambda _p, _r: "chart",
                400,
                300,
            )


def test_table_explicit_cell_geometry_and_formatting() -> None:

    table = ET.fromstring("""<a:tbl xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
      <a:tblPr/><a:tblGrid><a:gridCol w="2000000"/><a:gridCol w="1000000"/></a:tblGrid>
      <a:tr h="600000"><a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p>
        <a:pPr indent="0" marL="0" algn="ctr"><a:buNone/></a:pPr>
        <a:r><a:rPr sz="1700"><a:solidFill><a:srgbClr val="153735"/></a:solidFill>
          <a:latin typeface="Liberation Sans"/><a:ea typeface="Liberation Sans"/>
          <a:cs typeface="Liberation Sans"/></a:rPr><a:t>Station</a:t></a:r>
      </a:p></a:txBody><a:tcPr marL="120000" marR="120000" marT="60000" marB="60000" anchor="ctr">
        <a:lnL w="12000" cap="flat" cmpd="sng" algn="ctr">
          <a:solidFill><a:srgbClr val="B5C9BD"/></a:solidFill>
          <a:prstDash val="solid"/><a:round/><a:headEnd type="none"/>
          <a:tailEnd type="none"/></a:lnL>
        <a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill>
      </a:tcPr></a:tc><a:tc><a:txBody><a:p/></a:txBody></a:tc></a:tr></a:tbl>""")
    frame = ET.Element("frame")
    ET.SubElement(
        frame, "{http://schemas.openxmlformats.org/drawingml/2006/main}graphicData"
    ).append(table)

    def render() -> str:
        return graphic(frame, "slide", lambda _: b"", lambda _part, _rel: "", 3000000, 600000)

    rendered = render()
    assert "width:66.6667%" in rendered
    assert "padding-left:4.000000cqw" in rendered
    assert "padding-top:2.000000cqw" in rendered
    assert "border-left:0.400000cqw solid #B5C9BD" in rendered
    assert "vertical-align:middle" in rendered and "text-align:center" in rendered
    assert "font-size:7.196667cqw" in rendered
    assert "background:#FFFFFF" in rendered
    assert "overflow-wrap:anywhere" not in rendered
    line = table.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}prstDash")
    assert line is not None
    line.set("val", "dash")
    with pytest.raises(ValueError, match="border"):
        render()
