"""Compile an uploaded PowerPoint package into the editable native presentation format."""

import base64
import binascii
import hashlib
import io
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image

from .. import PROJECT_ROOT
from ..artifacts import ArtifactStore, legacy_project, write_legacy_project
from ..designs.authoring import write_starter
from ..models import Deck, Slide
from ..storage import ConflictError, SlideMarkup, Store, digest
from .graphics import graphic
from .package import A, P, Package, R
from .text import text_html

MAX_SOURCE_BYTES = 50_000_000


def local(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def first(nodes: Iterable[ET.Element], path: str) -> ET.Element | None:
    return next((child for node in nodes if (child := node.find(path)) is not None), None)


def check_children(node: ET.Element, allowed: set[str]) -> None:
    for child in node:
        if local(child) not in allowed:
            raise ValueError(f"{local(child)} is unsupported.")


def flag(node: ET.Element, attr: str, default: bool = False) -> bool:
    return node.get(attr, "1" if default else "0") in {"1", "true"}


def placeholder(shape: ET.Element) -> ET.Element | None:
    return shape.find(f"*/{P}nvPr/{P}ph")


def inherited(shape: ET.Element, tree: ET.Element, *, master: bool) -> ET.Element | None:
    ph = placeholder(shape)
    if ph is None:
        return None
    kind = ph.get("type", "obj")
    if master:
        kind = {"ctrTitle": "title", "obj": "body", "subTitle": "body"}.get(kind, kind)
    for candidate in tree:
        marker = placeholder(candidate)
        if marker is None:
            continue
        if master and marker.get("type", "obj") == kind:
            return candidate
        if not master and marker.get("idx", "0") == ph.get("idx", "0"):
            return candidate
    return None


def required(node: ET.Element, path: str) -> ET.Element:
    result = node.find(path)
    if result is None:
        raise ValueError(f"Missing {path.split('}')[-1]}.")
    return result


def pair(node: ET.Element, tag: str, x: str, y: str) -> tuple[int, int]:
    pair_node = required(node, A + tag)
    return int(pair_node.attrib[x]), int(pair_node.attrib[y])


@dataclass
class Canvas:
    x: int
    y: int
    width: int
    height: int
    text_width: int


@dataclass
class Painter:
    package: Package
    theme: ET.Element
    colors: dict[str, str]
    fonts: dict[str, str]
    defaults: list[ET.Element]
    master: ET.Element
    assets: dict[str, bytes]
    object_ids: set[str] = field(default_factory=set)
    titles: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)

    def color(self, prop: ET.Element, reference: str = "") -> str:
        if len(prop) != 1:
            raise ValueError("Unsupported fill color.")
        value = prop[0].get("val", "")
        if prop[0].tag == A + "schemeClr":
            value = reference if value == "phClr" else self.colors.get(value, "")
        elif prop[0].tag != A + "srgbClr":
            raise ValueError("Unsupported color space.")
        if not re.fullmatch("[0-9a-fA-F]{6}", value):
            raise ValueError("Unknown theme color.")
        children = list(prop[0])
        if children:
            if len(children) != 1 or children[0].tag != A + "alpha":
                raise ValueError("Unsupported color transformation.")
            opacity = int(children[0].attrib["val"])
            if not 0 <= opacity <= 100_000:
                raise ValueError("Invalid opacity.")
            value += f"{round(opacity * 255 / 100_000):02x}"
        return "#" + value

    def theme_style(self, ref: ET.Element, name: str) -> ET.Element | None:
        idx = int(ref.attrib["idx"])
        if not idx:
            return None
        styles = required(self.theme, f"{A}themeElements/{A}fmtScheme/{A}{name}")
        index = idx - (1001 if name == "bgFillStyleLst" else 1)
        if not 0 <= index < len(styles):
            raise ValueError("Invalid theme style reference.")
        return styles[index]

    def background(self, layers: list[ET.Element]) -> str:
        bg = first(layers, f"{P}cSld/{P}bg")
        if bg is None:
            return "#ffffff"
        direct = bg.find(P + "bgPr")
        if direct is not None:
            check_children(direct, {"solidFill", "effectLst"})
            if any(len(e) for e in direct.findall(A + "effectLst")):
                raise ValueError("Background effects are unsupported.")
            if flag(direct, "shadeToTitle"):
                raise ValueError("Background shading to the title is unsupported.")
            return self.color(required(direct, A + "solidFill"))
        ref = required(bg, P + "bgRef")
        fill = self.theme_style(
            ref, "bgFillStyleLst" if int(ref.attrib["idx"]) >= 1001 else "fillStyleLst"
        )
        if fill is None or fill.tag != A + "solidFill":
            raise ValueError("Non-solid theme backgrounds are unsupported.")
        return self.color(fill, self.color(ref)[1:])

    def picture(self, shape: ET.Element, part: str) -> str:
        fill = required(shape, P + "blipFill")
        check_children(fill, {"blip", "srcRect", "stretch"})
        blip = required(fill, A + "blip")
        check_children(blip, {"alphaModFix"})
        image = self.package.read(self.package.resolve(part, blip.attrib.get(R + "embed", "")))
        if blip.get(R + "link"):
            raise ValueError("Linked pictures are unsupported. Embed the picture.")
        with Image.open(io.BytesIO(image)) as im:
            if im.format not in {"PNG", "JPEG"} or im.width * im.height > 60_000_000:
                raise ValueError(
                    "Only static PNG or JPEG pictures up to 60 megapixels are supported."
                )
            if getattr(im, "n_frames", 1) != 1:
                raise ValueError("Animated pictures are unsupported.")
            fmt = str(im.format).lower()
            if "exif" in im.info and im.getexif().get(274, 1) != 1:
                raise ValueError("Pictures with EXIF orientation must be rotated before import.")
            if "exif" in im.info:
                with Image.open(io.BytesIO(image)) as original:
                    original.verify()
            else:
                im.verify()
        filename = hashlib.sha256(image).hexdigest() + "." + fmt
        self.assets[filename] = image
        stretch = required(fill, f"{A}stretch/{A}fillRect")
        if any(int(v) for v in stretch.attrib.values()):
            raise ValueError("Picture fill offsets are unsupported.")
        crop = fill.find(A + "srcRect")
        left, top, right, bottom = [
            int(crop.get(k, "0")) / 100_000 if crop is not None else 0 for k in ("l", "t", "r", "b")
        ]
        w, h = 1 - left - right, 1 - top - bottom
        if w <= 0 or h <= 0 or min(left, top, right, bottom) < 0:
            raise ValueError("Unsupported picture crop.")
        alpha = blip.find(A + "alphaModFix")
        opacity = int(alpha.get("amt", "100000")) / 100000 if alpha is not None else 1.0
        return (
            f'<img alt="" src="assets/{filename}" '
            'style="position:absolute;max-width:none;max-height:none;'
            f"width:{100 / w:.8g}%;height:{100 / h:.8g}%;"
            f"left:{-100 * left / w:.8g}%;top:{-100 * top / h:.8g}%;"
            f'opacity:{opacity:.8g}">'
        )

    def draw(
        self,
        shape: ET.Element,
        parents: list[ET.Element],
        part: str,
        layer: str,
        canvas: Canvas,
        depth: int = 0,
    ) -> str:
        if depth > 20:
            raise ValueError("Group nesting exceeds 20 levels.")
        nv = shape.find(f"*/{P}cNvPr")
        name = nv.get("name", "unnamed") if nv is not None else local(shape)
        try:
            if nv is None or shape.tag not in {
                P + "sp",
                P + "pic",
                P + "graphicFrame",
                P + "grpSp",
            }:
                raise ValueError(f"{local(shape)} is unsupported.")
            if flag(nv, "hidden"):
                raise ValueError("Hidden objects are unsupported.")
            sid = int(nv.attrib["id"])
            key = f"pptx-{layer}-{sid}"
            if key in self.object_ids:
                raise ValueError("Duplicate object ID.")
            self.object_ids.add(key)
            for e in shape.iter():
                if local(e) in {
                    "videoFile",
                    "audioFile",
                    "media",
                    "oleObj",
                    "hlinkClick",
                    "hlinkHover",
                    "custGeom",
                    "scene3d",
                    "sp3d",
                    "AlternateContent",
                    "contentPart",
                    "animEffect",
                    "effectDag",
                }:
                    raise ValueError(f"{local(e)} is unsupported.")
            title_marker = ""
            shapes = [shape, *parents]
            group = shape.tag == P + "grpSp"
            prop_tag = P + ("grpSpPr" if group else "spPr")
            props = [p for s in shapes if (p := s.find(prop_tag)) is not None]
            xf = (
                shape.find(P + "xfrm")
                if shape.tag == P + "graphicFrame"
                else first(props, A + "xfrm")
            )
            if xf is None:
                raise ValueError("Position is missing from the slide, layout and master.")
            x, y = pair(xf, "off", "x", "y")
            w, h = pair(xf, "ext", "cx", "cy")
            if min(w, h) <= 0:
                raise ValueError("Zero-size or line geometry is unsupported.")
            css = (
                "position:absolute;box-sizing:border-box;"
                f"left:{100 * (x - canvas.x) / canvas.width:.9g}%;"
                f"top:{100 * (y - canvas.y) / canvas.height:.9g}%;"
                f"width:{100 * w / canvas.width:.9g}%;height:{100 * h / canvas.height:.9g}%;"
            )
            if xf.get("rot") or flag(xf, "flipH") or flag(xf, "flipV"):
                angle = int(xf.get("rot", "0")) / 60000
                sx = -1 if flag(xf, "flipH") else 1
                sy = -1 if flag(xf, "flipV") else 1
                css += f"transform:rotate({angle:g}deg) scale({sx},{sy});"
            if group:
                cx, cy = pair(xf, "chOff", "x", "y")
                cw, ch = pair(xf, "chExt", "cx", "cy")
                if cw <= 0 or ch <= 0 or w * ch != h * cw:
                    raise ValueError("Non-uniform group scaling is unsupported.")
                for p in props:
                    check_children(p, {"xfrm"})
                child_canvas = Canvas(cx, cy, cw, ch, round(canvas.text_width * cw / w))
                body = "".join(
                    self.draw(s, [], part, layer, child_canvas, depth + 1)
                    for s in shape
                    if s.tag not in {P + "nvGrpSpPr", P + "grpSpPr"}
                )
            elif shape.tag == P + "graphicFrame":
                body = graphic(
                    shape,
                    part,
                    self.package.read,
                    self.package.resolve,
                    w,
                    h,
                    colors=self.colors,
                    fonts=self.fonts,
                )
            else:
                for p in props:
                    check_children(
                        p, {"xfrm", "prstGeom", "noFill", "solidFill", "ln", "effectLst"}
                    )
                    if any(len(e) for e in p.findall(A + "effectLst")):
                        raise ValueError("Shape effects are unsupported.")
                geom = first(props, A + "prstGeom")
                preset = geom.get("prst") if geom is not None else "rect"
                if preset not in {"rect", "ellipse"} or (
                    geom is not None and geom.find(f"{A}avLst/*") is not None
                ):
                    raise ValueError(f"Shape geometry {preset} is unsupported.")
                if preset == "ellipse":
                    css += "border-radius:50%;"
                prop_fill = next(
                    (c for p in props for c in p if local(c) in {"solidFill", "noFill"}), None
                )
                style = first(shapes, P + "style")
                fill_ref = style.find(A + "fillRef") if style is not None else None
                if prop_fill is not None:
                    if prop_fill.tag == A + "solidFill":
                        css += f"background-color:{self.color(prop_fill)};"
                elif fill_ref is not None:
                    theme_fill = self.theme_style(fill_ref, "fillStyleLst")
                    if theme_fill is not None:
                        if theme_fill.tag != A + "solidFill":
                            raise ValueError("Non-solid theme fill is unsupported.")
                        css += (
                            f"background-color:{self.color(theme_fill, self.color(fill_ref)[1:])};"
                        )
                line = first(props, A + "ln")
                line_ref = style.find(A + "lnRef") if style is not None else None
                if line is None and line_ref is not None:
                    line = self.theme_style(line_ref, "lnStyleLst")
                if line is not None and line.find(A + "noFill") is None:
                    check_children(line, {"solidFill", "prstDash", "miter", "round"})
                    dash = line.find(A + "prstDash")
                    if dash is not None and dash.get("val") != "solid":
                        raise ValueError("Dashed outlines are unsupported.")
                    line_color = self.color(
                        required(line, A + "solidFill"),
                        self.color(line_ref)[1:] if line_ref is not None and len(line_ref) else "",
                    )
                    thickness = int(line.get("w", "12700")) * 100 / canvas.text_width
                    css += f"outline:{thickness:g}cqw solid {line_color};"
                    css += f"outline-offset:{-thickness / 2:g}cqw;"
                effect = style.find(A + "effectRef") if style is not None else None
                if effect is not None:
                    effect_style = self.theme_style(effect, "effectStyleLst")
                    if effect_style is not None and any(len(e) for e in effect_style):
                        raise ValueError("Theme shape effects are unsupported.")
                if shape.tag == P + "pic":
                    css += "overflow:hidden;"
                    body = self.picture(shape, part)
                else:
                    ph = first(
                        [s for s in shapes if placeholder(s) is not None], f"*/{P}nvPr/{P}ph"
                    )
                    kind = next(
                        (
                            p.attrib["type"]
                            for s in shapes
                            if (p := placeholder(s)) is not None and "type" in p.attrib
                        ),
                        "obj",
                    )
                    title = kind in {"title", "ctrTitle"}
                    text_style = (
                        "titleStyle"
                        if title
                        else "bodyStyle"
                        if ph is not None and kind in {"obj", "body", "subTitle"}
                        else "otherStyle"
                    )
                    styles = self.master.findall(f"{P}txStyles/{P}{text_style}") + self.defaults
                    body = text_html(
                        shape, parents, styles, self.colors, self.fonts, canvas.text_width
                    )
                    text = " ".join(e.text or "" for e in shape.iter(A + "t")).strip()
                    if text and layer == "slide":
                        (self.titles if title else self.labels).append(text)
                        title_marker = f' data-pptx-title="{"title" if title else "fallback"}"'
            key = f"pptx-{layer}-{sid}"
            group_marker = ' data-pptx-group="true"' if group else ""
            return (
                f'<div id="{key}" data-element-id="{key}" '
                f'data-pptx-object="{escape(name, quote=True)}"{title_marker}{group_marker} '
                f'style="{css}">{body}</div>'
            )
        except (ValueError, KeyError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError(f"{name}: {exc}") from None


def compile_slides(pkg: Package) -> tuple[list[Slide], dict[str, bytes]]:
    pres = pkg.xml(pkg.presentation)
    size = required(pres, P + "sldSz")
    width, height = int(size.attrib["cx"]), int(size.attrib["cy"])
    if min(width, height) <= 0 or min(abs(width / height - r) for r in (4 / 3, 16 / 9)) > 0.0001:
        raise ValueError(
            "Only widescreen (16:9) and standard (4:3) slide dimensions are supported."
        )
    if pres.find(P + "embeddedFontLst") is not None:
        raise ValueError("Embedded fonts are unsupported. Install the fonts and remove embedding.")
    slide_ids = required(pres, P + "sldIdLst").findall(P + "sldId")
    if not 1 <= len(slide_ids) <= 100:
        raise ValueError("Import a presentation containing 1–100 slides.")
    slides: list[Slide] = []
    assets: dict[str, bytes] = {}
    failures: list[str] = []
    for i, slide_id in enumerate(slide_ids, 1):
        try:
            part = pkg.resolve(pkg.presentation, slide_id.attrib[R + "id"])
            layout_part = pkg.related(part, "slideLayout")
            master_part = pkg.related(layout_part, "slideMaster")
            layers = [pkg.xml(p) for p in (part, layout_part, master_part)]
            slide, layout, master = layers
            for layer in layers:
                timing = layer.find(P + "timing")
                if timing is not None:
                    targets = {item.get("spid") for item in timing.iter(P + "spTgt")}
                    names = [
                        item.get("name", "unnamed object")
                        for item in layer.iter(P + "cNvPr")
                        if item.get("id") in targets
                    ]
                    subject = ", ".join(names) or "slide timing"
                    raise ValueError(f"{subject}: animations are unsupported.")
                if layer.find(P + "transition") is not None:
                    raise ValueError("Slide transitions are unsupported.")
                if any(local(n) == "AlternateContent" for n in layer.iter()):
                    raise ValueError("AlternateContent is unsupported.")
            if any(
                kind.endswith("/themeOverride")
                for layer_part in (part, layout_part, master_part)
                for kind, _, _ in pkg.relationships(layer_part).values()
            ):
                raise ValueError("A custom theme override is unsupported.")
            theme = pkg.xml(pkg.related(master_part, "theme"))
            scheme = required(theme, f"{A}themeElements/{A}clrScheme")
            colors: dict[str, str] = {}
            for c in scheme:
                if len(c):
                    colors[local(c)] = c[0].get("lastClr", c[0].get("val", ""))
            master_map = master.find(P + "clrMap")
            mapping = master_map
            for layer in (layout, slide):
                override = layer.find(P + "clrMapOvr")
                if override is not None:
                    mapping = (
                        master_map
                        if override.find(A + "masterClrMapping") is not None
                        else required(override, A + "overrideClrMapping")
                    )
            if mapping is not None:
                colors.update({k: colors.get(v, "") for k, v in mapping.attrib.items()})
            fonts = {
                f"+{short}-lt": required(
                    theme, f"{A}themeElements/{A}fontScheme/{A}{label}Font/{A}latin"
                ).attrib["typeface"]
                for short, label in (("mj", "major"), ("mn", "minor"))
            }
            paint = Painter(
                pkg, theme, colors, fonts, pres.findall(P + "defaultTextStyle"), master, assets
            )
            trees = [required(layer, f"{P}cSld/{P}spTree") for layer in layers]
            objects: list[str] = []
            for source, tree, label in (
                (master_part, trees[2], "master"),
                (layout_part, trees[1], "layout"),
                (part, trees[0], "slide"),
            ):
                if label == "master" and not (
                    flag(slide, "showMasterSp", True) and flag(layout, "showMasterSp", True)
                ):
                    continue
                root_props = tree.find(P + "grpSpPr")
                if root_props is not None:
                    check_children(root_props, {"xfrm"})
                    xf = root_props.find(A + "xfrm")
                    if xf is not None and (
                        int(xf.get("rot", "0"))
                        or flag(xf, "flipH")
                        or flag(xf, "flipV")
                        or pair(xf, "off", "x", "y") != pair(xf, "chOff", "x", "y")
                        or pair(xf, "ext", "cx", "cy") != pair(xf, "chExt", "cx", "cy")
                    ):
                        raise ValueError(f"{label} canvas transform is unsupported.")
                for shape in tree:
                    if shape.tag in {P + "nvGrpSpPr", P + "grpSpPr"}:
                        continue
                    if label != "slide" and placeholder(shape) is not None:
                        continue
                    chain: list[ET.Element] = []
                    if label == "slide" and placeholder(shape) is not None:
                        match = inherited(shape, trees[1], master=False)
                        if match is not None:
                            chain.append(match)
                        match = inherited(chain[-1] if chain else shape, trees[2], master=True)
                        if match is not None:
                            chain.append(match)
                    objects.append(
                        paint.draw(shape, chain, source, label, Canvas(0, 0, width, height, width))
                    )
            background = paint.background(layers)
            markup = (
                f'<article class="slide" data-pptx-width="{width}" data-pptx-height="{height}" '
                f'style="position:relative;overflow:hidden;padding:0;background:{background}">'
                + "".join(objects)
                + "</article>"
            )
            SlideMarkup().feed(markup)
            notes = ""
            if any(rel[0].endswith("/notesSlide") for rel in pkg.relationships(part).values()):
                note_root = pkg.xml(pkg.related(part, "notesSlide"))
                notes = "\n".join(
                    "".join(p.itertext())
                    for s in note_root.iter(P + "sp")
                    if (ph := placeholder(s)) is not None and ph.get("type") == "body"
                    for p in s.findall(f"{P}txBody/{A}p")
                )
            titles = paint.titles or paint.labels or [f"Slide {i}"]
            slides.append(
                Slide(
                    id=f"slide-{i}",
                    title=titles[0][:160],
                    html=markup,
                    hidden=not flag(slide, "show", True),
                    notes=notes,
                )
            )
        except (ValueError, KeyError, IndexError, ET.ParseError, OSError) as exc:
            failures.append(f"Slide {i}, {exc}")
    if failures:
        raise ValueError("\n".join(failures))
    return slides, assets


def recorded_import(
    db: sqlite3.Connection, deck_id: str, folder: Path, project_id: str, payload: str
) -> Deck | None:
    """Replay the receipt only while the same creation is still in the selected project."""
    prior = db.execute(
        "SELECT payload_hash,marker,document FROM creation_receipts WHERE deck=?",
        (deck_id,),
    ).fetchone()
    if prior:
        identity = folder / ".artifact-studio-creation"
        if (
            prior[0] != payload
            or legacy_project(db, deck_id) != project_id
            or identity.is_symlink()
            or not identity.is_file()
            or identity.stat().st_size != 32
            or identity.read_bytes() != str(prior[1]).encode()
            or not (folder / "index.html").is_file()
        ):
            raise ConflictError("The PowerPoint import request conflicts with an earlier import.")
        return Deck.model_validate_json(prior[2])
    return None


def import_powerpoint(
    store: Store, project_id: str, title: str, data_base64: str, request_id: str = ""
) -> Deck:
    """Validate the whole package, then install a native deck in its selected project."""
    if not project_id or not title.strip() or len(title) > 160:
        raise ValueError("Choose a project and a presentation title of 1–160 characters.")
    if len(data_base64) > (MAX_SOURCE_BYTES + 2) // 3 * 4:
        raise ValueError("PowerPoint upload exceeds 50 MB.")
    if request_id and not 8 <= len(request_id) <= 200:
        raise ValueError("Use a request ID of 8–200 characters.")
    try:
        source = base64.b64decode(data_base64, validate=True)
    except binascii.Error as exc:
        raise ValueError("Invalid PowerPoint file encoding.") from exc
    if len(source) > MAX_SOURCE_BYTES:
        raise ValueError("PowerPoint upload exceeds 50 MB.")
    token = request_id or uuid.uuid4().hex
    deck_id = "pptx-" + digest(project_id + "\0" + token)[:32]
    folder = store.folder(deck_id)
    payload = digest(title + "\0" + hashlib.sha256(source).hexdigest())
    _ = ArtifactStore(store)
    if request_id:
        with store.connect() as db:
            if previous := recorded_import(db, deck_id, folder, project_id, payload):
                return previous
    try:
        package = Package(source)
        with package.archive:
            slides, assets = compile_slides(package)
    except (
        zipfile.BadZipFile,
        ET.ParseError,
        KeyError,
        RuntimeError,
        NotImplementedError,
        OverflowError,
        RecursionError,
    ) as exc:
        raise ValueError(
            "Invalid or unsupported PowerPoint package. Re-save as .pptx and try again."
        ) from exc
    deck = Deck(id=deck_id, title=title.strip(), slides=slides)
    with tempfile.TemporaryDirectory(dir=store.root, prefix=".pptx-") as temp:
        stage = Path(temp) / "deck"
        stage.mkdir()
        write_starter(
            stage,
            deck,
            (PROJECT_ROOT / "runtime/viewer.css").read_text()
            + "\n.slide{box-sizing:border-box}.slide *{box-sizing:border-box}",
        )
        for filename, data in assets.items():
            _ = (stage / "assets" / filename).write_bytes(data)
        _ = (stage / "README.md").write_text("Imported editable PowerPoint presentation.\n")
        marker = uuid.uuid4().hex
        _ = (stage / ".artifact-studio-creation").write_text(marker)
        with store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            if previous := recorded_import(db, deck_id, folder, project_id, payload):
                return previous
            if folder.exists():
                raise ConflictError("Presentation ID already exists.")
            write_legacy_project(db, deck_id, project_id)
            _ = stage.rename(folder)
            try:
                store.seed_slide_views(db, deck)
                _ = db.execute(
                    "INSERT INTO creation_receipts VALUES (?,?,?,?,?)",
                    (deck_id, token, payload, deck.model_dump_json(), marker),
                )
                db.commit()
            except BaseException:
                shutil.rmtree(folder)
                raise
    return deck
