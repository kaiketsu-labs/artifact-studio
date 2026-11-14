"""Private-state-free portable presentation source for future hosted sync."""

from pathlib import Path

import pytest

from artifact_studio.design_authoring import blank_presentation
from artifact_studio.models import AgentContext, Slide, Source
from artifact_studio.presentation_sync import parse_portable, portable_snapshot
from artifact_studio.storage import Store


def test_portable_snapshot_excludes_private_context_and_preserves_order(tmp_path: Path) -> None:
    store = Store(tmp_path / "collection", tmp_path / "review.sqlite")
    folder = store.root / "fictional-deck"
    folder.mkdir()
    deck = blank_presentation(folder, "fictional-deck", "Fictional deck")
    deck.slides[0].notes = "PRIVATE NOTES SENTINEL"
    deck.slides[0].revision = 17
    deck.slides[0].agent = AgentContext(summary="PRIVATE AGENT SENTINEL")
    deck.slides[0].sources = [Source(label="PRIVATE SOURCE SENTINEL")]
    deck.slides.append(Slide(id="second", title="Second", html="<article>Second</article>"))
    store.write(deck.id, deck)
    _ = (folder / "README.md").write_text("PRIVATE CONTEXT SENTINEL")
    _ = (folder / "assets" / "image.txt").write_bytes(b"fictional asset")

    with store.editing(deck.id):
        files = portable_snapshot(store, deck.id)
    serialized = b"\n".join(files.values())
    assert b"PRIVATE NOTES SENTINEL" not in serialized
    assert b"PRIVATE CONTEXT SENTINEL" not in serialized
    assert b"PRIVATE AGENT SENTINEL" not in serialized
    assert b"PRIVATE SOURCE SENTINEL" not in serialized
    assert b'"revision"' not in serialized
    assert "index.html" not in files and "README.md" not in files
    manifest, slides = parse_portable(files)
    assert manifest.slides == ["cover", "second"]
    assert [slide.id for slide in slides] == manifest.slides
    assert files["assets/image.txt"] == b"fictional asset"
    assert store.read(deck.id).slides[0].notes == "PRIVATE NOTES SENTINEL"
    assert store.read(deck.id).slides[0].revision == 17


def test_portable_rejects_extra_private_files_and_mismatched_identity(tmp_path: Path) -> None:
    store = Store(tmp_path / "collection", tmp_path / "review.sqlite")
    folder = store.root / "fictional-deck"
    folder.mkdir()
    _ = blank_presentation(folder, "fictional-deck", "Fictional deck")
    with store.editing("fictional-deck"):
        files = portable_snapshot(store, "fictional-deck")
    with pytest.raises(ValueError, match="Unexpected"):
        _ = parse_portable(files | {"README.md": b"private"})
    with pytest.raises(ValueError, match="identity"):
        _ = parse_portable(
            files
            | {"slides/cover.json": files["slides/cover.json"].replace(b'"cover"', b'"other"', 1)}
        )


def test_prepare_preserves_private_slide_state_and_revision(tmp_path: Path) -> None:
    from artifact_studio.presentation_sync import prepare_portable_deck

    store = Store(tmp_path / "collection", tmp_path / "review.sqlite")
    folder = store.root / "fictional-deck"
    folder.mkdir()
    deck = blank_presentation(folder, "fictional-deck", "Fictional deck")
    deck.slides[0].notes = "Keep private notes"
    deck.slides[0].notes_revision = 4
    deck.slides[0].agent = AgentContext(summary="Local context")
    deck.slides[0].sources = [Source(label="Local source")]
    deck.slides[0].revision = 9
    store.write(deck.id, deck)
    with store.editing(deck.id):
        files = portable_snapshot(store, deck.id)
    files["slides/cover.json"] = files["slides/cover.json"].replace(
        b"Fictional deck", b"Remote title"
    )
    updated = prepare_portable_deck(deck, files)
    assert updated.slides[0].title == "Remote title"
    assert updated.slides[0].notes == "Keep private notes"
    assert updated.slides[0].notes_revision == 4
    assert updated.slides[0].agent.summary == "Local context"
    assert updated.slides[0].sources[0].label == "Local source"
    assert updated.slides[0].revision == 10
    assert deck.slides[0].revision == 9


def test_preparation_reorders_and_removes_without_mutating_local_deck(tmp_path: Path) -> None:
    import json

    from artifact_studio.presentation_sync import prepare_portable_deck

    store = Store(tmp_path / "collection", tmp_path / "review.sqlite")
    folder = store.root / "fictional-deck"
    folder.mkdir()
    original = blank_presentation(folder, "fictional-deck", "Fictional deck")
    original.slides += [
        Slide(id="second", title="Second", html="<article>Second</article>"),
        Slide(id="third", title="Third", html="<article>Third</article>"),
    ]
    original.slides[1].notes = "Keep this note"
    store.write(original.id, original)
    with store.editing(original.id):
        files = portable_snapshot(store, original.id)
    manifest = json.loads(files["presentation.json"])
    manifest["slides"] = ["third", "second"]
    files["presentation.json"] = json.dumps(manifest).encode()
    del files["slides/cover.json"]
    prepared = prepare_portable_deck(original, files)
    assert [slide.id for slide in prepared.slides] == ["third", "second"]
    assert prepared.slides[1].notes == "Keep this note"
    assert prepared.slides[1].revision == 1
    assert [slide.id for slide in store.read(original.id).slides] == ["cover", "second", "third"]


@pytest.mark.parametrize("fail_at", [1, 2, 3, 4])
def test_journal_recovers_every_interrupted_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_at: int
) -> None:
    import json

    from artifact_studio import presentation_sync

    store = Store(tmp_path / "collection", tmp_path / "review.sqlite")
    folder = store.root / "fictional-deck"
    folder.mkdir()
    deck = blank_presentation(folder, "fictional-deck", "Fictional deck")
    deck.slides[0].notes = "Private note"
    deck.slides[0].revision = 4
    store.write(deck.id, deck)
    _ = (folder / "assets" / "deleted.txt").write_text("original")
    with store.editing(deck.id):
        before = presentation_sync.portable_snapshot(store, deck.id)
    source = before.copy()
    slide = json.loads(source["slides/cover.json"])
    slide["title"] = "Remote edit"
    source["slides/cover.json"] = json.dumps(slide).encode()
    source["theme.css"] = b"/* new theme */"
    del source["assets/deleted.txt"]
    source["assets/new.txt"] = b"new asset"
    write = presentation_sync._journal_write  # pyright: ignore[reportPrivateUsage]
    calls = 0

    def interrupt(path: Path, value: bytes | None) -> None:
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise OSError("Simulated interruption")
        write(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(presentation_sync, "_journal_write", interrupt)
        with pytest.raises(OSError, match="interruption"):
            presentation_sync.apply_portable(store, deck.id, before, source)
    # A restart or next edit completes the durable intent before other mutations.
    reopened = Store(store.root, store.database)
    with reopened.editing(deck.id):
        final = presentation_sync.portable_snapshot(reopened, deck.id)
    assert json.loads(final["slides/cover.json"]) == slide
    assert {k: v for k, v in final.items() if k != "slides/cover.json"} == {
        k: v for k, v in source.items() if k != "slides/cover.json"
    }
    result = reopened.read(deck.id)
    assert result.slides[0].notes == "Private note"
    assert result.slides[0].revision == 5
    with reopened.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM presentation_sync_journal").fetchone()[0] == 0
        assert (
            db.execute("SELECT COUNT(*) FROM revisions WHERE deck=?", (deck.id,)).fetchone()[0] == 2
        )


def test_journal_refuses_external_divergence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from artifact_studio import presentation_sync
    from artifact_studio.storage import ConflictError

    store = Store(tmp_path / "collection", tmp_path / "review.sqlite")
    folder = store.root / "fictional-deck"
    folder.mkdir()
    deck = blank_presentation(folder, "fictional-deck", "Fictional deck")
    with store.editing(deck.id):
        before = presentation_sync.portable_snapshot(store, deck.id)
    source = before | {"theme.css": b"remote theme"}

    def interrupt(_path: Path, _value: bytes | None) -> None:
        raise OSError("Simulated interruption")

    with monkeypatch.context() as patch:
        patch.setattr(presentation_sync, "_journal_write", interrupt)
        with pytest.raises(OSError):
            presentation_sync.apply_portable(store, deck.id, before, source)
    _ = (folder / "theme.css").write_bytes(b"external edit")
    with pytest.raises(ConflictError, match="changed during sync recovery"), store.editing(deck.id):
        pass
    assert (folder / "theme.css").read_bytes() == b"external edit"


def test_journal_recovers_after_files_before_history_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from artifact_studio import presentation_sync, version_history

    store = Store(tmp_path / "collection", tmp_path / "review.sqlite")
    folder = store.root / "fictional-deck"
    folder.mkdir()
    deck = blank_presentation(folder, "fictional-deck", "Fictional deck")
    with store.editing(deck.id):
        before = presentation_sync.portable_snapshot(store, deck.id)
    source = before | {"theme.css": b"Changed theme"}

    def interrupted(*_args: object, **_kwargs: object) -> None:
        raise OSError("Interrupted before history commit")

    with monkeypatch.context() as patch:
        patch.setattr(version_history, "capture_slide_version", interrupted)
        with pytest.raises(OSError, match="history commit"):
            presentation_sync.apply_portable(store, deck.id, before, source)
    assert (folder / "theme.css").read_bytes() == b"Changed theme"
    with store.editing(deck.id):
        assert presentation_sync.portable_snapshot(store, deck.id) == source
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM presentation_sync_journal").fetchone()[0] == 0
        assert (
            db.execute("SELECT COUNT(*) FROM revisions WHERE deck=?", (deck.id,)).fetchone()[0] == 1
        )


def test_deleted_slide_restores_its_private_context_when_returned(tmp_path: Path) -> None:
    import json

    from artifact_studio import presentation_sync

    store = Store(tmp_path / "collection", tmp_path / "review.sqlite")
    folder = store.root / "fictional-deck"
    folder.mkdir()
    deck = blank_presentation(folder, "fictional-deck", "Fictional deck")
    deck.slides.append(Slide(id="second", title="Second", html="<article>Second</article>"))
    deck.slides[1].notes = "Private retained note"
    deck.slides[1].revision = 7
    store.write(deck.id, deck)
    with store.editing(deck.id):
        original = presentation_sync.portable_snapshot(store, deck.id)
    removed = original.copy()
    manifest = json.loads(removed["presentation.json"])
    manifest["slides"] = ["cover"]
    removed["presentation.json"] = json.dumps(manifest).encode()
    del removed["slides/second.json"]
    presentation_sync.apply_portable(store, deck.id, original, removed)
    with store.editing(deck.id):
        current = presentation_sync.portable_snapshot(store, deck.id)
    presentation_sync.apply_portable(store, deck.id, current, original)
    restored = store.read(deck.id).slides[1]
    assert restored.notes == "Private retained note"
    assert restored.revision == 7


def test_journal_syncs_nested_directory_entries_before_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from artifact_studio import presentation_sync

    store = Store(tmp_path / "collection", tmp_path / "review.sqlite")
    folder = store.root / "fictional-deck"
    folder.mkdir()
    deck = blank_presentation(folder, "fictional-deck", "Fictional deck")
    with store.editing(deck.id):
        before = presentation_sync.portable_snapshot(store, deck.id)
    opened: dict[int, Path] = {}
    synced: set[Path] = set()
    real_open, real_fsync = os.open, os.fsync

    def record_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        *args: int,
        **kwargs: int,
    ) -> int:
        fd = real_open(path, flags, *args, **kwargs)
        if flags & os.O_DIRECTORY:
            opened[fd] = Path(os.fsdecode(path))
        return fd

    def record_fsync(fd: int) -> None:
        if fd in opened:
            synced.add(opened[fd])
        real_fsync(fd)

    monkeypatch.setattr(os, "open", record_open)
    monkeypatch.setattr(os, "fsync", record_fsync)
    presentation_sync.apply_portable(
        store, deck.id, before, before | {"assets/new/sub/image.txt": b"fictional"}
    )
    assert {folder / "assets", folder / "assets/new", folder / "assets/new/sub"} <= synced


@pytest.mark.parametrize("attack", ["asset_symlink", "theme_symlink", "path_traversal"])
def test_portable_apply_rejects_paths_outside_presentation(tmp_path: Path, attack: str) -> None:
    from artifact_studio import presentation_sync

    store = Store(tmp_path / "collection", tmp_path / "review.sqlite")
    folder = store.root / "fictional-deck"
    folder.mkdir()
    deck = blank_presentation(folder, "fictional-deck", "Fictional deck")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    _ = sentinel.write_bytes(b"untouched")
    with store.editing(deck.id):
        before = presentation_sync.portable_snapshot(store, deck.id)
    source = before.copy()
    if attack == "asset_symlink":
        (folder / "assets/linked").symlink_to(outside, target_is_directory=True)
        source["assets/linked/sentinel.txt"] = b"overwrite"
    elif attack == "theme_symlink":
        (folder / "theme.css").unlink()
        (folder / "theme.css").symlink_to(sentinel)
        source["theme.css"] = b"overwrite"
    else:
        source["assets/../../outside/sentinel.txt"] = b"overwrite"
    with pytest.raises((ValueError, OSError)):
        presentation_sync.apply_portable(store, deck.id, before, source)
    assert sentinel.read_bytes() == b"untouched"
