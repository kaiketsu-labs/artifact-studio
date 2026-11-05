"""Artifact-wide review leases, released delivery and presentation compatibility."""

import base64
from pathlib import Path
from typing import Literal

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from artifact_studio import app as service
from artifact_studio import artifacts as artifact_module
from artifact_studio.artifact_models import ArtifactFeedbackInput, ArtifactView
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.review import ReviewHookInput, Reviews
from artifact_studio.storage import Store


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    """Use an isolated collection for all HTTP and MCP-facing review operations."""
    store = Store(tmp_path / "collection", tmp_path / "review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    return store


def artifact(store: Store, tmp_path: Path, extension: str, title: str = "Example") -> ArtifactView:
    """Register an actual owned bundle with a viewer selected by its entrypoint."""
    artifacts = ArtifactStore(store)
    project = artifacts.create_project(title)
    source = tmp_path / project.id
    source.mkdir()
    name = "example." + extension
    data = {
        "html": b"<h1>Example</h1>",
        "md": b"# Example",
        "svg": b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>',
        "png": base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
        ),
        "csv": b"name,value\nExample,1\n",
    }[extension]
    _ = (source / name).write_bytes(data)
    return artifacts.register_artifact(project.id, title, source, name)


@pytest.mark.parametrize("extension", ["html", "md", "svg", "png", "csv"])
@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_all_artifact_kinds_release_feedback_to_their_owner(
    store: Store, tmp_path: Path, extension: str, harness: Literal["codex", "claude-code"]
) -> None:
    """Wake for released human requests only, retaining ended-review and cursor boundaries."""
    item = artifact(store, tmp_path, extension)
    other = artifact(store, tmp_path, "html", "Unrelated")
    artifacts = ArtifactStore(store)
    reviews = Reviews(store)
    body = ReviewHookInput(
        harness=harness,
        session_id="author",
        event="PostToolUse",
        artifact_id=item.id,
        auto_start=True,
    )
    assert service.get_live_review_status(artifact_id=item.id)["status"] == "unavailable"
    assert service.start_live_review(artifact_id=item.id)["status"] == "awaiting_host_hook"
    assert reviews.bind(body).listening
    assert not reviews.bind(body.model_copy(update={"session_id": "other"})).listening
    status = service.get_live_review_status(artifact_id=item.id)
    assert status["review_session"] == "author" and status["review_harness"] == harness
    assert status["workspace_url"] == "/?artifact=" + item.id
    assert artifacts.workflow(item.id).review_connected

    def comment(target: ArtifactView, text: str, author: Literal["user", "agent"] = "user") -> None:
        """Write through the same revision-bound queue as browser and agent replies."""
        _ = artifacts.add_feedback(
            target.id,
            ArtifactFeedbackInput(
                message=text,
                request_id="comment-" + text,
                content_hash=target.content_hash,
            ),
            author=author,
        )

    comment(other, "unrelated")
    _ = artifacts.set_mode(item.id, "batch")
    comment(item, "private")
    assert not reviews.pending(body).feedback_available
    assert not artifacts.feedback_feed(item.id).events
    _ = artifacts.set_mode(item.id, "live")
    comment(item, "released")
    pending = reviews.pending(body)
    assert pending.feedback_available
    assert f"read_artifact_feedback(artifact_id={item.id!r}, after_delivery_id=0)" in pending.reason
    assert "private" not in pending.reason and "released" not in pending.reason
    feed = artifacts.feedback_feed(item.id)
    assert [event.message for event in feed.events] == ["released"]
    body = body.model_copy(update={"seen_cursor": feed.next_cursor})
    assert reviews.bind(body).listening
    assert not reviews.pending(body).feedback_available
    comment(item, "agent-reply", "agent")
    assert not reviews.pending(body).feedback_available
    _ = artifacts.release_batch(item.id)
    assert reviews.pending(body).feedback_available
    late = artifacts.feedback_feed(item.id, feed.next_cursor)
    assert [event.message for event in late.events] == ["private"]

    client = TestClient(service.app, base_url="http://127.0.0.1")
    detail = client.get(f"/api/artifacts/{item.id}").json()
    assert detail["delivery"]["live_available"]
    ended = client.post(f"/api/artifacts/{item.id}/review/disconnect")
    assert ended.is_success and not ended.json()["review_connected"]
    assert not reviews.pending(body).listening
    assert not reviews.bind(body).listening
    assert reviews.bind(body.model_copy(update={"start_review": True})).listening
    assert reviews.pending(body).feedback_available
    assert not reviews.release(body).listening
    assert not artifacts.workflow(item.id).review_connected


def test_artifact_switch_resets_only_target_cursor_and_rejects_ambiguous_targets(
    store: Store, tmp_path: Path
) -> None:
    """Never carry one artifact's acknowledged feedback into another artifact's queue."""
    first = artifact(store, tmp_path, "html")
    second = artifact(store, tmp_path, "md")
    reviews = Reviews(store)
    body = ReviewHookInput(
        harness="codex",
        session_id="author",
        event="PostToolUse",
        artifact_id=first.id,
        auto_start=True,
        seen_cursor=900,
    )
    assert reviews.bind(body).listening
    reviews.disconnect(first.id)
    switched = body.model_copy(update={"artifact_id": second.id, "seen_cursor": None})
    assert reviews.bind(switched).listening
    assert "after_delivery_id=0" in reviews.pending(switched).reason
    assert not ArtifactStore(store).workflow(first.id).review_connected
    assert not reviews.bind(body.model_copy(update={"seen_cursor": None})).listening
    assert ArtifactStore(store).workflow(second.id).review_connected
    assert reviews.bind(body.model_copy(update={"start_review": True})).listening
    with pytest.raises(ValidationError):
        ReviewHookInput(
            harness="codex",
            session_id="author",
            event="PostToolUse",
            artifact_id=first.id,
            deck_id="some-deck",
        )
    with pytest.raises(ToolError):
        _ = service.get_live_review_status(artifact_id=first.id, deck_id="some-deck")
    with pytest.raises(ToolError):
        _ = service.start_live_review(artifact_id="missing")


def test_binding_and_status_do_not_rescan_artifact_files(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep connection setup and lease reads independent of artifact bundle size."""
    item = artifact(store, tmp_path, "html")

    def forbid_scan(*args: object, **kwargs: object) -> object:
        """Fail if a lifecycle metadata operation attempts to read authored file bodies."""
        pytest.fail("Review connection must not rescan artifact files.")

    monkeypatch.setattr(artifact_module, "read_tree", forbid_scan)
    reviews = Reviews(store)
    body = ReviewHookInput(
        harness="codex",
        session_id="metadata-only",
        event="PostToolUse",
        artifact_id=item.id,
        auto_start=True,
    )
    assert reviews.bind(body).listening
    assert service.get_live_review_status(artifact_id=item.id)["status"] == "connected"
