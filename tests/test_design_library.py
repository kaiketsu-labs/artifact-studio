"""Design contexts are reusable file collections, not synthetic presentations."""

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from artifact_studio import app as service
from artifact_studio.artifact_context import artifact_context
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.design_library import DesignCreate, DesignDelete, DesignLibrary, DesignTextEdit
from artifact_studio.design_references import ReferenceInput, RemoveReferenceInput
from artifact_studio.models import DesignManifest, FileInput
from artifact_studio.storage import ConflictError, Store
from tests.design_factory import TEMPLATE_ID, make_design


def upload(name: str, data: bytes = b"Fictional reference") -> FileInput:
    return FileInput(name=name, data_base64=base64.b64encode(data).decode())


@pytest.fixture
def library(tmp_path: Path) -> DesignLibrary:
    return DesignLibrary(Store(tmp_path / "collection", tmp_path / "state/review.sqlite3"))


def test_file_collection_needs_no_slides_and_matches_agent_context(library: DesignLibrary) -> None:
    item = library.create(
        DesignCreate(name="Editorial", files=[upload("layout.fig"), upload("rules.pdf")])
    )
    assert item.kind == "references" and item.file_count == 2
    assert not (library.store.folder(item.folder_id) / "index.html").exists()
    assert not ArtifactStore(library.store).list_artifacts()
    assert not library.store.designs() and library.store.default_design_id() == ""
    context = artifact_context(library.store, design_id=item.id, record_delivery=True)
    assert context["design_context"] == item.model_dump()
    sources = TypeAdapter(list[dict[str, str]]).validate_python(context["sources"])
    assert any(source["delivery"] == "pointer returned" for source in sources)
    assert library.file(item.id, item.files[0].path).read_bytes() == b"Fictional reference"
    with pytest.raises((ValueError, FileNotFoundError)):
        library.file(item.id, "../private.txt")


def test_exact_context_removal_and_changed_design_guard(library: DesignLibrary) -> None:
    item = library.create(DesignCreate(name="Brand", files=[upload("brand.pdf")]))
    updated = library.attach(item.id, ReferenceInput(files=[upload("palette.csv")]))
    assert updated.file_count == 2
    with pytest.raises(ConflictError):
        library.delete(item.id, DesignDelete(expected_version=item.version))
    file = next(file for file in updated.files if file.name == "brand.pdf")
    final = library.remove_file(
        item.id, RemoveReferenceInput(path=file.path, expected_version=file.version)
    )
    assert [file.name for file in final.files] == ["palette.csv"]
    assert not Path(file.local_path).exists()
    assert "brand.pdf" not in str(
        artifact_context(library.store, design_id=item.id)["design_context"]
    )


def test_deleting_a_pack_keeps_work_and_never_turns_examples_into_artifacts(
    library: DesignLibrary,
) -> None:
    make_design(library.store.root / TEMPLATE_ID)
    deck = library.store.create("2026-11-02-real-work", "Actual work", "test-brand")
    source = (library.store.folder(deck.id) / "index.html").read_bytes()
    pack = library.get("test-brand")
    assert {file.group for file in pack.files} >= {"References", "Guidance", "Examples & player"}
    assert library.list()[0].is_default
    library.delete(pack.id, DesignDelete(expected_version=pack.version))
    assert not library.list() and not library.store.designs()
    assert library.store.default_design_id() == ""
    assert len(ArtifactStore(library.store).list_artifacts()) == 1
    assert (library.store.folder(deck.id) / "index.html").read_bytes() == source
    assert (library.store.folder(TEMPLATE_ID) / "Master.pptx").exists()
    assert library.deleted()[0].id == pack.id
    restored = library.restore(pack.id)
    assert restored.is_default and library.store.design(pack.id).folder_id == TEMPLATE_ID


def test_invalid_batches_publish_nothing(library: DesignLibrary) -> None:
    with pytest.raises(ValueError):
        library.create(DesignCreate(name="Broken", files=[upload("ok.pdf"), upload("bad.pptx")]))
    assert not library.list()
    item = library.create(DesignCreate(name="Empty"))
    with pytest.raises(ValueError):
        library.attach(item.id, ReferenceInput(files=[upload("ok.pdf"), upload("bad.pptx")]))
    assert not library.get(item.id).files


def test_changed_or_symlinked_sources_cannot_be_removed_with_old_tokens(
    library: DesignLibrary, tmp_path: Path
) -> None:
    item = library.create(DesignCreate(name="Sources", files=[upload("guide.pdf")]))
    source = item.files[0]
    target = Path(source.local_path)
    _ = target.write_bytes(b"New content from another editor")
    with pytest.raises(ConflictError):
        library.remove_file(
            item.id, RemoveReferenceInput(path=source.path, expected_version=source.version)
        )
    private = tmp_path / "private.pdf"
    _ = private.write_bytes(b"Outside collection")
    target.unlink()
    target.symlink_to(private)
    assert not library.get(item.id).files
    with pytest.raises(FileNotFoundError):
        library.file(item.id, source.path)
    assert private.read_bytes() == b"Outside collection"


def test_reference_collection_id_cannot_be_reused_by_a_slide_pack(library: DesignLibrary) -> None:
    item = library.create(DesignCreate(name="Sources"))
    make_design(library.store.root / TEMPLATE_ID)
    with pytest.raises(ConflictError, match="reference collection"):
        library.store.register_design(
            TEMPLATE_ID, DesignManifest(id=item.id, name="Duplicate", description="Duplicate ID")
        )


def test_http_and_mcp_design_context_are_the_same_inert_files(
    library: DesignLibrary, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "store", library.store)
    client = TestClient(service.app, base_url="http://127.0.0.1")
    result = client.post(
        "/api/design-library",
        json=DesignCreate(
            name="Web references",
            files=[upload("reference.html", b"<script>window.bad=true</script>")],
        ).model_dump(),
    )
    assert result.status_code == 200, result.text
    item = result.json()
    assert service.get_design_context(item["id"]).model_dump() == item
    assert service.get_working_context(design_id=item["id"])["design_context"] == item
    download = client.get(item["files"][0]["url"])
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/octet-stream"
    assert "attachment" in download.headers["content-disposition"]
    assert (
        client.post(
            f"/api/design-library/{item['id']}/delete", json={"expected_version": item["version"]}
        ).status_code
        == 200
    )
    assert client.get(f"/api/design-library/{item['id']}").status_code == 404
    assert not client.get("/api/hub").json()["designs"]
    assert client.post(f"/api/design-library/{item['id']}/restore", json={}).status_code == 200


def test_design_preview_serves_only_inert_images(
    library: DesignLibrary, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "store", library.store)
    item = library.create(
        DesignCreate(
            name="Brand",
            files=[
                upload("preview.svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>'),
                upload("brief.md"),
            ],
        )
    )
    client = TestClient(service.app, base_url="http://127.0.0.1")
    response = client.get(item.preview_url)
    assert response.status_code == 200 and response.headers["content-type"].startswith(
        "image/svg+xml"
    )
    assert "sandbox; default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    brief = next(file for file in item.files if file.name == "brief.md")
    assert client.get(f"/api/design-library/{item.id}/image/{brief.path}").status_code == 400


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16-le", "utf-16-be"])
def test_text_saves_preserve_encoding_newlines_and_current_agent_inventory(
    library: DesignLibrary, encoding: str
) -> None:
    """Write canonical source bytes while retaining Unicode, BOMs and Windows line endings."""
    prefix = "\ufeff" if encoding.startswith("utf-16") else ""
    original = (prefix + "# Before\r\nGrüße\r\n").encode(encoding)
    item = library.create(DesignCreate(name="Editable", files=[upload("guide.md", original)]))
    source = item.files[0]
    body = DesignTextEdit(path=source.path, expected_hash=source.sha256, text="# After\nMünchen\n")
    saved = library.edit_text(item.id, body)
    assert Path(source.local_path).read_bytes() == (prefix + "# After\r\nMünchen\r\n").encode(
        encoding
    )
    assert saved.files[0].sha256 != source.sha256 and saved.version != item.version
    assert (
        artifact_context(library.store, design_id=item.id)["design_context"] == saved.model_dump()
    )
    with pytest.raises(ConflictError):
        library.edit_text(item.id, body)
    unchanged = library.edit_text(
        item.id, body.model_copy(update={"expected_hash": saved.files[0].sha256})
    )
    assert unchanged.version == saved.version


def test_text_editing_rejects_stale_binary_partial_and_unlisted_sources(
    library: DesignLibrary, tmp_path: Path
) -> None:
    """Never overwrite a newer file, a partial preview, binary media or an escaped path."""
    item = library.create(DesignCreate(name="Protected", files=[upload("guide.md", b"Original")]))
    source = item.files[0]
    body = DesignTextEdit(path=source.path, text="Human draft", expected_hash=source.sha256)
    target = Path(source.local_path)
    _ = target.write_text("Newer agent edit")
    with pytest.raises(ConflictError):
        library.edit_text(item.id, body)
    assert target.read_text() == "Newer agent edit"
    for content in [b"\x00binary", b"\xffbroken", b"x" * 1_048_577]:
        _ = target.write_bytes(content)
        current = library.get(item.id).files[0]
        with pytest.raises(ValueError):
            library.edit_text(item.id, body.model_copy(update={"expected_hash": current.sha256}))
        assert target.read_bytes() == content
    with pytest.raises(FileNotFoundError):
        library.edit_text(item.id, body.model_copy(update={"path": "../private.md"}))
    private = tmp_path / "private.md"
    _ = private.write_text("Keep private")
    target.unlink()
    target.symlink_to(private)
    with pytest.raises(FileNotFoundError):
        library.edit_text(item.id, body)
    assert private.read_text() == "Keep private"


def test_pack_text_edit_http_is_canonical_and_conflict_aware(
    library: DesignLibrary, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Edit guidance and runtime text through the same source inventory used by agents."""
    make_design(library.store.root / TEMPLATE_ID)
    monkeypatch.setattr(service, "store", library.store)
    client = TestClient(service.app, base_url="http://127.0.0.1")
    item = library.get("test-brand")
    for source in [
        file for file in item.files if file.name in {"README.md", "theme.css", "index.html"}
    ]:
        original = Path(source.local_path).read_text()
        body = DesignTextEdit(path=source.path, expected_hash=source.sha256, text=original + "\n")
        response = client.post(f"/api/design-library/{item.id}/files/edit", json=body.model_dump())
        assert response.status_code == 200, response.text
        assert client.get(source.url).content == (original + "\n").encode()
        assert (
            client.post(
                f"/api/design-library/{item.id}/files/edit", json=body.model_dump()
            ).status_code
            == 409
        )
