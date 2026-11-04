"""Reusable design tools preserve source ownership and reconcile deck overrides."""

import hashlib
from pathlib import Path
from typing import cast

import pytest

from artifact_studio import design_authoring
from artifact_studio.design_authoring import BASELINE, DesignAuthoring
from artifact_studio.design_library import DesignCreate, DesignLibrary
from artifact_studio.presentation_files import PresentationFiles
from artifact_studio.projects import audience_html
from artifact_studio.storage import ConflictError, Store


@pytest.fixture
def service(tmp_path: Path) -> DesignAuthoring:
    """Create a fictional pack through the actual authoring API."""
    service = DesignAuthoring(Store(tmp_path / "collection", tmp_path / "review.sqlite3"))
    _ = service.create("fictional", "Fictional", css=".slide{color:#123456}")
    return service


def write(service: DesignAuthoring, path: str, text: str) -> dict[str, object]:
    """Update an existing file from the version observed by the agent."""
    current = service.read_file("fictional", path)
    return service.write_file("fictional", path, text, str(current["expected_hash"]))


def test_scaffold_text_and_asset_authoring(service: DesignAuthoring) -> None:
    """One call registers a usable pack; tools write text and copy staged bytes compactly."""
    store = service.store
    pack = store.design("fictional")
    assert store.read(pack.folder_id).slides[0].id == "cover"
    assert service.read_file("fictional", "theme.css")["text"] == ".slide{color:#123456}"
    theme = service.read_file("fictional", "theme.css")
    result = service.write_file(
        "fictional", "theme.css", ".slide{color:red}", str(theme["expected_hash"])
    )
    assert "text" not in result
    with pytest.raises(ConflictError):
        service.write_file(
            "fictional", "theme.css", ".slide{color:blue}", str(theme["expected_hash"])
        )
    created = service.write_file("fictional", "assets/labels.json", '{"label":"Example"}')
    assert (
        service.read_file("fictional", "assets/labels.json")["expected_hash"]
        == created["expected_hash"]
    )
    folder = store.folder(pack.folder_id)
    source = folder / ".authoring-assets" / "sample.svg"
    assert source.parent.is_dir()
    _ = source.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    imported = service.import_asset("fictional", str(source), "assets/sample.svg")
    assert imported["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert "data_base64" not in imported
    deck = store.create("2026-11-04-created", "A deck", "fictional")
    assert (store.folder(deck.id) / BASELINE).is_file()
    assert "color:red" in audience_html(store, deck.id)
    assert service.sync_preview(deck.id)["changes"] == []


def test_existing_encoding_and_source_references(service: DesignAuthoring) -> None:
    """Reuse the existing design editor's UTF-16/newline and source-editing semantics."""
    pack = service.store.design("fictional")
    path = service.store.folder(pack.folder_id) / "reference" / "instructions.txt"
    path.parent.mkdir()
    _ = path.write_bytes("Original\r\n".encode("utf-16"))
    _ = write(service, "reference/instructions.txt", "Changed\n")
    assert path.read_bytes().decode("utf-16") == "Changed\r\n"


def test_new_pack_guidance_is_small_and_existing_brand_guidance_survives(
    service: DesignAuthoring,
) -> None:
    """Scaffolding omits shared workflow while retaining authored guidance in other packs/decks."""
    starter = service.read_file("fictional", "AGENT-GUIDE.md")
    assert len(str(starter["text"]).encode()) < 1_000
    assert 'get_authoring_guide(topic="workflow")' in str(starter["text"])
    brand_guidance = "Use the source's cobalt accent and preserve its diagram conventions."
    _ = service.write_file(
        "fictional", "AGENT-GUIDE.md", brand_guidance, str(starter["expected_hash"])
    )
    created = service.create("separate", "Separate brand")
    assert Path(str(created["import_directory"])).is_dir()
    assert service.read_file("fictional", "AGENT-GUIDE.md")["text"] == brand_guidance
    deck = service.store.create("2026-11-04-brand-guidance", "Brand guidance", "fictional")
    assert (service.store.folder(deck.id) / "AGENT-GUIDE.md").read_text() == brand_guidance


def test_design_sync_updates_multiple_decks_and_preserves_local_context(
    service: DesignAuthoring,
) -> None:
    """Edit the reusable design once; each selected deck receives the change without reauthoring."""
    store = service.store
    first = store.create("2026-11-04-first", "First", "fictional")
    second = store.create("2026-11-04-second", "Second", "fictional")
    _ = store.save_notes(first.id, "cover", "Human note", "")
    original = (store.folder(first.id) / "index.html").read_bytes()
    view = store.view(first.id).slides[0]
    store.approve(first.id, "cover", view.content_hash, True)
    _ = write(service, "theme.css", ".slide{color:#fedcba}")
    _ = service.write_file(
        "fictional", "assets/shape.svg", '<svg xmlns="http://www.w3.org/2000/svg"/>'
    )
    for deck in (first, second):
        preview = service.sync_preview(deck.id)
        assert preview["conflicts"] == []
        assert {item["action"] for item in cast(list[dict[str, str]], preview["changes"])} == {
            "update",
            "add",
        }
        result = service.sync(deck.id, str(preview["expected_hash"]))
        assert result["updated_files"] == 2
        assert "color:#fedcba" in audience_html(store, deck.id)
        assert service.sync_preview(deck.id)["changes"] == []
        with pytest.raises(ConflictError, match="Preview"):
            service.sync(deck.id, str(preview["expected_hash"]))
    assert (store.folder(first.id) / "index.html").read_bytes() == original
    assert store.read(first.id).slides[0].notes == "Human note"
    assert not store.view(first.id).slides[0].approved


def test_sync_overrides_legacy_and_concurrency(service: DesignAuthoring) -> None:
    """Distinguish unrelated overrides from conflicts and never overwrite a stale preview."""
    store = service.store
    deck = store.create("2026-11-04-overrides", "Overrides", "fictional")
    files = PresentationFiles(store)
    _ = files.update_theme(
        deck.id, ".slide{color:green}", str(files.theme(deck.id)["expected_hash"])
    )
    preview = service.sync_preview(deck.id)
    assert preview["changes"] == [] and preview["preserved_overrides"] == ["theme.css"]
    _ = write(service, "theme.css", ".slide{color:blue}")
    with pytest.raises(ConflictError, match="Preview"):
        service.sync(deck.id, str(preview["expected_hash"]))
    preview = service.sync_preview(deck.id)
    assert preview["conflicts"] == ["theme.css"]
    with pytest.raises(ConflictError, match="overrides"):
        service.sync(deck.id, str(preview["expected_hash"]))
    _ = service.sync(deck.id, str(preview["expected_hash"]), overwrite=True)
    assert files.theme(deck.id)["css"] == ".slide{color:blue}"
    (store.folder(deck.id) / BASELINE).unlink()
    _ = write(service, "theme.css", ".slide{color:purple}")
    assert service.sync_preview(deck.id)["conflicts"] == ["theme.css"]


def test_sync_preserves_local_files_and_detects_deleted_assets(service: DesignAuthoring) -> None:
    """A local deletion or new same-path asset remains an override when its design changes."""
    _ = service.write_file("fictional", "assets/logo.svg", "original")
    store = service.store
    deck = store.create("2026-11-04-deleted", "Deleted", "fictional")
    folder = store.folder(deck.id)
    (folder / "assets/logo.svg").unlink()
    _ = (folder / "assets/extra.svg").write_text("deck only")
    assert service.sync_preview(deck.id)["preserved_overrides"] == ["assets/logo.svg"]
    _ = write(service, "assets/logo.svg", "new logo")
    preview = service.sync_preview(deck.id)
    assert preview["conflicts"] == ["assets/logo.svg"]
    assert preview["preserved_deck_files"] == ["assets/extra.svg"]
    with pytest.raises(ConflictError):
        service.sync(deck.id, str(preview["expected_hash"]))
    _ = service.sync(deck.id, str(preview["expected_hash"]), overwrite=True)
    assert (folder / "assets/extra.svg").read_text() == "deck only"
    _ = service.write_file("fictional", "assets/extra.svg", "new design asset")
    assert service.sync_preview(deck.id)["conflicts"] == ["assets/extra.svg"]


def test_authoring_boundaries(service: DesignAuthoring, tmp_path: Path) -> None:
    """Keep managed identity, hidden state, symlinks and private sources outside writes."""
    for path in (
        "../secret",
        "assets/../theme.css",
        "index.html",
        "design.json",
        ".secret",
        "assets/secrets.json",
    ):
        with pytest.raises(ValueError):
            service.write_file("fictional", path, "bad")
    private = tmp_path / "private.txt"
    _ = private.write_text("Private")
    with pytest.raises(ValueError, match="sources"):
        service.import_asset("fictional", str(private), "assets/private.txt")
    folder = service.store.folder(service.store.design("fictional").folder_id)
    (folder / "assets/link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        service.write_file("fictional", "assets/link/private.txt", "bad")
    with pytest.raises(ValueError, match="symlink"):
        service.store.create("2026-11-04-invalid", "Invalid", "fictional")
    assert not service.store.folder("2026-11-04-invalid").exists()
    with pytest.raises(ConflictError):
        service.create("fictional", "Duplicate")
    reference = DesignLibrary(service.store).create(DesignCreate(name="References"))
    with pytest.raises(ConflictError):
        service.create(reference.id, "Duplicate reference ID")


def test_failed_baseline_does_not_strand_creation(
    service: DesignAuthoring, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new baseline failure leaves no deck or creation receipt, so the same request can retry."""

    def fail(_store: Store, _deck_id: str) -> None:
        raise OSError("Disk failure")

    with monkeypatch.context() as scope:
        scope.setattr(design_authoring, "record_design_baseline", fail)
        with pytest.raises(OSError):
            service.store.create(
                "2026-11-04-retry", "Retry", "fictional", request_id="retry-design-1"
            )
    assert not service.store.folder("2026-11-04-retry").exists()
    assert (
        service.store.create(
            "2026-11-04-retry", "Retry", "fictional", request_id="retry-design-1"
        ).id
        == "2026-11-04-retry"
    )


def test_failed_sync_restores_bytes_metadata_and_approval(
    service: DesignAuthoring, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A late write failure restores the original files, their mtimes and approval identity."""
    _ = service.write_file("fictional", "assets/a.svg", "original a")
    _ = service.write_file("fictional", "assets/b.svg", "original b")
    store = service.store
    deck = store.create("2026-11-04-rollback", "Rollback", "fictional")
    view = store.view(deck.id).slides[0]
    store.approve(deck.id, view.id, view.content_hash, True)
    before = store.visual_hash(deck.id)
    _ = write(service, "assets/a.svg", "new a")
    _ = write(service, "assets/b.svg", "new b")
    preview = service.sync_preview(deck.id)
    replace = Path.replace

    def failing_replace(path: Path, target: str | Path) -> Path:
        if "new" in path.parts and path.name == "b.svg":
            raise OSError("Write failure")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", failing_replace)
    with pytest.raises(OSError, match="Write failure"):
        service.sync(deck.id, str(preview["expected_hash"]))
    assert store.visual_hash(deck.id) == before
    assert store.view(deck.id).slides[0].approved
    assert service.sync_preview(deck.id)["expected_hash"] == preview["expected_hash"]
