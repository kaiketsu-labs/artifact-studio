"""Identity-bound, explicit restoration of human sharing grants."""

from pathlib import Path

import pytest

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.authentication import Principal
from artifact_studio.deployment import DeploymentConfig
from artifact_studio.grant_recovery import (
    preview_grants,
    preview_staged_grants,
    restore_grants,
    restore_staged_grants,
    snapshot_grants,
)
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
    staged, staged_token = preview_staged_grants(restored, new_owner, "default", config)
    assert staged.snapshot == snapshot
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
    assert restore_staged_grants(restored, new_owner, "default", config, staged_token) == 1
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


def test_owner_scoped_staged_grant_http(tmp_path: Path) -> None:
    """Only the owner can review and explicitly restore a fixed staged grant list."""
    import os

    from fastapi.testclient import TestClient
    from starlette.datastructures import Headers

    from artifact_studio.grant_recovery_models import GrantSnapshot, HumanGrant, StagedGrants
    from artifact_studio.multiplayer import create_app
    from artifact_studio.multiplayer_access import checksum
    from artifact_studio.workspace_recovery import STAGED_GRANTS

    class Provider:
        async def authenticate(self, headers: Headers) -> Principal:
            name = headers.get("x-fixture-auth", "owner")
            return Principal(name, f"{name}@example.test")

    config = DeploymentConfig(
        "hosted",
        "https://studio.example.test",
        "https://preview.example.test",
        "cloudflare",
        {"team_domain": "example.cloudflareaccess.com", "audience": "aud"},
        ("owner@example.test",),
        (),
    )
    store = Store(tmp_path / "collection", tmp_path / "state" / "review.sqlite3")
    hub = Sharing(tmp_path / "sharing", store, "owner@example.test")
    owner = hub.human(Principal("owner", "owner@example.test"))
    snapshot = GrantSnapshot(
        issuer="https://example.cloudflareaccess.com",
        audience="aud",
        workspace="default",
        owner=owner.person,
        owner_email="owner@example.test",
        grants=[
            HumanGrant(
                person=checksum("guest"),
                email="guest@example.test",
                kind="workspace",
                resource="",
                role="view",
            )
        ],
    )
    staged = store.database.parent / STAGED_GRANTS
    staged.write_text(StagedGrants(archive_sha256="a" * 64, snapshot=snapshot).model_dump_json())
    os.chmod(staged, 0o600)
    app = create_app(config, Provider(), hub)
    with TestClient(
        app, base_url=config.public_url, headers={"origin": config.public_url}
    ) as client:
        route = "/api/workspaces/default/grant-recovery"
        assert client.get(route, headers={"x-fixture-auth": "guest"}).status_code == 404
        response = client.get(route)
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview["grants"][0]["email"] == "guest@example.test"
        assert client.post(route, json={"preview_hash": preview["preview_hash"]}).status_code == 422
        assert (
            client.post(
                route, json={"preview_hash": "0" * 64, "restore_human_grants": True}
            ).status_code
            == 409
        )
        response = client.post(
            route, json={"preview_hash": preview["preview_hash"], "restore_human_grants": True}
        )
        assert response.status_code == 200 and response.json() == {"restored": 1}
        guest = hub.human(Principal("guest", "guest@example.test"))
        assert hub.level(guest, Scope("default")) == 1
        assert (
            client.post(
                route, json={"preview_hash": preview["preview_hash"], "restore_human_grants": True}
            ).status_code
            == 409
        )


def test_operator_cli_export_and_restore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Trusted operator commands create and stage an actual private archive."""
    import sys

    from artifact_studio import grant_recovery_cli

    config = DeploymentConfig(
        mode="hosted",
        auth_provider="cloudflare",
        auth_options={"team_domain": "example.cloudflareaccess.com", "audience": "aud"},
    )
    monkeypatch.setattr(DeploymentConfig, "from_env", lambda: config)
    source = tmp_path / "source"
    database = tmp_path / "state" / "review.sqlite3"
    hub = Sharing(tmp_path / "sharing", Store(source, database), "owner@example.test")
    owner = hub.human(Principal("owner", "owner@example.test"))
    guest = hub.human(Principal("guest", "guest@example.test"))
    token = hub.invite(owner, Scope("default"), "guest@example.test", "view")["token"]
    hub.accept(guest, token)
    archive = tmp_path / "archive.zip"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "grant-recovery",
            "export",
            "--collection",
            str(source),
            "--database",
            str(database),
            "--sharing-root",
            str(hub.root),
            "--owner-email",
            "owner@example.test",
            "--owner-subject",
            "owner",
            "--archive",
            str(archive),
        ],
    )
    assert grant_recovery_cli.main() == 0
    restored, restored_db = tmp_path / "restored", tmp_path / "restored-state" / "review.sqlite3"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "grant-recovery",
            "restore",
            "--archive",
            str(archive),
            "--collection",
            str(restored),
            "--database",
            str(restored_db),
        ],
    )
    assert grant_recovery_cli.main() == 0
    new_hub = Sharing(tmp_path / "new-sharing", Store(restored, restored_db), "owner@example.test")
    new_owner = new_hub.human(Principal("owner", "owner@example.test"))
    staged, _ = preview_staged_grants(new_hub, new_owner, "default", config)
    assert staged.snapshot.grants[0].email == "guest@example.test"
