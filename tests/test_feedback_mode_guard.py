"""Feedback publication honors the delivery mode the human actually reviewed."""

import base64
import json
from pathlib import Path
from typing import Literal

import pytest
from fastapi.testclient import TestClient

from artifact_studio import app as service
from artifact_studio.artifact_models import ArtifactFeedbackInput, ArtifactView
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.models import FileInput
from artifact_studio.storage import ConflictError, Store, digest
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ArtifactStore, ArtifactView]:
    """Use an owned fictional artifact and independent private review state."""
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    artifacts = ArtifactStore(store)
    project = artifacts.create_project("Fictional review")
    source = tmp_path / "source"
    source.mkdir()
    _ = (source / "index.html").write_text("<h1>Fictional evidence</h1>")
    artifact = artifacts.register_artifact(project.id, "Evidence", source, "index.html")
    return artifacts, artifact


@pytest.mark.parametrize("displayed,actual", [("batch", "live"), ("live", "batch")])
def test_stale_displayed_mode_rejects_http_publication_without_releasing_drafts(
    workspace: tuple[ArtifactStore, ArtifactView],
    displayed: Literal["live", "batch"],
    actual: Literal["live", "batch"],
) -> None:
    """A stale delivery choice creates no event, receipt or attachment and keeps prior drafts."""
    artifacts, artifact = workspace
    _ = artifacts.set_mode(artifact.id, "batch")
    pending = artifacts.add_feedback(
        artifact.id,
        ArtifactFeedbackInput(
            message="Keep private",
            request_id="pending-feedback",
            content_hash=artifact.content_hash,
        ),
    )
    _ = artifacts.set_mode(artifact.id, actual)
    body = ArtifactFeedbackInput(
        message="Written under the displayed setting",
        request_id="stale-mode-feedback",
        content_hash=artifact.content_hash,
        expected_mode=displayed,
        files=[FileInput(name="private.txt", data_base64=base64.b64encode(b"Private").decode())],
    )
    client = TestClient(service.app, base_url="http://127.0.0.1")
    response = client.post(f"/api/artifacts/{artifact.id}/feedback", json=body.model_dump())
    assert response.status_code == 409, response.text
    assert "Feedback mode changed" in response.text
    assert artifacts.feedback(artifact.id, include_pending=True) == [pending]
    assert artifacts.feedback_feed(artifact.id).events == []
    assert artifacts.workflow(artifact.id).pending == 1
    assert not (artifacts.store.root / ".state/artifact-feedback" / artifact.id).exists()
    with artifacts.store.connect() as db:
        assert (
            db.execute(
                "SELECT 1 FROM artifact_operations WHERE artifact_id=? AND request_id=?",
                (artifact.id, body.request_id),
            ).fetchone()
            is None
        )


@pytest.mark.parametrize("mode", ["live", "batch"])
def test_exact_retry_retains_original_receipt_after_delivery_mode_changes(
    workspace: tuple[ArtifactStore, ArtifactView], mode: Literal["live", "batch"]
) -> None:
    """An acknowledged or lost-response write is never republished under the new mode."""
    artifacts, artifact = workspace
    _ = artifacts.set_mode(artifact.id, mode)
    body = ArtifactFeedbackInput(
        message="Original feedback",
        request_id="mode-bound-retry",
        content_hash=artifact.content_hash,
        expected_mode=mode,
    )
    original = artifacts.add_feedback(artifact.id, body)
    next_mode: Literal["live", "batch"] = "live" if mode == "batch" else "batch"
    _ = artifacts.set_mode(artifact.id, next_mode)
    assert artifacts.add_feedback(artifact.id, body) == original
    assert (
        artifacts.add_feedback(artifact.id, body.model_copy(update={"expected_mode": next_mode}))
        == original
    )
    assert (
        artifacts.add_feedback(artifact.id, body.model_copy(update={"expected_mode": None}))
        == original
    )
    assert original.released == (mode == "live")
    assert len(artifacts.feedback(artifact.id, include_pending=True)) == 1


def test_omitted_guard_preserves_existing_payload_receipts(
    workspace: tuple[ArtifactStore, ArtifactView],
) -> None:
    """Older clients retain their exact payload identity and unguarded publication behavior."""
    artifacts, artifact = workspace
    body = ArtifactFeedbackInput(
        message="Compatible feedback",
        request_id="older-client-feedback",
        content_hash=artifact.content_hash,
    )
    original = artifacts.add_feedback(artifact.id, body)
    legacy_payload = body.model_dump(exclude={"expected_mode"})
    with artifacts.store.connect() as db:
        row = db.execute(
            "SELECT payload_hash FROM artifact_operations WHERE artifact_id=? AND request_id=?",
            (artifact.id, body.request_id),
        ).fetchone()
    assert row is not None
    assert row[0] == digest(json.dumps(["feedback", "user", legacy_payload], sort_keys=True))
    _ = artifacts.set_mode(artifact.id, "batch")
    assert artifacts.add_feedback(artifact.id, body) == original


def test_mode_guard_uses_the_transaction_state_after_artifact_discovery(
    workspace: tuple[ArtifactStore, ArtifactView], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mode change after the preliminary artifact read still conflicts before insertion."""
    artifacts, artifact = workspace
    _ = artifacts.set_mode(artifact.id, "batch")
    discover = artifacts.get_artifact

    def discover_then_change(identifier: str) -> ArtifactView:
        result = discover(identifier)
        _ = artifacts.set_mode(identifier, "live")
        return result

    monkeypatch.setattr(artifacts, "get_artifact", discover_then_change)
    with pytest.raises(ConflictError, match="Feedback mode changed"):
        _ = artifacts.add_feedback(
            artifact.id,
            ArtifactFeedbackInput(
                message="Private draft",
                request_id="mode-changed-during-save",
                content_hash=artifact.content_hash,
                expected_mode="batch",
            ),
        )
    assert not artifacts.feedback(artifact.id, include_pending=True)


@pytest.mark.parametrize("scope", ["slide", "presentation"])
@pytest.mark.parametrize("displayed,actual", [("batch", "live"), ("live", "batch")])
def test_legacy_endpoints_guard_mode_and_keep_exact_retries(
    workspace: tuple[ArtifactStore, ArtifactView],
    scope: str,
    displayed: Literal["live", "batch"],
    actual: Literal["live", "batch"],
) -> None:
    """Specialized slide and presentation feedback share the transactional publication guard."""
    artifacts, _ = workspace
    store = artifacts.store
    make_design(store.root / TEMPLATE_ID)
    deck = store.create("2026-11-03-mode-review", "Fictional review", "test-brand")
    _ = store.set_mode(deck.id, "batch")
    _ = store.add_feedback(
        deck.id, "cover", "Keep private", "", "legacy-pending", expected_mode="batch"
    )
    pending = store.comments(deck.id)
    _ = store.set_mode(deck.id, actual)
    body: dict[str, object] = {
        "message": "Reviewed delivery choice",
        "request_id": "legacy-mode-feedback",
        "expected_mode": displayed,
        "files": [{"name": "private.txt", "data_base64": base64.b64encode(b"Private").decode()}],
    }
    if scope == "slide":
        path = f"/api/decks/{deck.id}/slides/cover/feedback"
        body["content_hash"] = store.view(deck.id).slides[0].content_hash
    else:
        path = f"/api/decks/{deck.id}/feedback"
        body["context_slide_id"] = "cover"
    client = TestClient(service.app, base_url="http://127.0.0.1")
    response = client.post(path, json=body)
    assert response.status_code == 409, response.text
    assert "Feedback mode changed" in response.text
    assert store.comments(deck.id) == pending
    assert store.workflow(deck.id).pending == 1
    assert not (store.database.parent / "feedback-files" / deck.id).exists()
    _ = store.set_mode(deck.id, displayed)
    saved = client.post(path, json=body)
    assert saved.status_code == 200, saved.text
    _ = store.set_mode(deck.id, actual)
    replay = client.post(path, json=body)
    assert replay.status_code == 200 and replay.text == saved.text
    changed_precondition = client.post(path, json=body | {"expected_mode": actual})
    assert changed_precondition.status_code == 200 and changed_precondition.text == saved.text
    unguarded_replay = client.post(path, json=body | {"expected_mode": None})
    assert unguarded_replay.status_code == 200 and unguarded_replay.text == saved.text
    events = store.comments(deck.id)
    assert len(events) == 2
    assert events[-1].released == (displayed == "live")
    assert not events[0].released


def test_legacy_omitted_guard_preserves_original_payload_hash(
    workspace: tuple[ArtifactStore, ArtifactView],
) -> None:
    """Existing storage and agent callers can replay feedback created before mode guards."""
    artifacts, _ = workspace
    store = artifacts.store
    make_design(store.root / TEMPLATE_ID)
    event_id = store.add_feedback(TEMPLATE_ID, "cover", "Legacy", "", "legacy-client-feedback")
    with store.connect() as db:
        row = db.execute(
            "SELECT payload_hash FROM operations WHERE deck=? AND request_id=?",
            (TEMPLATE_ID, "legacy-client-feedback"),
        ).fetchone()
    assert row is not None
    assert row[0] == digest(json.dumps(["cover", "Legacy", "", None, None]))
    _ = store.set_mode(TEMPLATE_ID, "batch")
    assert (
        store.add_feedback(TEMPLATE_ID, "cover", "Legacy", "", "legacy-client-feedback") == event_id
    )
    assert len(store.comments(TEMPLATE_ID)) == 1
