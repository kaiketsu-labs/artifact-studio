"""Exercise the shared property inspector in a real browser."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_inspector_emits_changed_values_and_cancel(page: Page) -> None:
    """Keep controls compact, accessible and limited to changed properties."""
    _ = page.goto("about:blank")
    page.set_content('<div id="inspector"></div>')
    page.add_script_tag(path=str(Path(__file__).resolve().parents[2] / "web/element-properties.js"))
    page.add_script_tag(path=str(Path(__file__).resolve().parents[2] / "web/artifact-editor.js"))
    page.evaluate("""() => {
      window.patches = []; window.sent = 0; window.cancelled = 0;
      window.inspector = ElementProperties.create({container: document.querySelector('#inspector'),
        onPreview: patch => window.patches.push(patch), onSend: () => window.sent++,
        onCancel: () => window.cancelled++});
      window.inspector.setSelection({label:'text',text:'Hello',canText:true,typography:{
        font_size:2,font_size_unit:'cqw',font_weight:'400',font_style:'normal',
        text_align:'left',color:'#123456'}});
    }""")
    expect(page.get_by_label("Font size unit")).to_have_value("cqw")
    page.get_by_label("Text", exact=True).fill("Changed")
    page.get_by_label("Increase font size").click()
    assert page.evaluate("window.patches.at(-1)") == {"font_size": 3, "font_size_unit": "cqw"}
    assert page.evaluate("ArtifactEditor.validPatch(window.patches.at(-1))") is True
    page.get_by_label("Decrease font size").click()
    assert page.evaluate("window.patches.at(-1)") == {"font_size": 2, "font_size_unit": "cqw"}
    page.get_by_label("Text", exact=True).fill("Hello")
    assert page.evaluate("window.patches.at(-1)") == {"text": "Hello"}
    page.get_by_label("Text color").fill("#abcdef")
    page.get_by_label("Text color").fill("#123456")
    assert page.evaluate("window.patches.at(-1)") == {"color": "#123456"}
    page.get_by_label("Bold", exact=True).click()
    assert page.evaluate("window.patches.at(-1)") == {"font_weight": "bold"}
    page.evaluate("window.inspector.setState({dirty:true})")
    page.get_by_label("Send edit").click()
    page.get_by_label("Cancel edit").click()
    assert page.evaluate("[window.sent, window.cancelled]") == [1, 1]
    page.evaluate("window.inspector.clear()")
    expect(page.locator("#inspector")).to_be_hidden()
