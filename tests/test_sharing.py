"""Check revision-pinned local audience sharing with fictional artifacts."""

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from artifact_studio.artifact_models import ArtifactView
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.sharing import SharingService
from artifact_studio.storage import Store


def test_artifact_and_project_capture_audience_bundle(tmp_path: Path) -> None:
    """Retain executable assets and pin bytes while leaving workspace metadata out."""
    store = Store(tmp_path / "workspace", tmp_path / "state" / "review.sqlite3")
    artifacts = ArtifactStore(store)
    project = artifacts.create_project("Fictional project")
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.html").write_text('<script src="app.js"></script>')
    (source / "app.js").write_text("window.answer = 42")
    (source / "config.json").write_text('{"secret": "private"}')
    artifact = artifacts.register_artifact(project.id, "Interactive", source, "index.html")
    sharing = SharingService(store, artifacts)
    prepared = sharing.prepare_artifact(artifact.id)
    (Path(artifact.source_path) / "app.js").write_text("window.answer = 7")
    assert prepared.revisions == ((artifact.id, 1),)
    assert prepared.excluded == ("config.json",)
    with ZipFile(BytesIO(prepared.data)) as archive:
        assert set(archive.namelist()) == {"index.html", "app.js"}
        assert archive.read("app.js") == b"window.answer = 42"
    project_export = sharing.prepare_project(project.id)
    assert project_export.revisions == ((artifact.id, 2),)
    with ZipFile(BytesIO(project_export.data)) as archive:
        assert set(archive.namelist()) == {"01-Interactive/index.html", "01-Interactive/app.js"}


def test_empty_project_rejected(tmp_path: Path) -> None:
    """Avoid producing a misleading empty project archive."""
    store = Store(tmp_path / "workspace", tmp_path / "state" / "review.sqlite3")
    artifacts = ArtifactStore(store)
    project = artifacts.create_project("Empty")
    with pytest.raises(ValueError, match="no artifacts"):
        SharingService(store, artifacts).prepare_project(project.id)


def test_presentation_requires_format_and_excludes_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Presentation shares require an explicit audience format and omit private notes."""
    from tests.design_factory import TEMPLATE_ID, make_design

    make_design(tmp_path / "workspace" / TEMPLATE_ID)
    store = Store(tmp_path / "workspace", tmp_path / "state" / "review.sqlite3")
    deck = store.create("2026-11-02-fictional-deck", "Fictional deck")
    slide = store.view(deck.id).slides[0]
    store.save_notes(deck.id, slide.id, "PRIVATE_NOTE_SENTINEL", slide.notes)
    sharing = SharingService(store, ArtifactStore(store))
    with pytest.raises(ValueError, match="Choose HTML or PDF"):
        sharing.prepare_artifact(deck.id)
    prepared = sharing.prepare_artifact(deck.id, "html")
    assert prepared.media_type == "text/html"
    assert b"PRIVATE_NOTE_SENTINEL" not in prepared.data
    assert b'data-export="audience"' in prepared.data

    def fake_pdf(html: str) -> bytes:
        """Stand in for the browser renderer while checking the sanitized print input."""
        assert "PRIVATE_NOTE_SENTINEL" not in html
        return b"%PDF-fake"

    monkeypatch.setattr("artifact_studio.sharing._pdf", fake_pdf)
    pdf = sharing.prepare_artifact(deck.id, "pdf")
    assert pdf.media_type == "application/pdf" and pdf.data == b"%PDF-fake"


def test_original_file_and_excluded_dependency(tmp_path: Path) -> None:
    """Share a single original file, while refusing a broken excluded dependency."""
    store = Store(tmp_path / "workspace", tmp_path / "state" / "review.sqlite3")
    artifacts = ArtifactStore(store)
    project = artifacts.create_project("Project")
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.html").write_text("<h1>One</h1>")
    artifact = artifacts.register_artifact(project.id, "One", source, "index.html")
    sharing = SharingService(store, artifacts)
    result = sharing.prepare_artifact(artifact.id)
    assert result.filename == "One.html" and result.data == b"<h1>One</h1>"
    (Path(artifact.source_path) / "index.html").write_text('<script src="config.json"></script>')
    (Path(artifact.source_path) / "config.json").write_text("{}")
    with pytest.raises(ValueError, match="references excluded"):
        sharing.prepare_artifact(artifact.id)


def test_project_with_duplicate_titles_uses_unique_folders(tmp_path: Path) -> None:
    """Package duplicate titled artifacts without filename collisions or dropping assets."""
    store = Store(tmp_path / "workspace", tmp_path / "state" / "review.sqlite3")
    artifacts = ArtifactStore(store)
    project = artifacts.create_project("Project")
    items: list[ArtifactView] = []
    for index in range(2):
        source = tmp_path / str(index)
        source.mkdir()
        (source / "index.html").write_text(str(index))
        items.append(artifacts.register_artifact(project.id, "Same title", source, "index.html"))
    result = SharingService(store, artifacts).prepare_project(project.id)
    with ZipFile(BytesIO(result.data)) as archive:
        assert {archive.read(f"{index:02d}-Same-title/Same-title.html") for index in (1, 2)} == {
            b"0",
            b"1",
        }
