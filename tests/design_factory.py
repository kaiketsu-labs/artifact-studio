"""Generate tiny fictional test fixtures without shipping corporate design assets."""

import shutil
from pathlib import Path

from studio.models import Deck, DesignManifest, Slide, Source

TEMPLATE_ID = "2026-10-28-test-design"
PROJECT = Path(__file__).resolve().parents[1]


def make_design(folder: Path, design_id: str = "test-brand") -> None:
    """Prepare the smallest independent pack needed to exercise the storage contract."""
    folder.mkdir(parents=True)
    (folder / "assets").mkdir()
    for name in (f"{design_id}.svg", "custom.svg"):
        _ = (folder / "assets" / name).write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    for name in ("presentation.js", "icons.js", "slideshow.css"):
        _ = shutil.copy2(PROJECT / "runtime" / name, folder / name)
    _ = shutil.copy2(PROJECT / "runtime/viewer.css", folder / "theme.css")
    _ = shutil.copy2(PROJECT / "docs/AGENT-GUIDE.md", folder / "AGENT-GUIDE.md")
    _ = (folder / "PREFERENCES.md").write_text("Concise text; compose freely.")
    _ = (folder / "README.md").write_text("Fictional test design.")
    _ = (folder / "Master.pptx").write_bytes(b"Fixture source pointer")
    deck = Deck(
        id=folder.name,
        title="Test design",
        design_id=design_id,
        slides=[
            Slide(
                id="cover",
                title="Cover",
                html='<article class="slide"><h1>Test</h1>'
                f'<img src="assets/{design_id}.svg" alt=""></article>',
                sources=[Source(label="Test master", url="Master.pptx")],
            ),
            Slide(
                id="message", title="Message", html='<article class="slide">Test message</article>'
            ),
        ],
    )
    shell = (PROJECT / "runtime/viewer.html").read_text()
    _ = (folder / "index.html").write_text(
        shell.replace("{{DECK_JSON}}", deck.model_dump_json().replace("<", r"\u003c"))
    )
    _ = (folder / "design.json").write_text(
        DesignManifest(
            id=design_id,
            name=design_id,
            description="Fictional test design",
        ).model_dump_json()
    )
