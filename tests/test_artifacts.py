"""Behavioral checks for owned artifacts, retained review versions and draft isolation."""

import base64
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from artifact_studio import artifacts as artifact_module
from artifact_studio.artifact_models import ArtifactFeedbackInput, ArtifactFileInput, ArtifactTarget
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.models import FileInput
from artifact_studio.storage import ConflictError, Store
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def artifacts(tmp_path: Path) -> ArtifactStore:
    """Create a clean local workspace without a presentation design or user files."""
    return ArtifactStore(Store(tmp_path / "workspace", tmp_path / "state" / "review.sqlite3"))


def bundle(tmp_path: Path) -> Path:
    """Build arbitrary original source with an entrypoint and independently hashed assets."""
    folder = tmp_path / "source"
    folder.mkdir()
    _ = (folder / "index.html").write_text('<script src="app.js"></script><h1>Hello</h1>')
    _ = (folder / "app.js").write_text("document.body.dataset.state='ready'")
    _ = (folder / "image.bin").write_bytes(b"\xff\x00\x81")
    return folder


def test_registration_preserves_original_and_needs_no_design(
    artifacts: ArtifactStore,
    tmp_path: Path,
) -> None:
    """Register arbitrary source without template restrictions or moving originals."""
    original = bundle(tmp_path)
    project = artifacts.create_project("Research", "Shared audience")
    artifact = artifacts.register_artifact(project.id, "Interactive story", original, "index.html")
    assert artifact.kind == "html" and artifact.status == "draft"
    assert artifact.revision == 1 and not artifact.has_unseen_changes
    assert len(artifact.files) == 3
    assert artifacts.list_projects()[0].artifact_count == 1
    assert artifacts.list_artifacts(project.id)[0].id == artifact.id
    assert (original / "app.js").read_text() == "document.body.dataset.state='ready'"
    assert Path(artifact.source_path).is_relative_to(artifacts.store.root / "projects")
    assert (Path(project.source_path) / "project.json").exists()
    assert (Path(artifact.source_path).parent / "artifact.json").exists()
    contents = artifacts.read_artifact_files(artifact.id, ["image.bin", "index.html"])
    assert base64.b64decode(contents[0].data_base64 or "") == b"\xff\x00\x81"
    assert contents[1].text and '<script src="app.js">' in contents[1].text
    _ = (Path(project.source_path) / "README.md").write_text("Updated shared audience")
    assert artifacts.list_projects()[0].context == "Updated shared audience"
    assert artifacts.snapshot_files(artifact.id, 1)["image.bin"] == b"\xff\x00\x81"


def test_revisions_dependencies_approvals_seen_and_exact_retries(
    artifacts: ArtifactStore,
    tmp_path: Path,
) -> None:
    """Asset-only changes invalidate approval and retain the earlier bytes for review."""
    project = artifacts.create_project("Project")
    initial = artifacts.register_artifact(project.id, "Page", bundle(tmp_path), "index.html")
    assert artifacts.approve(initial.id, initial.content_hash, True).approved
    changes = [ArtifactFileInput(path="app.js", text="console.log('changed')")]
    updated = artifacts.revise_artifact(initial.id, changes, initial.content_hash, "revision-001")
    assert updated.revision == 2 and updated.status == "updated" and not updated.approved
    assert (
        artifacts.revise_artifact(initial.id, changes, initial.content_hash, "revision-001")
        == updated
    )
    assert (
        artifacts.read_artifact_files(initial.id, ["app.js"], revision=1)[0].text
        == "document.body.dataset.state='ready'"
    )
    assert artifacts.read_artifact_files(initial.id, ["app.js"])[0].text == "console.log('changed')"
    with pytest.raises(ConflictError):
        _ = artifacts.revise_artifact(initial.id, changes, 2, "revision-001")
    with pytest.raises(ConflictError):
        _ = artifacts.revise_artifact(initial.id, changes, 1, "revision-002")
    with pytest.raises(ConflictError):
        _ = artifacts.approve(initial.id, initial.content_hash, True)
    with pytest.raises(ConflictError):
        _ = artifacts.mark_seen(initial.id, initial.content_hash)
    assert artifacts.mark_seen(initial.id, updated.content_hash).status == "draft"
    assert artifacts.approve(initial.id, updated.content_hash, True).status == "approved"


def test_external_edits_become_new_revisions_and_snapshot_tampering_is_detected(
    artifacts: ArtifactStore,
    tmp_path: Path,
) -> None:
    """Reconcile working sources while refusing to silently rewrite retained history."""
    project = artifacts.create_project("Project")
    initial = artifacts.register_artifact(project.id, "Page", bundle(tmp_path), "index.html")
    _ = artifacts.approve(initial.id, initial.content_hash, True)
    _ = (Path(initial.source_path) / "image.bin").write_bytes(b"\xff\x00\x82")
    updated = artifacts.get_artifact(initial.id)
    assert updated.revision == 2 and updated.status == "updated"
    assert (
        artifacts.read_artifact_files(initial.id, ["image.bin"], revision=1)[0].data_base64
        == base64.b64encode(b"\xff\x00\x81").decode()
    )
    snapshot = artifacts.resolve_file(initial.id, "app.js", revision=2)
    _ = snapshot.write_text("tampered")
    with pytest.raises(ConflictError, match="immutable"):
        _ = artifacts.get_artifact(initial.id)


@pytest.mark.parametrize(
    "path", ["../secret", "/etc/passwd", "a/../../secret", "a\\secret", "a//b", "./index.html"]
)
def test_unsafe_paths_never_resolve(
    artifacts: ArtifactStore,
    tmp_path: Path,
    path: str,
) -> None:
    """Reject traversal and aliases for both source reads and revisions."""
    project = artifacts.create_project("Project")
    artifact = artifacts.register_artifact(project.id, "Page", bundle(tmp_path), "index.html")
    with pytest.raises(ValueError):
        _ = artifacts.resolve_file(artifact.id, path)
    with pytest.raises(ValueError):
        _ = artifacts.revise_artifact(
            artifact.id, [ArtifactFileInput(path=path, text="x")], 1, "unsafe-path"
        )


def test_symlinks_and_entrypoint_deletion_are_rejected(
    artifacts: ArtifactStore,
    tmp_path: Path,
) -> None:
    """Prevent linked files from crossing ownership boundaries before any publication."""
    original = bundle(tmp_path)
    project = artifacts.create_project("Project")
    (original / "linked").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symlink"):
        _ = artifacts.register_artifact(project.id, "Page", original, "index.html")
    (original / "linked").unlink()
    artifact = artifacts.register_artifact(project.id, "Page", original, "index.html")
    with pytest.raises(ValueError, match="entrypoint"):
        _ = artifacts.revise_artifact(
            artifact.id, [ArtifactFileInput(path="index.html", delete=True)], 1, "delete-entry"
        )
    assert artifacts.get_artifact(artifact.id).revision == 1
    local = Path(artifact.source_path) / "app.js"
    local.unlink()
    local.symlink_to(original / "app.js")
    with pytest.raises(ValueError, match="symlink"):
        _ = artifacts.get_artifact(artifact.id)


def test_batch_feedback_drafts_and_attachments_are_private(
    artifacts: ArtifactStore,
    tmp_path: Path,
) -> None:
    """Agents cannot read or reply to withheld comments or fetch their attachments."""
    project = artifacts.create_project("Project")
    artifact = artifacts.register_artifact(project.id, "Page", bundle(tmp_path), "index.html")
    _ = artifacts.set_mode(artifact.id, "batch")
    group = ArtifactTarget(
        kind="group",
        elements=[
            ArtifactTarget(kind="element", selector="h1"),
            ArtifactTarget(kind="text", quote="Hello"),
        ],
    )
    body = ArtifactFeedbackInput(
        message="Private draft",
        request_id="private-comment",
        content_hash=artifact.content_hash,
        target=group,
        files=[
            FileInput(name="brief.txt", data_base64=base64.b64encode(b"private reference").decode())
        ],
    )
    draft = artifacts.add_feedback(artifact.id, body)
    assert not draft.released and draft.target == group
    assert artifacts.add_feedback(artifact.id, body) == draft
    assert artifacts.feedback(artifact.id) == []
    assert artifacts.feedback_feed(artifact.id).events == []
    assert artifacts.feedback(artifact.id, include_pending=True)[0].message == "Private draft"
    with pytest.raises(FileNotFoundError):
        _ = artifacts.resolve_feedback_file(artifact.id, draft.event_id, 0)
    assert (
        artifacts.resolve_feedback_file(artifact.id, draft.event_id, 0, include_pending=True)[
            0
        ].read_bytes()
        == b"private reference"
    )
    reply = ArtifactFeedbackInput(
        message="Agent reply",
        request_id="reply-private",
        content_hash=artifact.content_hash,
        reply_to=draft.event_id,
    )
    with pytest.raises(ValueError, match="visible feedback"):
        _ = artifacts.add_feedback(artifact.id, reply, author="agent")
    _ = artifacts.set_mode(artifact.id, "live")
    live = artifacts.add_feedback(
        artifact.id,
        ArtifactFeedbackInput(
            message="Later live", request_id="later-live", content_hash=artifact.content_hash
        ),
    )
    assert live.delivery_id is not None
    assert artifacts.workflow(artifact.id).pending == 1
    assert [event.message for event in artifacts.feedback_feed(artifact.id).events] == [
        "Later live"
    ]
    _ = artifacts.release_batch(artifact.id)
    delivered = artifacts.feedback_feed(artifact.id, live.delivery_id)
    assert [event.message for event in delivered.events] == ["Private draft"]
    assert delivered.events[0].batch_id and delivered.events[0].target == group
    assert (
        artifacts.resolve_feedback_file(artifact.id, draft.event_id, 0)[0].read_bytes()
        == b"private reference"
    )
    assert artifacts.add_feedback(artifact.id, reply, author="agent").released


def test_old_feedback_stays_bound_to_original_snapshot(
    artifacts: ArtifactStore,
    tmp_path: Path,
) -> None:
    """Keep original targets and source available after later agent changes."""
    project = artifacts.create_project("Project")
    artifact = artifacts.register_artifact(project.id, "Page", bundle(tmp_path), "index.html")
    target = ArtifactTarget(kind="element", selector="h1", quote="Hello")
    _ = artifacts.add_feedback(
        artifact.id,
        ArtifactFeedbackInput(
            message="Revise this",
            request_id="old-feedback",
            content_hash=artifact.content_hash,
            target=target,
        ),
    )
    _ = artifacts.revise_artifact(
        artifact.id, [ArtifactFileInput(path="index.html", text="Changed")], 1, "new-content"
    )
    old = artifacts.feedback(artifact.id)[0]
    assert old.stale and old.revision == 1 and old.target == target
    assert (
        artifacts.read_artifact_files(artifact.id, ["index.html"], old.revision)[0].text
        == '<script src="app.js"></script><h1>Hello</h1>'
    )


def test_concurrent_writers_cannot_overwrite_each_other(
    artifacts: ArtifactStore,
    tmp_path: Path,
) -> None:
    """Only one writer can publish against a shared revision baseline."""
    project = artifacts.create_project("Project")
    artifact = artifacts.register_artifact(project.id, "Page", bundle(tmp_path), "index.html")

    def revise(index: int) -> bool:
        """Attempt a distinct concurrent update against the same version."""
        try:
            _ = artifacts.revise_artifact(
                artifact.id,
                [ArtifactFileInput(path="app.js", text=str(index))],
                1,
                f"concurrent-{index}",
            )
        except ConflictError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(revise, [1, 2]))
    assert sorted(results) == [False, True]
    assert artifacts.get_artifact(artifact.id).revision == 2


def test_legacy_decks_keep_ids_sources_and_feedback(
    artifacts: ArtifactStore,
) -> None:
    """Expose existing presentations and explicit project membership entirely in place."""
    store = artifacts.store
    make_design(store.root / TEMPLATE_ID)
    deck = store.create("2026-11-02-legacy-deck", "Existing deck")
    source = (store.folder(deck.id) / "index.html").read_bytes()
    _ = store.add_feedback(deck.id, None, "Existing feedback", "", "legacy-comment")
    adapted = artifacts.get_artifact(deck.id)
    assert adapted.id == deck.id and adapted.legacy_deck_id == deck.id
    assert adapted.kind == "presentation"
    assert artifacts.feedback(deck.id)[0].message == "Existing feedback"
    project = artifacts.create_project("Deck and handout")
    assert artifacts.set_artifact_project(deck.id, project.id).project_id == project.id
    assert artifacts.list_artifacts(project.id)[0].id == deck.id
    assert (store.folder(deck.id) / "index.html").read_bytes() == source


def test_legacy_discovery_uses_stats_without_bundle_limits_or_binary_reads(
    artifacts: ArtifactStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Large existing files and symlinks cannot prevent stat-only legacy discovery."""
    store = artifacts.store
    make_design(store.root / TEMPLATE_ID)
    deck = store.create("2026-11-02-large-legacy", "Large existing deck")
    folder = store.folder(deck.id)
    huge = folder / "assets" / "large-original.pptx"
    with huge.open("wb") as stream:
        _ = stream.truncate(400_000_001)
    external = tmp_path / "external-reference.txt"
    _ = external.write_text("External reference")
    (folder / "assets" / "linked.txt").symlink_to(external)
    (folder / "assets" / "linked-folder").symlink_to(tmp_path, target_is_directory=True)
    reads: list[Path] = []
    original_read = Path.read_bytes

    def reject_tree(path: Path) -> dict[str, bytes]:
        """Fail any attempt to capture legacy bytes through the owned-bundle reader."""
        raise AssertionError(f"Legacy discovery must not read_tree: {path}")

    def track_read(path: Path) -> bytes:
        """Record individual byte reads without ever allowing the large fixture to load."""
        assert path != huge
        reads.append(path)
        return original_read(path)

    monkeypatch.setattr(artifact_module, "read_tree", reject_tree)
    monkeypatch.setattr(Path, "read_bytes", track_read)
    discovered = next(item for item in artifacts.list_artifacts() if item.id == deck.id)
    assert not reads
    assert discovered.snapshot_mode == "legacy-in-place"
    assert all(file.sha256 == "" for file in discovered.files)
    oversized = next(file for file in discovered.files if file.path == "assets/large-original.pptx")
    assert oversized.size == 400_000_001
    assert not any("linked" in file.path for file in discovered.files)
    requested = artifacts.read_artifact_files(deck.id, ["theme.css"])
    assert reads == [folder / "theme.css"]
    assert requested[0].sha256 == hashlib.sha256((requested[0].text or "").encode()).hexdigest()
    with pytest.raises(ValueError, match="symlink"):
        _ = artifacts.read_artifact_files(deck.id, ["assets/linked.txt"])
    with pytest.raises(ValueError, match="symlink"):
        _ = artifacts.resolve_file(deck.id, "assets/linked-folder/external-reference.txt")


def test_group_targets_reject_nested_or_unbounded_selections() -> None:
    """Preserve structured multiselections while bounding their shape and depth."""
    group = ArtifactTarget(kind="group", elements=[ArtifactTarget(kind="element", selector="h1")])
    with pytest.raises(ValidationError):
        _ = ArtifactTarget(kind="group", elements=[group])
    with pytest.raises(ValidationError):
        _ = ArtifactTarget(kind="group", elements=[ArtifactTarget()] * 101)


def test_project_reassignment_preserves_sources_and_all_revisions(
    artifacts: ArtifactStore,
    tmp_path: Path,
) -> None:
    """Move membership independently of stable owned storage and historical review context."""
    original = artifacts.create_project("Original")
    destination = artifacts.create_project("Destination")
    artifact = artifacts.register_artifact(original.id, "Page", bundle(tmp_path), "index.html")
    historical_path = artifacts.resolve_file(artifact.id, "app.js", 1)
    moved = artifacts.set_artifact_project(artifact.id, destination.id)
    assert moved.project_id == destination.id and moved.source_path == artifact.source_path
    assert artifacts.resolve_file(artifact.id, "app.js", 1) == historical_path
    assert artifacts.list_artifacts(original.id) == []
    assert artifacts.list_artifacts(destination.id)[0].id == artifact.id
    revised = artifacts.revise_artifact(
        artifact.id,
        [ArtifactFileInput(path="app.js", text="updated")],
        1,
        "after-project-move",
    )
    assert revised.project_id == destination.id and revised.source_path == artifact.source_path
    assert artifacts.read_artifact_files(artifact.id, ["app.js"])[0].text == "updated"
    assert artifacts.read_artifact_files(artifact.id, ["app.js"], 1)[0].text != "updated"


@pytest.mark.parametrize("failure", ["record", "mirror", "commit"])
def test_failed_revision_restores_working_files_and_manifest(
    artifacts: ArtifactStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """A failed publication leaves the prior revision intact and allows an exact retry."""
    project = artifacts.create_project("Project")
    initial = artifacts.register_artifact(project.id, "Page", bundle(tmp_path), "index.html")
    mirror = Path(initial.source_path).parent / "artifact.json"
    original_manifest = mirror.read_bytes()
    changes = [ArtifactFileInput(path="app.js", text="changed")]

    with monkeypatch.context() as patch:
        if failure == "commit":
            original_connect = sqlite3.connect

            class FailingConnection(sqlite3.Connection):
                """Inject a failure at the revision's explicit commit."""

                def commit(self) -> None:
                    """Reject the commit before it publishes any changes."""
                    raise sqlite3.OperationalError("injected commit failure")

            def failing_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
                """Create a connection that fails on explicit commit."""
                return original_connect(*args, **kwargs, factory=FailingConnection)  # type: ignore[arg-type]

            patch.setattr(sqlite3, "connect", failing_connect)
        else:

            def fail(*args: object) -> None:
                """Reject publication after the working directory is swapped."""
                raise OSError("injected publication failure")

            patch.setattr(artifacts, "_record" if failure == "record" else "_mirror", fail)
        with pytest.raises((OSError, sqlite3.OperationalError)):
            artifacts.revise_artifact(initial.id, changes, initial.revision, "revision-failure")

    assert mirror.read_bytes() == original_manifest
    assert artifacts.get_artifact(initial.id).revision == initial.revision
    assert artifacts.read_artifact_files(initial.id, ["app.js"])[0].text == (
        "document.body.dataset.state='ready'"
    )
    updated = artifacts.revise_artifact(initial.id, changes, initial.revision, "revision-failure")
    assert updated.revision == 2
    assert (
        artifacts.revise_artifact(initial.id, changes, initial.revision, "revision-failure")
        == updated
    )


def test_structured_edit_intent_round_trips_with_revision(
    artifacts: ArtifactStore, tmp_path: Path
) -> None:
    """Persist edit proposals with their original target and return them through the feed."""
    from artifact_studio.models import EditIntent

    project = artifacts.create_project("Project")
    artifact = artifacts.register_artifact(project.id, "Page", bundle(tmp_path), "index.html")
    intent = EditIntent(action="replace_text", values={"text": "Updated"})
    request = ArtifactFeedbackInput(
        message="Replace text.",
        request_id="edit-proposal-001",
        content_hash=artifact.content_hash,
        target=ArtifactTarget(kind="element", selector="h1"),
        edit_intent=intent,
    )
    saved = artifacts.add_feedback(artifact.id, request)
    assert saved.edit_intent == intent
    assert artifacts.add_feedback(artifact.id, request) == saved
    event = artifacts.feedback_feed(artifact.id).events[0]
    assert event.edit_intent == intent
    assert event.target == request.target and event.content_hash == artifact.content_hash
    old_request = ArtifactFeedbackInput(
        message="Ordinary feedback", request_id="ordinary-001", content_hash=artifact.content_hash
    )
    assert artifacts.add_feedback(artifact.id, old_request).edit_intent is None
