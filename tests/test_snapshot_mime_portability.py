"""Saved artifact identity survives host MIME registry differences without hiding tampering."""

import mimetypes
import shutil
from pathlib import Path

import pytest

from artifact_studio.artifact_models import ArtifactFileInput, ArtifactManifest, ArtifactView
from artifact_studio.artifacts import ArtifactStore, artifact_revision_hash
from artifact_studio.libraries import Libraries
from artifact_studio.storage import ConflictError, Store
from artifact_studio.workspace_recovery import WorkspaceRecovery


@pytest.fixture
def artifact(tmp_path: Path) -> tuple[ArtifactStore, ArtifactView]:
    """Create approved fictional HTML with a JavaScript dependency on the first host."""
    store = Store(tmp_path / "original/collection", tmp_path / "original/state/review.sqlite3")
    registry = ArtifactStore(store)
    project = registry.create_project("Fictional portable MIME")
    source = tmp_path / "authored"
    source.mkdir()
    _ = (source / "index.html").write_text('<h1>Portable</h1><script src="app.js"></script>')
    _ = (source / "app.js").write_text("document.body.dataset.ready='yes';")
    view = registry.register_artifact(project.id, "Portable script", source, "index.html")
    _ = registry.approve(view.id, view.content_hash, True)
    return registry, registry.get_artifact(view.id)


def change_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Model a second host with a different valid JavaScript MIME association."""
    original = mimetypes.guess_type
    current = original("app.js")[0]
    replacement = (
        "application/x-javascript" if current != "application/x-javascript" else "text/javascript"
    )

    def guess_type(url: str, strict: bool = True) -> tuple[str | None, str | None]:
        return (replacement, None) if url.endswith(".js") else original(url, strict=strict)

    monkeypatch.setattr(mimetypes, "guess_type", guess_type)


def test_reads_keep_saved_identity_approval_and_manifest_bytes(
    artifact: tuple[ArtifactStore, ArtifactView],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, saved = artifact
    folder = Path(saved.source_path).parent
    before = {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    change_registry(monkeypatch)
    current = registry.get_artifact(saved.id)
    assert current.content_hash == saved.content_hash and current.revision == 1
    assert current.approved and current.files == saved.files
    assert registry.list_artifacts()[0].content_hash == saved.content_hash
    assert registry.get_artifact_revision(saved.id, 1).approved
    for revision in [None, 1]:
        assert (
            registry.snapshot_files(saved.id, revision)["app.js"] == before[folder / "files/app.js"]
        )
        view, selected = registry.verified_snapshot(saved.id, revision, paths="entrypoint")
        assert view.content_hash == saved.content_hash and list(selected) == ["index.html"]
    no_op = registry.revise_artifact(
        saved.id,
        [ArtifactFileInput(path="app.js", text=before[folder / "files/app.js"].decode())],
        saved.content_hash,
        "portable-noop-edit",
    )
    assert no_op.revision == 1 and no_op.approved and no_op.content_hash == saved.content_hash
    assert all(path.read_bytes() == contents for path, contents in before.items())


@pytest.mark.parametrize("mutation", ["change", "add", "remove"])
@pytest.mark.parametrize("selected", [False, True])
def test_changed_registry_does_not_hide_real_snapshot_tampering(
    artifact: tuple[ArtifactStore, ArtifactView],
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    selected: bool,
) -> None:
    registry, saved = artifact
    snapshot = Path(saved.source_path).parent / "revisions/1/files"
    change_registry(monkeypatch)
    if mutation == "change":
        _ = (snapshot / "app.js").write_text("different bytes")
    elif mutation == "add":
        _ = (snapshot / "added.js").write_text("new file")
    else:
        (snapshot / "app.js").unlink()
    with pytest.raises(ConflictError, match="immutable artifact snapshot"):
        _ = registry.verified_snapshot(saved.id, 1, paths="entrypoint" if selected else None)


@pytest.mark.parametrize("external", [False, True])
def test_real_edits_keep_known_mime_and_assign_current_mime_only_to_new_paths(
    artifact: tuple[ArtifactStore, ArtifactView],
    monkeypatch: pytest.MonkeyPatch,
    external: bool,
) -> None:
    registry, saved = artifact
    original_types = {file.path: file.mime_type for file in saved.files}
    change_registry(monkeypatch)
    if external:
        _ = (Path(saved.source_path) / "app.js").write_text("changed script")
        _ = (Path(saved.source_path) / "new.js").write_text("new script")
        changed = registry.get_artifact(saved.id)
    else:
        changed = registry.revise_artifact(
            saved.id,
            [
                ArtifactFileInput(path="app.js", text="changed script"),
                ArtifactFileInput(path="new.js", text="new script"),
            ],
            saved.content_hash,
            "portable-mime-edit",
        )
    assert changed.revision == 2 and changed.content_hash != saved.content_hash
    assert not changed.approved
    types = {file.path: file.mime_type for file in changed.files}
    assert types["app.js"] == original_types["app.js"]
    assert types["new.js"] == mimetypes.guess_type("new.js")[0]
    assert types["new.js"] != types["app.js"]
    assert registry.get_artifact(saved.id).revision == 2
    assert registry.snapshot_files(saved.id, 1)["app.js"] != b"changed script"
    assert registry.snapshot_files(saved.id)["app.js"] == b"changed script"


@pytest.mark.parametrize("full_archive", [False, True])
def test_recovery_opens_on_host_with_different_registry(
    artifact: tuple[ArtifactStore, ArtifactView],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    full_archive: bool,
) -> None:
    registry, saved = artifact
    recovery = WorkspaceRecovery(registry.store)
    if full_archive:
        source = tmp_path / "workspace.zip"
        _ = recovery.export_archive(source)
    else:
        source = tmp_path / "portable"
        source.mkdir()
        for file in Libraries(registry.store).snapshot_files():
            target = source / file.path
            target.parent.mkdir(parents=True, exist_ok=True)
            _ = shutil.copy2(file.local_path, target)
    change_registry(monkeypatch)
    destination = Store(
        tmp_path / "restored/collection", tmp_path / "restored/state/review.sqlite3"
    )
    restore = WorkspaceRecovery(destination)
    mode = "archive" if full_archive else "current"
    preview = restore.preview(source, mode=mode)
    assert preview.can_restore
    _ = restore.restore(source, preview.content_hash, mode=mode)
    restored = ArtifactStore(destination)
    view = restored.get_artifact(saved.id)
    assert view.content_hash == saved.content_hash and view.revision == saved.revision
    assert view.files == saved.files
    if full_archive:
        assert view.approved
    assert restored.snapshot_files(saved.id)["app.js"] == b"document.body.dataset.ready='yes';"


@pytest.mark.parametrize("field", ["mime_type", "sha256"])
@pytest.mark.parametrize("selected", [False, True])
def test_saved_inventory_tampering_without_new_identity_is_rejected(
    artifact: tuple[ArtifactStore, ArtifactView],
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    selected: bool,
) -> None:
    """Persisted interpretation and checksums remain bound to the saved content hash."""
    registry, saved = artifact
    change_registry(monkeypatch)
    files = [
        file.model_copy(
            update={field: "application/x-tampered" if field == "mime_type" else "0" * 64}
        )
        if file.path == "app.js"
        else file
        for file in saved.files
    ]
    # Change only stored inventory; retain its original content_hash and source bytes.
    manifest = ArtifactManifest.model_validate(
        saved.model_dump(include=set(ArtifactManifest.model_fields))
    )
    document = manifest.model_copy(update={"files": files}).model_dump_json()
    with registry.store.connect() as db:
        _ = db.execute("UPDATE artifact_items SET document=? WHERE id=?", (document, saved.id))
        _ = db.execute(
            "UPDATE artifact_revisions SET document=? WHERE artifact_id=? AND revision=1",
            (document, saved.id),
        )
    for revision in [None, 1]:
        with pytest.raises(ConflictError, match="immutable artifact snapshot"):
            _ = registry.verified_snapshot(
                saved.id, revision, paths="entrypoint" if selected else None
            )


def test_restore_retains_selected_mime_identity_and_clears_approval(
    artifact: tuple[ArtifactStore, ArtifactView],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restoring saved source preserves its identity while requiring a new approval."""
    registry, saved = artifact
    change_registry(monkeypatch)
    changed = registry.revise_artifact(
        saved.id,
        [ArtifactFileInput(path="app.js", text="changed script")],
        saved.content_hash,
        "portable-before-restore",
    )
    _ = registry.approve(saved.id, changed.content_hash, True)
    restored = registry.restore_artifact(
        saved.id,
        1,
        artifact_revision_hash(saved.id, changed.revision, changed.content_hash),
        "portable-mime-restore",
    )
    assert restored.revision == 3 and restored.content_hash == saved.content_hash
    assert restored.files == saved.files and not restored.approved
    assert registry.get_artifact(saved.id).revision == 3
    assert registry.snapshot_files(saved.id)["app.js"] == b"document.body.dataset.ready='yes';"
    assert registry.snapshot_files(saved.id, 2)["app.js"] == b"changed script"
