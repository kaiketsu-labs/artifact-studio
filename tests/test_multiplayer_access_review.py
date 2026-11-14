import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.authentication import Principal
from artifact_studio.multiplayer_access import AccessDenied, Actor, Role, Scope, Sharing
from artifact_studio.storage import Store

Fixture = tuple[Sharing, Actor, Actor, Actor, str, str, str]


@pytest.fixture
def setup(tmp_path: Path) -> Fixture:
    default = Store(tmp_path / "collection", tmp_path / "state" / "review.sqlite3")
    sharing = Sharing(tmp_path / "sharing", default, "owner@example.test")
    owner = sharing.human(Principal("owner", "owner@example.test"))
    alice = sharing.human(Principal("alice", "alice@example.test"))
    bob = sharing.human(Principal("bob", "bob@example.test"))
    artifacts = ArtifactStore(default)
    project = artifacts.create_project("Fictional project")
    source = tmp_path / "fiction.md"
    source.write_text("fictional content")
    one = artifacts.register_artifact(project.id, "One", source, "fiction.md")
    two = artifacts.register_artifact(project.id, "Two", source, "fiction.md")
    return sharing, owner, alice, bob, project.id, one.id, two.id


def invite(
    sharing: Sharing, owner: Actor, guest: Actor, scope: Scope, email: str, role: Role
) -> dict[str, str]:
    result = sharing.invite(owner, scope, email, role)
    assert sharing.accept(guest, result["token"]) == scope
    return result


def test_inherited_and_artifact_isolation(setup: Fixture) -> None:
    sharing, owner, alice, bob, project, one, two = setup
    ws = Scope("default")
    p = Scope("default", "project", project)
    a = Scope("default", "artifact", one)
    sibling = Scope("default", "artifact", two)
    invite(sharing, owner, alice, a, "alice@example.test", "edit")
    assert sharing.level(alice, a) == 3
    assert sharing.level(alice, sibling) == 0
    assert sharing.level(alice, p) == 0
    invite(sharing, owner, bob, p, "bob@example.test", "edit")
    invite(sharing, owner, bob, a, "bob@example.test", "view")
    assert sharing.level(bob, a) == 3
    assert sharing.level(bob, sibling) == 3
    grant = next(
        row
        for row in cast(list[dict[str, str]], sharing.access(owner, a)["grants"])
        if row["person"] == bob.person and row["kind"] == "artifact"
    )
    sharing.revoke(owner, a, grant["id"])
    assert sharing.level(bob, a) == 3
    assert sharing.level(bob, ws) == 0


def test_invite_wrong_user_expiry_replay(setup: Fixture) -> None:
    sharing, owner, alice, bob, _, one, _ = setup
    scope = Scope("default", "artifact", one)
    invitation = sharing.invite(owner, scope, "alice@example.test", "view")
    with pytest.raises(AccessDenied):
        sharing.accept(bob, invitation["token"])
    sharing.accept(alice, invitation["token"])
    with pytest.raises(AccessDenied):
        sharing.accept(alice, invitation["token"])
    expired = sharing.invite(owner, scope, "alice@example.test", "view")
    with sharing.connect() as db:
        db.execute("UPDATE invitations SET expires=? WHERE id=?", (time.time() - 1, expired["id"]))
    with pytest.raises(AccessDenied):
        sharing.accept(alice, expired["token"])


def test_revoke_removes_agent_scope(setup: Fixture) -> None:
    sharing, owner, alice, _, _, one, _ = setup
    scope = Scope("default", "artifact", one)
    invite(sharing, owner, alice, scope, "alice@example.test", "edit")
    connection = sharing.connect_agent(alice, scope, "Fictional agent")
    agent = sharing.agent(connection["token"])
    assert sharing.level(agent, scope) == 3
    grant = cast(list[dict[str, str]], sharing.access(owner, scope)["grants"])[0]
    sharing.revoke(owner, scope, grant["id"])
    with pytest.raises(AccessDenied):
        sharing.require(agent, scope)


def test_invitation_without_expiry_stays_email_bound_and_revocable(setup: Fixture) -> None:
    """Permanent links remain one-use and are invalid after cancellation."""
    sharing, owner, alice, bob, _, one, sibling = setup
    scope = Scope("default", "artifact", one)
    link = sharing.invite(owner, scope, "alice@example.test", "edit", no_expiry=True)
    assert sharing.access(owner, scope)["invitations"] == [
        {"id": link["id"], "email": "alice@example.test", "role": "edit", "expires": 0}
    ]
    with pytest.raises(AccessDenied):
        sharing.accept(bob, link["token"])
    sharing.revoke(owner, scope, link["id"])
    with pytest.raises(AccessDenied):
        sharing.accept(alice, link["token"])
    new_link = sharing.invite(owner, scope, "alice@example.test", "edit", no_expiry=True)
    assert sharing.accept(alice, new_link["token"]) == scope
    assert sharing.level(alice, Scope("default", "artifact", sibling)) == 0
    with pytest.raises(AccessDenied):
        sharing.accept(alice, new_link["token"])


def test_same_ids_isolated(setup: Fixture) -> None:
    sharing, owner, alice, _, project, one, _ = setup
    other = sharing.create_workspace(owner, "Fictional second")
    target = sharing.root / "workspaces" / other
    target.mkdir(parents=True)
    shutil.copytree(sharing.store("default").root, target / "collection")
    (target / "state").mkdir()
    shutil.copy2(sharing.store("default").database, target / "state" / "review.sqlite3")
    assert ArtifactStore(sharing.store(other)).get_artifact(one).id == one
    invite(
        sharing, owner, alice, Scope("default", "project", project), "alice@example.test", "edit"
    )
    assert sharing.level(alice, Scope("default", "artifact", one)) == 3
    assert sharing.level(alice, Scope(other, "artifact", one)) == 0


def test_old_pending_invite_after_grant_revocation(setup: Fixture) -> None:
    sharing, owner, alice, _, _, one, _ = setup
    scope = Scope("default", "artifact", one)
    old = sharing.invite(owner, scope, "alice@example.test", "edit")
    invite(sharing, owner, alice, scope, "alice@example.test", "edit")
    grant = cast(list[dict[str, str]], sharing.access(owner, scope)["grants"])[0]
    sharing.revoke(owner, scope, grant["id"])
    assert sharing.level(alice, scope) == 0
    with pytest.raises(AccessDenied):
        sharing.accept(alice, old["token"])


def test_old_invitation_cannot_override_newer_role(setup: Fixture) -> None:
    sharing, owner, alice, _, _, one, _ = setup
    scope = Scope("default", "artifact", one)
    old = sharing.invite(owner, scope, "alice@example.test", "edit")
    invite(sharing, owner, alice, scope, "alice@example.test", "view")
    assert sharing.level(alice, scope) == 1
    with pytest.raises(AccessDenied):
        sharing.accept(alice, old["token"])
    assert sharing.level(alice, scope) == 1


def test_personal_provisioning_keeps_existing_subject_and_is_idempotent(setup: Fixture) -> None:
    """Enable provisioning for a registered guest without transferring any grants."""
    previous, owner, alice, bob, _, artifact, _ = setup
    invite(
        previous, owner, alice, Scope("default", "artifact", artifact), "alice@example.test", "view"
    )
    kwargs = {"personal_workspace_emails": ("alice@example.test",)}
    first = Sharing(previous.root, previous.store("default"), previous.owner_email, **kwargs)
    second = Sharing(previous.root, previous.store("default"), previous.owner_email, **kwargs)
    principal = Principal("alice", "alice@example.test")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [executor.submit(hub.human, principal) for hub in (first, second)]
        assert [result.result() for result in results] == [alice, alice]
    with first.connect() as db:
        rows = db.execute("SELECT id FROM workspaces WHERE owner=?", (alice.person,)).fetchall()
    assert len(rows) == 1
    scope = Scope(cast(str, rows[0]["id"]))
    assert first.owner(alice, scope.workspace)
    assert first.level(owner, scope) == first.level(bob, scope) == 0
    assert first.level(alice, Scope("default", "artifact", artifact)) == 1
    with pytest.raises(AccessDenied):
        second.human(Principal("different-subject", "alice@example.test"))
