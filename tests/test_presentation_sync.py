"""Private-state-free portable presentation source for future hosted sync."""

from pathlib import Path

import pytest

from artifact_studio.design_authoring import blank_presentation
from artifact_studio.models import AgentContext, Slide
from artifact_studio.presentation_sync import parse_portable, portable_snapshot
from artifact_studio.storage import Store


def test_portable_snapshot_excludes_private_context_and_preserves_order(tmp_path: Path) -> None:
    store = Store(tmp_path / "collection", tmp_path / "review.sqlite")
    folder = store.root / "fictional-deck"
    folder.mkdir()
    deck = blank_presentation(folder, "fictional-deck", "Fictional deck")
    deck.slides[0].notes = "PRIVATE NOTES SENTINEL"
    deck.slides[0].revision = 17
    deck.slides[0].agent = AgentContext.model_validate({})
    deck.slides.append(Slide(id="second", title="Second", html="<article>Second</article>"))
    store.write(deck.id, deck)
    _ = (folder / "README.md").write_text("PRIVATE CONTEXT SENTINEL")
    _ = (folder / "assets" / "image.txt").write_bytes(b"fictional asset")

    with store.editing(deck.id):
        files = portable_snapshot(store, deck.id)
    serialized = b"\n".join(files.values())
    assert b"PRIVATE NOTES SENTINEL" not in serialized
    assert b"PRIVATE CONTEXT SENTINEL" not in serialized
    assert b'"revision"' not in serialized
    assert "index.html" not in files and "README.md" not in files
    manifest, slides = parse_portable(files)
    assert manifest.slides == ["cover", "second"]
    assert [slide.id for slide in slides] == manifest.slides
    assert files["assets/image.txt"] == b"fictional asset"
    assert store.read(deck.id).slides[0].notes == "PRIVATE NOTES SENTINEL"
    assert store.read(deck.id).slides[0].revision == 17


def test_portable_rejects_extra_private_files_and_mismatched_identity(tmp_path: Path) -> None:
    store = Store(tmp_path / "collection", tmp_path / "review.sqlite")
    folder = store.root / "fictional-deck"
    folder.mkdir()
    _ = blank_presentation(folder, "fictional-deck", "Fictional deck")
    with store.editing("fictional-deck"):
        files = portable_snapshot(store, "fictional-deck")
    with pytest.raises(ValueError, match="Unexpected"):
        _ = parse_portable(files | {"README.md": b"private"})
    with pytest.raises(ValueError, match="identity"):
        _ = parse_portable(
            files
            | {"slides/cover.json": files["slides/cover.json"].replace(b'"cover"', b'"other"', 1)}
        )
