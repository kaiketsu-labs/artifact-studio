"""Preserve multi-object feedback through API, storage, projects and common artifact reads."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from studio import app as service
from studio.artifacts import ArtifactStore
from studio.models import ElementGroupTarget, ElementTarget, SlidePatch, SlideTarget
from studio.projects import ClientState, FeedbackDraft, open_project, save_project
from studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """Use fictional slide content with independent private review storage."""
    make_design(tmp_path / "collection" / TEMPLATE_ID)
    return Store(tmp_path / "collection", tmp_path / "state" / "review.sqlite3")


def group() -> ElementGroupTarget:
    """Provide distinct original snapshots, including labels, markup and geometry."""
    return ElementGroupTarget(
        label="2 objects",
        elements=[
            ElementTarget(
                selector=f"#{name}",
                label=name,
                quote=f"Original {name}",
                html=f'<p id="{name}">Original {name}</p>',
                x=0.1,
                y=y,
                width=0.8,
                height=0.2,
            )
            for name, y in [("heading", 0.1), ("chart", 0.4)]
        ],
    )


def test_group_feedback_retains_original_version_through_all_readers(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Submit stale reviewed context, retry safely and keep it intact across project transport."""
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    original = store.view(TEMPLATE_ID).slides[0]
    target = group()
    _ = store.revise(
        TEMPLATE_ID,
        original.id,
        original.expected_hash,
        "new-slide-version",
        SlidePatch(html='<article class="slide"><h1>New heading</h1></article>'),
    )
    body = {
        "message": "Align both objects",
        "target": target.model_dump(),
        "content_hash": original.content_hash,
        "request_id": "multi-object-comment",
    }
    endpoint = f"/api/decks/{TEMPLATE_ID}/slides/{original.id}/feedback"
    first = client.post(endpoint, json=body)
    assert first.status_code == 200
    assert client.post(endpoint, json=body).json() == first.json()
    feedback = store.feedback_feed(TEMPLATE_ID).events
    assert len(feedback) == 1
    assert feedback[0].target == target
    assert feedback[0].content_hash == original.content_hash
    adapted = ArtifactStore(store).feedback(TEMPLATE_ID)[0]
    assert adapted.target is not None and adapted.target.kind == "group"
    assert [item.selector for item in adapted.target.elements] == ["#heading", "#chart"]
    assert adapted.content_hash == original.content_hash

    state = ClientState(
        feedback={
            original.id: FeedbackDraft(
                feedback="Unsent group comment",
                target=target,
                slide_id=original.id,
                content_hash=original.content_hash,
                request_id="group-draft-request",
            )
        }
    )
    destination = Store(tmp_path / "restored", tmp_path / "restored-state" / "review.sqlite3")
    deck, recovered = open_project(destination, save_project(store, TEMPLATE_ID, state))
    assert recovered == state
    assert destination.comments(deck.id)[0].target == target


def test_groups_reject_nested_empty_oversized_and_direct_edit_requests(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bound context selections and prevent group anchors from becoming single-object edits."""
    target = group().model_dump()
    for elements in [[], [target], [group().elements[0].model_dump()] * 101]:
        with pytest.raises(ValidationError):
            _ = TypeAdapter(SlideTarget).validate_python({**target, "elements": elements})
    monkeypatch.setattr(service, "store", store)
    slide = store.view(TEMPLATE_ID).slides[0]
    response = TestClient(service.app).post(
        f"/api/decks/{TEMPLATE_ID}/slides/{slide.id}/feedback",
        json={
            "message": "Replace text",
            "target": target,
            "content_hash": slide.content_hash,
            "request_id": "invalid-group-edit",
            "edit_intent": {"action": "replace_text"},
        },
    )
    assert response.status_code == 400
    assert "exactly one" in response.text
    assert store.comments(TEMPLATE_ID) == []
