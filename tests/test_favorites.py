"""Bookmark metadata must not change source; references must retain the chosen visual."""

import base64
import json
import re
from pathlib import Path

import pytest

from artifact_studio.favorites import DescriptionInput, Favorites, StarInput
from artifact_studio.models import Slide, SlidePatch
from artifact_studio.storage import ConflictError, Store, digest
from tests.design_factory import TEMPLATE_ID, make_design

DECK = "2026-11-05-favorites"


@pytest.fixture
def favorites(tmp_path: Path) -> Favorites:
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "review.sqlite3")
    deck = store.create(DECK, "Fieldnotes")
    slide = deck.slides[0]
    _ = store.revise(
        DECK,
        slide.id,
        digest(slide.model_dump_json()),
        "fixture-reference",
        SlidePatch(
            html='<article class="slide"><h1>Keep this idea</h1></article>',
            notes="PRIVATE SPEAKER NOTES",
        ),
    )
    return Favorites(store)


def star(favorites: Favorites, slide: str = "cover", active: bool = True) -> None:
    _ = favorites.set(
        StarInput(
            kind="slide" if slide else "artifact", artifact_id=DECK, slide_id=slide, starred=active
        )
    )


def test_stars_persist_without_changing_source_approval_or_revision(favorites: Favorites) -> None:
    store = favorites.store
    slide = store.read(DECK).slides[0]
    token = store.content_hash(DECK, slide)
    store.approve(DECK, slide.id, token, True)
    source = store.folder(DECK) / "index.html"
    before = source.read_bytes(), source.stat().st_mtime_ns, store.view(DECK)
    star(favorites, slide.id)
    star(favorites, slide.id)
    star(favorites, "")
    assert Favorites(store).index() == {"artifacts": [DECK], "slides": [DECK + "/" + slide.id]}
    assert (source.read_bytes(), source.stat().st_mtime_ns, store.view(DECK)) == before
    star(favorites, slide.id, False)
    star(favorites, "", False)
    assert not favorites.index()["slides"] and not favorites.index()["artifacts"]


def test_favorites_follow_stable_ids_and_archived_decks_reappear(favorites: Favorites) -> None:
    slide = favorites.store.read(DECK).slides[0]
    star(favorites, slide.id)
    _ = favorites.store.add_slide(DECK, Slide(id="another", title=slide.title, html=slide.html))
    assert [entry.slide_id for entry in favorites.slides()] == [slide.id]
    favorites.store.set_archived(DECK, archived=True)
    assert not favorites.slides()
    favorites.store.set_archived(DECK, archived=False)
    assert len(favorites.slides()) == 1
    assert len(favorites.index()["slides"]) == 1


def test_reference_freezes_visual_and_provenance_without_private_context(
    favorites: Favorites,
) -> None:
    slide = favorites.store.read(DECK).slides[0]
    star(favorites, slide.id)
    entry = favorites.slides()[0]
    reference = favorites.reference(DECK, slide.id, entry.content_hash)
    source = base64.b64decode(reference.data_base64).decode()
    assert "Keep this idea" in source and "PRIVATE SPEAKER NOTES" not in source
    assert '"role": "read-only visual reference"' in source
    assert entry.content_hash in source and '"deck_id": "' + DECK in source
    _ = favorites.store.revise(
        DECK,
        slide.id,
        digest(slide.model_dump_json()),
        "later-edit",
        SlidePatch(html='<article class="slide">New idea</article>'),
    )
    with pytest.raises(ValueError, match="changed"):
        _ = favorites.reference(DECK, slide.id, entry.content_hash)
    assert "Keep this idea" in source and "New idea" not in source
    star(favorites, slide.id, False)
    with pytest.raises(ValueError, match="no longer"):
        _ = favorites.reference(DECK, slide.id, favorites.store.content_hash(DECK, slide))


def test_reference_detects_edit_during_preparation(
    favorites: Favorites, monkeypatch: pytest.MonkeyPatch
) -> None:
    from artifact_studio import favorites as module

    slide = favorites.store.read(DECK).slides[0]
    star(favorites, slide.id)
    original = module.snapshot

    def changed(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        _ = favorites.store.revise(
            DECK,
            slide.id,
            digest(slide.model_dump_json()),
            "concurrent-edit",
            SlidePatch(title="A newer title"),
        )
        return result

    expected = favorites.slides()[0].content_hash
    monkeypatch.setattr(module, "snapshot", changed)
    with pytest.raises(ValueError, match="changed while"):
        _ = favorites.reference(DECK, slide.id, expected)


def test_reference_embeds_interactive_local_assets(favorites: Favorites) -> None:
    store = favorites.store
    slide = store.read(DECK).slides[0]
    _ = (store.folder(DECK) / "assets/example.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><circle r="5"/></svg>'
    )
    _ = store.revise(
        DECK,
        slide.id,
        digest(slide.model_dump_json()),
        "interactive-reference",
        SlidePatch(interactive_html='<img src="assets/example.svg">'),
    )
    star(favorites, slide.id)
    source = base64.b64decode(
        favorites.reference(DECK, slide.id, favorites.slides()[0].content_hash).data_base64
    ).decode()
    assert "data:image/svg+xml;base64," in source
    assert 'src=\\"assets/example.svg' not in source


def test_cannot_star_missing_or_ambiguous_targets(favorites: Favorites) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        star(favorites, "missing")
    with pytest.raises(ValueError, match="slide ID"):
        _ = favorites.set(
            StarInput(kind="artifact", artifact_id=DECK, slide_id="cover", starred=True)
        )


def test_reference_rejects_changed_root_script_even_when_native_slide_hash_is_unchanged(
    favorites: Favorites,
) -> None:
    store = favorites.store
    slide = store.read(DECK).slides[0]
    path = store.folder(DECK) / "widget.js"
    _ = path.write_text("window.example = 1;")
    _ = store.revise(
        DECK,
        slide.id,
        digest(slide.model_dump_json()),
        "root-script",
        SlidePatch(interactive_html='<script src="widget.js"></script>'),
    )
    star(favorites, slide.id)
    before = favorites.slides()[0].content_hash
    original = store.content_hash(DECK, store.read(DECK).slides[0])
    _ = path.write_text("window.example = 2;")
    assert original == store.content_hash(DECK, store.read(DECK).slides[0])
    with pytest.raises(ValueError, match="changed"):
        _ = favorites.reference(DECK, slide.id, before)


@pytest.mark.parametrize("existing", ("", "A saved description"))
def test_description_migrates_old_bookmarks_and_preserves_slide_state(
    favorites: Favorites, existing: str
) -> None:
    store = favorites.store
    with store.connect() as db:
        _ = db.execute("DROP TABLE favorites")
        _ = db.execute(
            "CREATE TABLE favorites(kind TEXT, artifact TEXT, slide TEXT, "
            "PRIMARY KEY(kind, artifact, slide))"
        )
        _ = db.execute("INSERT INTO favorites VALUES ('slide', ?, 'cover')", (DECK,))
        if existing:
            _ = db.execute("ALTER TABLE favorites ADD COLUMN description TEXT NOT NULL DEFAULT ''")
            _ = db.execute("UPDATE favorites SET description=?", (existing,))
    restored = Favorites(store)
    assert restored.slides()[0].description == existing
    before = store.view(DECK), (store.folder(DECK) / "index.html").read_bytes()
    assert (
        restored.describe(
            DECK,
            "cover",
            DescriptionInput(
                description="  A spacious comparison layout.  ", expected_description=existing
            ),
        )
        == "A spacious comparison layout."
    )
    assert Favorites(store).slides()[0].description == "A spacious comparison layout."
    assert "description" not in restored.index()
    assert (store.view(DECK), (store.folder(DECK) / "index.html").read_bytes()) == before
    # Idempotent re-starring retains context; explicitly removing the favorite clears it.
    star(restored)
    assert restored.slides()[0].description == "A spacious comparison layout."
    star(restored, active=False)
    star(restored)
    assert restored.slides()[0].description == ""


def test_description_conflicts_clear_and_absent_bookmarks(favorites: Favorites) -> None:
    star(favorites)
    body = DescriptionInput(description="Use this for comparison", expected_description="")
    assert favorites.describe(DECK, "cover", body) == body.description
    assert favorites.describe(DECK, "cover", body) == body.description
    with pytest.raises(ConflictError, match="changed elsewhere"):
        _ = favorites.describe(
            DECK,
            "cover",
            DescriptionInput(description="Stale replacement", expected_description=""),
        )
    assert (
        favorites.describe(
            DECK, "cover", DescriptionInput(description="", expected_description=body.description)
        )
        == ""
    )
    star(favorites, active=False)
    with pytest.raises(ValueError, match="no longer"):
        _ = favorites.describe(DECK, "cover", body)
    with pytest.raises(ValueError):
        _ = DescriptionInput(description="x" * 301, expected_description="")


def test_reference_freezes_description_and_rejects_a_stale_picker(favorites: Favorites) -> None:
    star(favorites)
    before = favorites.slides()[0]
    description = "Comparison layout </script><p>with evidence</p>"
    _ = favorites.describe(
        DECK, "cover", DescriptionInput(description=description, expected_description="")
    )
    with pytest.raises(ValueError, match="description changed"):
        _ = favorites.reference(DECK, "cover", before.content_hash)
    entry = favorites.slides()[0]
    source = base64.b64decode(
        favorites.reference(DECK, "cover", entry.content_hash).data_base64
    ).decode()
    metadata = re.search(r'id="artifact-studio-slide-reference">(.*?)</script>', source)
    assert metadata and json.loads(metadata[1])["description"] == description
    assert source.index('id="artifact-studio-slide-reference"') < source.index("<style>")
    assert "PRIVATE SPEAKER NOTES" not in source
    _ = favorites.describe(
        DECK,
        "cover",
        DescriptionInput(description="New description", expected_description=description),
    )
    assert json.loads(metadata[1])["description"] == description
    with pytest.raises(ValueError, match="description changed"):
        _ = favorites.reference(DECK, "cover", entry.content_hash)
