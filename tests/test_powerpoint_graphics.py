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
