"""Fictional clustered charts exercise semantic properties and rejection paths."""

import pytest

from artifact_studio.powerpoint import clustered_chart
from artifact_studio.powerpoint import text as pptx_text
from artifact_studio.powerpoint.graphics import _chart  # pyright: ignore[reportPrivateUsage]


def fictional_chart() -> bytes:
    def points(values: list[str]) -> str:
        return f'<c:ptCount val="{len(values)}"/>' + "".join(
            f'<c:pt idx="{i}"><c:v>{value}</c:v></c:pt>' for i, value in enumerate(values)
        )

    categories = ["Orchard", "Harbor"]
    series: list[str] = []
    for i, (name, color, values) in enumerate(
        (("Old", "998877", ["3", "5"]), ("New", "336699", ["7", "9"]))
    ):
        cat_points = "".join(
            f'<c:pt idx="{j}"><c:v>{v}</c:v></c:pt>' for j, v in enumerate(categories)
        )
        series.append(f'''<c:ser><c:idx val="{i}"/><c:order val="{i}"/>
          <c:tx><c:strRef><c:f>S!${"BC"[i]}$1</c:f><c:strCache>{points([name])}</c:strCache></c:strRef></c:tx>
          <c:spPr><a:solidFill><a:srgbClr val="{color}"/></a:solidFill></c:spPr>
          <c:cat><c:multiLvlStrRef><c:f>S!$A$2:$A$3</c:f><c:multiLvlStrCache>
          <c:ptCount val="2"/><c:lvl>{cat_points}</c:lvl></c:multiLvlStrCache>
          </c:multiLvlStrRef></c:cat>
          <c:val><c:numRef><c:f>S!${"BC"[i]}$2:${"BC"[i]}$3</c:f>
          <c:numCache><c:formatCode>General</c:formatCode>{points(values)}</c:numCache></c:numRef></c:val></c:ser>''')
    line = (
        '<c:spPr><a:ln w="12700"><a:solidFill><a:srgbClr val="777777"/>'
        "</a:solidFill></a:ln></c:spPr>"
    )
    return f"""<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"
      xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><c:chart>
      <c:autoTitleDeleted val="1"/><c:plotArea><c:barChart><c:barDir val="col"/>
      <c:grouping val="clustered"/>{"".join(series)}<c:gapWidth val="100"/>
      <c:axId val="1"/><c:axId val="2"/></c:barChart><c:catAx><c:axId val="1"/>
      <c:scaling/><c:axPos val="b"/><c:tickLblPos val="low"/>{line}<c:crossAx val="2"/></c:catAx>
      <c:valAx><c:axId val="2"/><c:scaling><c:min val="0"/><c:max val="10"/></c:scaling>
      <c:axPos val="l"/><c:majorGridlines>{line}</c:majorGridlines><c:tickLblPos val="nextTo"/>
      {line}<c:crossAx val="1"/><c:crossBetween val="between"/><c:majorUnit val="2"/>
      </c:valAx><c:spPr><a:noFill/></c:spPr></c:plotArea>
      <c:legend><c:legendPos val="b"/></c:legend></c:chart>
      <c:spPr><a:noFill/></c:spPr><c:externalData r:id="rId1"/></c:chartSpace>""".encode()


def test_clustered_chart_properties_and_prefixes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pptx_text, "_font_families", lambda: {"fixture sans"})
    fonts = {"+mn-lt": "Fixture Sans"}
    ranges = {
        "S!$A$2:$A$3": ["Orchard", "Harbor"],
        "S!$B$1": ["Old"],
        "S!$C$1": ["New"],
        "S!$B$2:$B$3": ["3", "5"],
        "S!$C$2:$C$3": ["7", "9"],
    }
    reads: list[str] = []

    def workbook_values(_book: bytes, ref: str) -> list[str]:
        reads.append(ref)
        return ranges[ref]

    monkeypatch.setattr(clustered_chart, "_workbook_values", workbook_values)

    def render(data: bytes) -> str:
        return _chart(
            data, "chart", lambda _: b"book", lambda _part, _rel: "book", {}, fonts, 5000000
        )

    source = fictional_chart()
    result = render(source)
    assert fonts == {"+mn-lt": "Fixture Sans"}
    assert "Fixture Sans" in result
    assert reads.count("S!$A$2:$A$3") == 1
    assert result.count('data-chart-fixed-max="10.0"') == 4
    assert "height:30.0000%" in result and "#336699" in result
    assert "Orchard" in result and "New" in result
    assert render(source.replace(b"c:", b"z:").replace(b"xmlns:c=", b"xmlns:z=")) == result
    assert render(source.replace(b'val="100"', b'val="200"')) != result
    assert "#224466" in render(source.replace(b"336699", b"224466"))
    with pytest.raises(ValueError, match="workbook data"):
        render(source.replace(b"<c:v>3</c:v>", b"<c:v>4</c:v>"))
    assert 'data-chart-max="20"' in render(source.replace(b'<c:max val="10"', b'<c:max val="20"'))
    with pytest.raises(ValueError, match="chart axis format"):
        render(
            source.replace(b'<c:axPos val="l"/>', b'<c:axPos val="l"/><c:numFmt formatCode="0%"/>')
        )
    with pytest.raises(ValueError, match="feature"):
        render(source.replace(b"<c:grouping", b"<c:trendline/><c:grouping"))


def test_clustered_chart_rejects_unrepresentable_scale_and_oversized_workbook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pptx_text, "_font_families", lambda: {"fixture sans"})
    source = fictional_chart()
    fonts = {"+mn-lt": "Fixture Sans"}
    with pytest.raises(ValueError, match="unsupported chart scale"):
        _chart(
            source.replace(b'<c:max val="10"', b'<c:max val="1e-320"'),
            "chart",
            lambda _: b"",
            lambda _part, _rel: "book",
            {},
            fonts,
            5000000,
        )
    with pytest.raises(ValueError, match="workbook is oversized"):
        _chart(
            source,
            "chart",
            lambda _: b"x" * 8_000_001,
            lambda _part, _rel: "book",
            {},
            fonts,
            5000000,
        )
