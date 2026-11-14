from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import pytest

from artifact_studio.artifact_models import ArtifactTarget
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.authentication import Principal
from artifact_studio.multiplayer_access import AccessDenied, Actor, Scope, Sharing
from artifact_studio.multiplayer_chat import Conversations
from artifact_studio.review import ReviewHookInput
from artifact_studio.storage import ConflictError, Store
from tests.design_factory import TEMPLATE_ID, make_design


@dataclass
class Scenario:
    sharing: Sharing
    chat: Conversations
    scope: Scope
    owner: Actor
    guest: Actor
    a: Actor
    b: Actor


@pytest.fixture(params=[False], ids=["artifact"])
def scenario(tmp_path: Path, request: pytest.FixtureRequest) -> Scenario:
    store = Store(tmp_path / "collection", tmp_path / "state" / "db.sqlite3")
    sharing = Sharing(tmp_path / "sharing", store, "owner@example.test")
    owner = sharing.human(Principal("owner", "owner@example.test"))
    guest = sharing.human(Principal("guest", "guest@example.test"))
    registry = ArtifactStore(store)
    project = registry.create_project("Fictional")
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.html").write_text("<h1>Fictional</h1>")
    artifact = registry.register_artifact(project.id, "Page", source, "index.html")
    resource = artifact.id
    if request.param is True:
        make_design(store.root / TEMPLATE_ID)
        resource = store.create("2026-11-01-delegation", "Fictional deck", "test-brand").id
    scope = Scope("default", "artifact", resource)
    invitation = sharing.invite(owner, scope, "guest@example.test", "edit")
    sharing.accept(guest, invitation["token"])
    a = sharing.agent(sharing.connect_agent(owner, scope, "Owner agent")["token"])
    b = sharing.agent(sharing.connect_agent(guest, scope, "Guest agent")["token"])
    return Scenario(sharing, Conversations(sharing), scope, owner, guest, a, b)


def ids(value: object) -> list[int]:
    return [cast(int, row["id"]) for row in cast(list[dict[str, object]], value)]


def test_visibility_drafts_claims_and_retry(scenario: Scenario) -> None:
    s = scenario
    private = s.chat.post(s.owner, s.scope, "private", "owner private", "private-1", s.a.agent)
    draft = s.chat.post(s.owner, s.scope, "shared", "owner withheld", "draft-001", s.a.agent, False)
    other_draft = s.chat.post(
        s.guest, s.scope, "shared", "guest withheld", "draft-002", s.b.agent, False
    )
    shared = s.chat.post(s.guest, s.scope, "shared", "guest shared", "shared-1", s.b.agent)
    assert s.chat.history(s.guest, s.scope, "private") == []
    assert s.chat.history(s.b, s.scope, "private") == []
    assert ids(s.chat.history(s.guest, s.scope, "shared")) == [other_draft["id"], shared["id"]]
    assert ids(s.chat.tasks(s.a, s.scope)["events"]) == [private["id"]]
    assert ids(s.chat.tasks(s.b, s.scope)["events"]) == [shared["id"]]
    event = cast(int, private["id"])
    with pytest.raises(AccessDenied):
        s.chat.claim(s.b, s.scope, event, "b")
    claim = cast(str, s.chat.claim(s.a, s.scope, event, "a")["claim"])
    assert s.chat.claim(s.a, s.scope, event, "a")["claim"] == claim
    with pytest.raises(ConflictError):
        s.chat.claim(s.a, s.scope, event, "another-session")
    reply = s.chat.reply(s.a, s.scope, event, claim, "Done", "reply-001")
    assert s.chat.reply(s.a, s.scope, event, claim, "Done", "reply-001") == reply
    with pytest.raises(AccessDenied):
        s.chat.reply(s.b, s.scope, event, claim, "Forged", "reply-002")
    s.chat.release(s.owner, s.scope, "shared")
    assert ids(s.chat.tasks(s.a, s.scope)["events"]) == [draft["id"]]
    assert ids(s.chat.tasks(s.b, s.scope)["events"]) == [shared["id"]]
    assert ids(s.chat.history(s.guest, s.scope, "shared")) == [
        other_draft["id"],
        shared["id"],
        draft["id"],
    ]


def test_hooks_independent_and_explicit_resume(scenario: Scenario) -> None:
    s = scenario

    def hook(
        agent: Actor,
        event: Literal["PostToolUse", "Stop", "SessionEnd"],
        *,
        start: bool = False,
        auto: bool = False,
    ) -> dict[str, object]:
        return s.chat.hook(
            agent,
            ReviewHookInput(
                harness="codex",
                session_id="same",
                event=event,
                artifact_id=s.scope.resource,
                start_review=start,
                auto_start=auto,
            ),
        )

    def connect(agent: Actor) -> dict[str, object]:
        return hook(agent, "PostToolUse", auto=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(connect, [s.a, s.b]))
    assert all(x["listening"] for x in result)
    assert not hook(s.a, "SessionEnd")["listening"]
    assert not hook(s.a, "Stop")["listening"]
    assert not hook(s.a, "PostToolUse", auto=True)["listening"]
    assert hook(s.b, "Stop")["listening"]
    assert hook(s.a, "PostToolUse", start=True)["listening"]


@pytest.mark.parametrize("scenario", [True], indirect=True, ids=["presentation"])
def test_private_person_and_shared_cross_assignment(scenario: Scenario) -> None:
    s = scenario
    another = s.sharing.agent(s.sharing.connect_agent(s.owner, s.scope, "Replacement")["token"])
    private = s.chat.post(s.owner, s.scope, "private", "owner private", "private-2", s.a.agent)
    assert ids(s.chat.history(another, s.scope, "private")) == [private["id"]]
    assert s.chat.history(s.b, s.scope, "private") == []
    with pytest.raises(AccessDenied):
        s.chat.post(s.owner, s.scope, "private", "not yours", "private-3", s.b.agent)
    with pytest.raises(AccessDenied):
        s.chat.post(s.owner, s.scope, "shared", "guest please", "shared-2", s.b.agent)
    with pytest.raises(AccessDenied):
        s.chat.collaborator_requests(s.owner, s.scope, s.b.agent, "direct")
    with pytest.raises(AccessDenied):
        s.chat.collaborator_requests(s.b, s.scope, s.b.agent, "direct")
    assert s.chat.collaborator_requests(s.guest, s.scope, s.b.agent) == "off"
    assert s.chat.collaborator_requests(s.guest, s.scope, s.b.agent, "direct") == "direct"
    generation = cast(int, s.chat.agents(s.owner, s.scope)[1]["collaborator_request_generation"])
    shared = s.chat.post(
        s.owner,
        s.scope,
        "shared",
        "guest please",
        "shared-2",
        s.b.agent,
        collaborator_request_generation=generation,
    )
    assert shared["collaborator_request_mode"] == "direct"
    assert ids(s.chat.tasks(s.b, s.scope)["events"]) == [shared["id"]]
    event = cast(int, shared["id"])
    claim = cast(str, s.chat.claim(s.b, s.scope, event, "guest-session")["claim"])
    reply = s.chat.reply(s.b, s.scope, event, claim, "Done", "guest-reply")
    assert ids(s.chat.history(s.owner, s.scope, "shared")) == [shared["id"], reply["id"]]


@pytest.mark.parametrize("scenario", [True], indirect=True, ids=["presentation"])
def test_consent_revocation_stops_claimed_and_unreleased_requests(scenario: Scenario) -> None:
    """A new policy generation never revives requests authorized by an older one."""
    s = scenario
    _ = s.chat.collaborator_requests(s.owner, s.scope, s.a.agent, "direct")
    active = s.chat.post(
        s.guest,
        s.scope,
        "shared",
        "Delegated",
        "delegated-01",
        s.a.agent,
        collaborator_request_generation=1,
    )
    _ = s.chat.post(
        s.guest,
        s.scope,
        "shared",
        "Draft",
        "delegated-02",
        s.a.agent,
        False,
        collaborator_request_generation=1,
    )
    event = cast(int, active["id"])
    claim = cast(str, s.chat.claim(s.a, s.scope, event, "owned-session")["claim"])
    _ = s.chat.collaborator_requests(s.owner, s.scope, s.a.agent, "off")
    _ = s.chat.collaborator_requests(s.owner, s.scope, s.a.agent, "direct")
    assert not s.chat.tasks(s.a, s.scope)["events"]
    with pytest.raises(AccessDenied):
        _ = s.chat.claim(s.a, s.scope, event, "owned-session")
    with pytest.raises(AccessDenied):
        _ = s.chat.reply(s.a, s.scope, event, claim, "Stale", "stale-reply")
    with pytest.raises(AccessDenied):
        s.chat.release(s.guest, s.scope, "shared")
    with pytest.raises(ConflictError, match="setting changed"):
        _ = s.chat.post(
            s.guest,
            s.scope,
            "shared",
            "Stale picker",
            "delegated-03",
            s.a.agent,
            collaborator_request_generation=1,
        )
    own = s.chat.post(s.owner, s.scope, "private", "My own request", "own-request", s.a.agent)
    assert ids(s.chat.tasks(s.a, s.scope)["events"]) == [own["id"]]


@pytest.mark.parametrize("scenario", [True], indirect=True, ids=["presentation"])
def test_revoked_target_cannot_receive_or_release(scenario: Scenario) -> None:
    s = scenario
    _ = s.chat.collaborator_requests(s.guest, s.scope, s.b.agent, "direct")
    s.chat.post(
        s.owner,
        s.scope,
        "shared",
        "withheld",
        "revoked-target",
        s.b.agent,
        False,
        collaborator_request_generation=1,
    )
    s.sharing.revoke_agent(s.guest, s.b.agent)
    with pytest.raises(AccessDenied):
        s.chat.release(s.owner, s.scope, "shared")
    with pytest.raises(AccessDenied):
        s.chat.tasks(s.b, s.scope)
    assert s.chat.history(s.guest, s.scope, "shared") == []


@pytest.mark.parametrize("scenario", [True], indirect=True, ids=["presentation"])
def test_downgraded_recipient_cannot_read_pending_requests(scenario: Scenario) -> None:
    """Viewer access keeps the presentation readable without actionable requests."""
    s = scenario
    _ = s.chat.collaborator_requests(s.guest, s.scope, s.b.agent, "direct")
    _ = s.chat.post(
        s.owner,
        s.scope,
        "shared",
        "Please edit",
        "downgrade-agent",
        s.b.agent,
        collaborator_request_generation=1,
    )
    assert s.chat.tasks(s.b, s.scope)["events"]
    [grant] = cast(list[dict[str, object]], s.sharing.access(s.owner, s.scope)["grants"])
    s.sharing.revoke(s.owner, s.scope, cast(str, grant["id"]))
    invitation = s.sharing.invite(s.owner, s.scope, "guest@example.test", "view")
    _ = s.sharing.accept(s.guest, invitation["token"])
    assert s.sharing.level(s.b, s.scope) == 1
    with pytest.raises(AccessDenied):
        _ = s.chat.tasks(s.b, s.scope)


@pytest.mark.parametrize("scenario", [True], indirect=True, ids=["presentation"])
def test_revoked_author_cannot_leave_actionable_hook_work(scenario: Scenario) -> None:
    s = scenario
    _ = s.chat.collaborator_requests(s.owner, s.scope, s.a.agent, "direct")
    shared = s.chat.post(
        s.guest,
        s.scope,
        "shared",
        "guest request",
        "revoked-author",
        s.a.agent,
        collaborator_request_generation=1,
    )
    grant = cast(list[dict[str, object]], s.sharing.access(s.owner, s.scope)["grants"])[0]
    s.sharing.revoke(s.owner, s.scope, cast(str, grant["id"]))
    assert s.chat.tasks(s.a, s.scope)["events"] == []
    with pytest.raises(AccessDenied):
        s.chat.claim(s.a, s.scope, cast(int, shared["id"]), "owner-session")
    status = s.chat.hook(
        s.a,
        ReviewHookInput(
            harness="codex",
            session_id="owner-session",
            event="PostToolUse",
            artifact_id=s.scope.resource,
            auto_start=True,
        ),
    )
    assert not status["feedback_available"]


def test_late_release_retains_anchor_and_paginates_once(scenario: Scenario) -> None:
    s = scenario
    target = ArtifactTarget(kind="text", quote="Fictional", start=1, end=5)
    draft = s.chat.post(
        s.owner, s.scope, "shared", "Late draft", "old-draft-1", s.a.agent, False, target
    )
    for index in range(105):
        s.chat.post(s.owner, s.scope, "shared", f"Live {index}", f"live-{index:04}")
    assert draft["id"] not in ids(s.chat.history(s.guest, s.scope, "shared"))
    s.chat.release(s.owner, s.scope, "shared")
    newest = s.chat.history(s.guest, s.scope, "shared")
    assert len(newest) == 100
    released = newest[-1]
    assert released["id"] == draft["id"]
    assert released["target"] == target.model_dump()
    assert released["created"] == draft["created"]
    assert released["content_hash"] == draft["content_hash"]
    older = s.chat.history(s.guest, s.scope, "shared", before=cast(int, newest[0]["cursor"]))
    all_ids = ids(older) + ids(newest)
    assert len(all_ids) == 106
    assert len(set(all_ids)) == 106
    post = s.chat.post(s.owner, s.scope, "shared", "Next live", "live-after-release")
    current = s.chat.history(s.guest, s.scope, "shared")[-1]
    assert post["cursor"] == current["cursor"]


@pytest.mark.parametrize("scenario", [True], indirect=True, ids=["presentation"])
def test_revoked_authors_do_not_starve_tasks(scenario: Scenario) -> None:
    s = scenario
    _ = s.chat.collaborator_requests(s.owner, s.scope, s.a.agent, "direct")
    for index in range(100):
        s.chat.post(
            s.guest,
            s.scope,
            "shared",
            f"Old {index}",
            f"old-{index:04}",
            s.a.agent,
            collaborator_request_generation=1,
        )
    valid = s.chat.post(s.owner, s.scope, "shared", "New task", "new-task-1", s.a.agent)
    grant = cast(list[dict[str, object]], s.sharing.access(s.owner, s.scope)["grants"])[0]
    s.sharing.revoke(s.owner, s.scope, cast(str, grant["id"]))
    assert ids(s.chat.tasks(s.a, s.scope)["events"]) == [valid["id"]]
