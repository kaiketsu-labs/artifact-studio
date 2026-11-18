"""Reusable fictional presentation for PowerPoint import journeys.

The fixtures were generated with python-pptx from fictional content and then
adjusted as OOXML to use explicit supported styles and installed fonts. The garden
deck contains native text boxes, a table, and a chart with an embedded workbook.
"""

import base64
import io
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

FIXTURE = Path(__file__).parent / "fixtures" / "fictional-garden.pptx.base64"


def fictional_powerpoint() -> bytes:
    """Return an independent copy of the two-slide Garden presentation."""
    return base64.b64decode(FIXTURE.read_bytes())


def corrupt_powerpoint() -> bytes:
    """Return truncated package bytes for rejection tests."""
    return fictional_powerpoint()[:100]


def unsupported_powerpoint() -> bytes:
    """Replace the chart type with a pie chart to exercise explicit rejection."""
    output = io.BytesIO()
    with (
        ZipFile(io.BytesIO(fictional_powerpoint())) as source,
        ZipFile(output, "w", ZIP_DEFLATED) as target,
    ):
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "ppt/charts/chart1.xml":
                data = data.replace(b"barChart", b"pieChart")
            target.writestr(item.filename, data)
    return output.getvalue()


def fictional_layers_powerpoint() -> bytes:
    """Return a 4:3 slide with grouped, overlapping shapes and a PNG picture."""
    return base64.b64decode((FIXTURE.parent / "fictional-layers.pptx.base64").read_bytes())
