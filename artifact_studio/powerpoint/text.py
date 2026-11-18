"""Translate the supported DrawingML text subset into editable slide HTML."""

from __future__ import annotations

import ctypes
import ctypes.util
import html
import re
import subprocess
import sys
from functools import lru_cache
from xml.etree import ElementTree as ET

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"


@lru_cache(maxsize=1)
def _font_families() -> set[str]:
    if sys.platform == "darwin":
        return _mac_font_families()
    try:
        result = subprocess.run(
            ["fc-list", "--format", "%{family}\n"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("Cannot verify available PowerPoint fonts") from exc
    return {
        name.strip().casefold() for line in result.stdout.splitlines() for name in line.split(",")
    }


def _mac_font_families() -> set[str]:
    """Read actual family names from CoreText without requiring fontconfig."""
    try:
        core_text_path = ctypes.util.find_library("CoreText")
        core_foundation_path = ctypes.util.find_library("CoreFoundation")
        if not core_text_path or not core_foundation_path:
            raise OSError("CoreText is unavailable")
        ct = ctypes.CDLL(core_text_path)
        cf = ctypes.CDLL(core_foundation_path)
        ct.CTFontManagerCopyAvailableFontFamilyNames.restype = ctypes.c_void_p
        cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        cf.CFArrayGetCount.restype = ctypes.c_long
        cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
        cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
        cf.CFStringGetLength.argtypes = [ctypes.c_void_p]
        cf.CFStringGetLength.restype = ctypes.c_long
        cf.CFStringGetMaximumSizeForEncoding.argtypes = [ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetMaximumSizeForEncoding.restype = ctypes.c_long
        cf.CFStringGetCString.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_long,
            ctypes.c_uint32,
        ]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        array = ct.CTFontManagerCopyAvailableFontFamilyNames()
        if not array:
            raise OSError("CoreText returned no fonts")
        try:
            families: set[str] = set()
            for index in range(cf.CFArrayGetCount(array)):
                name = cf.CFArrayGetValueAtIndex(array, index)
                size = (
                    cf.CFStringGetMaximumSizeForEncoding(cf.CFStringGetLength(name), 0x08000100) + 1
                )
                buffer = ctypes.create_string_buffer(size)
                if not cf.CFStringGetCString(name, buffer, size, 0x08000100):
                    raise OSError("Cannot decode CoreText font name")
                families.add(buffer.value.decode("utf-8").casefold())
            return families
        finally:
            cf.CFRelease(array)
    except (OSError, AttributeError, UnicodeError) as exc:
        raise ValueError("Cannot verify available PowerPoint fonts") from exc


def font(value: str, fonts: dict[str, str]) -> str:
    name = fonts.get(value, value)
    if not name or name.startswith("+") or name.casefold() not in _font_families():
        raise ValueError(f"PowerPoint font is unavailable: {name or value}")
    # CSS string escaping, then HTML escaping is applied to the style attribute.
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def color(fill: ET.Element, colors: dict[str, str]) -> str:
    children = list(fill)
    if len(children) != 1:
        raise ValueError("Unsupported text fill")
    color = children[0]
    if list(color):
        raise ValueError("Unsupported text color transform")
    if color.tag == A + "srgbClr":
        value = color.get("val", "")
    elif color.tag == A + "schemeClr":
        value = colors.get(color.get("val", ""), "")
    else:
        raise ValueError("Unsupported text color")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        raise ValueError("Invalid or unresolved PowerPoint text color")
    return "#" + value


def _style_attr(styles: list[str]) -> str:
    return html.escape(";".join(styles), quote=True)


def _first_attr(nodes: list[ET.Element], attr: str) -> str | None:
    return next((node.get(attr) for node in nodes if node.get(attr) is not None), None)


def _first_child(nodes: list[ET.Element], tag: str) -> ET.Element | None:
    return next((child for node in nodes if (child := node.find(A + tag)) is not None), None)


def _first_choice(nodes: list[ET.Element], tags: set[str]) -> ET.Element | None:
    return next(
        (child for node in nodes for child in node if child.tag in {A + tag for tag in tags}), None
    )


def _number(value: str, context: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {context}") from exc


def text_html(
    shape: ET.Element,
    inherited_shapes: list[ET.Element],
    defaults: list[ET.Element],
    colors: dict[str, str],
    fonts: dict[str, str],
    slide_width: int,
) -> str:
    """Render a shape's text as editable HTML, rejecting unsupported text features."""
    if slide_width <= 0:
        raise ValueError("Invalid slide width")
    body = shape.find(P + "txBody")
    if body is None:
        return ""

    def length(emu: int) -> str:
        return f"{emu * 100 / slide_width:.8g}cqw"

    def points(hundredths: int) -> str:
        return length(hundredths * 127)

    bodies = [body]
    bodies.extend(b for s in inherited_shapes if (b := s.find(P + "txBody")) is not None)
    body_props = [b.find(A + "bodyPr") for b in bodies]
    body_props = [b for b in body_props if b is not None]
    if (_first_attr(body_props, "vert") or "horz") != "horz" or (
        _first_attr(body_props, "rot") or "0"
    ) != "0":
        raise ValueError("Unsupported text orientation")
    fit = _first_choice(body_props, {"noAutofit", "normAutofit", "spAutoFit"})
    if fit is not None and fit.tag != A + "noAutofit":
        raise ValueError("Unsupported text autofit")
    if _first_choice(body_props, {"scene3d", "sp3d"}) is not None:
        raise ValueError("Unsupported text 3D")
    if (_first_attr(body_props, "numCol") or "1") != "1":
        raise ValueError("Multiple text columns are unsupported")
    if (_first_attr(body_props, "rtlCol") or "0") in {"1", "true"}:
        raise ValueError("Right-to-left text columns are unsupported")
    if _first_child(body_props, "prstTxWarp") is not None:
        raise ValueError("WordArt text warping is unsupported")
    wrap = _first_attr(body_props, "wrap") or "square"
    if wrap not in {"square", "none"}:
        raise ValueError("Unsupported text wrapping")
    anchor = _first_attr(body_props, "anchor") or "t"
    if anchor not in {"t", "ctr", "b"}:
        raise ValueError("Unsupported vertical text alignment")
    outer = [
        "box-sizing:border-box",
        "width:100%",
        "height:100%",
        "display:flex",
        "flex-direction:column",
        f"justify-content:{ {'t': 'flex-start', 'ctr': 'center', 'b': 'flex-end'}[anchor] }",
        "overflow:hidden",
        "white-space:pre-wrap" if wrap == "square" else "white-space:pre",
    ]
    for attr, css, default in (
        ("lIns", "left", 91440),
        ("rIns", "right", 91440),
        ("tIns", "top", 45720),
        ("bIns", "bottom", 45720),
    ):
        outer.append(
            f"padding-{css}:{length(_number(_first_attr(body_props, attr) or str(default), attr))}"
        )

    paragraphs: list[str] = []
    for para in body.findall(A + "p"):
        ppr = para.find(A + "pPr")
        level = _number(ppr.get("lvl", "0"), "paragraph level") if ppr is not None else 0
        if not 0 <= level <= 8:
            raise ValueError("Unsupported paragraph level")
        pnodes = [ppr] if ppr is not None else []
        for b in bodies:
            lst = b.find(A + "lstStyle")
            if lst is not None and (node := lst.find(A + f"lvl{level + 1}pPr")) is not None:
                pnodes.append(node)
        for default in defaults:
            node = default.find(A + f"lvl{level + 1}pPr")
            if node is not None:
                pnodes.append(node)
        bullet = _first_choice(pnodes, {"buNone", "buChar", "buAutoNum", "buBlip"})
        if bullet is not None and bullet.tag != A + "buNone":
            raise ValueError("Unsupported paragraph bullets")
        if _first_child(pnodes, "tabLst") is not None:
            raise ValueError("Unsupported paragraph tabs")
        if _first_attr(pnodes, "rtl") in {"1", "true"}:
            raise ValueError("Right-to-left paragraph layout is unsupported")
        align = _first_attr(pnodes, "algn") or "l"
        if align not in {"l", "ctr", "r", "just"}:
            raise ValueError("Unsupported paragraph alignment")
        alignment = {"l": "left", "ctr": "center", "r": "right", "just": "justify"}[align]
        ps = ["margin:0", f"text-align:{alignment}"]
        for attr, css in (
            ("marL", "margin-left"),
            ("marR", "margin-right"),
            ("indent", "text-indent"),
        ):
            value = _first_attr(pnodes, attr)
            if value is not None:
                ps.append(f"{css}:{length(_number(value, attr))}")
        p_run_defaults = [n for pnode in pnodes if (n := pnode.find(A + "defRPr")) is not None]
        for tag, css in (
            ("spcBef", "margin-top"),
            ("spcAft", "margin-bottom"),
            ("lnSpc", "line-height"),
        ):
            spacing = _first_child(pnodes, tag)
            if spacing is not None:
                if len(spacing) != 1:
                    raise ValueError("Unsupported paragraph spacing")
                child = spacing[0]
                value = _number(child.get("val", ""), tag)
                if child.tag == A + "spcPts":
                    ps.append(f"{css}:{points(value)}")
                elif child.tag == A + "spcPct" and tag == "lnSpc":
                    ps.append(f"{css}:{value / 1000:g}%")
                elif child.tag == A + "spcPct":
                    first_run = para.find(f"{A}r/{A}rPr")
                    spacing_nodes = ([first_run] if first_run is not None else []) + p_run_defaults
                    paragraph_size = _number(
                        _first_attr(spacing_nodes, "sz") or "1800", "font size"
                    )
                    ps.append(f"{css}:{points(round(paragraph_size * value / 100000))}")
                else:
                    raise ValueError("Unsupported paragraph spacing")
        spans: list[str] = []
        for item in para:
            if item.tag == A + "pPr" or item.tag == A + "endParaRPr":
                continue
            if item.tag == A + "br":
                spans.append("<br>")
                continue
            if item.tag != A + "r":
                raise ValueError("Unsupported PowerPoint text content")
            rp = item.find(A + "rPr")
            rnodes = ([rp] if rp is not None else []) + p_run_defaults
            for choices in (
                {"solidFill", "gradFill", "pattFill", "blipFill", "noFill", "grpFill"},
                {"effectLst", "effectDag"},
                {"uFill", "uFillTx"},
                {"uLn", "uLnTx"},
            ):
                selected = _first_choice(rnodes, choices)
                if selected is not None and selected.tag not in {
                    A + "solidFill",
                    A + "uFillTx",
                    A + "uLnTx",
                }:
                    raise ValueError("Unsupported PowerPoint text styling")
            for tag in ("hlinkClick", "hlinkMouseOver", "highlight", "ln"):
                if _first_child(rnodes, tag) is not None:
                    raise ValueError("Unsupported PowerPoint text styling")
            for attr in ("baseline", "strike", "cap", "spc"):
                if _first_attr(rnodes, attr) not in (None, "0", "none"):
                    raise ValueError("Unsupported PowerPoint text styling")
            font_node = _first_child(rnodes, "latin")
            font_name = font_node.get("typeface", "") if font_node is not None else "+mn-lt"
            rs = [f"font-family:{font(font_name, fonts)}"]
            size = _first_attr(rnodes, "sz") or "1800"
            font_size = _number(size, "font size")
            rs.append(f"font-size:{points(font_size)}")
            kern = _first_attr(rnodes, "kern")
            if kern is not None:
                threshold = _number(kern, "kerning threshold")
                rs.append(f"font-kerning:{'normal' if font_size >= threshold else 'none'}")
            for attr, css, on, off in (
                ("b", "font-weight", "bold", "normal"),
                ("i", "font-style", "italic", "normal"),
            ):
                value = _first_attr(rnodes, attr)
                if value is not None:
                    if value not in {"0", "1", "true", "false"}:
                        raise ValueError(f"Invalid {attr}")
                    rs.append(f"{css}:{on if value in {'1', 'true'} else off}")
            underline = _first_attr(rnodes, "u")
            if underline is not None:
                if underline not in {"none", "sng"}:
                    raise ValueError("Unsupported underline")
                rs.append(f"text-decoration:{'underline' if underline == 'sng' else 'none'}")
            fill = _first_child(rnodes, "solidFill")
            if fill is not None:
                rs.append(f"color:{color(fill, colors)}")
            text = item.findtext(A + "t", default="")
            spans.append(f'<span style="{_style_attr(rs)}">{html.escape(text)}</span>')
        paragraphs.append(f'<div style="{_style_attr(ps)}">{"".join(spans) or "<br>"}</div>')
    return f'<div style="{_style_attr(outer)}">{"".join(paragraphs)}</div>'
