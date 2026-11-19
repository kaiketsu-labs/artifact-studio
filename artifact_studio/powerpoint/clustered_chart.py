"""Import explicitly supported clustered column charts as editable HTML."""

import html
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from artifact_studio.powerpoint.graphics import A, C, R, _workbook_values  # pyright: ignore[reportPrivateUsage]
from artifact_studio.powerpoint.text import font as resolve_font


def _children(node: ET.Element, allowed: str) -> None:
    names = set(allowed.split())
    if any(child.tag not in {C + name for name in names} for child in node):
        raise ValueError("unsupported clustered chart feature")
    repeated = {C + name for name in ("ser", "axId", "pt", "lvl")}
    tags = [child.tag for child in node if child.tag not in repeated]
    if len(tags) != len(set(tags)):
        raise ValueError("duplicate chart feature")


def _val(node: ET.Element, name: str, expected: str, *, default: str = "") -> None:
    child = node.find(C + name)
    if (child.get("val", "") if child is not None else default) != expected:
        raise ValueError(f"unsupported chart {name}")


def _points(node: ET.Element, cache_path: str) -> list[str]:
    cache = node.find(cache_path)
    if cache is None:
        raise ValueError("missing chart cache")
    points = cache.findall(C + "pt")
    count = cache.find(C + "ptCount")
    if count is None:
        # Multi-level point count is a sibling of the level.
        count = node.find(C + "multiLvlStrCache/" + C + "ptCount")
    if count is None or count.get("val") != str(len(points)):
        raise ValueError("invalid chart point count")
    result: list[str] = []
    for i, point in enumerate(points):
        if point.get("idx") != str(i) or len(point) != 1 or point[0].tag != C + "v":
            raise ValueError("sparse or invalid chart points")
        result.append(point[0].text or "")
    return result


def _check_labels(node: ET.Element | None) -> None:
    if node is None:
        return
    _children(
        node,
        "numFmt txPr showLegendKey showVal showCatName showSerName "
        "showPercent showBubbleSize showLeaderLines",
    )
    for child in node:
        if child.tag.startswith(C + "show") and child.get("val") != "0":
            raise ValueError("visible chart data labels are unsupported")
    fmt = node.find(C + "numFmt")
    if fmt is not None and fmt.get("formatCode") not in ("#,##0", "General"):
        raise ValueError("unsupported chart data label format")


def _font(node: ET.Element | None, default: int, fonts: dict[str, str]) -> tuple[int, str]:
    if node is None:
        return default, resolve_font("+mn-lt", fonts)
    if any(child.tag not in (A + "bodyPr", A + "lstStyle", A + "p") for child in node):
        raise ValueError("unsupported chart text formatting")
    for child in node:
        if child.tag in (A + "bodyPr", A + "lstStyle") and (child.attrib or len(child)):
            raise ValueError("unsupported chart text layout")
    paragraphs = node.findall(A + "p")
    if len(paragraphs) != 1 or any(
        child.tag not in (A + "pPr", A + "endParaRPr") for child in paragraphs[0]
    ):
        raise ValueError("unsupported chart text formatting")
    paragraph_props = paragraphs[0].find(A + "pPr")
    if (
        paragraph_props is None
        or paragraph_props.attrib
        or len(paragraph_props) != 1
        or paragraph_props[0].tag != A + "defRPr"
    ):
        raise ValueError("unsupported chart text formatting")
    p = node.find(f"{A}p/{A}pPr/{A}defRPr")
    if p is None:
        raise ValueError("unsupported chart text formatting")
    if set(p.attrib) - {"sz", "b", "i", "u", "strike"}:
        raise ValueError("unsupported chart text formatting")
    if any(
        p.get(key, value) != value
        for key, value in (("b", "0"), ("i", "0"), ("u", "none"), ("strike", "noStrike"))
    ):
        raise ValueError("unsupported chart font style")
    if any(child.tag not in (A + "solidFill", A + "latin") for child in p):
        raise ValueError("unsupported chart font")
    fill_container = p.find(A + "solidFill")
    if fill_container is not None and (
        len(fill_container) != 1 or fill_container[0].tag != A + "srgbClr"
    ):
        raise ValueError("unsupported chart text color")
    fill = p.find(A + "solidFill/" + A + "srgbClr")
    if fill is not None and (fill.get("val", "").upper() != "000000" or len(fill)):
        raise ValueError("unsupported chart text color")
    face = p.find(A + "latin")
    family = face.get("typeface", "") if face is not None else "+mn-lt"
    size = p.get("sz", str(default))
    if not size.isdecimal() or not 100 <= int(size) <= 40000:
        raise ValueError("unsupported chart font")
    return int(size), resolve_font(family, fonts)


def _line(node: ET.Element | None) -> str:
    line = node.find(A + "ln") if node is not None else None
    if (
        line is None
        or set(line.attrib) - {"w", "cap"}
        or line.get("w", "12700") != "12700"
        or line.get("cap", "flat") != "flat"
    ):
        raise ValueError("unsupported chart axis line")
    if any(child.tag not in (A + "solidFill", A + "prstDash", A + "round") for child in line):
        raise ValueError("unsupported chart axis line")
    dash = line.find(A + "prstDash")
    if dash is not None and dash.get("val") != "solid":
        raise ValueError("unsupported chart axis line")
    fill_container = line.find(A + "solidFill")
    if fill_container is None or len(fill_container) != 1 or fill_container[0].tag != A + "srgbClr":
        raise ValueError("unsupported chart line color")
    rgb = line.find(A + "solidFill/" + A + "srgbClr")
    value = rgb.get("val", "") if rgb is not None else ""
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value) or (rgb is not None and len(rgb)):
        raise ValueError("unsupported chart line color")
    return "#" + value


def _transparent(node: ET.Element | None) -> None:
    if node is None:
        raise ValueError("chart requires an explicit transparent background")
    if any(child.tag not in (A + "noFill", A + "ln", A + "effectLst") for child in node):
        raise ValueError("unsupported chart background")
    fill = node.find(A + "noFill")
    line = node.find(A + "ln")
    effects = node.find(A + "effectLst")
    if (
        fill is None
        or len(fill)
        or (line is not None and (len(line) != 1 or line[0].tag != A + "noFill"))
        or (effects is not None and len(effects))
    ):
        raise ValueError("unsupported chart background")


def _reference(node: ET.Element, cache: str) -> None:
    _children(node, "f " + cache)
    if len(node.findall(C + "f")) != 1 or len(node.findall(C + cache)) != 1:
        raise ValueError("invalid chart reference")
    cached = node.find(C + cache)
    assert cached is not None
    _children(cached, "ptCount lvl" if cache == "multiLvlStrCache" else "ptCount pt formatCode")
    if cache == "multiLvlStrCache":
        levels = cached.findall(C + "lvl")
        if len(levels) != 1:
            raise ValueError("multiple chart category levels are unsupported")
        _children(levels[0], "pt")
    code = cached.find(C + "formatCode")
    if code is not None and code.text != "General":
        raise ValueError("unsupported chart number format")


def clustered_chart(
    root: ET.Element,
    part: str,
    read_part: Callable[[str], bytes],
    resolve: Callable[[str, str], str],
    width: int,
    height: int,
    fonts: dict[str, str],
) -> str:
    _children(root, "date1904 roundedCorners chart spPr externalData")
    _val(root, "date1904", "0", default="0")
    _val(root, "roundedCorners", "1", default="1")
    _transparent(root.find(C + "spPr"))
    chart_root = root.find(C + "chart")
    if chart_root is None:
        raise ValueError("missing chart")
    _children(chart_root, "autoTitleDeleted plotArea legend plotVisOnly dispBlanksAs")
    _val(chart_root, "autoTitleDeleted", "1")
    _val(chart_root, "plotVisOnly", "1", default="1")
    _val(chart_root, "dispBlanksAs", "span", default="span")
    plot = chart_root.find(C + "plotArea")
    legend = chart_root.find(C + "legend")
    if plot is None or legend is None:
        raise ValueError("clustered chart requires plot and legend")
    _children(plot, "layout barChart catAx valAx spPr")
    _transparent(plot.find(C + "spPr"))
    layout = plot.find(C + "layout")
    if layout is not None and len(layout):
        raise ValueError("manual chart layout is unsupported")
    _children(legend, "legendPos overlay txPr")
    _val(legend, "legendPos", "b")
    _val(legend, "overlay", "0", default="0")
    legend_size, legend_family = _font(legend.find(C + "txPr"), 1300, fonts)
    bar = plot.find(C + "barChart")
    cat = plot.find(C + "catAx")
    axis = plot.find(C + "valAx")
    if (
        bar is None
        or cat is None
        or axis is None
        or len(plot.findall(C + "catAx")) != 1
        or len(plot.findall(C + "valAx")) != 1
    ):
        raise ValueError("unsupported chart axes")
    _children(bar, "barDir grouping varyColors ser dLbls gapWidth overlap axId")
    _val(bar, "barDir", "col")
    _val(bar, "grouping", "clustered")
    _val(bar, "varyColors", "0", default="0")
    _val(bar, "overlap", "0", default="0")
    _check_labels(bar.find(C + "dLbls"))
    gap_node = bar.find(C + "gapWidth")
    gap_text = gap_node.get("val", "150") if gap_node is not None else "150"
    if not gap_text.isdecimal() or int(gap_text) > 500:
        raise ValueError("invalid chart gap width")
    gap = int(gap_text) / 100
    _children(
        cat,
        "axId scaling delete axPos numFmt majorTickMark minorTickMark tickLblPos "
        "spPr txPr crossAx crosses auto lblAlgn noMultiLvlLbl",
    )
    _children(
        axis,
        "axId scaling delete axPos majorGridlines numFmt majorTickMark minorTickMark "
        "tickLblPos spPr txPr crossAx crosses crossBetween majorUnit",
    )
    for current, position in ((cat, "b"), (axis, "l")):
        _val(current, "delete", "0", default="0")
        _val(current, "axPos", position)
        _val(current, "majorTickMark", "out", default="out")
        _val(current, "minorTickMark", "none", default="none")
        _val(current, "crosses", "autoZero", default="autoZero")
        scaling = current.find(C + "scaling")
        if scaling is None:
            raise ValueError("missing chart axis scale")
        _children(scaling, "orientation min max")
        _val(scaling, "orientation", "minMax", default="minMax")
    _val(cat, "tickLblPos", "low")
    _val(axis, "tickLblPos", "nextTo")
    _val(axis, "crossBetween", "between")
    _val(cat, "lblAlgn", "ctr", default="ctr")
    _val(cat, "noMultiLvlLbl", "1", default="1")
    _val(cat, "auto", "1", default="1")
    cat_scale = cat.find(C + "scaling")
    assert cat_scale is not None
    if cat_scale.find(C + "min") is not None or cat_scale.find(C + "max") is not None:
        raise ValueError("unsupported category axis scale")
    for current, other in ((cat, axis), (axis, cat)):
        a, b = current.find(C + "crossAx"), other.find(C + "axId")
        if a is None or b is None or a.get("val") != b.get("val"):
            raise ValueError("chart axes do not cross")
    axis_ids = [n.get("val") for n in bar.findall(C + "axId")]
    cat_id, val_id = cat.find(C + "axId"), axis.find(C + "axId")
    if (
        cat_id is None
        or val_id is None
        or len(set(axis_ids)) != len(axis_ids)
        or axis_ids[:2] != [cat_id.get("val"), val_id.get("val")]
        or len(axis_ids) not in (2, 3)
    ):
        raise ValueError("invalid chart axis IDs")
    scale = axis.find(C + "scaling")
    assert scale is not None
    try:
        low = float(scale.find(C + "min").get("val", ""))  # type: ignore[union-attr]
        maximum = float(scale.find(C + "max").get("val", ""))  # type: ignore[union-attr]
        unit = float(axis.find(C + "majorUnit").get("val", ""))  # type: ignore[union-attr]
    except (ValueError, AttributeError) as exc:
        raise ValueError("chart requires an explicit scale and tick interval") from exc
    if (
        not all(math.isfinite(v) for v in (low, maximum, unit))
        or low != 0
        or not 1e-300 <= maximum <= 1e300
        or not 1e-300 <= unit <= 1e300
        or not 1 <= maximum / unit <= 20
        or abs(maximum / unit - round(maximum / unit)) > 1e-9
    ):
        raise ValueError("unsupported chart scale")
    for current in (cat, axis):
        fmt = current.find(C + "numFmt")
        if fmt is not None and fmt.get("formatCode") != "General":
            raise ValueError("unsupported chart axis format")
    cat_size, cat_family = _font(cat.find(C + "txPr"), 1200, fonts)
    axis_size, axis_family = _font(axis.find(C + "txPr"), 1100, fonts)
    grid = axis.find(C + "majorGridlines")
    if grid is None:
        raise ValueError("chart gridlines are required")
    _children(grid, "spPr")
    grid_color = _line(grid.find(C + "spPr"))
    cat_color = _line(cat.find(C + "spPr"))
    axis_color = _line(axis.find(C + "spPr"))
    external = root.find(C + "externalData")
    if external is not None:
        _children(external, "autoUpdate")
        _val(external, "autoUpdate", "0", default="0")
    if external is None or not external.get(R + "id"):
        raise ValueError("embedded chart workbook is required")
    book = read_part(resolve(part, external.get(R + "id", "")))
    if len(book) > 8_000_000:
        raise ValueError("chart workbook is oversized")
    workbook_ranges: dict[str, list[str]] = {}

    def workbook_values(formula: str) -> list[str]:
        if formula not in workbook_ranges:
            workbook_ranges[formula] = _workbook_values(book, formula)
        return workbook_ranges[formula]

    labels: list[str] = []
    data: list[tuple[str, str, list[str]]] = []
    series = bar.findall(C + "ser")
    if not 2 <= len(series) <= 8:
        raise ValueError("unsupported chart series count")
    for index, item in enumerate(series):
        _children(item, "idx order tx spPr invertIfNegative dLbls cat val")
        _val(item, "idx", str(index))
        _val(item, "order", str(index))
        _val(item, "invertIfNegative", "0", default="0")
        _check_labels(item.find(C + "dLbls"))
        title = item.find(C + "tx/" + C + "strRef")
        category = item.find(C + "cat/" + C + "multiLvlStrRef")
        values = item.find(C + "val/" + C + "numRef")
        if title is None or category is None or values is None:
            raise ValueError("unsupported chart data references")
        for container_name in ("tx", "cat", "val"):
            container = item.find(C + container_name)
            if container is None or len(container) != 1:
                raise ValueError("unsupported chart data reference")
        _reference(title, "strCache")
        _reference(category, "multiLvlStrCache")
        _reference(values, "numCache")
        names = _points(title, C + "strCache")
        cats = _points(category, C + "multiLvlStrCache/" + C + "lvl")
        nums = _points(values, C + "numCache")
        if (
            len(names) != 1
            or not cats
            or len(cats) != len(nums)
            or len(cats) > 100
            or (labels and cats != labels)
        ):
            raise ValueError("chart categories and values do not match")
        labels = cats
        try:
            parsed = [Decimal(v) for v in nums]
            book_values = [Decimal(v) for v in workbook_values(values.findtext(C + "f", ""))]
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("invalid chart data") from exc
        if any(
            not v.is_finite()
            or v < 0
            or v > Decimal(str(maximum))
            or (v != 0 and v < Decimal("1e-300"))
            for v in parsed
        ):
            raise ValueError("chart value outside axis scale")
        if (
            workbook_values(category.findtext(C + "f", "")) != cats
            or workbook_values(title.findtext(C + "f", "")) != names
            or book_values != parsed
        ):
            raise ValueError("chart workbook data does not match chart cache")
        props = item.find(C + "spPr")
        if props is None or any(
            child.tag not in (A + "solidFill", A + "effectLst") for child in props
        ):
            raise ValueError("unsupported chart series formatting")
        solid = props.find(A + "solidFill")
        if solid is None or len(solid) != 1 or solid[0].tag != A + "srgbClr":
            raise ValueError("unsupported chart series color")
        effect = props.find(A + "effectLst")
        rgb = props.find(A + "solidFill/" + A + "srgbClr")
        color = rgb.get("val", "") if rgb is not None else ""
        if (
            (effect is not None and (len(effect) or effect.attrib))
            or (rgb is not None and len(rgb))
            or not re.fullmatch(r"[0-9a-fA-F]{6}", color)
        ):
            raise ValueError("unsupported chart series color or effects")
        data.append((names[0], "#" + color, nums))
    return _render(
        labels,
        data,
        maximum,
        unit,
        gap,
        grid_color,
        cat_color,
        axis_color,
        cat_size,
        axis_size,
        legend_size,
        cat_family,
        axis_family,
        legend_family,
        width,
        height,
    )


def _render(
    labels: list[str],
    data: list[tuple[str, str, list[str]]],
    maximum: float,
    unit: float,
    gap: float,
    grid: str,
    category_color: str,
    axis_color: str,
    cat_size: int,
    axis_size: int,
    legend_size: int,
    cat_family: str,
    axis_family: str,
    legend_family: str,
    width: int,
    height: int,
) -> str:
    """Lay out an editable plot in the source canvas, reserving space for labels and legend."""

    def relative(points: float) -> str:
        return f"{points * 12700 / width * 100:.7g}cqw"

    ticks = [i * unit for i in range(round(maximum / unit) + 1)]
    axis_pt, cat_pt, legend_pt = axis_size / 100, cat_size / 100, legend_size / 100
    # An automatic plot area has no stored rectangle. Reserve space in typographic
    # units, including the longest value label, the outward ticks and the legend.
    label_width = max(len(f"{tick:g}") for tick in ticks) * 0.56 * axis_pt
    left = axis_pt + label_width + 5
    right = top = axis_pt
    bottom = 2 * legend_pt + 1.5 * cat_pt + 0.85 * axis_pt
    if (left + right) * 12700 >= width or (bottom + top) * 12700 >= height:
        raise ValueError("Chart is too small for its axis labels and legend.")
    stroke, tick_length = relative(1), relative(3.5)
    axis_style = f"font-size:{relative(axis_pt)};font-family:{html.escape(axis_family, quote=True)}"
    cat_style = f"font-size:{relative(cat_pt)};font-family:{html.escape(cat_family, quote=True)}"
    out = [
        f'<div data-pptx-chart="clustered-column" data-chart-max="{maximum:g}" '
        'style="position:relative;width:100%;height:100%;color:#000;container-type:inline-size">',
        f'<div data-chart-plot style="position:absolute;left:{relative(left)};top:{relative(top)};'
        f'right:{relative(right)};bottom:{relative(bottom)}">',
    ]
    for tick in ticks:
        fraction = tick / maximum
        color = category_color if tick == 0 else grid
        out.append(
            f'<div data-chart-tick="{fraction:.12g}" style="position:absolute;'
            f'bottom:{fraction * 100:.7g}%;width:100%;height:0;border-top:{stroke} solid {color}">'
            f'<span style="position:absolute;right:100%;width:{tick_length};height:0;'
            f'border-top:{stroke} solid {axis_color};top:-{stroke}"></span>'
            '<span style="position:absolute;right:100%;white-space:nowrap;line-height:1;'
            f"{axis_style};"
            f'transform:translate(-0.9em,-50%)">{tick:g}</span></div>'
        )
    out.append(
        '<div style="position:absolute;left:0;top:0;height:100%;'
        f'border-left:{stroke} solid {axis_color}"></div>'
    )
    band = 100 / len(labels)
    bar_width = band / (len(data) + gap)
    for i, label in enumerate(labels):
        for j, (_name, color, values) in enumerate(data):
            raw = values[i]
            start = i * band + (band - len(data) * bar_width) / 2 + j * bar_width
            percent = float(raw) / maximum * 100
            escaped = html.escape(raw, quote=True)
            out.append(
                f'<div data-chart-fixed-max="{maximum!r}" style="position:absolute;'
                f"left:{start:.7g}%;"
                f'bottom:0;width:{bar_width:.7g}%;height:100%;pointer-events:none">'
                f'<div data-chart-bar style="position:absolute;bottom:0;width:100%;'
                f'height:{percent:.4f}%;background:{color}"></div>'
                f'<span data-chart-value="{escaped}" aria-label="Chart value {escaped}" '
                'style="position:absolute;z-index:1;pointer-events:auto;left:0;bottom:0;'
                f'width:100%;height:{percent:.4f}%;min-height:8px;opacity:0">{escaped}</span></div>'
            )
        out.append(
            f'<span data-chart-category style="position:absolute;left:{i * band:.7g}%;'
            f"width:{band:.7g}%;top:100%;padding-top:.75em;line-height:1.2;"
            f'text-align:center;{cat_style}">'
            f"{html.escape(label)}</span>"
        )
    for boundary in range(len(labels) + 1):
        out.append(
            f'<span style="position:absolute;left:{band * boundary:.7g}%;top:100%;'
            f'height:{tick_length};border-left:{stroke} solid {category_color}"></span>'
        )
    out.append(
        '</div><div data-chart-legend style="position:absolute;bottom:.75em;width:100%;'
        f"display:flex;justify-content:center;align-items:center;gap:.8em;line-height:1;"
        f'font-family:{html.escape(legend_family, quote=True)};font-size:{relative(legend_pt)}">'
    )
    for name, color, _values in data:
        out.append(
            '<div style="display:flex;align-items:center;gap:.25em">'
            f'<span style="background:{color};width:.52em;height:.52em"></span>'
            f"<span>{html.escape(name)}</span></div>"
        )
    return "".join(out) + "</div></div>"
