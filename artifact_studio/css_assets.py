"""Rewrite CSS resources from parsed tokens so preview and offline output agree."""

from collections.abc import Callable
from typing import cast

import tinycss2
from tinycss2.ast import (
    AtKeywordToken,
    CurlyBracketsBlock,
    FunctionBlock,
    IdentToken,
    LiteralToken,
    Node,
    ParenthesesBlock,
    SquareBracketsBlock,
    StringToken,
    URLToken,
)
from tinycss2.serializer import serialize_string_value


def embed_css(source: str, asset: Callable[[str], str], imported: Callable[[str], str]) -> str:
    """Embed imports, escaped/quoted URLs and image-set strings without regex parsing."""

    def string(value: str) -> str:
        return '"' + serialize_string_value(value) + '"'

    def url_value(node: Node) -> str:
        if isinstance(node, (URLToken, StringToken)):
            return node.value
        if isinstance(node, FunctionBlock) and node.lower_name in {"url", "src"}:
            values = [part for part in node.arguments if part.type not in {"whitespace", "comment"}]
            if len(values) == 1 and isinstance(values[0], StringToken):
                return values[0].value
        raise ValueError("Use a valid CSS asset URL or quoted stylesheet import.")

    def import_conditions(content: str, tokens: list[Node]) -> str:
        """Retain import layer/supports semantics, followed by the optional media query."""
        qualifiers = [node for node in tokens if node.type not in {"whitespace", "comment"}]
        layer: str | None = None
        supports = ""
        if (
            qualifiers
            and isinstance(qualifiers[0], IdentToken)
            and qualifiers[0].lower_value == "layer"
        ):
            layer = ""
            qualifiers.pop(0)
        elif (
            qualifiers
            and isinstance(qualifiers[0], FunctionBlock)
            and qualifiers[0].lower_name == "layer"
        ):
            layer = tinycss2.serialize(cast(FunctionBlock, qualifiers.pop(0)).arguments)
        if (
            qualifiers
            and isinstance(qualifiers[0], FunctionBlock)
            and qualifiers[0].lower_name == "supports"
        ):
            supports = tinycss2.serialize(cast(FunctionBlock, qualifiers.pop(0)).arguments).strip()
            # A declaration inside supports() needs parentheses in an @supports condition.
            if any(
                isinstance(node, LiteralToken) and node.value == ":"
                for node in tinycss2.parse_component_value_list(supports)
            ):
                supports = "(" + supports + ")"
        media = " ".join(node.serialize() for node in qualifiers)
        if media:
            content = f"@media {media}{{{content}}}"
        if supports:
            content = f"@supports {supports}{{{content}}}"
        if layer is not None:
            content = f"@layer {layer}{{{content}}}"
        return content

    def rewrite(tokens: list[Node], depth: int = 0) -> str:
        if depth > 64:
            raise ValueError("CSS nesting exceeds the portable document limit.")
        result: list[str] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if isinstance(token, AtKeywordToken) and token.lower_value == "import":
                index += 1
                while index < len(tokens) and tokens[index].type in {"whitespace", "comment"}:
                    index += 1
                if index == len(tokens):
                    raise ValueError("A CSS import needs a local stylesheet URL.")
                content = imported(url_value(tokens[index]))
                index += 1
                media: list[Node] = []
                while index < len(tokens):
                    end = tokens[index]
                    index += 1
                    if isinstance(end, LiteralToken) and end.value == ";":
                        break
                    media.append(end)
                result.append(import_conditions(content, media))
                continue
            if isinstance(token, URLToken):
                result.append("url(" + string(asset(token.value)) + ")")
            elif isinstance(token, FunctionBlock):
                if token.lower_name in {"url", "src"}:
                    result.append("url(" + string(asset(url_value(token))) + ")")
                elif token.lower_name in {"image", "image-set", "-webkit-image-set"}:
                    arguments = "".join(
                        string(asset(argument.value))
                        if isinstance(argument, StringToken)
                        else rewrite([argument], depth + 1)
                        for argument in token.arguments
                    )
                    result.append(token.name + "(" + arguments + ")")
                else:
                    result.append(token.name + "(" + rewrite(token.arguments, depth + 1) + ")")
            elif isinstance(token, (ParenthesesBlock, SquareBracketsBlock, CurlyBracketsBlock)):
                brackets = {"() block": ("(", ")"), "[] block": ("[", "]"), "{} block": ("{", "}")}
                opening, closing = brackets[token.type]
                result.append(opening + rewrite(token.content, depth + 1) + closing)
            else:
                if token.type == "error":
                    raise ValueError("Invalid CSS cannot be embedded into an offline document.")
                result.append(token.serialize())
            index += 1
        return "".join(result)

    return rewrite(tinycss2.parse_component_value_list(source))
