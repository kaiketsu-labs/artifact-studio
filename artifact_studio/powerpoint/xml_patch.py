"""Targeted OOXML edits that keep the original namespace declarations and markup."""

import re
from dataclasses import dataclass
from xml.etree import ElementTree as ET
from xml.parsers.expat import ParserCreate
from xml.sax.saxutils import escape, quoteattr

_TAG = re.compile(rb"""<(?:[^<>"']|"[^"]*"|'[^']*')*>""")
_ATTR = re.compile(rb"""\s+([^\s=]+)\s*=\s*(?:"[^"]*"|'[^']*')""")
_INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def xml_text(value: str) -> bytes:
    if _INVALID_XML.search(value):
        raise ValueError("PowerPoint text contains invalid XML characters.")
    return escape(value, {"\r": "&#13;"}).encode()


@dataclass
class Span:
    start: int
    content: int
    close: int = 0
    end: int = 0


class XmlPatch:
    """Associate the parsed XML tree with byte ranges using the original token stream."""

    def __init__(self, data: bytes, root: ET.Element):
        self.data = data
        self.spans: dict[ET.Element, Span] = {}
        self.edits: dict[tuple[int, int], bytes] = {}
        self.attributes: dict[ET.Element, dict[str, str | None]] = {}
        items = iter(root.iter())
        stack: list[Span] = []
        parser = ParserCreate(namespace_separator="}")

        def start(name: str, _attributes: dict[str, str]) -> None:
            node = next(items)
            qualified = "{" + name if "}" in name else name
            pos = parser.CurrentByteIndex
            opening = _TAG.match(data, pos)
            if node.tag != qualified or opening is None:
                raise ValueError("Original PowerPoint markup could not be matched.")
            span = Span(pos, opening.end())
            self.spans[node] = span
            stack.append(span)

        def end(_name: str) -> None:
            span = stack.pop()
            if data[span.content - 2 : span.content] == b"/>":
                span.close = span.end = span.content
                return
            span.close = parser.CurrentByteIndex
            closing = _TAG.match(data, span.close)
            if closing is None:
                raise ValueError("Original PowerPoint markup could not be matched.")
            span.end = closing.end()

        def comment(_value: str) -> None:
            pos = parser.CurrentByteIndex
            self.edits[pos, data.index(b"-->", pos) + 3] = b""

        def instruction(_target: str, _value: str) -> None:
            pos = parser.CurrentByteIndex
            self.edits[pos, data.index(b"?>", pos) + 2] = b""

        def declaration(_version: str, encoding: str | None, _standalone: int) -> None:
            if encoding and encoding.lower() != "utf-8":
                raise ValueError("PowerPoint export requires UTF-8 source markup.")

        parser.StartElementHandler = start
        parser.EndElementHandler = end
        parser.CommentHandler = comment
        parser.ProcessingInstructionHandler = instruction
        parser.XmlDeclHandler = declaration
        parser.Parse(data, True)

    def attribute(self, node: ET.Element, name: str, value: str | None) -> None:
        if value is not None:
            _ = xml_text(value)
        self.attributes.setdefault(node, {})[name] = value

    def replace(self, node: ET.Element, value: bytes) -> None:
        span = self.spans[node]
        self.edits[span.start, span.end] = value

    def original(self, node: ET.Element) -> bytes:
        span = self.spans[node]
        return self.data[span.start : span.end]

    def text(self, node: ET.Element, value: str) -> None:
        if len(node):
            raise ValueError("PowerPoint text has unsupported child markup.")
        self.inner(node, xml_text(value))

    def inner(self, node: ET.Element, value: bytes) -> None:
        span = self.spans[node]
        if span.close == span.content and self.data[span.content - 2 : span.content] == b"/>":
            name = self.data[span.start + 1 : span.content].split(None, 1)[0].removesuffix(b"/>")
            self.edits[span.content - 2, span.content] = b">" + value + b"</" + name + b">"
        else:
            self.edits[span.content, span.close] = value

    def insert_before(self, node: ET.Element, value: bytes) -> None:
        pos = self.spans[node].start
        self.edits[pos, pos] = value

    def render(self) -> bytes:
        for node, changes in self.attributes.items():
            span = self.spans[node]
            original = self.data[span.start : span.content]
            pos = span.content - (2 if original.endswith(b"/>") else 1)
            additions = b""
            for name, value in changes.items():
                attr = name.encode()
                match = next(
                    (match for match in _ATTR.finditer(original) if match[1] == attr), None
                )
                addition = b"" if value is None else b" " + attr + b"=" + quoteattr(value).encode()
                if match is None:
                    additions += addition
                else:
                    self.edits[span.start + match.start(), span.start + match.end()] = addition
            if additions:
                self.edits[pos, pos] = additions
        output: list[bytes] = []
        start = 0
        for (left, right), value in sorted(self.edits.items()):
            if left < start:
                if right <= start and not value:
                    continue
                raise ValueError("PowerPoint changes overlap in the original markup.")
            output.extend((self.data[start:left], value))
            start = right
        output.append(self.data[start:])
        return b"".join(output)
