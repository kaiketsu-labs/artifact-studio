"""Free slide styling and deck file tools retain review anchors and offline boundaries."""

import hashlib
import os
from pathlib import Path

import pytest

from artifact_studio.interactive import prepare_document
from artifact_studio.models import Slide, SlidePatch
from artifact_studio.presentation_files import BASE, PresentationFiles
from artifact_studio.projects import audience_html
from artifact_studio.storage import ConflictError, SlideMarkup, Store, digest
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """Use fictional design sources and a separate database."""
    make_design(tmp_path / TEMPLATE_ID)
    return Store(tmp_path, tmp_path / "review.sqlite3")


def test_scoped_styles_backgrounds_roundtrip_and_exports(store: Store) -> None:
    """Allow local styling without manual theme edits or changing selector anchors on reads."""
    source = (
        '<article class="slide"><style>h1{color:#134b9b} '
        '.slide{background:url("assets/custom.svg")}</style>'
        '<h1 id="title">Local style</h1></article>'
    )
    slide = store.add_slide(TEMPLATE_ID, Slide(id="styled", title="Styled", html=source))
    assert "@scope" in slide.html and "data-slide-style" in slide.html
    assert 'id="title"' in slide.html
    assert store.read(TEMPLATE_ID).slides[-1].html == slide.html
    SlideMarkup().feed(slide.html)
    assert (
        'style="background:url(assets/custom.svg)"'
        in store.add_slide(
            TEMPLATE_ID,
            Slide(
                id="background",
                title="Background",
                html='<article class="slide" style="background:url(assets/custom.svg)">'
                "Image</article>",
            ),
        ).html
    )
    for printing in (False, True):
        exported = audience_html(store, TEMPLATE_ID, printing=printing)
        assert "@scope" in exported and "data:image/svg+xml;base64," in exported
    revised = store.revise(
        TEMPLATE_ID,
        slide.id,
        digest(slide.model_dump_json()),
        "style-notes-only",
        SlidePatch(notes="Human notes"),
    )
    assert revised.html == slide.html
    copy = Slide.model_validate(slide.model_dump() | {"id": "styled-copy"})
    assert copy.html == slide.html


@pytest.mark.parametrize(
    "css",
    [
        "background:u\\72l(https://example.invalid/x)",
        'background:image-set("https://example.invalid/x" 1x)',
        '--remote:"https://example.invalid/x";background:image-set(var(--remote) 1x)',
        "background:url(assets/%2e%2e/private)",
        "background:url(assets/a%5cb.svg)",
        "background:url(/api/decks)",
        "background:url(//example.invalid/x)",
        "background:expression(alert(1))",
    ],
)
def test_css_resource_validation_uses_decoded_tokens(css: str) -> None:
    """Reject escaped resource paths and alternate CSS image syntax in static markup."""
    with pytest.raises(ValueError):
        SlideMarkup().feed(f"<article class=\"slide\" style='{css}'>Static</article>")


@pytest.mark.parametrize(
    "css",
    [
        '@import "assets/a.css";',
        "@font-face{font-family:Collision;src:url(assets/a.woff2)}",
        "@keyframes pulse{to{opacity:0}}",
        '@property --a{syntax:"*";inherits:true}',
        '@media screen{@\\69mport "assets/a.css";}',
        "} .deck-toolbar{display:none}",
    ],
)
def test_global_css_stays_in_theme_or_isolated_document(css: str) -> None:
    """Global constructs cannot escape a generated slide-local style scope."""
    with pytest.raises(ValueError):
        Slide(id="x", title="X", html=f'<article class="slide"><style>{css}</style></article>')


def test_theme_tools_keep_base_styles_scope_and_approval(store: Store) -> None:
    """Manage base CSS, reject stale updates, and leave reusable design and notes untouched."""
    deck = store.create("2026-11-04-styling", "Styling", "test-brand")
    service = PresentationFiles(store)
    before = service.theme(deck.id)
    assert before["css"] == "" and before["base_styles_included"] is True
    view = store.view(deck.id).slides[0]
    store.approve(deck.id, view.id, view.content_hash, True)
    css = ".slide{background:#194b99;color:white}"
    saved = service.update_theme(deck.id, css, str(before["expected_hash"]))
    assert service.theme(deck.id)["css"] == css
    assert BASE.read_text() in (store.folder(deck.id) / "theme.css").read_text()
    assert service.update_theme(deck.id, css, str(before["expected_hash"])) == saved
    with pytest.raises(ConflictError):
        service.update_theme(deck.id, ".slide{color:red}", str(before["expected_hash"]))
    assert not store.view(deck.id).slides[0].approved
    assert (store.folder(TEMPLATE_ID) / "theme.css").read_text() == BASE.read_text()


def test_asset_import_receipts_replacement_and_boundaries(store: Store, tmp_path: Path) -> None:
    """Copy binaries without base64, require overwrite hashes, and reject path/symlink escape."""
    deck = store.create("2026-11-04-assets", "Assets", "test-brand")
    service = PresentationFiles(store)
    source = Path(str(service.assets(deck.id)["import_directory"])) / "download.svg"
    assert source.parent.is_dir()
    _ = source.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>New artwork</text></svg>')
    original = service.import_asset(deck.id, str(source), "assets/new/art.svg")
    assert original["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert service.import_asset(deck.id, str(source), "assets/new/art.svg") == original
    assert "data_base64" not in original
    _ = source.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>Changed</text></svg>')
    with pytest.raises(ConflictError):
        service.import_asset(deck.id, str(source), "assets/new/art.svg")
    replacement = service.import_asset(
        deck.id, str(source), "assets/new/art.svg", str(original["sha256"])
    )
    assert replacement["sha256"] != original["sha256"]
    assert not (store.folder(TEMPLATE_ID) / "assets/new/art.svg").exists()
    assert service.assets(deck.id, "assets/new/")["assets"] == [
        {
            "path": "assets/new/art.svg",
            "size": source.stat().st_size,
            "sha256": replacement["sha256"],
        }
    ]
    for path in ("theme.css", "assets/../theme.css", "/assets/x"):
        with pytest.raises(ValueError):
            service.import_asset(deck.id, str(source), path)
    link = tmp_path / "linked.svg"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        service.import_asset(deck.id, str(link), "assets/linked.svg")
    (store.folder(deck.id) / "assets/escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        service.import_asset(deck.id, str(source), "assets/escape/x.svg")


def test_asset_sources_are_scoped_to_authorized_material(store: Store, tmp_path: Path) -> None:
    """Private files and another deck cannot be turned into downloadable slide assets."""
    deck = store.create("2026-11-04-source-scope", "Scope", "test-brand")
    service = PresentationFiles(store)
    credential = tmp_path / ".credentials"
    _ = credential.write_text("PRIVATE_SOURCE")
    for source in (credential, store.database, store.folder(TEMPLATE_ID) / "index.html"):
        with pytest.raises(ValueError, match="Asset sources must be staged"):
            service.import_asset(deck.id, str(source), "assets/private.bin")
    assert not (store.folder(deck.id) / "assets/private.bin").exists()
    selected_brand_asset = store.folder(TEMPLATE_ID) / "assets/custom.svg"
    assert (
        service.import_asset(deck.id, str(selected_brand_asset), "assets/brand.svg")["path"]
        == "assets/brand.svg"
    )


def test_staging_is_ready_and_missing_sources_are_actionable(store: Store) -> None:
    """The advertised staging path works immediately and source failures explain the cause."""
    service = PresentationFiles(store)
    first = service.assets(TEMPLATE_ID)
    directory = Path(str(first["import_directory"]))
    assert directory.is_dir()
    assert service.assets(TEMPLATE_ID)["import_directory"] == str(directory)
    with pytest.raises(FileNotFoundError, match="does not exist.*create the file first"):
        service.import_asset(TEMPLATE_ID, str(directory / "missing.svg"), "assets/missing.svg")
    with pytest.raises(ValueError, match="not a directory"):
        service.import_asset(TEMPLATE_ID, str(directory), "assets/directory.svg")


@pytest.mark.parametrize("blocker", ["symlink", "file"])
def test_staging_rejects_symlinks_and_files(store: Store, tmp_path: Path, blocker: str) -> None:
    """Listing never creates a staging directory through an external link or existing file."""
    staging = store.folder(TEMPLATE_ID) / ".authoring-assets"
    outside = tmp_path / "outside"
    outside.mkdir()
    if blocker == "symlink":
        staging.symlink_to(outside, target_is_directory=True)
    else:
        _ = staging.write_text("Keep this file")
    with pytest.raises(ValueError, match="import (directory|path)"):
        PresentationFiles(store).assets(TEMPLATE_ID)
    assert list(outside.iterdir()) == []


def test_css_asset_embedding_matches_accepted_syntax(store: Store) -> None:
    """Escaped URL functions, image-set and parentheses in filenames remain portable."""
    from artifact_studio.projects import AssetEmbedder

    folder = store.folder(TEMPLATE_ID)
    _ = (folder / "assets/a)b.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    embedder = AssetEmbedder(folder)
    for css in (
        r'background:u\72l("assets/a)b.svg")',
        'background:image-set("assets/a)b.svg" 1x)',
        'background:url("assets/a)b.svg")',
    ):
        SlideMarkup().feed('<article class="slide" style=\'' + css + "'>Portable</article>")
        assert "data:image/svg+xml;base64," in embedder.css(css)
        assert "assets/a" not in embedder.css(css)


@pytest.mark.skipif(os.environ.get("ARTIFACT_STUDIO_RENDER_TESTS") != "1", reason="Opt-in Chromium")
@pytest.mark.parametrize("background", [r'url("assets/a)b.png")', 'image-set("assets/a)b.png" 1x)'])
def test_static_css_background_capture_matches_preview(
    store: Store, tmp_path: Path, background: str
) -> None:
    """Render real offline screenshots of supported CSS image syntaxes in sandboxed Chromium."""
    from PIL import Image

    from artifact_studio.visuals import capture

    Image.new("RGB", (32, 32), "#e1edfc").save(store.folder(TEMPLATE_ID) / "assets/a)b.png")
    slide = store.add_slide(
        TEMPLATE_ID,
        Slide(
            id="capture-css",
            title="Capture CSS",
            html=(
                '<article class="slide" style=\'background:' + background + "'>Background</article>"
            ),
        ),
    )
    result = capture(store, TEMPLATE_ID, tmp_path / "captures", slide_id=slide.id)
    with Image.open(result.frames[0].path) as rendered:
        assert rendered.convert("RGB").getpixel((1500, 850)) == (225, 237, 252)


def test_nested_iframe_preparation_and_bounds(store: Store) -> None:
    """Prepare assets and scripts in srcdoc, escaping attributes once and bounding nesting."""
    from html import escape

    folder = store.folder(TEMPLATE_ID)
    _ = (folder / "assets/child.js").write_text("document.querySelector('h1').textContent='A & B';")
    child = (
        "<style>body{background:url(assets/custom.svg)}</style><h1>Child</h1>"
        '<script src="assets/child.js"></script>'
    )
    prepared = prepare_document(
        store, TEMPLATE_ID, '<iframe srcdoc="' + escape(child, quote=True) + '"></iframe>'
    )
    assert "data:image/svg+xml;base64," in prepared and "A &amp; B" in prepared
    assert "assets/child.js" not in prepared
    for _ in range(7):
        child = '<iframe srcdoc="' + escape(child, quote=True) + '"></iframe>'
    with pytest.raises(ValueError, match="6 levels"):
        prepare_document(store, TEMPLATE_ID, child)


def test_css_import_conditions_and_expansion_budget(store: Store) -> None:
    """Retain layer/supports/media qualifiers and stop acyclic repeated-import amplification."""
    from artifact_studio.projects import AssetEmbedder

    folder = store.folder(TEMPLATE_ID)
    _ = (folder / "assets/import.css").write_text(".slide{color:red}")
    css = AssetEmbedder(folder).css(
        '@import "assets/import.css" layer(brand) supports(display:grid) screen;'
    )
    assert "@layer brand{" in css and "@supports (display:grid){" in css
    assert "@media screen{" in css and ".slide{color:red}" in css
    for index in range(10):
        _ = (folder / f"assets/expand-{index}.css").write_text(
            f'@import "expand-{index + 1}.css";@import "expand-{index + 1}.css";'
        )
    _ = (folder / "assets/expand-10.css").write_text('.leaf{--pad:"' + "x" * 16_384 + '"}')
    with pytest.raises(ValueError, match="CSS expansion"):
        prepare_document(store, TEMPLATE_ID, '<link rel="stylesheet" href="assets/expand-0.css">')
