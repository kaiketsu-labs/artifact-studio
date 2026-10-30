"""Opt-in browser regression for SVG elements without HTML layout dimensions."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


@pytest.mark.skipif(
    os.environ.get("SLIDE_STUDIO_RENDER_TESTS") != "1", reason="Opt-in local Chromium integration"
)
def test_svg_rotation_pivot_matches_real_dom() -> None:
    """Keep nested SVG rotation correct through viewBox, custom origin, scale and rotation."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content("""
<div style="position:absolute;left:100px;top:80px;
            transform:rotate(25deg) scale(1.4,.65)">
  <svg id="outer" width="200" height="100" viewBox="20 10 400 200"
       style="transform:rotate(30deg);transform-origin:25% 75%">
    <rect x="40" y="30" width="50" height="20"/>
  </svg>
  <svg width="100" height="100">
    <svg id="inner" x="10" y="20" width="80" height="60" viewBox="0 0 400 300"
         style="transform:rotate(30deg);transform-origin:25% 75%">
      <rect x="0" y="0" width="200" height="100"/>
    </svg>
  </svg>
</div>""")
            page.add_script_tag(
                content=(Path(__file__).resolve().parents[1] / "web/slide-edits.js").read_text()
            )
            page.evaluate("""() => {
  for (const id of ['outer', 'inner']) {
    const element=document.getElementById(id), transform=getComputedStyle(element).transform;
    const probe=(x,y,rotation=0)=>{
      element.style.transform=`translate(${x}px,${y}px) ${transform} rotate(${rotation}deg)`;
      return element.getBoundingClientRect();
    };
    const rect=probe(0,0), x=probe(10,0), y=probe(0,10);
    const basis={xx:(x.left-rect.left)/10, yx:(x.top-rect.top)/10,
                 xy:(y.left-rect.left)/10, yy:(y.top-rect.top)/10};
    const opposite=probe(0,0,180);
    const geometry=SlideEdits.rotationGeometry(
      rect,opposite,basis,new DOMMatrixReadOnly(transform));
    const local=SlideEdits.localDelta(rect.left+rect.width/2-geometry.center.x,
                                    rect.top+rect.height/2-geometry.center.y,geometry.basis);
    const predicted={x:geometry.center.x-geometry.basis.xx*local.y+geometry.basis.xy*local.x,
                     y:geometry.center.y-geometry.basis.yx*local.y+geometry.basis.yy*local.x};
    const quarter=probe(0,0,90);
    if (Math.hypot(predicted.x-quarter.left-quarter.width/2,
                   predicted.y-quarter.top-quarter.height/2)>.01) {
      throw new Error(`${id}: rotation pivot does not predict the real rotated SVG`);
    }
    if (id==='inner' && (element.clientWidth!==0 || element.clientHeight!==0)) {
      throw new Error('Nested SVG fixture must reproduce missing layout dimensions');
    }
    element.style.transform=transform;
  }
}""")
        finally:
            browser.close()
