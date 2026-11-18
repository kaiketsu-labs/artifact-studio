# PowerPoint import

Artifact Studio can import a supported `.pptx` into a new editable presentation. It translates slide objects to native HTML; it does not embed a PowerPoint player or preserve the original file as the editing format. The imported result should be reviewed against the source before use. A deck with unsupported content is rejected with an explanation instead of silently importing only some slides. Uploads are limited to 50 MB and 1–100 slides, with widescreen 16:9 or standard 4:3 dimensions.

The supported subset includes horizontal text with available installed fonts, runs, paragraph alignment, spacing, margins and theme styles; simple shapes, grouped shapes and pictures; and a subset of tables and column charts. These are editable Studio objects, although their editing controls and fidelity differ from PowerPoint. Theme and placeholder inheritance are supported within this subset. Text fonts must be installed on the server running the import, including resolved theme fonts; embedded fonts are rejected. On Linux font discovery uses fontconfig, and on macOS it uses CoreText.

Unsupported examples include slide animations and transitions, text autofit, bullets, vertical text, text effects, unsupported colors and fonts, dashed outlines and many shape effects, merged table cells, and unsupported chart types or chart formatting. Import errors identify the slide and object where possible. Simplify the source or install the required fonts and try again. Exact feature support depends on the source object's properties; the list above is not exhaustive.

## Reference comparison

On 18 November 2026, both slides of the fictional `tests/fixtures/fictional-garden.pptx.base64` and the single 4:3 slide of `fictional-layers.pptx.base64` were independently opened with LibreOfficeDev 26.8.0.0.alpha0 and exported to PDF. The same fixtures were imported in Studio and cropped from the editor and presenter in Chromium. PDF pages were rendered at the exact dimensions of each crop for measurements; the side-by-side images below are resized for display. LibreOffice is an independent implementation, **not Microsoft PowerPoint**. No Microsoft PowerPoint reference rendering was available for either fixture.

The title slide's text positions differ by a few pixels. The table and chart retain visible typography, spacing and positioning differences, though the chart title, grid intervals and bar widths are now closer to the LibreOffice reference. The layered fixture preserves the overlapping blocks and image, and its text wraps onto the same three lines. Text baselines differ slightly; the compared presenter crop contains a thin border at the slide edge. These examples cover only a small subset of PowerPoint and do not establish fidelity for other presentations.

For supplemental measurements, the table reports mean absolute RGB difference on a 0–255 scale and the percentage of pixels with a nonzero luminance difference. Editor crops were 960 × 539 for the garden slides and 960 × 720 for layers; presenter crops were 1600 × 900 and 1334 × 1001 respectively. Browser controls, font rasterization and antialiasing affect these values. They are not semantic or Microsoft PowerPoint fidelity scores.

| Fixture | Editor mean / changed pixels | Presenter mean / changed pixels |
| --- | --- | --- |
| Garden slide 1 | 1.524 / 1.50% | 1.423 / 1.20% |
| Garden slide 2 | 3.898 / 3.42% | 3.917 / 2.95% |
| Layers slide 1 | 1.151 / 1.25% | 1.742 / 1.82% |

Side-by-side evidence: [garden slide 1](screenshots/powerpoint-garden-1-comparison.png), [garden slide 2](screenshots/powerpoint-garden-2-comparison.png), and [layers slide](screenshots/powerpoint-layers-1-comparison.png). Each image shows LibreOffice, Studio editor, and Studio presenter from left to right.

To reproduce the reference render from the repository root (with LibreOffice and Poppler installed):

```sh
mkdir -p /tmp/pptx-review
uv run python -c 'from pathlib import Path; from tests.pptx_fixture import fictional_powerpoint; Path("/tmp/pptx-review/garden.pptx").write_bytes(fictional_powerpoint())'
soffice -env:UserInstallation=file:///tmp/pptx-review/profile --headless --convert-to pdf --outdir /tmp/pptx-review /tmp/pptx-review/garden.pptx
pdftoppm -png -scale-to-x 1600 -scale-to-y 900 /tmp/pptx-review/garden.pdf /tmp/pptx-review/libreoffice
```

For the Studio comparison, import each fixture through the project import dialog and capture the `.slide` element in the editor preview and in the presenter. Render the reference PDF at each crop's exact dimensions before comparing. Use identical installed fonts, compare at full size, and exercise selection, text editing and save/reopen behavior. The browser journeys in `tests/integration/test_powerpoint_import.py` cover these paths.
