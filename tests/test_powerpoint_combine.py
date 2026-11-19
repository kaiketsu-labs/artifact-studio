import base64
import os
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from pptx import Presentation
from pptx.shapes.autoshape import Shape
from pptx.util import Inches

from artifact_studio.powerpoint.combine import combine_slides


def deck(*titles: str, width: int = 10) -> bytes:
    presentation = Presentation()
    presentation.slide_width = Inches(width)
    for title in titles:
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        label = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(2), Inches(1))
        label.text = title
    encoded = BytesIO()
    presentation.save(encoded)
    return encoded.getvalue()


def test_native_and_imported_slides_interleave_with_independent_masters() -> None:
    original = deck("Field", "Trial")
    added = deck("Opening", "Review")
    combined = combine_slides(original, added, [False, True, False, True])
    presentation = Presentation(BytesIO(combined))
    assert len(presentation.slide_masters) == 2
    text: list[str] = []
    for slide in presentation.slides:
        shape = slide.shapes[0]
        assert isinstance(shape, Shape)
        text.append(shape.text)
        assert slide.slide_layout.slide_master is not None
    assert text == ["Opening", "Field", "Review", "Trial"]
    with ZipFile(BytesIO(combined)) as result, ZipFile(BytesIO(original)) as source:
        assert result.read("ppt/slides/slide1.xml") == source.read("ppt/slides/slide1.xml")
        assert len(result.namelist()) == len(set(result.namelist()))
    with pytest.raises(ValueError, match="canvas size"):
        combine_slides(original, deck("Wider", width=12), [True, True, False])


@pytest.mark.browser
@pytest.mark.skipif(
    os.environ.get("ARTIFACT_STUDIO_RENDER_TESTS") != "1",
    reason="Requires the sandboxed Chromium renderer",
)
def test_four_by_three_import_with_new_studio_slide(tmp_path: Path) -> None:
    from artifact_studio.artifacts import ArtifactStore
    from artifact_studio.models import Slide
    from artifact_studio.powerpoint import import_powerpoint
    from artifact_studio.powerpoint.download import powerpoint_bytes
    from artifact_studio.storage import Store
    from tests.pptx_fixture import fictional_layers_powerpoint

    store = Store(tmp_path / "collection", tmp_path / "review.sqlite3")
    project = ArtifactStore(store).create_project("Mixed presentation")
    imported = import_powerpoint(
        store, project.id, "Layers", base64.b64encode(fictional_layers_powerpoint()).decode()
    )
    _ = store.add_slide(
        imported.id,
        Slide(
            id="closing",
            title="Closing",
            html='<article class="slide"><div style="position:absolute;left:100px;top:100px;'
            'font:24px Arial;color:#234">Final observation</div></article>',
        ),
    )
    deck = store.read(imported.id)
    deck.slides.reverse()
    store.write(imported.id, deck)
    binary = powerpoint_bytes(store, imported.id)
    result = Presentation(BytesIO(binary))
    assert len(result.slides) == 2 and len(result.slide_masters) == 2
    assert result.slide_height is not None and result.slide_width is not None
    assert result.slide_height * 4 == result.slide_width * 3
    assert any(
        isinstance(shape, Shape) and shape.text == "Final observation"
        for shape in result.slides[0].shapes
    )
