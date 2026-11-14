"""Fictional admission reconciliation; never contacts an identity provider."""

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.authentication import AuthenticationError, Principal
from artifact_studio.cloudflare_admission import CloudflareGroupAdmission
from artifact_studio.deployment import DeploymentConfig
from artifact_studio.invitation_provisioning import InvitationProvisioning, invitation_destination
from artifact_studio.multiplayer import create_app
from artifact_studio.multiplayer_access import AccessDenied, Actor, Scope, Sharing
from artifact_studio.storage import Store


class FictionalAdmission:
    namespace = "fictional/staging-group"

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool, str]] = []
        self.members: set[str] = set()
        self.lose_response = False
        self.removing: threading.Event | None = None
        self.resume: threading.Event | None = None

    def reconcile(self, email: str, eligible: bool, operation_id: str, *, timeout: float) -> None:
        assert timeout == 10
        self.calls.append((email, eligible, operation_id))
        if not eligible and self.removing is not None and self.resume is not None:
            self.removing.set()
            assert self.resume.wait(5)
        if eligible:
            self.members.add(email)
        else:
            self.members.discard(email)
        if self.lose_response:
            self.lose_response = False
            raise RuntimeError("fixture-provider-secret-must-not-be-persisted")


Fixture = tuple[Sharing, Actor, Actor, Scope, Scope, DeploymentConfig, FictionalAdmission]


@pytest.fixture
def setup(tmp_path: Path) -> Fixture:
    store = Store(tmp_path / "collection", tmp_path / "state" / "review.sqlite")
    sharing = Sharing(tmp_path / "sharing", store, "owner@example.test")
    owner = sharing.human(Principal("owner", "owner@example.test"))
    alice = sharing.human(Principal("alice", "alice@example.test"))
    service = ArtifactStore(store)
    project = service.create_project("Fictional project")
    source = tmp_path / "fictional.md"
    source.write_text("Fictional source")
    artifact = service.register_artifact(project.id, "Fictional artifact", source, source.name)
    config = DeploymentConfig(
        "hosted",
        "https://studio.example.test",
        "https://preview.example.test",
        "cloudflare",
        {"team_domain": "fictional.cloudflareaccess.com", "audience": "staging"},
        ("owner@example.test",),
        (),
    )
    return (
        sharing,
        owner,
        alice,
        Scope("default", "project", project.id),
        Scope("default", "artifact", artifact.id),
        config,
        FictionalAdmission(),
    )


def test_lost_response_retries_same_operation_after_restart(setup: Fixture) -> None:
    sharing, owner, _, project, _, config, adapter = setup
    admission = InvitationProvisioning(sharing, config, adapter)
    sharing.invite(owner, project, "alice@example.test", "edit")
    assert admission.status(owner, "default")[0]["state"] == "pending"
    adapter.lose_response = True
    admission.tick()
    assert adapter.members == {"alice@example.test"}
    assert admission.status(owner, "default")[0]["state"] == "error"
    restarted = Sharing(sharing.root, sharing.store("default"), sharing.owner_email)
    admission = InvitationProvisioning(restarted, config, adapter)
    admission.retry(owner, "default", "alice@example.test")
    admission.tick()
    assert adapter.calls[0] == adapter.calls[1]
    assert admission.status(owner, "default")[0]["state"] == "active"
    admission.tick()
    assert len(adapter.calls) == 2
    with sharing.connect() as db:
        assert "fixture-provider-secret" not in "\n".join(db.iterdump())


def test_overlapping_grants_and_expired_invites(setup: Fixture) -> None:
    sharing, owner, alice, project, artifact, config, adapter = setup
    admission = InvitationProvisioning(sharing, config, adapter)
    first = sharing.invite(owner, project, "alice@example.test", "edit")
    sharing.accept(alice, first["token"])
    second = sharing.invite(owner, artifact, "alice@example.test", "view")
    sharing.accept(alice, second["token"])
    admission.tick()
    grants = cast(list[dict[str, str]], sharing.access(owner, artifact)["grants"])
    for grant in grants:
        scope = project if grant["kind"] == "project" else artifact
        sharing.revoke(owner, scope, grant["id"])
        admission.tick()
        if grant is grants[0]:
            assert adapter.members == {"alice@example.test"}
    assert adapter.members == set()
    assert [call[1] for call in adapter.calls] == [True, False]
    invitation = sharing.invite(owner, artifact, "alice@example.test", "view")
    admission.tick()
    with sharing.connect() as db:
        db.execute(
            "UPDATE invitations SET expires=? WHERE id=?", (time.time() - 1, invitation["id"])
        )
    admission.tick()
    assert adapter.members == set()
    with pytest.raises(AccessDenied):
        sharing.accept(alice, invitation["token"])


def test_verified_recipient_context_and_destination(setup: Fixture) -> None:
    sharing, owner, alice, project, artifact, config, adapter = setup
    admission = InvitationProvisioning(sharing, config, adapter)
    invitation = sharing.invite(owner, artifact, "alice@example.test", "view")
    with pytest.raises(AccessDenied):
        sharing.accept(owner, invitation["token"])
    assert sharing.accept(alice, invitation["token"]) == artifact
    assert invitation_destination(artifact) == "/?artifact=" + artifact.resource
    assert invitation_destination(project) == "/?project=" + project.resource
    with pytest.raises(AccessDenied):
        sharing.accept(alice, invitation["token"])
    with pytest.raises(AccessDenied):
        admission.status(alice, "default")
    agent = sharing.agent(sharing.connect_agent(owner, project, "Fictional agent")["token"])
    with pytest.raises(AccessDenied):
        admission.retry(agent, "default", "alice@example.test")
    with pytest.raises(AccessDenied):
        admission.retry(owner, "default", "unknown@example.test")
    with pytest.raises(ValueError, match="binding changed"):
        InvitationProvisioning(
            sharing,
            replace(
                config,
                auth_options={
                    "team_domain": "fictional.cloudflareaccess.com",
                    "audience": "other-app",
                },
            ),
            adapter,
        )


def test_invitation_and_outbox_rollback_together(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    sharing, owner, _, project, _, config, adapter = setup
    admission = InvitationProvisioning(sharing, config, adapter)
    original = admission.changed

    def fail(db: sqlite3.Connection, email: str) -> None:
        original(db, email)
        raise RuntimeError("Fictional interrupted transaction")

    monkeypatch.setattr(admission, "changed", fail)
    with pytest.raises(RuntimeError):
        sharing.invite(owner, project, "alice@example.test", "view")
    with sharing.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM invitations").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM guest_admission").fetchone()[0] == 0
    assert not adapter.calls


def test_uninvited_identity_and_revoked_provider_session_are_denied(setup: Fixture) -> None:
    sharing, owner, alice, project, _, config, adapter = setup
    admission = InvitationProvisioning(sharing, config, adapter)
    with pytest.raises(AccessDenied):
        sharing.human(Principal("uninvited", "uninvited@example.test"))
    invitation = sharing.invite(owner, project, "alice@example.test", "view")
    assert sharing.human(Principal("alice", "alice@example.test")) == alice
    sharing.accept(alice, invitation["token"])
    with pytest.raises(AccessDenied):
        sharing.human(Principal("different-subject", "alice@example.test"))
    grant = cast(list[dict[str, str]], sharing.access(owner, project)["grants"])[0]
    sharing.revoke(owner, project, grant["id"])
    with pytest.raises(AccessDenied):
        sharing.human(Principal("alice", "alice@example.test"))
    assert sharing.human(Principal("owner", "owner@example.test")) == owner
    admission.tick()


def test_new_invite_cannot_race_eligibility_removal(setup: Fixture) -> None:
    sharing, owner, _, project, artifact, config, adapter = setup
    admission = InvitationProvisioning(sharing, config, adapter)
    first = sharing.invite(owner, project, "alice@example.test", "view")
    admission.tick()
    sharing.revoke(owner, project, first["id"])
    adapter.removing, adapter.resume = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(admission.tick)
        assert adapter.removing.wait(5)
        inviting = pool.submit(sharing.invite, owner, artifact, "alice@example.test", "edit")
        try:
            assert not inviting.done()
        finally:
            adapter.resume.set()
        running.result()
        inviting.result()
    admission.tick()
    assert adapter.members == {"alice@example.test"}
    assert [call[1] for call in adapter.calls] == [True, False, True]


def test_default_disabled_and_http_owner_boundary(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    sharing, owner, _, project, _, config, adapter = setup
    monkeypatch.delenv("ARTIFACT_STUDIO_EXPERIMENTAL_INVITATIONS", raising=False)
    assert InvitationProvisioning.from_env(sharing, config) is None
    monkeypatch.setenv("ARTIFACT_STUDIO_EXPERIMENTAL_INVITATIONS", "1")
    monkeypatch.delenv("ARTIFACT_STUDIO_INVITATION_PROVISIONER", raising=False)
    with pytest.raises(ValueError, match="Select an installed"):
        InvitationProvisioning.from_env(sharing, config)

    class FictionalAuth:
        async def authenticate(self, headers: Headers) -> Principal:
            name = headers.get("x-fixture-auth", "")
            if name not in {"owner", "alice"}:
                raise AuthenticationError()
            return Principal(name, name + "@example.test")

    admission = InvitationProvisioning(sharing, config, adapter)
    invitation = sharing.invite(owner, project, "alice@example.test", "edit")
    app = create_app(config, FictionalAuth(), sharing, invitation_provisioning=admission)
    with TestClient(app, base_url=config.public_url) as client:
        path = "/api/workspaces/default/invitation-provisioning"
        assert client.get(path, headers={"X-Fixture-Auth": "alice"}).status_code == 404
        assert client.get(path, headers={"X-Fixture-Auth": "owner"}).status_code == 200
        response = client.post(
            "/api/invitations/accept",
            headers={
                "Origin": config.public_url,
                "X-Fixture-Auth": "alice",
            },
            json={"token": invitation["token"]},
        )
        assert response.status_code == 200
        assert response.json()["destination"] == "/?project=" + project.resource


def test_operator_can_select_builtin_adapter_without_contacting_provider(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    sharing, _, _, _, _, config, _ = setup
    monkeypatch.setenv("ARTIFACT_STUDIO_EXPERIMENTAL_INVITATIONS", "1")
    monkeypatch.setenv("ARTIFACT_STUDIO_INVITATION_PROVISIONER", "cloudflare-group")
    for field, value in {
        "ACCOUNT": "a" * 32,
        "APPLICATION": "b" * 32,
        "POLICY": "c" * 32,
        "GROUP": "d" * 32,
        "TOKEN": "fictional-token",
    }.items():
        monkeypatch.setenv("ARTIFACT_STUDIO_CLOUDFLARE_ADMISSION_" + field, value)
    admission = InvitationProvisioning.from_env(sharing, config)
    assert admission is not None and isinstance(admission.adapter, CloudflareGroupAdmission)
    with sharing.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM guest_admission").fetchone()[0] == 0
        assert "fictional-token" not in "\n".join(db.iterdump())


def test_sweep_discovers_restored_grants_without_restart(setup: Fixture) -> None:
    sharing, owner, alice, project, _, config, adapter = setup
    admission = InvitationProvisioning(sharing, config, adapter)
    with sharing.connect() as db:
        db.execute(
            "INSERT INTO grants VALUES (?,?,?,?,?,?)",
            (
                "fictional-restored-grant",
                "default",
                "project",
                project.resource,
                alice.person,
                "view",
            ),
        )
    admission.tick()
    assert adapter.members == {"alice@example.test"}
    assert admission.status(owner, "default")[0]["state"] == "active"
