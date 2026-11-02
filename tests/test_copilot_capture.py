"""Audience-only staging and actual gateway capture checks using fictional sources."""

import json
import os
from pathlib import Path

import pytest

from artifact_studio.copilot_capture import audience_clone
from artifact_studio.copilot_tools import ScopedTools
from artifact_studio.models import AgentContext, SlidePatch
from artifact_studio.storage import Store, digest
from tests.design_factory import TEMPLATE_ID, make_design

DECK = "2026-11-02-cloud-capture"
RENDER = pytest.mark.skipif(
    os.environ.get("ARTIFACT_STUDIO_RENDER_TESTS") != "1", reason="Opt-in local Chromium rendering"
)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """Build a slide with audience markup and private files that the clone must exclude."""
    make_design(tmp_path / "collection" / TEMPLATE_ID)
    result = Store(tmp_path / "collection", tmp_path / "review.sqlite3")
    _ = result.create(DECK, "Fictional cloud capture")
    slide = result.read(DECK).slides[0]
    _ = result.revise(
        DECK,
        slide.id,
        digest(slide.model_dump_json()),
        "private-capture-context",
        SlidePatch(
            notes="PRIVATE SPEAKER NOTES",
            agent=AgentContext(summary="PRIVATE DISCUSSION"),
            html='<article class="slide"><h1>Audience only</h1>'
            '<div data-build="1">Reveal</div></article>',
        ),
    )
    _ = (result.folder(DECK) / "private.json").write_text('{"secret":"PRIVATE LOCAL FILE"}')
    return result


def test_clone_excludes_private_state_and_preserves_audience_assets(
    store: Store, tmp_path: Path
) -> None:
    """Copy presentation inputs without speaker notes, discussion, README or miscellaneous files."""
    clone = audience_clone(store, DECK, tmp_path / "clone")
    folder = clone.folder(DECK)
    assert not (folder / "README.md").exists()
    assert not (folder / "private.json").exists()
    assert not (folder / "brief-files.json").exists()
    assert (folder / "assets" / "test-brand.svg").exists()
    source = (folder / "index.html").read_text()
    assert "PRIVATE" not in source and "Audience only" in source
    assert clone.read(DECK).slides[0].notes == ""
    assert "PRIVATE SPEAKER NOTES" in (store.folder(DECK) / "index.html").read_text()


def test_clone_rejects_symlinks(store: Store, tmp_path: Path) -> None:
    """Never copy a linked file from outside the explicit audience asset tree."""
    (store.folder(DECK) / "assets" / "linked.txt").symlink_to(store.database)
    with pytest.raises(ValueError, match="symlinks"):
        _ = audience_clone(store, DECK, tmp_path / "clone")


@RENDER
def test_real_slide_capture_contains_pixels_and_original_tokens(store: Store) -> None:
    """Run offline Chromium and return full-size pixels with original revision tokens."""
    scoped = ScopedTools(store, "legacy-presentations", False)
    slide = store.read(DECK).slides[0]
    result = scoped.capture_slide(DECK, slide.id, stage=0)
    assert not result.is_error
    assert any(item.type == "image" and item.mime_type == "image/png" for item in result.content)
    metadata = result.structured_content
    assert metadata is not None
    serialized = json.dumps(metadata)
    assert "PRIVATE" not in serialized
    assert '"path"' not in serialized
    assert digest(slide.model_dump_json()) in serialized
    assert store.content_hash(DECK, slide) in serialized
    assert '"width": 1600' in serialized and '"height": 900' in serialized


@RENDER
def test_capture_cannot_embed_an_unshared_deck_file(store: Store) -> None:
    """Refuse dependency references outside theme.css and assets even when local files exist."""
    slide = store.read(DECK).slides[0]
    theme = store.folder(DECK) / "theme.css"
    _ = theme.write_text(theme.read_text() + '\n@import "private.json";')
    scoped = ScopedTools(store, "legacy-presentations", False)
    with pytest.raises(ValueError, match="outside"):
        _ = scoped.capture_slide(DECK, slide.id)
