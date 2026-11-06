"""Run the favorites prototype with fictional work: uv run python -m scripts.preview_favorites.

The temporary collection is removed on exit. Production data is never opened.
Preview covers are rendered by the diagnostic browser because this managed host
cannot launch the production renderer's OS sandbox. Application routes stay unchanged.
"""

import tempfile
import threading
from contextlib import suppress
from pathlib import Path

from playwright.sync_api import Browser, sync_playwright

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.favorites import DescriptionInput, Favorites, StarInput
from scripts.screenshot_fixture import seed
from tests.integration.support import DECK_ID, RunningApp, running_app


def seed_favorites(app: RunningApp, root: Path) -> None:
    """Create a small, recognizable library and three cross-deck slide bookmarks."""
    _ = seed(app)
    store = app.store
    other = store.create("2026-11-05-fieldnotes", "Fieldnotes · Launch", "studio-blue")
    examples = store.read(DECK_ID)
    other.slides = [
        examples.slides[2].model_copy(deep=True),
        examples.slides[4].model_copy(deep=True),
    ]
    store.write(other.id, other)
    artifacts = ArtifactStore(store)
    project = artifacts.create_project("Fieldnotes")
    for title, color, eyebrow, headline in (
        ("Brand direction", "#e5eadc", "FIELDNOTES / BRAND", "A little more human."),
        ("Launch brief", "#f2e8df", "FIELDNOTES / STRATEGY", "Make room for the next idea."),
        ("Research notes", "#dce7ef", "FIELDNOTES / RESEARCH", "What we heard."),
    ):
        source = root / title
        source.mkdir()
        _ = (source / "index.html").write_text(
            '<!doctype html><html lang="en"><meta charset="utf-8"><title>' + title + "</title>"
            "<style>*{box-sizing:border-box}body{margin:0;padding:8vw;font-family:Arial;"
            f"background:{color};color:#28392d}}p{{font-size:1.5vw;letter-spacing:.18em}}"
            "h1{font-size:7vw;letter-spacing:-.07em;max-width:70vw;line-height:1.05}"
            "</style><p>" + eyebrow + "</p><h1>" + headline + "</h1></html>"
        )
        item = artifacts.register_artifact(project.id, title, source, "index.html")
        if title == "Brand direction":
            _ = Favorites(store).set(StarInput(kind="artifact", artifact_id=item.id, starred=True))
    favorite = Favorites(store)
    _ = favorite.set(StarInput(kind="artifact", artifact_id=DECK_ID, starred=True))
    for deck, slide in ((DECK_ID, "workflow"), (DECK_ID, "interactive"), (other.id, "story")):
        _ = favorite.set(StarInput(kind="slide", artifact_id=deck, slide_id=slide, starred=True))
    for deck, slide, description in (
        (DECK_ID, "workflow", "Clear three-step process."),
        (other.id, "story", "Two-column comparison with space for evidence."),
    ):
        _ = favorite.describe(
            deck, slide, DescriptionInput(description=description, expected_description="")
        )


def prepare_covers(app: RunningApp, browser: Browser) -> None:
    """Populate only the disposable cover cache from actual authored fixture documents."""
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    try:
        for item in ArtifactStore(app.store).list_artifacts():
            if item.legacy_deck_id:
                first = app.store.read(item.id).slides[0]
                url = f"{app.url}/api/decks/{item.id}/slides/{first.id}/preview"
            else:
                url = f"{app.url}/api/artifacts/{item.id}/files/{item.entrypoint}"
            page.goto(url)
            _ = page.evaluate("document.fonts.ready")
            path = app.store.database.parent / "artifact-previews" / (item.content_hash + ".png")
            path.parent.mkdir(exist_ok=True)
            _ = page.screenshot(path=path)
    finally:
        page.close()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="favorite-slides-prototype-") as temporary:
        root = Path(temporary)
        with running_app(root, root / "server.log") as app:
            seed_favorites(app, root)
            with sync_playwright() as renderer:
                browser = renderer.chromium.launch()
                prepare_covers(app, browser)
                browser.close()
            print(f"Prototype: {app.url}\nWorkspace: {root}", flush=True)
            with suppress(KeyboardInterrupt):
                threading.Event().wait()


if __name__ == "__main__":
    main()
