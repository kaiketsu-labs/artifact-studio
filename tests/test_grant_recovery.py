"""Identity-bound, explicit restoration of human sharing grants."""

from pathlib import Path

import pytest

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.authentication import Principal
from artifact_studio.deployment import DeploymentConfig
from artifact_studio.grant_recovery import preview_grants, restore_grants, snapshot_grants
from artifact_studio.multiplayer_access import AccessDenied, Scope, Sharing
from artifact_studio.storage import ConflictError, Store
from artifact_studio.workspace_recovery import WorkspaceRecovery


def test_restore_fictional_workspace_and_grants(tmp_path: Path) -> None:
    config = DeploymentConfig(
        mode="hosted",
        auth_provider="cloudflare",
        auth_options={
            "team_domain": "example.cloudflareaccess.com",
            "audience": "fictional-audience",
        },
    )
    source = Store(tmp_path / "source", tmp_path / "source-state" / "review.sqlite3")
    sharing = Sharing(tmp_path / "source-sharing", source, "owner@example.test")
    owner = sharing.human(Principal("owner-subject", "owner@example.test"))
    guest = sharing.human(Principal("guest-subject", "guest@example.test"))
    registry = ArtifactStore(source)
    project = registry.create_project("Fictional project")
    path = tmp_path / "fiction.txt"
    path.write_text("fiction")
    artifact = registry.register_artifact(project.id, "Fiction", path, "fiction.txt")
    scope = Scope("default", "artifact", artifact.id)
    invitation = sharing.invite(owner, scope, "guest@example.test", "edit")
    sharing.accept(guest, invitation["token"])
    snapshot = snapshot_grants(sharing, owner, "default", config)
    assert len(snapshot.grants) == 1
    assert invitation["token"] not in snapshot.model_dump_json()
    archive = tmp_path / "backup.zip"
    WorkspaceRecovery(source).export_archive(archive, human_grants=snapshot)
    destination = Store(tmp_path / "restored", tmp_path / "restored-state" / "review.sqlite3")
    recovery = WorkspaceRecovery(destination)
    assert recovery.archive_grants(archive) == snapshot
    preview = recovery.preview(archive, mode="archive")
    assert (
        "Human access grants are included but are not restored with the files." in preview.warnings
    )
    recovery.restore(archive, preview.content_hash, mode="archive")
    restored = Sharing(tmp_path / "new-sharing", destination, "owner@example.test")
    new_owner = restored.human(Principal("owner-subject", "owner@example.test"))
    new_guest = restored.human(Principal("guest-subject", "guest@example.test"))
    assert restored.level(new_guest, scope) == 0
    other_workspace = restored.create_workspace(new_owner, "Separate workspace")
    other_scope = Scope(other_workspace)
    other_invitation = restored.invite(new_owner, other_scope, "guest@example.test", "view")
    restored.accept(new_guest, other_invitation["token"])
    token = preview_grants(restored, new_owner, snapshot, config)
    wrong = config.__class__(
        mode="hosted",
        auth_provider="cloudflare",
        auth_options={
            "team_domain": "other.cloudflareaccess.com",
            "audience": "fictional-audience",
        },
    )
    with pytest.raises(ValueError):
        restore_grants(restored, new_owner, snapshot, wrong, token)
    with pytest.raises(ConflictError):
        restore_grants(restored, new_owner, snapshot, config, "0" * 64)
    with pytest.raises(AccessDenied):
        restore_grants(restored, new_guest, snapshot, config, token)
    assert restore_grants(restored, new_owner, snapshot, config, token) == 1
    assert restored.level(new_guest, scope) == 3
    assert restored.level(new_guest, other_scope) == 1
    with pytest.raises(ConflictError):
        restore_grants(restored, new_owner, snapshot, config, token)


def test_reject_invalid_or_conflicting_grants(tmp_path: Path) -> None:
    from artifact_studio.grant_recovery_models import GrantSnapshot, HumanGrant
    from artifact_studio.multiplayer_access import checksum

    config = DeploymentConfig(
        mode="hosted",
        auth_provider="cloudflare",
        auth_options={"team_domain": "example.cloudflareaccess.com", "audience": "aud"},
    )
    store = Store(tmp_path / "collection", tmp_path / "state" / "review.sqlite3")
    hub = Sharing(tmp_path / "sharing", store, "owner@example.test")
    owner = hub.human(Principal("owner", "owner@example.test"))
    grant = HumanGrant(
        person=checksum("guest"),
        email="guest@example.test",
        kind="workspace",
        resource="",
        role="view",
    )
    snapshot = GrantSnapshot(
        issuer="https://example.cloudflareaccess.com",
        audience="aud",
        workspace="default",
        owner=owner.person,
        owner_email="owner@example.test",
        grants=[grant],
    )
    duplicate = snapshot.model_copy(update={"grants": [grant, grant]})
    with pytest.raises(ValueError, match="Duplicate"):
        preview_grants(hub, owner, duplicate, config)
    missing = grant.model_copy(update={"kind": "project", "resource": "absent"})
    with pytest.raises(AccessDenied):
        preview_grants(hub, owner, snapshot.model_copy(update={"grants": [missing]}), config)
    with hub.connect() as db:
        db.execute("INSERT INTO people VALUES (?,?)", (checksum("different"), grant.email))
    with pytest.raises(ConflictError, match="identity conflicts"):
        preview_grants(hub, owner, snapshot, config)
    with hub.connect() as db:
        assert db.execute("SELECT count(*) FROM grants").fetchone()[0] == 0
