"""Map the imported HTML editor's supported changes back to original DrawingML."""

import html
import math
import re
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from ..models import Slide
from .edit import Node, Tree, edit_pptx_text
from .graphics import C
from .importer import OriginalObject, required
from .package import A, P, Package, R
from .xml_patch import XmlPatch

_TEXT_CACHE = {"strCache", "strLit", "numCache", "numLit", "lvl"}
_GEOMETRY = ("left", "top", "width", "height")
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_ROTATE = re.compile(rf"rotate\(({_NUMBER})deg\)")
_SCALE = re.compile(r"scale\((-?1),(-?1)\)")
_STYLE = re.compile(r'\bstyle="[^"]*"')


@dataclass
class ChartRange:
    formula: str
    cells: list[ET.Element]
    numeric: bool


@dataclass
class Chart:
    part: str
    ranges: list[ChartRange]
    edits: dict[ET.Element, str]


def chart_ranges(root: ET.Element) -> list[list[ChartRange]]:
    chart = required(root, f"{C}chart/{C}plotArea/{C}barChart")
    result: list[list[ChartRange]] = []
    for series in chart.findall(C + "ser"):
        ranges: list[ChartRange] = []
        for field in ("tx", "cat", "val"):
            container = series.find(C + field)
            cache = (
                next((n for n in container.iter() if n.tag.removeprefix(C) in _TEXT_CACHE), None)
                if container is not None
                else None
            )
            if cache is None:
                ranges.append(ChartRange("", [], field == "val"))
                continue
            points = [required(point, C + "v") for point in cache.findall(C + "pt")]
            formula = next(iter(container.iter(C + "f")), None) if container is not None else None
            ranges.append(
                ChartRange(
                    formula.text or "" if formula is not None else "", points, field == "val"
                )
            )
        result.append(ranges)
    if not result:
        raise ValueError("PowerPoint chart series could not be mapped.")
    return result


def _visit(node: Node, path: tuple[int, ...]) -> list[tuple[Node, tuple[int, ...]]]:
    return [(node, path)] + [
        item for i, child in enumerate(node.children) for item in _visit(child, (*path, i))
    ]


def _article(tree: Tree) -> Node:
    if (
        len(tree.root.children) != 1
        or tree.root.children[0].tag != "article"
        or not tree.root.children[0].end
    ):
        raise ValueError("The imported slide has unsupported markup.")
    return tree.root.children[0]


def _at(article: Node, path: tuple[int, ...]) -> Node:
    try:
        for index in path:
            article = article.children[index]
    except IndexError as exc:
        raise ValueError(
            "PowerPoint objects or text runs were added, removed, or rearranged."
        ) from exc
    return article


def _leaf(tree: Tree, node: Node) -> str:
    if node.children or not node.end:
        raise ValueError("PowerPoint text has unsupported markup.")
    return html.unescape(tree.source[node.content : node.end])


def _style(node: Node) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in (node.attrs.get("style") or "").split(";"):
        if entry:
            key, separator, value = entry.partition(":")
            if not separator or key in result:
                raise ValueError("The imported object has unsupported CSS.")
            result[key] = value
    return result


def _rotation(style: dict[str, str]) -> tuple[float, tuple[str, ...]]:
    angle = 0.0
    scales: tuple[str, ...] = ()
    for transform in style.get("transform", "").split():
        if match := _ROTATE.fullmatch(transform):
            angle += float(match[1]) * (-1 if scales.count("-1") == 1 else 1)
        elif match := _SCALE.fullmatch(transform):
            if scales:
                raise ValueError("The imported object has multiple flip transforms.")
            scales = match.groups()
        else:
            raise ValueError("The imported object has an unsupported transform.")
    if not math.isfinite(angle):
        raise ValueError("The imported object has an invalid rotation.")
    return angle, scales


def _percent(value: str) -> float:
    if re.fullmatch(_NUMBER + "%", value) is None:
        raise ValueError("PowerPoint object geometry must use percentages.")
    result = float(value[:-1]) / 100
    if not math.isfinite(result) or abs(result) > 1_000:
        raise ValueError("PowerPoint object geometry is out of range.")
    return result


def _geometry(original: OriginalObject, before: Node, after: Node, patch: XmlPatch) -> str | None:
    if before.attrs.get("style") == after.attrs.get("style"):
        return None
    base, current = _style(before), _style(after)
    geometry = {*_GEOMETRY, "transform"}
    if {key: value for key, value in base.items() if key not in geometry} != {
        key: value for key, value in current.items() if key not in geometry
    }:
        raise ValueError("PowerPoint export cannot apply changes to object styling.")
    if original.layer != "slide" or before.attrs.get("data-pptx-group") == "true":
        raise ValueError("PowerPoint export cannot change master, layout or group geometry.")
    angle, scale = _rotation(base)
    new_angle, new_scale = _rotation(current)
    if scale != new_scale:
        raise ValueError("PowerPoint export cannot change object flip transforms.")
    xf = original.transform
    replacement = ET.fromstring(ET.tostring(xf))
    canvas = original.canvas
    for tag, axes in (
        ("off", (("x", "left", canvas.width, canvas.x), ("y", "top", canvas.height, canvas.y))),
        ("ext", (("cx", "width", canvas.width, 0), ("cy", "height", canvas.height, 0))),
    ):
        for attribute, prop, scale_emu, origin in axes:
            if current.get(prop) == base.get(prop):
                continue
            value = round(_percent(current.get(prop, "")) * scale_emu) + origin
            if (tag == "ext" and value <= 0) or abs(value) > 27_273_042_316_900:
                raise ValueError("PowerPoint object size or position is out of range.")
            required(replacement, A + tag).set(attribute, str(value))
    if angle != new_angle:
        replacement.set("rot", str(round(new_angle * 60_000) % 21_600_000))
    if xf in patch.spans:
        for updated, old in zip(replacement.iter(), xf.iter(), strict=True):
            for key, value in updated.attrib.items():
                if old.get(key) != value:
                    patch.attribute(old, key, value)
    else:
        props = required(original.shape, P + "spPr")
        if len(props):
            patch.insert_before(props[0], ET.tostring(replacement))
        else:
            patch.inner(props, ET.tostring(replacement))
    return after.attrs.get("style") or ""


def _object_text(
    shape: ET.Element, node: Node, path: tuple[int, ...]
) -> list[tuple[tuple[int, ...], list[ET.Element]]]:
    descendants = _visit(node, path)
    if shape.tag == P + "sp":
        nodes = [(item, key) for item, key in descendants if item.tag == "span"]
        runs = shape.findall(f"{P}txBody/{A}p/{A}r")
        if len(nodes) != len(runs):
            raise ValueError("PowerPoint text runs could not be mapped.")
        return [(key, [required(run, A + "t")]) for (_, key), run in zip(nodes, runs, strict=True)]
    cells = shape.findall(f".//{A}tbl/{A}tr/{A}tc")
    if cells:
        nodes = [key for item, key in descendants if item.tag == "td"]
        if len(nodes) != len(cells):
            raise ValueError("PowerPoint table cells could not be mapped.")
        return [(key, list(cell.iter(A + "t"))) for key, cell in zip(nodes, cells, strict=True)]
    return []


def _chart_text(
    root: ET.Element, node: Node, path: tuple[int, ...]
) -> tuple[list[ChartRange], list[tuple[tuple[int, ...], list[ET.Element]]]]:
    series = chart_ranges(root)
    chart_path = (*path, 0)
    chart = node.children[0]
    labels: list[tuple[tuple[int, ...], list[ET.Element]]] = []
    if chart.attrs.get("data-pptx-chart") == "column" and len(series) == 1:
        title, category, values = series[0]
        groups = [
            (item, (*chart_path, index))
            for index, item in enumerate(chart.children)
            if "data-chart-tick" not in item.attrs and "data-chart-title" not in item.attrs
        ]
        for index, (group, key) in enumerate(groups):
            if len(group.children) != 3 or "data-chart-value" not in group.children[0].attrs:
                raise ValueError("PowerPoint chart point could not be mapped.")
            labels.extend(
                [((*key, 0), [values.cells[index]]), ((*key, 2), [category.cells[index]])]
            )
        for index, item in enumerate(chart.children):
            if "data-chart-title" in item.attrs:
                labels.append(((*chart_path, index), title.cells))
    elif chart.attrs.get("data-pptx-chart") == "clustered-column":
        items = _visit(chart, chart_path)
        values = [key for item, key in items if "data-chart-value" in item.attrs]
        categories = [key for item, key in items if "data-chart-category" in item.attrs]
        plotted = len(series[0][1].cells)
        if len(values) != len(series) * plotted or len(categories) != plotted:
            raise ValueError("PowerPoint chart points could not be mapped.")
        for point, key in enumerate(categories):
            labels.append((key, [category.cells[point] for _, category, _ in series]))
            for index, (_, _, column) in enumerate(series):
                labels.append((values[point * len(series) + index], [column.cells[point]]))
        legend = chart.children[1]
        if "data-chart-legend" not in legend.attrs or len(legend.children) != len(series):
            raise ValueError("PowerPoint chart legend could not be mapped.")
        for index, (title, _, _) in enumerate(series):
            labels.append(((*chart_path, 1, index, 1), title.cells))
    else:
        raise ValueError("PowerPoint chart could not be mapped.")
    return [column for item in series for column in item], labels


def patch_slide(
    package: Package,
    source: Slide,
    current: Slide,
    part: str,
    objects: dict[str, OriginalObject],
    patch: XmlPatch,
) -> list[Chart]:
    if current.interactive_html:
        raise ValueError("PowerPoint export cannot apply interactive HTML.")
    if current.title != source.title:
        patch.attribute(required(package.xml(part), P + "cSld"), "name", current.title)
    original = Tree(source.html)
    updated = Tree(current.html)
    original_article, updated_article = _article(original), _article(updated)
    expected = source.html
    text_edits: list[tuple[tuple[int, ...], str]] = []
    tag_edits: list[tuple[int, int, str]] = []
    charts: list[Chart] = []
    for node, path in _visit(original_article, ()):
        key = node.attrs.get("data-element-id")
        if key is None:
            continue
        before = objects[key]
        after = _at(updated_article, path)
        geometry = _geometry(before, node, after, patch)
        if geometry is not None:
            edited = _STYLE.sub(
                lambda _, value=geometry: f'style="{html.escape(value)}"',
                original.source[node.start : node.content],
                count=1,
            )
            tag_edits.append((node.start, node.content, edited))
        if before.shape.tag == P + "grpSp":
            continue
        chart_ref = before.shape.find(f".//{A}graphicData/{C}chart")
        chart = None
        if chart_ref is not None:
            chart_part = package.resolve(before.part, chart_ref.attrib[R + "id"])
            ranges, text = _chart_text(package.xml(chart_part), node, path)
            chart = Chart(chart_part, ranges, {})
            charts.append(chart)
        else:
            text = _object_text(before.shape, node, path)
        for leaf, targets in text:
            prior = _leaf(original, _at(original_article, leaf))
            value = _leaf(updated, _at(updated_article, leaf))
            if value == prior:
                continue
            if before.layer != "slide" or not targets:
                raise ValueError("PowerPoint export cannot change inherited or unmapped text.")
            if any((target.text or "") != prior for target in targets):
                raise ValueError("PowerPoint text no longer matches its original object.")
            if chart is None:
                if len(targets) != 1:
                    raise ValueError("PowerPoint export cannot change mixed table text.")
                patch.text(targets[0], value)
                if value != value.strip():
                    patch.attribute(targets[0], "xml:space", "preserve")
            else:
                for target in targets:
                    if chart.edits.setdefault(target, value) != value:
                        raise ValueError("PowerPoint chart edits conflict in a shared category.")
            text_edits.append((leaf, value))
    for start, end, tag in sorted(tag_edits, reverse=True):
        expected = expected[:start] + tag + expected[end:]
    for path, value in text_edits:
        expected = edit_pptx_text(expected, list(path), value)
    if expected != current.html:
        raise ValueError("PowerPoint export cannot apply the slide's markup or styling changes.")
    return charts
