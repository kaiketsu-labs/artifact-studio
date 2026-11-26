"""Editable layout extraction through the actual slide capture document."""

import json
import os
from io import BytesIO
from pathlib import Path
from typing import cast
from zipfile import ZipFile

import pytest
from PIL import Image

from artifact_studio.models import Slide
from artifact_studio.powerpoint.dom_export import extract_dom_slides
from artifact_studio.powerpoint.download import powerpoint_bytes
from artifact_studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design

DECK = "2026-11-19-editable-layout"
RENDER = pytest.mark.skipif(
    os.environ.get("ARTIFACT_STUDIO_RENDER_TESTS") != "1", reason="Opt-in local Chromium rendering"
)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    root = tmp_path / "collection"
    make_design(root / TEMPLATE_ID)
    result = Store(root, tmp_path / "review.sqlite3")
    _ = result.create(DECK, "Fictional editable layout")
    assets = result.folder(DECK) / "assets"
    with Image.new("RGB", (200, 100), color="darkblue") as image:
        image.save(assets / "landscape.png")
    return result


def extract(store: Store, html: str) -> dict[str, object]:
    slide = Slide(id="export-slide", title="Editable layout", html=html)
    return extract_dom_slides(store, DECK, [slide], slide_numbers=[4])[0]


@pytest.mark.browser
@RENDER
def test_extract_native_layout(store: Store) -> None:
    """Keep editable rich lines, geometry, crop, table, chart, group and paint order."""
    layout = extract(
        store,
        """
        <article class="slide" style="background:linear-gradient(45deg,#fff,#eef 80%)">
          <div data-element-id="front" style="position:absolute;z-index:2;left:75px;top:70px;
            width:220px;height:80px;font:22px Arial">
            A long <em>editable phrase</em> wraps across this text box.
          </div>
          <div data-pptx-export-group="true" style="position:absolute;top:190px;left:75px">
            <div style="width:150px;height:40px;border-radius:20px;background:red"></div>
            <div style="width:150px;height:30px;border-left:3px solid blue;">Native group</div>
          </div>
          <img src="assets/landscape.png" alt="Landscape" style="position:absolute;top:190px;
            left:400px;width:100px;height:100px;object-fit:cover;object-position:75% 50%">
          <table style="position:absolute;left:75px;top:340px;border-collapse:collapse">
            <tr><td style="padding:8px;border-bottom:2px solid black">North</td><td>12</td></tr>
            <tr><td style="padding:8px">South</td><td>21</td></tr>
          </table>
          <svg style="position:absolute;left:75px;top:465px" width="180" height="90">
            <g><rect x="2" y="2" width="30" height="22" fill="blue"/>
              <path d="M40 20 q20 -30 40 0 t40 0 L120 55 Z" fill="green"/>
            </g>
          </svg>
          <div style="position:absolute;left:550px;top:340px;width:350px;height:200px"
            data-pptx-export-chart='{"chartType":"line","categories":["Q1","Q2"],
              "series":[{"name":"Revenue","values":[12,21]}]}'></div>
        </article>
        """.strip(),
    )
    assert (layout["width"], layout["height"]) == (1600, 900)
    assert layout["gradient"] == {
        "angle": 45,
        "stops": [
            {"color": "rgb(255,255,255)", "position": 0},
            {"color": "rgb(238,238,255)", "position": 0.8},
        ],
    }
    elements = cast(list[dict[str, object]], layout["elements"])
    assert [item["type"] for item in elements] == [
        "group",
        "image",
        "table",
        "group",
        "chart",
        "text",
    ]
    group, picture, table, vectors, chart, text = elements
    assert '"shape": "roundRect"' in json.dumps(group)
    assert str(picture["data"]).startswith("data:image/png;base64,")
    assert picture["crop"] == {"left": 0.375, "right": 0.125, "top": 0, "bottom": 0}
    rows = cast(list[list[dict[str, object]]], table["rows"])
    assert cast(dict[str, object], rows[0][0]["borders"])["bottom"] == {
        "stroke": "rgb(0,0,0)",
        "strokeWidth": 2,
    }
    paths = cast(list[dict[str, object]], vectors["elements"])
    assert paths[1]["shape"] == "freeform"
    assert cast(list[dict[str, object]], paths[1]["path"])[1]["x1"] == pytest.approx(128.3333)
    assert chart["chartType"] == "line"
    assert chart["series"] == [{"name": "Revenue", "values": [12, 21]}]
    paragraphs = cast(list[dict[str, object]], text["paragraphs"])
    runs = [
        run for paragraph in paragraphs for run in cast(list[dict[str, object]], paragraph["runs"])
    ]
    assert len(paragraphs) > 1
    assert any(run["italic"] and "editable" in str(run["text"]) for run in runs)


@pytest.mark.browser
@RENDER
def test_unsupported_element_names_source_slide_and_object(store: Store) -> None:
    """Fail for visible content the native writer cannot represent."""
    with pytest.raises(ValueError, match=r"Slide 4 .*hero uses a CSS mask"):
        _ = extract(
            store,
            '<article class="slide"><div data-element-id="hero" '
            'style="width:40px;height:30px;mask-image:linear-gradient(black,transparent);'
            'background:blue"></div></article>',
        )


def test_interactive_slide_names_source_before_opening_browser(store: Store) -> None:
    """Prevent interactive content from reaching the native renderer."""
    slide = Slide(
        id="interactive",
        title="Interactive journey",
        html='<article class="slide"></article>',
        interactive_html="<button>Play</button>",
    )
    with pytest.raises(ValueError, match=r"Slide 9 .*uses an interactive document"):
        _ = extract_dom_slides(store, DECK, [slide], slide_numbers=[9])


@pytest.mark.browser
@RENDER
def test_default_studio_slide_writes_native_powerpoint_text(tmp_path: Path) -> None:
    """Write an unmodified authored title slide with the standard Studio theme."""
    store = Store(tmp_path / "empty-collection", tmp_path / "review.sqlite3")
    _ = store.create(DECK, "Quarterly update", scoped=True)
    with ZipFile(BytesIO(powerpoint_bytes(store, DECK))) as package:
        markup = package.read("ppt/slides/slide1.xml")
        assert b"<a:t>Quarterly update</a:t>" in markup
        assert b"<p:pic>" not in markup


@pytest.mark.browser
@RENDER
def test_rejects_opacity_that_composites_overlapping_objects(store: Store) -> None:
    """Group alpha cannot be reproduced by lowering alpha on individual shapes."""
    with pytest.raises(
        ValueError, match=r"Slide 4 .*badge uses group opacity across overlapping objects"
    ):
        _ = extract(
            store,
            '<article class="slide"><div data-element-id="badge" style="margin:40px;'
            'width:100px;height:40px;background:blue;opacity:0.5">'
            "<strong>Label</strong></div></article>",
        )


@pytest.mark.browser
@RENDER
@pytest.mark.parametrize(
    ("attributes", "feature"),
    [
        ('data-build="1"', "a Studio reveal step"),
        ("data-auto-reveal", "a Studio reveal step"),
        ('data-animate-grow="x"', "a Studio growth animation"),
        ('data-animate-grow="x-reverse"', "a Studio growth animation"),
        ('data-count-to="42"', "a Studio counter animation"),
    ],
)
def test_studio_motion_cannot_silently_become_static(
    store: Store, attributes: str, feature: str
) -> None:
    with pytest.raises(ValueError, match=rf"Slide 4 .*quantity uses {feature}"):
        _ = extract(
            store,
            f'<article class="slide"><p data-element-id="quantity" {attributes}>42</p></article>',
        )


@pytest.mark.browser
@RENDER
@pytest.mark.parametrize("timing", ["1s", "1ms -2s"])
def test_css_animation_detected_even_if_suppressed_for_reduced_motion(
    store: Store, timing: str
) -> None:
    theme = store.folder(DECK) / "theme.css"
    _ = theme.write_text(
        theme.read_text() + "\n@keyframes slide-in { from { transform: translateX(-100px) } }"
    )
    with pytest.raises(ValueError, match=r"Slide 4 .*quantity uses an? .*CSS .*animation"):
        _ = extract(
            store,
            f"""
            <article class="slide">
              <style>
                @media (prefers-reduced-motion: no-preference) {{
                  #quantity {{ animation: slide-in {timing} }}
                }}
              </style>
              <p id="quantity">42</p>
            </article>
            """.strip(),
        )


@pytest.mark.browser
@RENDER
@pytest.mark.parametrize(
    ("limit", "maximum", "feature"),
    [
        ("MAX_LAYOUT_OBJECTS", 1, "object budget"),
        ("MAX_LAYOUT_BYTES", 650, "data budget"),
    ],
)
def test_native_layout_budgets_apply_across_slides(
    store: Store, monkeypatch: pytest.MonkeyPatch, limit: str, maximum: int, feature: str
) -> None:
    from artifact_studio.powerpoint import dom_export

    monkeypatch.setattr(dom_export, limit, maximum)
    html = '<article class="slide"><p>Editable PowerPoint layout</p></article>'
    slides = [
        Slide(id="first", title="First", html=html),
        Slide(id="second", title="Second", html=html),
    ]
    assert len(extract_dom_slides(store, DECK, slides[:1])) == 1
    with pytest.raises(ValueError, match=rf'Slide 12 \("Second"\).*{feature}'):
        _ = extract_dom_slides(store, DECK, slides, slide_numbers=[10, 12])
