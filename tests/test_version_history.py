"""Fictional-source checks for compact history, exact previews and safe restores."""

import os
from pathlib import Path

import pytest

from artifact_studio.artifact_models import ArtifactFileInput
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.models import AgentContext, SlidePatch
from artifact_studio.storage import ConflictError, Store, digest
from artifact_studio.version_history import VersionHistory, capture_slide_version
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def history(tmp_path: Path) -> VersionHistory:
    """Keep every test in a separate fictional workspace and database."""
    root = tmp_path / "workspace"
    make_design(root / TEMPLATE_ID)
    store = Store(root, tmp_path / "state/review.sqlite3")
    return VersionHistory(store, ArtifactStore(store))


def save_slide(history: VersionHistory, number: int) -> None:
    """Apply an audience-visible save with a distinct idempotency ID."""
    slide = history.store.locate(history.store.read(TEMPLATE_ID), "cover")
    _ = history.store.revise(
        TEMPLATE_ID,
        "cover",
        digest(slide.model_dump_json()),
        f"history-save-{number}",
        SlidePatch(title=f"Fictional version {number}"),
    )


def register_bundle(history: VersionHistory, tmp_path: Path) -> str:
    """Register a source bundle containing both readable content and arbitrary binary bytes."""
    source = tmp_path / "bundle"
    source.mkdir()
    _ = (source / "index.html").write_text("<h1>Original fictional page</h1>")
    _ = (source / "asset.bin").write_bytes(b"\x00\xff" * 30_000)
    project = history.artifacts.create_project("Fictional project", "Current project context")
    return history.artifacts.register_artifact(
        project.id, "Fictional artifact", source, "index.html", context="Current artifact context"
    ).id


def test_slide_history_is_bounded_source_free_and_stable_during_new_saves(
    history: VersionHistory,
) -> None:
    """Paginate rapid saves without repeating source or skipping versions after a new write."""
    for number in range(1, 4):
        save_slide(history, number)
    page = history.list_slide(TEMPLATE_ID, "cover", limit=1)
    assert [item.revision for item in page.items] == [4]
    assert page.items[0].is_current and page.items[0].created_at is not None
    assert page.continues_group == page.items[0].group_id
    assert page.groups[0].count == 3 and page.next_cursor is not None
    assert "<article" not in page.model_dump_json() and '"notes"' not in page.model_dump_json()
    assert len(page.model_dump_json()) < 3000
    save_slide(history, 4)
    older = history.list_slide(TEMPLATE_ID, "cover", cursor=page.next_cursor, limit=10)
    assert [item.revision for item in older.items] == [3, 2, 1]
    assert older.items[-1].created_at is None and older.next_cursor is None
    selected = history.get_slide_version(TEMPLATE_ID, "cover", page.items[0].version_id)
    assert selected.slide.title == "Fictional version 3"
    assert "shared theme" in selected.fidelity_warning


def test_private_only_saves_do_not_clutter_slide_history(history: VersionHistory) -> None:
    """Human notes, visibility and agent context share the current visual timeline entry."""
    save_slide(history, 1)
    before = history.list_slide(TEMPLATE_ID, "cover")
    current = history.store.locate(history.store.read(TEMPLATE_ID), "cover")
    _ = history.store.save_notes(TEMPLATE_ID, "cover", "Human note", current.notes)
    history.store.set_slide_hidden(TEMPLATE_ID, "cover", hidden=True, expected_hidden=False)
    current = history.store.locate(history.store.read(TEMPLATE_ID), "cover")
    _ = history.store.revise(
        TEMPLATE_ID,
        "cover",
        digest(current.model_dump_json()),
        "context-only-save",
        SlidePatch(agent=AgentContext(summary="New private summary")),
    )
    after = history.list_slide(TEMPLATE_ID, "cover")
    assert [item.version_id for item in before.items] == [item.version_id for item in after.items]
    assert before.expected_hash != after.expected_hash
    assert after.items[0].is_current


def test_slide_save_groups_split_after_idle_time(history: VersionHistory) -> None:
    """A three-minute idle boundary groups rapid saves while retaining every individual save."""
    deck = history.store.read(TEMPLATE_ID)
    original = deck.slides[0]
    with history.store.connect() as db:
        for revision, saved_at in (
            (1, "2026-11-03T10:00:00+00:00"),
            (2, "2026-11-03T10:02:00+00:00"),
            (3, "2026-11-03T10:08:00+00:00"),
        ):
            slide = original.model_copy(update={"revision": revision, "title": f"Save {revision}"})
            capture_slide_version(db, TEMPLATE_ID, slide, saved_at=saved_at)
            deck.slides[0] = slide
    history.store.write(TEMPLATE_ID, deck)
    page = history.list_slide(TEMPLATE_ID, "cover")
    assert len(page.items) == 3 and len(page.groups) == 2
    assert page.items[1].group_id == page.items[2].group_id != page.items[0].group_id
    assert [group.count for group in page.groups] == [1, 2]


def test_legacy_slide_metadata_migration_never_invents_saved_dates(history: VersionHistory) -> None:
    """Backfilled source hashes remain stable and unknown timestamps stay explicitly nullable."""
    save_slide(history, 1)
    before = (history.store.folder(TEMPLATE_ID) / "index.html").read_bytes()
    with history.store.connect() as db:
        _ = db.execute("DROP TRIGGER retain_slide_version_metadata")
        _ = db.execute("DROP TABLE slide_version_metadata")
    reopened = Store(history.store.root, history.store.database)
    migrated = VersionHistory(reopened, ArtifactStore(reopened)).list_slide(TEMPLATE_ID, "cover")
    assert len(migrated.items) == 2
    assert all(item.created_at is None for item in migrated.items)
    assert (history.store.folder(TEMPLATE_ID) / "index.html").read_bytes() == before


def test_slide_restore_preserves_current_notes_visibility_order_and_drops_old_approval(
    history: VersionHistory,
) -> None:
    """A restore changes audience source while preserving independent human presentation state."""
    original = history.store.read(TEMPLATE_ID).slides[0]
    original_hash = history.store.content_hash(TEMPLATE_ID, original)
    history.store.approve(TEMPLATE_ID, "cover", original_hash, True)
    save_slide(history, 1)
    _ = history.store.save_notes(TEMPLATE_ID, "cover", "Current human note", "")
    history.store.set_slide_hidden(TEMPLATE_ID, "cover", hidden=True, expected_hidden=False)
    _ = history.store.reorder(TEMPLATE_ID, ["message", "cover"])
    page = history.list_slide(TEMPLATE_ID, "cover")
    historical_id = page.items[-1].version_id
    restored = history.restore_slide(
        TEMPLATE_ID, "cover", historical_id, page.expected_hash, "restore-slide-001"
    )
    deck = history.store.read(TEMPLATE_ID)
    assert restored.html == original.html and restored.title == original.title
    assert restored.revision == 3 and not restored.approved
    assert (
        restored.notes == "Current human note" and restored.notes_revision == 1 and restored.hidden
    )
    assert [slide.id for slide in deck.slides] == ["message", "cover"]
    assert restored.expected_hash != page.expected_hash
    _ = history.store.save_notes(TEMPLATE_ID, "cover", "Later human note", restored.notes)
    assert (
        history.restore_slide(
            TEMPLATE_ID, "cover", historical_id, page.expected_hash, "restore-slide-001"
        )
        == restored
    )
    assert (
        history.store.locate(history.store.read(TEMPLATE_ID), "cover").notes == "Later human note"
    )
    with pytest.raises(ConflictError, match="another payload"):
        _ = history.restore_slide(
            TEMPLATE_ID, "cover", page.items[0].version_id, page.expected_hash, "restore-slide-001"
        )
    with pytest.raises(ConflictError, match="Slide changed"):
        _ = history.restore_slide(
            TEMPLATE_ID, "cover", historical_id, page.expected_hash, "restore-slide-002"
        )


def test_artifact_lists_do_not_open_snapshot_files_and_retain_group_pagination(
    history: VersionHistory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Metadata-only reads avoid hashing bundles and include individual save expansion metadata."""
    artifact_id = register_bundle(history, tmp_path)
    for number in range(1, 4):
        _ = history.artifacts.revise_artifact(
            artifact_id,
            [ArtifactFileInput(path="index.html", text=f"Version {number}")],
            number,
            f"artifact-history-{number}",
        )

    def forbidden_read(_path: Path) -> bytes:
        """Fail if listing tries to load binary or source file bodies."""
        raise AssertionError("History metadata must not read source files")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    page = history.list_artifact(artifact_id, limit=2)
    assert [item.revision for item in page.items] == [4, 3]
    assert page.groups[0].count == 4 and page.next_cursor is not None
    assert page.continues_group == page.items[0].group_id
    assert page.items[0].file_count == 2 and page.items[0].size_bytes > 60_000
    assert len(page.model_dump_json()) < 4000
    older = history.list_artifact(artifact_id, cursor=page.next_cursor)
    assert [item.revision for item in older.items] == [2, 1]
    assert older.groups[0].count == 4 and older.next_cursor is None


def test_artifact_restore_recovers_exact_binary_bundle_as_new_unapproved_revision(
    history: VersionHistory, tmp_path: Path
) -> None:
    """Restore removes newer files and recovers old bytes without reviving prior approval."""
    artifact_id = register_bundle(history, tmp_path)
    original = history.artifacts.get_artifact(artifact_id)
    initial_token = history.list_artifact(artifact_id).expected_hash
    _ = history.artifacts.approve(artifact_id, original.content_hash, True)
    changed = history.artifacts.revise_artifact(
        artifact_id,
        [
            ArtifactFileInput(path="index.html", text="Changed"),
            ArtifactFileInput(path="asset.bin", delete=True),
            ArtifactFileInput(path="new.txt", text="New file"),
        ],
        original.content_hash,
        "change-before-restore",
    )
    target_project = history.artifacts.create_project("Current destination")
    _ = history.artifacts.set_artifact_project(artifact_id, target_project.id)
    assert changed.revision == 2
    expected = history.list_artifact(artifact_id).expected_hash
    restored = history.restore_artifact(artifact_id, "artifact:1", expected, "restore-artifact-001")
    assert restored.revision == 3 and restored.content_hash == original.content_hash
    assert not restored.approved and restored.project_id == target_project.id
    assert restored.context == original.context and restored.title == original.title
    assert history.artifacts.snapshot_files(artifact_id, 3) == history.artifacts.snapshot_files(
        artifact_id, 1
    )
    assert (Path(restored.source_path) / "asset.bin").read_bytes() == b"\x00\xff" * 30_000
    assert not (Path(restored.source_path) / "new.txt").exists()
    assert (
        history.restore_artifact(artifact_id, "artifact:1", expected, "restore-artifact-001")
        == restored
    )
    same_bytes = history.restore_artifact(
        artifact_id,
        "artifact:1",
        history.list_artifact(artifact_id).expected_hash,
        "restore-same-bytes",
    )
    assert same_bytes.revision == 4 and not same_bytes.approved
    with pytest.raises(ConflictError, match="another artifact operation"):
        _ = history.restore_artifact(artifact_id, "artifact:2", expected, "restore-artifact-001")
    with pytest.raises(ConflictError, match="Artifact changed"):
        _ = history.restore_artifact(artifact_id, "artifact:1", expected, "restore-artifact-002")
    with pytest.raises(ConflictError, match="Artifact changed"):
        _ = history.restore_artifact(
            artifact_id, "artifact:1", initial_token, "restore-reject-stale-aba"
        )


def test_unchanged_snapshot_files_share_inodes_but_working_sources_never_do(
    history: VersionHistory, tmp_path: Path
) -> None:
    """Disk deduplication links only retained immutable snapshots, isolating all working edits."""
    artifact_id = register_bundle(history, tmp_path)
    original = history.artifacts.get_artifact(artifact_id)
    _ = history.artifacts.revise_artifact(
        artifact_id,
        [ArtifactFileInput(path="index.html", text="Changed")],
        original.content_hash,
        "deduplicated-revision",
    )
    old = history.artifacts.resolve_file(artifact_id, "asset.bin", revision=1)
    new = history.artifacts.resolve_file(artifact_id, "asset.bin", revision=2)
    working = Path(original.source_path) / "asset.bin"
    assert old.stat().st_ino == new.stat().st_ino
    assert working.stat().st_ino != old.stat().st_ino
    _ = working.write_bytes(b"Mutable working edit")
    assert old.read_bytes() == b"\x00\xff" * 30_000
    assert new.read_bytes() == old.read_bytes()


def test_snapshot_deduplication_falls_back_when_hardlinks_are_unavailable(
    history: VersionHistory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A filesystem without hardlink support still retains complete independent versions."""
    artifact_id = register_bundle(history, tmp_path)

    def unavailable_link(*_args: object, **_kwargs: object) -> None:
        """Simulate a storage device which refuses hardlinks."""
        raise OSError("Hardlinks unavailable")

    monkeypatch.setattr(os, "link", unavailable_link)
    _ = history.artifacts.revise_artifact(
        artifact_id,
        [ArtifactFileInput(path="index.html", text="Changed")],
        1,
        "revision-without-hardlinks",
    )
    old = history.artifacts.resolve_file(artifact_id, "asset.bin", revision=1)
    new = history.artifacts.resolve_file(artifact_id, "asset.bin", revision=2)
    assert old.read_bytes() == new.read_bytes() and old.stat().st_ino != new.stat().st_ino


@pytest.mark.parametrize("cursor", ["bad", "-1", "0", "9" * 30])
def test_invalid_cursors_are_rejected(history: VersionHistory, cursor: str) -> None:
    """Reject malformed cursors before reaching SQLite integer conversion."""
    with pytest.raises(ValueError, match="cursor"):
        _ = history.list_slide(TEMPLATE_ID, "cover", cursor=cursor)
    with pytest.raises(ValueError, match="cursor"):
        _ = history.list_artifact("artifact-nonexistent", cursor=cursor)


@pytest.mark.parametrize("version_id", ["artifact:0", "artifact:01", "artifact:../1", "slide:1"])
def test_invalid_artifact_versions_are_rejected(history: VersionHistory, version_id: str) -> None:
    """Version IDs cannot alias revisions or become file paths."""
    with pytest.raises(ValueError, match="version ID"):
        _ = history.restore_artifact("artifact-nonexistent", version_id, "", "invalid-version-id")
