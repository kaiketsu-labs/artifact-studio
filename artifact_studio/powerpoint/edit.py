"""Bounded text edits to imported PowerPoint markup."""

import html
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser


@dataclass
class Node:
    tag: str
    attrs: dict[str, str | None]
    start: int
    content: int
    end: int = 0
    children: list[Node] = field(default_factory=list)
    parent: Node | None = None


class Tree(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.lines = [0]
        for match in re.finditer("\n", source):
            self.lines.append(match.end())
        self.root = Node("", {}, 0, 0)
        self.current = self.root
        self.feed(source)

    def source_offset(self) -> int:
        line, col = self.getpos()
        return self.lines[line - 1] + col

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        start = self.source_offset()
        node = Node(
            tag,
            dict(attrs),
            start,
            start + len(self.get_starttag_text() or ""),
            parent=self.current,
        )
        self.current.children.append(node)
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.current = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.current.tag == tag:
            self.current = self.current.parent or self.root

    def handle_endtag(self, tag: str) -> None:
        if self.current.tag != tag:
            raise ValueError("Imported slide markup is malformed.")
        self.current.end = self.source_offset()
        self.current = self.current.parent or self.root


def edit_pptx_text(source: str, path: list[int], text: str) -> str:
    """Replace one leaf and update a chart's bars, preserving all other source bytes."""
    tree = Tree(source)
    articles = [
        node
        for node in tree.root.children
        if node.tag == "article"
        and "data-pptx-width" in node.attrs
        and "data-pptx-height" in node.attrs
    ]
    if len(articles) != 1 or not path or len(path) > 40:
        raise ValueError("Select imported slide text.")
    node = articles[0]
    for index in path:
        if index < 0 or index >= len(node.children):
            raise ValueError("Selected text no longer exists.")
        node = node.children[index]
    if (
        node.tag
        not in {"span", "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "label", "li"}
        or node.children
        or not node.end
    ):
        raise ValueError("Select a single text run, cell, or chart label.")
    if not text.strip() or len(text) > 9000:
        raise ValueError("Keep replacement text between 1 and 9,000 characters.")
    edits: list[tuple[int, int, str]] = [(node.content, node.end, html.escape(text))]
    if "data-chart-value" in node.attrs:
        try:
            number = Decimal(text)
            value = float(number)
        except (InvalidOperation, ValueError, OverflowError) as exc:
            raise ValueError("Chart values must be finite nonnegative numbers.") from exc
        if not math.isfinite(value) or number < 0 or (number != 0 and not 1e-300 <= value <= 1e300):
            raise ValueError("Chart values must be zero or between 1e-300 and 1e300.")
        chart = node.parent.parent if node.parent else None
        if chart is None or chart.attrs.get("data-pptx-chart") != "column":
            raise ValueError("Unsupported chart structure.")
        values: list[float] = []
        bars: list[Node] = []
        for group in chart.children:
            if "data-chart-tick" in group.attrs or "data-chart-title" in group.attrs:
                continue
            value_nodes = [child for child in group.children if "data-chart-value" in child.attrs]
            bar_nodes = [child for child in group.children if "data-chart-bar" in child.attrs]
            if len(value_nodes) != 1 or len(bar_nodes) != 1:
                raise ValueError("Unsupported chart structure.")
            values.append(
                value
                if value_nodes[0] is node
                else float(value_nodes[0].attrs["data-chart-value"] or "")
            )
            bars.append(bar_nodes[0])
        if any(not math.isfinite(v) or v < 0 or (v and not 1e-300 <= v <= 1e300) for v in values):
            raise ValueError("Chart values must be zero or between 1e-300 and 1e300.")
        maximum = max(values) or 1
        magnitude = 10 ** math.floor(math.log10(maximum))
        maximum = math.ceil(maximum / magnitude) * magnitude
        chart_tag = source[chart.start : chart.content]
        updated, count = re.subn(
            r'data-chart-max="[^"]*"', f'data-chart-max="{maximum:g}"', chart_tag, count=1
        )
        if count != 1:
            raise ValueError("Unsupported chart structure.")
        edits.append((chart.start, chart.content, updated))
        for tick in chart.children:
            if "data-chart-tick" in tick.attrs:
                ratio = float(tick.attrs["data-chart-tick"] or "")
                if not 0 <= ratio <= 1 or len(tick.children) != 1:
                    raise ValueError("Unsupported chart structure.")
                label = tick.children[0]
                if label.children or not label.end:
                    raise ValueError("Unsupported chart structure.")
                edits.append((label.content, label.end, f"{maximum * ratio:g}"))
        for bar, number in zip(bars, values, strict=True):
            start_tag = source[bar.start : bar.content]
            updated, count = re.subn(
                r"height:[0-9.]+%", f"height:{number / maximum * 70:.4f}%", start_tag, count=1
            )
            if count != 1:
                raise ValueError("Unsupported chart structure.")
            edits.append((bar.start, bar.content, updated))
            if bar.parent is None:
                raise ValueError("Unsupported chart structure.")
            value_node = next(
                child for child in bar.parent.children if "data-chart-value" in child.attrs
            )
            value_tag = source[value_node.start : value_node.content]
            if value_node is node:
                value_tag = re.sub(
                    r'data-chart-value="[^"]*"',
                    'data-chart-value="' + html.escape(text, quote=True) + '"',
                    value_tag,
                    count=1,
                )
            if value_node is node:
                value_tag = re.sub(
                    r'aria-label="[^"]*"',
                    'aria-label="Chart value ' + html.escape(text, quote=True) + '"',
                    value_tag,
                    count=1,
                )
            value_tag, count = re.subn(
                r"height:[0-9.]+%", f"height:{number / maximum * 70:.4f}%", value_tag, count=1
            )
            if count != 1:
                raise ValueError("Unsupported chart structure.")
            edits.append((value_node.start, value_node.content, value_tag))
    for start, end, value in sorted(edits, reverse=True):
        source = source[:start] + value + source[end:]
    return source


def synchronized_title(source: str, updated: str, path: list[int], title: str) -> str | None:
    """Follow the original title shape only while the slide name still mirrors it."""

    def candidates(tree: Tree) -> list[Node]:
        result: list[Node] = []

        def visit(node: Node) -> None:
            if "data-pptx-title" in node.attrs:
                result.append(node)
            for child in node.children:
                visit(child)

        visit(tree.root)
        titles = [node for node in result if node.attrs["data-pptx-title"] == "title"]
        return titles or [node for node in result if node.attrs["data-pptx-title"] == "fallback"]

    def title_text(tree: Tree, node: Node) -> str:
        runs: list[str] = []

        def visit(item: Node) -> None:
            if item.tag == "span" and not item.children and item.end:
                runs.append(html.unescape(tree.source[item.content : item.end]))
            else:
                for child in item.children:
                    visit(child)

        visit(node)
        return " ".join(runs).strip()[:160]

    original, revised = Tree(source), Tree(updated)
    before, after = candidates(original), candidates(revised)
    if not before or not after or title_text(original, before[0]) != title:
        return None
    article = next((item for item in original.root.children if item.tag == "article"), None)
    if article is None:
        return None
    node = article
    for index in path:
        if index < 0 or index >= len(node.children):
            return None
        node = node.children[index]
    while node is not None and node is not before[0]:
        node = node.parent
    if node is None:
        return None
    result = title_text(revised, after[0])
    return result or None


def transform_pptx_object(source: str, index: int, dx: float, dy: float, rotation: float) -> str:
    """Move and rotate an imported top-level object without altering its contents."""
    if not all(math.isfinite(v) for v in (dx, dy, rotation)) or not (
        -2 <= dx <= 2 and -2 <= dy <= 2 and -180 <= rotation <= 180
    ):
        raise ValueError("Invalid imported object transform.")
    tree = Tree(source)
    articles = [
        n for n in tree.root.children if n.tag == "article" and "data-pptx-width" in n.attrs
    ]
    if len(articles) != 1 or index < 0 or index >= len(articles[0].children):
        raise ValueError("Select an imported object.")
    node = articles[0].children[index]
    if (
        node.tag != "div"
        or "data-pptx-object" not in node.attrs
        or "data-pptx-group" in node.attrs
        or any("data-pptx-object" in child.attrs for child in node.children)
    ):
        raise ValueError("Select a top-level imported shape, not a group.")
    style = node.attrs.get("style") or ""
    for prop, change in (("left", dx * 100), ("top", dy * 100)):
        pattern = rf"(?<![\w-]){prop}:([-+\d.eE]+)%"
        match = re.search(pattern, style)
        if not match:
            raise ValueError("Unsupported imported object position.")
        position = float(match.group(1)) + change
        if not math.isfinite(position):
            raise ValueError("Invalid imported object position.")
        style = style[: match.start(1)] + f"{position:.9g}" + style[match.end(1) :]
    if rotation:
        match = re.search(r"(?<![\w-])transform:([^;]*);", style)
        if match:
            value = match.group(1) + f" rotate({rotation:.9g}deg)"
            style = style[: match.start(1)] + value + style[match.end(1) :]
        else:
            style += f"transform:rotate({rotation:.9g}deg);"
    tag = source[node.start : node.content]
    updated, count = re.subn(
        r'\bstyle="[^"]*"', lambda _: 'style="' + html.escape(style, quote=True) + '"', tag, count=1
    )
    if count != 1:
        raise ValueError("Unsupported imported object style.")
    return source[: node.start] + updated + source[node.content :]
