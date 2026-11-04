"""Keep slide-local CSS portable and contained without limiting custom sandbox documents."""

import hashlib
import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

import tinycss2
from tinycss2.ast import (
    AtRule,
    CurlyBracketsBlock,
    Declaration,
    FunctionBlock,
    IdentToken,
    LiteralToken,
    Node,
    ParenthesesBlock,
    QualifiedRule,
    SquareBracketsBlock,
    StringToken,
    URLToken,
)

SCOPE = re.compile(r'^\s*\(\.slide\[data-slide-style~="([a-f0-9]{16})"\]\)\s*$')


def local_resource(value: str) -> None:
    """Permit bundled assets, image/font data and SVG anchors, never workspace routes."""
    value = value.strip()
    if value.startswith(("#", "data:image/", "data:font/", "data:application/font-")):
        return
    parsed = urlsplit(value)
    path = unquote(parsed.path)
    if (
        parsed.scheme
        or parsed.netloc
        or not path.startswith("assets/")
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or "\\" in path
        or any(ord(char) < 32 for char in value + path)
    ):
        raise ValueError(
            "Slide CSS resources must use local assets/ paths or image/font data URLs."
        )


def css_values(tokens: list[Node]) -> None:
    """Inspect decoded CSS tokens, including escaped URLs and image-set string sources."""
    for token in tokens:
        if token.type == "error":
            raise ValueError(
                "Slide CSS contains invalid syntax. Use valid CSS or interactive_html."
            )
        if isinstance(token, URLToken):
            local_resource(token.value)
        elif isinstance(token, FunctionBlock):
            if token.lower_name == "expression":
                raise ValueError("Use interactive_html for executable content.")
            if token.lower_name in {"url", "src", "image", "image-set", "-webkit-image-set"}:
                for argument in token.arguments:
                    if isinstance(argument, StringToken):
                        local_resource(argument.value)
                    if isinstance(argument, FunctionBlock) and argument.lower_name in {
                        "var",
                        "attr",
                    }:
                        raise ValueError("Use an explicit local asset URL in static CSS images.")
            css_values(token.arguments)
        elif isinstance(token, (ParenthesesBlock, SquareBracketsBlock, CurlyBracketsBlock)):
            css_values(token.content)


def declarations(source: str | list[Node]) -> None:
    """Validate declarations without imposing a visual-property catalogue."""
    for node in tinycss2.parse_declaration_list(source, skip_comments=True, skip_whitespace=True):
        if not isinstance(node, Declaration) or node.lower_name in {"behavior", "-moz-binding"}:
            raise ValueError("Use interactive_html or theme.css for nested or global CSS rules.")
        css_values(node.value)


def root_selectors(tokens: list[Node]) -> list[Node]:
    """Resolve a leading .slide as the @scope root, retaining descendant selectors."""
    result: list[Node] = []
    beginning = True
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if isinstance(token, LiteralToken) and token.value == ",":
            beginning = True
        elif token.type not in {"whitespace", "comment"}:
            if (
                beginning
                and isinstance(token, LiteralToken)
                and token.value == "."
                and index + 1 < len(tokens)
                and isinstance(name := tokens[index + 1], IdentToken)
                and name.value == "slide"
            ):
                result.extend(tinycss2.parse_component_value_list(":scope"))
                index += 2
                beginning = False
                continue
            beginning = False
        result.append(token)
        index += 1
    return result


def stylesheet(source: str) -> str:
    """Serialize balanced, local style rules; global at-rules belong in theme or sandbox."""
    rules = tinycss2.parse_stylesheet(source, skip_comments=True, skip_whitespace=True)
    if len(rules) == 1 and isinstance(rules[0], AtRule) and rules[0].lower_at_keyword == "scope":
        rule = rules[0]
        if SCOPE.fullmatch(tinycss2.serialize(rule.prelude)) and rule.content is not None:
            return stylesheet(tinycss2.serialize(rule.content))
    for rule in rules:
        if isinstance(rule, QualifiedRule):
            css_values(rule.prelude)
            rule.prelude = root_selectors(rule.prelude)
            declarations(rule.content)
        elif isinstance(rule, AtRule) and rule.lower_at_keyword in {
            "media",
            "supports",
            "container",
        }:
            if rule.content is None:
                raise ValueError("Slide CSS condition needs a rule block.")
            css_values(rule.prelude)
            rule.content = tinycss2.parse_component_value_list(
                stylesheet(tinycss2.serialize(rule.content))
            )
        else:
            raise ValueError(
                "Use theme.css or interactive_html for global CSS rules such as "
                "@import, @font-face and @keyframes. Slide styles support selectors and conditions."
            )
    return tinycss2.serialize(rules)


class ScopedStyles(HTMLParser):
    """Scope each style block to only the slide roots containing that same stylesheet."""

    def __init__(self) -> None:
        """Retain original markup apart from generated scope attributes and style bodies."""
        super().__init__(convert_charrefs=False)
        self.parts: list[str | list[tuple[str, str | None]]] = []
        self.styles: list[str] = []
        self.css: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Collect style text and mark slide roots without affecting element selectors."""
        if tag == "style":
            self.css = []
        self.parts.append(
            attrs
            if "slide" in (dict(attrs).get("class") or "").split()
            else self.get_starttag_text() or ""
        )
        if isinstance(self.parts[-1], list):
            self.parts.insert(len(self.parts) - 1, "<" + tag)
            self.parts.append(">")

    def handle_endtag(self, tag: str) -> None:
        """Replace a stylesheet with an idempotent scope around balanced CSS."""
        if tag == "style" and self.css is not None:
            css = stylesheet("".join(self.css)).replace("<", r"\3c ")
            key = hashlib.sha256(css.encode()).hexdigest()[:16]
            self.styles.append(key)
            selector = f'.slide[data-slide-style~="{key}"]'
            self.parts.append(f"@scope ({selector}){{{css}}}")
            self.css = None
        self.parts.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Keep SVG and image self-closing markup intact."""
        self.parts.append(self.get_starttag_text() or "")

    def handle_data(self, data: str) -> None:
        """Keep text unchanged while collecting stylesheet tokens separately."""
        (self.css if self.css is not None else self.parts).append(data)

    def handle_entityref(self, name: str) -> None:
        """Retain encoded text rather than normalizing review target quotes."""
        self.handle_data(f"&{name};")

    def handle_charref(self, name: str) -> None:
        """Retain numeric text entities."""
        self.handle_data(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        """Retain ordinary comments, excluding comments embedded in CSS."""
        self.handle_data(f"<!--{data}-->")

    def result(self) -> str:
        """Attach scopes only to slide roots and serialize the original token stream."""
        if self.css is not None:
            raise ValueError("Close the slide's <style> block.")
        if self.styles and not any(isinstance(part, list) for part in self.parts):
            raise ValueError('Wrap styled slide content in <article class="slide">.')
        keys = " ".join(dict.fromkeys(self.styles))
        return "".join(
            "".join(
                " " + name + ("" if value is None else '="' + escape(value, quote=True) + '"')
                for name, value in part
                if name != "data-slide-style"
            )
            + f' data-slide-style="{keys}"'
            if isinstance(part, list)
            else part
            for part in self.parts
        )


def scope_styles(html: str) -> str:
    """Leave ordinary HTML byte-identical and scope only explicit style blocks."""
    if not re.search(r"<style\b", html, re.I):
        return html
    parser = ScopedStyles()
    parser.feed(html)
    parser.close()
    return parser.result()
