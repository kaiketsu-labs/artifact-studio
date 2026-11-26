"""Initialize editable text slots without reserializing the design pack's HTML or SVG."""

import re
from html import escape
from html.parser import HTMLParser


class CoverSlots(HTMLParser):
    """Locate the first heading and designated subtitle slots in the cover source."""

    def __init__(self, source: str, title: str) -> None:
        super().__init__(convert_charrefs=False)
        self.lines = [0]
        self.lines.extend(match.end() for match in re.finditer("\n", source))
        self.title = escape(title)
        self.has_heading = False
        self.tag = ""
        self.depth = 0
        self.content_start = 0
        self.replacement = ""
        self.edits: list[tuple[int, int, str]] = []
        self.feed(source)
        self.close()

    def source_offset(self) -> int:
        """Translate the parser position into an offset in the original markup."""
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track only the element whose contents will be replaced."""
        if self.tag:
            self.depth += tag == self.tag
            return
        attributes = dict(attrs)
        slots = (attributes.get("class") or "").split()
        slots.append(attributes.get("data-element-id") or "")
        if tag == "h1" and not self.has_heading:
            self.has_heading = True
            self.replacement = self.title
        elif {"subtitle", "subline"}.intersection(slots):
            self.replacement = ""
        else:
            return
        self.tag = tag
        self.depth = 1
        self.content_start = self.source_offset() + len(self.get_starttag_text() or "")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Self-closing elements contain no editable text."""

    def handle_endtag(self, tag: str) -> None:
        """Record a complete slot, including any nested inline markup."""
        if tag == self.tag:
            self.depth -= 1
            if self.depth == 0:
                self.edits.append((self.content_start, self.source_offset(), self.replacement))
                self.tag = ""


def cover_html(source: str, title: str) -> str:
    """Fill the title and empty subtitle slots, retaining the rest of the design."""
    for start, end, replacement in reversed(CoverSlots(source, title).edits):
        source = source[:start] + replacement + source[end:]
    return source
