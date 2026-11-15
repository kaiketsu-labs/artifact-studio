"""Human thread writes retain authorship, original anchors and publication boundaries."""

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from artifact_studio import app as service
from artifact_studio.artifact_models import ArtifactFeedbackInput, ArtifactFileInput, ArtifactTarget
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.designs.slides import DesignInput, SlideDesigns
from artifact_studio.models import (
    CommentInput,
    ElementTarget,
    PresentationCommentInput,
    QuestionAnswerInput,
)
from artifact_studio.multiplayer import ChatInput
from artifact_studio.questions import Questions
from artifact_studio.storage import ConflictError, Store, digest
from tests.design_factory import TEMPLATE_ID, make_design
from tests.integration.multiplayer_fixture import running_multiplayer_app
from tests.test_multiplayer_server import accept, grant, human


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    result = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    make_design(result.root / TEMPLATE_ID)
    monkeypatch.setattr(service, "store", result)
    return result


@pytest.mark.parametrize("parent", [0, -1, True, "1", 1.5, 2**63])
@pytest.mark.parametrize(
    "model", [CommentInput, PresentationCommentInput, ArtifactFeedbackInput, ChatInput]
)
def test_human_parent_ids_are_strict_positive_sqlite_ids(
    parent: object, model: type[BaseModel]
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(
            {"request_id": "human-reply", "content_hash": "a" * 64, "reply_to": parent}
            if model in (CommentInput, ArtifactFeedbackInput)
            else {"request_id": "human-reply", "message": "Reply", "reply_to": parent}
        )


def test_slide_human_reply_retains_original_anchor_and_batch_authorship(store: Store) -> None:
    target = ElementTarget(
        selector="h1", label="Original heading", quote="Test", x=0, y=0, width=1, height=0.1
    )
    original_hash = store.view(TEMPLATE_ID).slides[0].content_hash
    parent = store.add_feedback(
        TEMPLATE_ID, "cover", "Original", original_hash, "original-slide", target=target
    )
    deck = store.read(TEMPLATE_ID)
    deck.slides[0].html = '<article class="slide"><h1>Revised</h1></article>'
    store.write(TEMPLATE_ID, deck)
    _ = store.set_mode(TEMPLATE_ID, "batch")
    body = {
        "message": "Keep the earlier wording",
        "request_id": "human-slide-reply",
        "reply_to": int(parent),
        "content_hash": original_hash,
        "target": target.model_dump(),
        "expected_mode": "batch",
        "files": [{"name": "evidence.txt", "data_base64": base64.b64encode(b"Evidence").decode()}],
    }
    client = TestClient(service.app, base_url="http://127.0.0.1")
    path = f"/api/decks/{TEMPLATE_ID}/slides/cover/feedback"
    saved = client.post(path, json=body)
    assert saved.status_code == 200, saved.text
    reply = store.comments(TEMPLATE_ID)[-1]
    assert reply.author == "user" and not reply.released
    assert reply.reply_to == int(parent) and reply.content_hash == original_hash
    assert reply.target == target and reply.files[0].name == "evidence.txt"
    _ = store.set_mode(TEMPLATE_ID, "live")
    assert client.post(path, json=body).json() == saved.json()
    assert client.post(path, json=body | {"reply_to": reply.event_id}).status_code == 409
    retargeted = client.post(
        path,
        json=body
        | {
            "request_id": "human-retarget-reply",
            "expected_mode": "live",
            "content_hash": store.view(TEMPLATE_ID).slides[0].content_hash,
        },
    )
    assert retargeted.status_code == 409 and "original context" in retargeted.text
    assert (
        client.post(
            f"/api/decks/{TEMPLATE_ID}/slides/message/feedback",
            json=body | {"request_id": "wrong-slide"},
        ).status_code
        == 400
    )
    assert store.comments(TEMPLATE_ID)[0].target == target
    _ = store.release_batch(TEMPLATE_ID)
    assert store.comments(TEMPLATE_ID)[-1].released
    agent_reply = int(
        store.add_feedback(
            TEMPLATE_ID, "cover", "Agent response", "", "original-agent-reply", reply_to=int(parent)
        )
    )
    inherited = client.post(
        path,
        json={
            "message": "Another human follow-up",
            "request_id": "human-inherited-reply",
            "reply_to": agent_reply,
            "content_hash": original_hash,
        },
    )
    assert inherited.is_success, inherited.text
    assert store.comments(TEMPLATE_ID)[-1].target == target
    assert store.comments(TEMPLATE_ID)[-1].content_hash == original_hash
    assert store.comments(TEMPLATE_ID)[-1].files == []


@pytest.mark.parametrize("context_slide", [None, "cover"])
def test_presentation_human_reply_retains_optional_viewed_slide(
    store: Store,
    context_slide: str | None,
) -> None:
    parent = store.add_feedback(
        TEMPLATE_ID,
        None,
        "Whole presentation",
        "",
        "presentation-root",
        context_slide_id=context_slide,
    )
    body = {
        "message": "Follow-up",
        "request_id": "presentation-reply",
        "reply_to": int(parent),
        "context_slide_id": context_slide,
    }
    client = TestClient(service.app, base_url="http://127.0.0.1")
    saved = client.post(f"/api/decks/{TEMPLATE_ID}/feedback", json=body)
    assert saved.status_code == 200, saved.text
    reply = store.comments(TEMPLATE_ID)[-1]
    assert reply.author == "user" and reply.scope == "presentation"
    assert reply.reply_to == int(parent) and reply.context_slide_id == context_slide
    conflicting = client.post(
        f"/api/decks/{TEMPLATE_ID}/feedback",
        json=body | {"request_id": "presentation-retarget", "context_slide_id": "message"},
    )
    assert conflicting.status_code == 409 and "original context" in conflicting.text
    inherited = client.post(
        f"/api/decks/{TEMPLATE_ID}/feedback",
        json={"message": "Inherit", "request_id": "presentation-inherit", "reply_to": int(parent)},
    )
    assert inherited.is_success, inherited.text
    assert store.comments(TEMPLATE_ID)[-1].context_slide_id == context_slide
    other = store.create("2026-11-15-other", "Other fictional deck", "test-brand")
    denied = client.post(f"/api/decks/{other.id}/feedback", json=body)
    assert denied.status_code == 400


def test_human_and_mcp_replies_cannot_alias_each_others_receipts(store: Store) -> None:
    parent = int(store.add_feedback(TEMPLATE_ID, "cover", "Original", "", "authorship-root"))
    _ = store.add_feedback(TEMPLATE_ID, "cover", "Same text", "", "mcp-receipt", reply_to=parent)
    with pytest.raises(ConflictError, match="another action"):
        _ = store.add_feedback(
            TEMPLATE_ID, "cover", "Same text", "", "mcp-receipt", reply_to=parent, author="user"
        )
    _ = store.add_feedback(
        TEMPLATE_ID, "cover", "Same text", "", "human-receipt", reply_to=parent, author="user"
    )
    with pytest.raises(ConflictError, match="another action"):
        _ = store.add_feedback(
            TEMPLATE_ID, "cover", "Same text", "", "human-receipt", reply_to=parent
        )
    assert [event.author for event in store.comments(TEMPLATE_ID)] == ["user", "agent", "user"]


def test_slide_thread_reply_inherits_saved_proposal_without_retargeting(store: Store) -> None:
    slide = store.read(TEMPLATE_ID).slides[0]
    group = SlideDesigns(store).publish(
        TEMPLATE_ID,
        "cover",
        store.content_hash(TEMPLATE_ID, slide),
        "local-thread-proposals",
        [DesignInput(name="Fictional proposal", html='<article class="slide">Proposal</article>')],
    )
    option = group.options[1]
    parent = store.add_feedback(
        TEMPLATE_ID,
        "cover",
        "Proposal feedback",
        option.source_hash,
        "local-proposal-root",
        design_option_id=option.id,
    )
    client = TestClient(service.app, base_url="http://127.0.0.1")
    reply = client.post(
        f"/api/decks/{TEMPLATE_ID}/slides/cover/feedback",
        json={
            "message": "Follow-up",
            "request_id": "local-proposal-reply",
            "reply_to": int(parent),
            "content_hash": option.source_hash,
        },
    )
    assert reply.is_success, reply.text
    event = store.comments(TEMPLATE_ID)[-1]
    assert event.design_option_id == option.id and event.content_hash == option.source_hash
    assert event.target is None and event.edit_intent is None


@pytest.mark.parametrize("status", ["open", "answered", "cancelled"])
def test_thread_reply_cannot_bypass_question_answer_lifecycle(store: Store, status: str) -> None:
    questions = Questions(store)
    slide = store.read(TEMPLATE_ID).slides[0]
    question = questions.ask(
        TEMPLATE_ID,
        "cover",
        "Which fictional direction?",
        ["Blue", "Green"],
        digest(slide.model_dump_json()),
        "thread-question-root",
    )
    if status == "answered":
        _ = questions.answer(
            TEMPLATE_ID,
            "cover",
            question.event_id,
            QuestionAnswerInput(answer="Blue", request_id="thread-question-answer"),
        )
    elif status == "cancelled":
        _ = questions.cancel(TEMPLATE_ID, "cover", question.event_id, "thread-question-cancel")
    before = store.comments(TEMPLATE_ID)
    client = TestClient(service.app, base_url="http://127.0.0.1")
    response = client.post(
        f"/api/decks/{TEMPLATE_ID}/slides/cover/feedback",
        json={
            "message": "Generic reply",
            "request_id": "thread-question-bypass",
            "reply_to": question.event_id,
            "content_hash": question.content_hash,
        },
    )
    assert response.status_code == 400
    assert store.comments(TEMPLATE_ID) == before
    assert questions.list(TEMPLATE_ID, include_resolved=True)[0].status == status


def test_artifact_human_replies_keep_original_revision_and_reject_cross_artifact(
    store: Store, tmp_path: Path
) -> None:
    artifacts = ArtifactStore(store)
    project = artifacts.create_project("Fictional thread")
    source = tmp_path / "source"
    source.mkdir()
    _ = (source / "index.html").write_text("<h1>Original</h1>")
    artifact = artifacts.register_artifact(project.id, "Original", source, "index.html")
    parent = artifacts.add_feedback(
        artifact.id,
        ArtifactFeedbackInput(
            message="Original",
            content_hash=artifact.content_hash,
            request_id="artifact-parent",
            target=ArtifactTarget(kind="text", quote="Original"),
        ),
    )
    _ = artifacts.revise_artifact(
        artifact.id,
        [ArtifactFileInput(path="index.html", text="<h1>Revised</h1>")],
        artifact.content_hash,
        "artifact-revision",
    )
    body = ArtifactFeedbackInput(
        message="Follow-up",
        content_hash=artifact.content_hash,
        request_id="artifact-human-reply",
        reply_to=parent.event_id,
    )
    client = TestClient(service.app, base_url="http://127.0.0.1")
    path = f"/api/artifacts/{artifact.id}/feedback"
    saved = client.post(path, json=body.model_dump())
    assert saved.status_code == 200, saved.text
    reply = artifacts.feedback(artifact.id)[-1]
    assert reply.author == "user" and reply.reply_to == parent.event_id
    assert reply.revision == parent.revision == 1 and reply.stale
    assert reply.target == parent.target
    assert client.post(path, json=body.model_dump()).json() == saved.json()
    conflicting = client.post(
        path,
        json=body.model_dump()
        | {
            "request_id": "artifact-retarget",
            "content_hash": artifacts.get_artifact(artifact.id).content_hash,
        },
    )
    assert conflicting.status_code == 409 and "original context" in conflicting.text
    other = artifacts.register_artifact(project.id, "Other", source, "index.html")
    assert (
        client.post(f"/api/artifacts/{other.id}/feedback", json=body.model_dump()).status_code
        == 400
    )


@pytest.mark.parametrize("artifact_scope", [False, True], ids=["slide", "artifact"])
def test_unreleased_local_roots_cannot_receive_live_human_replies(
    store: Store, tmp_path: Path, artifact_scope: bool
) -> None:
    client = TestClient(service.app, base_url="http://127.0.0.1")
    if artifact_scope:
        artifacts = ArtifactStore(store)
        project = artifacts.create_project("Private fictional feedback")
        source = tmp_path / "source"
        source.mkdir()
        _ = (source / "index.html").write_text("<h1>Private</h1>")
        artifact = artifacts.register_artifact(project.id, "Private", source, "index.html")
        _ = artifacts.set_mode(artifact.id, "batch")
        parent = artifacts.add_feedback(
            artifact.id,
            ArtifactFeedbackInput(
                message="Draft", request_id="draft-root", content_hash=artifact.content_hash
            ),
        ).event_id
        _ = artifacts.set_mode(artifact.id, "live")
        path, content_hash = f"/api/artifacts/{artifact.id}/feedback", artifact.content_hash
    else:
        _ = store.set_mode(TEMPLATE_ID, "batch")
        content_hash = store.view(TEMPLATE_ID).slides[0].content_hash
        parent = int(store.add_feedback(TEMPLATE_ID, "cover", "Draft", content_hash, "draft-root"))
        _ = store.set_mode(TEMPLATE_ID, "live")
        path = f"/api/decks/{TEMPLATE_ID}/slides/cover/feedback"
    denied = client.post(
        path,
        json={
            "message": "Live follow-up",
            "request_id": "draft-live-reply",
            "reply_to": parent,
            "content_hash": content_hash,
        },
    )
    assert denied.status_code == 400


def test_hosted_chat_http_accepts_human_thread_reply_for_commenter(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(tmp_path / "server", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as commenter,
    ):
        accept(commenter, grant(owner, "alice", app.artifact_id, "comment"))
        path = f"/api/workspaces/default/artifacts/{app.artifact_id}/chat"
        root = owner.post(path, json={"message": "Original", "request_id": "http-thread-root"})
        assert root.is_success, root.text
        payload = {
            "message": "Follow-up",
            "request_id": "http-thread-reply",
            "reply_to": root.json()["id"],
        }
        saved = commenter.post(path, json=payload)
        assert saved.is_success, saved.text
        reply = saved.json()
        assert reply["reply_to"] == root.json()["id"]
        assert reply["author"] == reply["person"] != root.json()["author"]
        assert reply["assigned"] == ""
        assert commenter.post(path, json=payload).json() == reply
        assert commenter.post(path, json=payload | {"author": "spoofed"}).status_code == 422
        assert len(owner.get(path).json()) == 2
