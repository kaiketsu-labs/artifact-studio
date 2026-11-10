"""Preserve audience exports while writing each owned project member only once."""

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest

from artifact_studio import sharing
from artifact_studio.artifact_models import ArtifactView
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.sharing import ShareFormat, SharingService
from artifact_studio.storage import ConflictError, Store
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Store, ArtifactStore, str]:
    """Use only fictional files in an isolated collection and database."""
    store = Store(tmp_path / "workspace", tmp_path / "state" / "review.sqlite3")
    artifacts = ArtifactStore(store)
    return store, artifacts, artifacts.create_project("Fictional / project").id


def _register(
    artifacts: ArtifactStore,
    project_id: str,
    source: Path,
    files: dict[str, bytes],
    *,
    title: str = "Same / title",
    entrypoint: str = "index.html",
) -> ArtifactView:
    """Register a small fictional owned bundle, including any private test files."""
    for name, data in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(data)
    return artifacts.register_artifact(project_id, title, source, entrypoint)


def _ordered(
    monkeypatch: pytest.MonkeyPatch, artifacts: ArtifactStore, items: list[ArtifactView]
) -> None:
    """Make artifact order explicit without depending on randomly allocated IDs."""

    def listed(_project_id: str) -> list[ArtifactView]:
        return items

    monkeypatch.setattr(artifacts, "list_artifacts", listed)


@pytest.mark.parametrize("format", [None, "html", "pdf"])
def test_owned_members_keep_paths_bytes_and_metadata(
    workspace: tuple[Store, ArtifactStore, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    format: ShareFormat | None,
) -> None:
    """Owned formats, duplicate titles, exclusions and original ZIPs remain unchanged."""
    store, artifacts, project_id = workspace
    original_zip = BytesIO()
    with ZipFile(original_zip, "w") as archive:
        archive.writestr("../opaque.txt", b"Original uploaded content")
    single = _register(
        artifacts,
        project_id,
        tmp_path / "single",
        {"nested/home.htm": b"<h1>Single</h1>", ".private": b"Private"},
        entrypoint="nested/home.htm",
    )
    bundle = _register(
        artifacts,
        project_id,
        tmp_path / "bundle",
        {
            "index.html": b'<script src="assets/app.js"></script>',
            "assets/app.js": b"window.fictional = 42",
            "notes/private.txt": b"Private notes",
            "config.json": b"{}",
        },
    )
    uploaded = _register(
        artifacts,
        project_id,
        tmp_path / "uploaded",
        {"original.zip": original_zip.getvalue()},
        title="Uploaded",
        entrypoint="original.zip",
    )
    items = [single, bundle, uploaded]
    _ordered(monkeypatch, artifacts, items)
    result = SharingService(store, artifacts).prepare_project(project_id, format)
    expected = {
        "01-Same-title/Same-title.htm": b"<h1>Single</h1>",
        "02-Same-title/assets/app.js": b"window.fictional = 42",
        "02-Same-title/index.html": b'<script src="assets/app.js"></script>',
        "03-Uploaded/Uploaded.zip": original_zip.getvalue(),
    }
    with ZipFile(BytesIO(result.data)) as archive:
        assert archive.namelist() == list(expected)
        assert {name: archive.read(name) for name in archive.namelist()} == expected
    assert result.filename == "Fictional-project.zip"
    assert result.media_type == "application/zip"
    assert result.bundle is False
    assert result.revisions == tuple((item.id, item.revision) for item in items)
    assert result.excluded == (
        "01-Same-title/.private",
        "02-Same-title/config.json",
        "02-Same-title/notes/private.txt",
    )


def test_each_artifact_is_written_once_before_capturing_the_next(
    workspace: tuple[Store, ArtifactStore, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Count compression inputs and snapshot boundaries to catch nested ZIP regressions."""
    store, artifacts, project_id = workspace
    contents = {"index.html": b"<h1>Fictional</h1>", "asset.bin": bytes(range(256))}
    items = [
        _register(artifacts, project_id, tmp_path / str(index), contents) for index in range(2)
    ]
    _ordered(monkeypatch, artifacts, items)
    snapshot = artifacts.snapshot_files
    write = ZipFile.writestr
    events: list[str] = []
    compressed_bytes: list[int] = []
    archive_ids: set[int] = set()

    def counted_snapshot(artifact_id: str, revision: int | None = None) -> dict[str, bytes]:
        events.append(f"snapshot:{artifact_id}:{revision}")
        return snapshot(artifact_id, revision)

    def counted_write(
        archive: ZipFile,
        name: str | ZipInfo,
        data: str | bytes,
        compress_type: int | None = None,
        compresslevel: int | None = None,
    ) -> None:
        assert isinstance(name, str)
        events.append(f"write:{name}")
        compressed_bytes.append(len(data))
        archive_ids.add(id(archive))
        write(archive, name, data, compress_type, compresslevel)

    monkeypatch.setattr(artifacts, "snapshot_files", counted_snapshot)
    monkeypatch.setattr(ZipFile, "writestr", counted_write)
    result = SharingService(store, artifacts).prepare_project(project_id)
    assert events == [
        f"snapshot:{items[0].id}:1",
        "write:01-Same-title/asset.bin",
        "write:01-Same-title/index.html",
        f"snapshot:{items[1].id}:1",
        "write:02-Same-title/asset.bin",
        "write:02-Same-title/index.html",
    ]
    assert len(archive_ids) == 1
    assert sum(compressed_bytes) == 2 * sum(map(len, contents.values()))
    with ZipFile(BytesIO(result.data)) as archive:
        assert len(archive.namelist()) == 4


def test_large_project_preserves_member_order_and_metadata_order(
    workspace: tuple[Store, ArtifactStore, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lexical ZIP ordering above 99 artifacts must not reorder provenance or exclusions."""
    store, artifacts, project_id = workspace
    item = _register(
        artifacts,
        project_id,
        tmp_path / "source",
        {"index.html": b"Original", "config.json": b"{}"},
    )
    items = [item.model_copy(update={"id": f"fixture-{index}"}) for index in range(101)]
    _ordered(monkeypatch, artifacts, items)

    def snapshot(_artifact_id: str, _revision: int | None = None) -> dict[str, bytes]:
        return {"index.html": b"Original", "config.json": b"{}"}

    monkeypatch.setattr(artifacts, "snapshot_files", snapshot)
    result = SharingService(store, artifacts).prepare_project(project_id)
    folders = [f"{index:02d}-Same-title" for index in range(1, 102)]
    with ZipFile(BytesIO(result.data)) as archive:
        assert archive.namelist() == sorted(f"{folder}/Same-title.html" for folder in folders)
    assert result.revisions == tuple((artifact.id, 1) for artifact in items)
    assert result.excluded == tuple(f"{folder}/config.json" for folder in folders)


def test_export_keeps_the_listed_revision_when_live_files_change(
    workspace: tuple[Store, ArtifactStore, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Export bytes and provenance stay pinned even if the live mirror advances mid-export."""
    store, artifacts, project_id = workspace
    artifact = _register(artifacts, project_id, tmp_path / "source", {"index.html": b"Original"})
    snapshot = artifacts.snapshot_files

    def changed_snapshot(artifact_id: str, revision: int | None = None) -> dict[str, bytes]:
        assert (artifact_id, revision) == (artifact.id, 1)
        _ = (Path(artifact.source_path) / "index.html").write_bytes(b"Changed")
        assert artifacts.get_artifact(artifact_id).revision == 2
        return snapshot(artifact_id, revision)

    monkeypatch.setattr(artifacts, "snapshot_files", changed_snapshot)
    result = SharingService(store, artifacts).prepare_project(project_id)
    assert result.revisions == ((artifact.id, 1),)
    with ZipFile(BytesIO(result.data)) as archive:
        assert archive.read("01-Same-title/Same-title.html") == b"Original"


@pytest.mark.parametrize("private_path", ["config.json", "notes/private.txt", ".secret"])
def test_referenced_private_files_reject_the_project(
    workspace: tuple[Store, ArtifactStore, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    private_path: str,
) -> None:
    """Reject unsafe references even after an earlier artifact has been written."""
    store, artifacts, project_id = workspace
    first = _register(artifacts, project_id, tmp_path / "first", {"index.html": b"Safe"})
    second = _register(
        artifacts,
        project_id,
        tmp_path / "second",
        {"index.html": f'<a href="{private_path}">Private</a>'.encode(), private_path: b"Secret"},
    )
    _ordered(monkeypatch, artifacts, [first, second])
    with pytest.raises(ValueError, match="references excluded private metadata"):
        SharingService(store, artifacts).prepare_project(project_id)


def test_private_entrypoint_rejects_the_project(
    workspace: tuple[Store, ArtifactStore, str], tmp_path: Path
) -> None:
    """An excluded entrypoint cannot produce a misleading audience archive."""
    store, artifacts, project_id = workspace
    _ = _register(
        artifacts,
        project_id,
        tmp_path / "source",
        {".private.html": b"Private", "asset.txt": b"Public"},
        entrypoint=".private.html",
    )
    with pytest.raises(ValueError, match="entrypoint is not an audience-shareable"):
        SharingService(store, artifacts).prepare_project(project_id)


@pytest.fixture
def presentation(
    workspace: tuple[Store, ArtifactStore, str],
) -> tuple[Store, ArtifactStore, str, str]:
    """Add a presentation with private notes to the fictional project."""
    store, artifacts, project_id = workspace
    make_design(store.root / TEMPLATE_ID)
    deck = store.create("2026-11-02-fictional-deck", "Fictional deck")
    slide = store.view(deck.id).slides[0]
    store.save_notes(deck.id, slide.id, "PRIVATE_NOTE_SENTINEL", slide.notes)
    _ = artifacts.set_artifact_project(deck.id, project_id)
    return store, artifacts, project_id, deck.id


@pytest.mark.parametrize("format", ["html", "pdf"])
def test_project_presentation_exports_keep_audience_format(
    presentation: tuple[Store, ArtifactStore, str, str],
    monkeypatch: pytest.MonkeyPatch,
    format: ShareFormat,
) -> None:
    """Use the existing HTML and PDF presentation paths without leaking private notes."""
    store, artifacts, project_id, deck_id = presentation
    pdf_inputs: list[str] = []
    renderer_checks: list[bool] = []

    def fake_pdf(document: str) -> bytes:
        pdf_inputs.append(document)
        assert "PRIVATE_NOTE_SENTINEL" not in document
        return b"%PDF-fictional"

    def check_renderer() -> None:
        renderer_checks.append(True)

    monkeypatch.setattr(sharing, "_pdf", fake_pdf)
    monkeypatch.setattr(sharing, "check_renderer_available", check_renderer)
    artifact = artifacts.get_artifact(deck_id)
    result = SharingService(store, artifacts).prepare_project(project_id, format)
    assert result.revisions == ((deck_id, artifact.revision),)
    with ZipFile(BytesIO(result.data)) as archive:
        filename = f"01-Fictional-deck/Fictional-deck.{format}"
        assert archive.namelist() == [filename]
        data = archive.read(filename)
    if format == "pdf":
        assert data == b"%PDF-fictional"
        assert len(pdf_inputs) == 1
        assert renderer_checks == [True]
    else:
        assert b'data-export="audience"' in data
        assert b"PRIVATE_NOTE_SENTINEL" not in data
        assert pdf_inputs == []
        assert renderer_checks == []


def test_project_presentation_requires_explicit_format(
    presentation: tuple[Store, ArtifactStore, str, str],
) -> None:
    """An omitted project format still requires choosing HTML or PDF for presentations."""
    store, artifacts, project_id, _ = presentation
    with pytest.raises(ValueError, match="Choose HTML or PDF"):
        SharingService(store, artifacts).prepare_project(project_id)


@pytest.mark.parametrize("format", ["html", "pdf"])
def test_project_rejects_stale_presentation_before_pdf_render(
    presentation: tuple[Store, ArtifactStore, str, str],
    monkeypatch: pytest.MonkeyPatch,
    format: ShareFormat,
) -> None:
    """A presentation changed during capture cannot return an export of a stale revision."""
    store, artifacts, project_id, deck_id = presentation
    audience_html = sharing.audience_html

    def changed_html(
        store: Store, deck_id: str, *, printing: bool = False, include_hidden: bool = True
    ) -> str:
        document = audience_html(store, deck_id, printing=printing, include_hidden=include_hidden)
        theme = store.folder(deck_id) / "theme.css"
        _ = theme.write_text(theme.read_text() + "\n/* Changed during capture */\n")
        return document

    def unexpected_pdf(_document: str) -> bytes:
        pytest.fail("Stale presentations must be rejected before PDF rendering")

    def check_renderer() -> None:
        pass

    monkeypatch.setattr(sharing, "audience_html", changed_html)
    monkeypatch.setattr(sharing, "_pdf", unexpected_pdf)
    monkeypatch.setattr(sharing, "check_renderer_available", check_renderer)
    with pytest.raises(ConflictError, match="Presentation changed while preparing"):
        SharingService(store, artifacts).prepare_project(project_id, format)
    assert artifacts.get_artifact(deck_id).legacy_deck_id == deck_id
