"""Keep operational locks stable when a disposable library cache is removed."""

import fcntl
import shutil
from pathlib import Path

import pytest

from artifact_studio.autosync import AutoSync
from artifact_studio.libraries import Libraries
from artifact_studio.storage import Store


def test_idle_sync_survives_missing_cache_without_recreating_old_state(tmp_path: Path) -> None:
    """An idle tick needs no cache, and a removed workspace is never resurrected."""
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    worker = AutoSync(store)
    shutil.rmtree(worker.libraries.cache)
    worker.tick()
    assert not worker.libraries.cache.exists()
    assert store.database.exists()
    shutil.rmtree(store.database.parent)
    with pytest.raises(FileNotFoundError):
        worker.tick()
    with pytest.raises(FileNotFoundError):
        _ = Libraries(store)
    assert not store.database.parent.exists()


def test_cache_removal_does_not_replace_an_active_library_mutex(tmp_path: Path) -> None:
    """A new service respects the original lock even after the cache directory changes."""
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    libraries = Libraries(store)
    with libraries.locked():
        shutil.rmtree(libraries.cache)
        other = Libraries(store)
        with pytest.raises(ValueError, match="operation is running"), other.locked():
            pytest.fail("Cache removal must not create a different operational lock")
    shutil.rmtree(libraries.cache)
    with other.locked():
        assert libraries.cache.is_dir()


def test_sync_mutex_survives_cache_removal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A held worker lock continues to exclude ticks after cache eviction."""
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    worker = AutoSync(store)
    observed: list[float] = []
    monkeypatch.setattr(worker, "_tick", observed.append)
    with (store.database.parent / ".auto-sync.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        shutil.rmtree(worker.libraries.cache)
        worker.tick(10)
        assert not observed
        fcntl.flock(lock, fcntl.LOCK_UN)
    worker.tick(11)
    assert observed == [11]
