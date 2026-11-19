from xml.etree import ElementTree as ET

import pytest

from artifact_studio.powerpoint.text import text_html

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"


def xml(body: str) -> ET.Element:
    return ET.fromstring(f'<p:sp xmlns:p="{P}" xmlns:a="{A}">{body}</p:sp>')


def test_text_inherits_styles_and_escapes_content() -> None:
    shape = xml("""<p:txBody><a:bodyPr anchor="ctr"/><a:lstStyle/>
        <a:p><a:pPr algn="ctr"/><a:r><a:rPr b="1"/><a:t>A &amp; B</a:t></a:r></a:p>
    </p:txBody>""")
    default = ET.fromstring(f'''<p:bodyStyle xmlns:p="{P}" xmlns:a="{A}">
        <a:lvl1pPr><a:defRPr sz="2400"><a:solidFill><a:schemeClr val="accent1"/>
        </a:solidFill><a:latin typeface="+mn-lt"/></a:defRPr></a:lvl1pPr></p:bodyStyle>''')
    rendered = text_html(
        shape, [], [default], {"accent1": "123456"}, {"+mn-lt": "DejaVu Sans"}, 9144000
    )
    assert "contenteditable" not in rendered
    assert "A &amp; B" in rendered
    assert "font-size:3.3333333cqw" in rendered
    assert "font-weight:bold" in rendered
    assert "color:#123456" in rendered
    assert "text-align:center" in rendered
    assert "justify-content:center" in rendered


def test_rejects_unavailable_font_and_unsupported_bullet() -> None:
    shape = xml("""<p:txBody><a:bodyPr/><a:p><a:r><a:rPr><a:latin typeface="Missing Font XYZ"/>
        </a:rPr><a:t>Text</a:t></a:r></a:p></p:txBody>""")
    with pytest.raises(ValueError, match="unavailable"):
        text_html(shape, [], [], {}, {}, 9144000)
    shape = xml("""<p:txBody><a:bodyPr/><a:p><a:pPr><a:buChar char="•"/></a:pPr>
        <a:r><a:t>Text</a:t></a:r></a:p></p:txBody>""")
    with pytest.raises(ValueError, match="bullets"):
        text_html(shape, [], [], {}, {"+mn-lt": "DejaVu Sans"}, 9144000)


def test_local_overrides_inherited_autofit_and_bullet() -> None:
    shape = xml("""<p:txBody><a:bodyPr><a:noAutofit/></a:bodyPr>
        <a:p><a:pPr><a:buNone/></a:pPr><a:r><a:t>Text</a:t></a:r></a:p>
        </p:txBody>""")
    inherited = xml("""<p:txBody><a:bodyPr><a:normAutofit/></a:bodyPr>
        <a:lstStyle><a:lvl1pPr><a:buChar char="•"/></a:lvl1pPr></a:lstStyle>
        </p:txBody>""")
    assert "Text" in text_html(shape, [inherited], [], {}, {"+mn-lt": "DejaVu Sans"}, 9144000)
    shape.find(f".//{{{A}}}noAutofit").tag = f"{{{A}}}normAutofit"  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="autofit"):
        text_html(shape, [inherited], [], {}, {"+mn-lt": "DejaVu Sans"}, 9144000)
