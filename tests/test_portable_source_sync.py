"""Exercise cross-computer metadata migration without granting stale upload ownership."""

import json
from pathlib import Path

import pytest

from artifact_studio import libraries as module
from artifact_studio import workspace_recovery as recovery_module
from artifact_studio.artifact_models import ArtifactFileInput, ArtifactManifest
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.libraries import Libraries, command
from artifact_studio.storage import Store
from artifact_studio.workspace_recovery import PORTABLE_METADATA, WorkspaceRecovery
from tests.test_libraries import library_setup as library_setup  # noqa: F401
from tests.test_library_sync_conflicts import register, remote_file
from tests.test_sync_checkpoint_recovery import checkpoint


def owned_workspace(libraries: Libraries, tmp_path: Path) -> tuple[str, str]:
    """Include authored JSON whose path-shaped fields must remain byte-for-byte intact."""
    artifacts = ArtifactStore(libraries.store)
    project = artifacts.create_project("Shared project", "Authoritative shared brief")
    bundle = tmp_path / "authored"
    bundle.mkdir()
    _ = (bundle / "index.html").write_text("<h1>Shared content</h1>")
    _ = (bundle / "data.json").write_text(
        '{"source_path":"C:\\\\fictional\\\\source", "context":"Keep authored data"}'
    )
    artifact = artifacts.register_artifact(project.id, "Shared page", bundle, "index.html")
    _ = artifacts.approve(artifact.id, artifact.content_hash, True)
    return project.id, artifact.id


def restore_replica(libraries: Libraries, identifier: str, tmp_path: Path) -> Libraries:
    """Use the real current-source recovery path and a second independent database."""
    replica = Libraries(Store(tmp_path / "replica", tmp_path / "replica-state/review.sqlite3"))
    source = Path(libraries.get(identifier).path)
    recovery = WorkspaceRecovery(replica.store)
    preview = recovery.preview(source)
    assert preview.can_restore
    _ = recovery.restore(source, preview.content_hash)
    return Libraries(replica.store)


def test_restored_replica_publishes_identical_portable_metadata_without_touching_local_state(
    library_setup: tuple[Libraries, Path],
    tmp_path: Path,
) -> None:
    libraries, remote = library_setup
    project_id, artifact_id = owned_workspace(libraries, tmp_path)
    identifier = register(libraries)
    local = libraries.store.root
    before = {path: path.read_bytes() for path in local.glob("projects/**/*.json")}
    artifact = ArtifactStore(libraries.store).get_artifact(artifact_id)
    first = libraries.sync(identifier)
    assert all(path.read_bytes() == content for path, content in before.items())
    assert ArtifactStore(libraries.store).get_artifact(artifact_id).approved
    project_path = f"projects/{project_id}/project.json"
    artifact_path = f"projects/{project_id}/artifacts/{artifact_id}/artifact.json"
    assert json.loads(remote_file(remote, project_path)) == {
        "id": project_id,
        "title": "Shared project",
        "legacy": False,
    }
    manifest = ArtifactManifest.model_validate_json(remote_file(remote, artifact_path))
    assert manifest.original_source_path == ""
    assert manifest.content_hash == artifact.content_hash and manifest.revision == artifact.revision
    assert "source_root" not in json.loads(remote_file(remote, PORTABLE_METADATA))
    assert (
        remote_file(remote, artifact_path.replace("artifact.json", "files/data.json"))
        == (local / artifact_path.replace("artifact.json", "files/data.json")).read_text()
    )
    replica = restore_replica(libraries, identifier, tmp_path)
    second_id = register(replica)
    second = replica.sync(second_id, automatic=True)
    assert second.commit == first.commit and second.files_changed == 0
    assert checkpoint(replica, second_id).confirmed.commit == first.commit
    restored_project = next(
        item for item in ArtifactStore(replica.store).list_projects() if item.id == project_id
    )
    assert restored_project.context == "Authoritative shared brief"


def test_legacy_host_paths_migrate_without_baseline_and_preserve_pending_commit_receipts(
    library_setup: tuple[Libraries, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    libraries, remote = library_setup
    project_id, artifact_id = owned_workspace(libraries, tmp_path)
    identifier = register(libraries)
    normalize = recovery_module.portable_source_metadata

    def legacy_metadata(_relative: str, _source: Path) -> None:
        """Model the previous release's raw metadata transport."""

    monkeypatch.setattr(recovery_module, "portable_source_metadata", legacy_metadata)
    legacy = libraries.sync(identifier)
    monkeypatch.setattr(recovery_module, "portable_source_metadata", normalize)
    replica = restore_replica(libraries, identifier, tmp_path)
    second_id = register(replica)
    project_file = replica.store.root / f"projects/{project_id}/project.json"
    assert str(libraries.store.root) not in project_file.read_text()
    run = module.command

    def lost_ack(*args: str, cwd: Path | None = None) -> str:
        result = run(*args, cwd=cwd)
        if "push" in args:
            raise ValueError("Acknowledgement lost after metadata migration")
        return result

    monkeypatch.setattr(module, "command", lost_ack)
    with pytest.raises(ValueError, match="Acknowledgement lost"):
        _ = replica.sync(second_id)
    prepared = checkpoint(replica, second_id).pending
    assert prepared is not None and prepared.base_commit == legacy.commit
    assert prepared.commit != legacy.commit
    monkeypatch.setattr(module, "command", run)
    retry = Libraries(replica.store).sync(second_id)
    assert retry.commit == prepared.commit and retry.files_changed == 0
    assert checkpoint(replica, second_id).pending is None
    assert libraries.sync(identifier).commit == retry.commit
    assert ArtifactStore(libraries.store).get_artifact(artifact_id).approved
    assert "source_path" not in json.loads(
        remote_file(remote, f"projects/{project_id}/project.json")
    )


@pytest.mark.parametrize("changed", ["title", "brief", "unknown", "source"])
def test_unknown_replica_still_rejects_real_changes_including_authored_path_fields(
    library_setup: tuple[Libraries, Path],
    tmp_path: Path,
    changed: str,
) -> None:
    libraries, remote = library_setup
    project_id, artifact_id = owned_workspace(libraries, tmp_path)
    identifier = register(libraries)
    first = libraries.sync(identifier)
    replica = restore_replica(libraries, identifier, tmp_path)
    second_id = register(replica)
    project = replica.store.root / "projects" / project_id
    if changed in {"title", "unknown"}:
        metadata = json.loads((project / "project.json").read_text())
        metadata["title" if changed == "title" else "future_field"] = "Independent change"
        _ = (project / "project.json").write_text(json.dumps(metadata))
    elif changed == "brief":
        _ = (project / "README.md").write_text("Different actual project instructions")
    else:
        registry = ArtifactStore(replica.store)
        artifact = registry.get_artifact(artifact_id)
        _ = registry.revise_artifact(
            artifact_id,
            [
                ArtifactFileInput(
                    path="data.json", text='{"source_path":"different authored value"}'
                )
            ],
            artifact.content_hash,
            "independent-authoring-change",
        )
    with pytest.raises(ValueError, match="Remote changes"):
        _ = replica.sync(second_id)
    assert command("git", "--git-dir", str(remote), "rev-parse", "main") == first.commit


def test_metadata_normalization_never_rewrites_author_json_or_historical_manifests(
    tmp_path: Path,
) -> None:
    source = tmp_path / "project.json"
    _ = source.write_text('{"id":"p", "title":"Project", "source_path":"/old/p"}')
    for relative in [
        "projects/p/artifacts/a/files/project.json",
        "projects/p/artifacts/a/revisions/1/project.json",
        "other/project.json",
        "projects/other/project.json",
    ]:
        assert recovery_module.portable_source_metadata(relative, source) is None
