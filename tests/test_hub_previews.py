"""Saved cover scheduling, failure recovery and memory-safe metadata contracts."""

import io
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image

from artifact_studio import hub_previews
from artifact_studio.artifact_models import ArtifactFileInput, ArtifactView
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.hub_previews import (
    CoverWorker,
    cover_metadata,
    cover_path,
    cover_token,
    cover_url,
)
from artifact_studio.renderer_health import RendererUnavailable
from artifact_studio.storage import Store
from tests.design_factory import make_design


@pytest.fixture
def registry(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(Store(tmp_path / "collection", tmp_path / "state" / "review.sqlite3"))


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> CoverWorker:
    result = CoverWorker(capacity=2)
    monkeypatch.setattr(hub_previews, "WORKER", result)
    monkeypatch.setattr(hub_previews, "ensure_renderer_ready", lambda: None)
    monkeypatch.setattr(hub_previews, "check_renderer_available", lambda: None)
    return result


def register(registry: ArtifactStore, tmp_path: Path, name: str = "page") -> ArtifactView:
    source = tmp_path / name
    source.mkdir()
    (source / "index.html").write_text("<h1>One</h1>")
    project = registry.create_project(name)
    return registry.register_artifact(project.id, name, source, "index.html")


def image_render(view: ArtifactView, output: Path) -> Path:
    target = output / "fixture.png"
    target.write_bytes(view.content_hash.encode())
    return target


def test_burst_is_coalesced_bounded_and_does_not_wait_for_rendering(
    registry: ArtifactStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker: CoverWorker
) -> None:
    """Thirty callers share one render; a full queue immediately defers new identities."""
    views = [register(registry, tmp_path, f"page-{index}") for index in range(4)]
    started, release = threading.Event(), threading.Event()
    calls: list[str] = []

    def render(view: ArtifactView, output: Path) -> Path:
        calls.append(view.id)
        started.set()
        assert release.wait(5)
        return image_render(view, output)

    def request(view: ArtifactView):
        return hub_previews.request_cover(
            registry.store, view.id, cover_token(registry.store, view), render
        )

    try:
        assert request(views[0]).code == "cover_pending"
        assert started.wait(5)
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(request, views[0]) for _ in range(30)]
            assert all(result.result(timeout=2).code == "cover_pending" for result in futures)
        assert request(views[1]).code == request(views[2]).code == "cover_pending"
        assert request(views[3]).code == "cover_busy"
        assert len(worker.pending) == 3 and worker.jobs.qsize() == 2
        assert calls == [views[0].id]
    finally:
        release.set()
        worker.jobs.join()
    assert len(calls) == 3 and not worker.pending
    assert worker.worker and worker.worker.daemon
    assert request(views[0]).status == 200


def test_renderer_failure_precedes_source_reads_and_survives_worker_restart(
    registry: ArtifactStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker: CoverWorker
) -> None:
    view = register(registry, tmp_path)
    token = cover_token(registry.store, view)
    probes: list[bool] = []

    def unavailable() -> None:
        probes.append(True)
        raise RendererUnavailable("The sandbox is unavailable.", 30)

    def forbidden(*args: object, **kwargs: object) -> ArtifactView:
        raise AssertionError("Failed renderer must not read or allocate an artifact bundle")

    monkeypatch.setattr(hub_previews, "ensure_renderer_ready", unavailable)
    monkeypatch.setattr(ArtifactStore, "get_artifact", forbidden)
    assert hub_previews.request_cover(registry.store, view.id, token, image_render).status == 202
    worker.jobs.join()
    monkeypatch.setattr(hub_previews, "WORKER", CoverWorker())
    for _ in range(10):
        result = hub_previews.request_cover(registry.store, view.id, token, image_render)
        assert result.code == "renderer_unavailable" and result.status == 503
        assert 1 <= result.retry_after <= 30
    assert probes == [True]


def test_last_good_is_retained_during_changes_failures_and_restarts(
    registry: ArtifactStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker: CoverWorker
) -> None:
    view = register(registry, tmp_path)
    first = cover_token(registry.store, view)
    assert hub_previews.request_cover(registry.store, view.id, first, image_render).status == 202
    worker.jobs.join()
    assert cover_metadata(registry.store, view)["preview_cached"] is True
    next_view = registry.revise_artifact(
        view.id, [ArtifactFileInput(path="index.html", text="<h1>Two</h1>")], 1, "revision-two"
    )
    next_token = cover_token(registry.store, next_view)
    metadata = cover_metadata(registry.store, next_view)
    assert metadata["preview_url"] == cover_url(view.id, next_token)
    assert metadata["preview_fallback_url"] == cover_url(view.id, first)
    assert metadata["preview_cached"] is False

    def failed(view: ArtifactView, output: Path) -> Path:
        raise ValueError("A fictional page failed to render.")

    assert hub_previews.request_cover(registry.store, view.id, next_token, failed).status == 202
    worker.jobs.join()
    assert hub_previews.request_cover(registry.store, view.id, next_token, failed).status == 503
    replacement = CoverWorker()
    monkeypatch.setattr(hub_previews, "WORKER", replacement)
    old = hub_previews.request_cover(registry.store, view.id, first, failed)
    assert old.path and old.path.read_bytes() == view.content_hash.encode()
    failure = hub_previews.cache_folder(registry.store) / f"failure-{view.id}-{next_token}.json"
    failure.write_text(json.dumps({"retry_at": 0, "code": "cover_unavailable", "message": "old"}))
    assert (
        hub_previews.request_cover(registry.store, view.id, next_token, image_render).status == 202
    )
    replacement.jobs.join()
    assert cover_metadata(registry.store, next_view)["preview_fallback_url"] == ""
    assert hub_previews.request_cover(registry.store, view.id, next_token, failed).status == 200
    assert not list(hub_previews.cache_folder(registry.store).glob("pending-*"))


def test_unknown_stale_tokens_never_publish_another_versions_bytes(
    registry: ArtifactStore, tmp_path: Path, worker: CoverWorker
) -> None:
    view = register(registry, tmp_path)
    wrong = "0" * 64
    assert hub_previews.request_cover(registry.store, view.id, wrong, image_render).status == 202
    worker.jobs.join()
    result = hub_previews.request_cover(registry.store, view.id, wrong, image_render)
    assert result.status == 409 and result.code == "cover_changed"
    assert not cover_path(registry.store, view.id, wrong).exists()


def test_changed_while_rendering_preserves_the_old_cover(
    registry: ArtifactStore, tmp_path: Path, worker: CoverWorker
) -> None:
    view = register(registry, tmp_path)
    token = cover_token(registry.store, view)

    def render(old: ArtifactView, output: Path) -> Path:
        registry.revise_artifact(
            old.id, [ArtifactFileInput(path="index.html", text="<h1>Two</h1>")], 1, "mid-capture"
        )
        return image_render(old, output)

    assert hub_previews.request_cover(registry.store, view.id, token, render).status == 202
    worker.jobs.join()
    assert not cover_path(registry.store, view.id, token).exists()
    assert (
        hub_previews.request_cover(registry.store, view.id, token, render).code == "cover_changed"
    )


def test_metadata_streams_owned_files_and_detects_preserved_timestamp_edits(
    registry: ArtifactStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = register(registry, tmp_path)

    def forbidden(_path: Path) -> bytes:
        raise AssertionError("Unchanged metadata must not allocate whole bundle byte strings")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", forbidden)
        assert registry.get_artifact(view.id).content_hash == view.content_hash
        assert registry.list_artifacts()[0].content_hash == view.content_hash
    source = Path(view.source_path) / "index.html"
    original = source.stat()
    source.write_text("<h1>Two</h1>")
    os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
    changed = registry.get_artifact(view.id)
    assert changed.revision == 2 and changed.content_hash != view.content_hash
    assert cover_token(registry.store, changed) != cover_token(registry.store, view)


def test_legacy_metadata_omits_discussion_and_cover_detects_same_size_asset_change(
    registry: ArtifactStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = registry.store.root / "fictional-deck"
    make_design(folder)

    def forbidden(*args: object, **kwargs: object):
        raise AssertionError("Library metadata must not build private review conversations")

    monkeypatch.setattr(Store, "view", forbidden)
    monkeypatch.setattr(Store, "comments", forbidden)
    view = registry.get_artifact(folder.name)
    before = cover_token(registry.store, view)
    asset = folder / "assets" / "custom.svg"
    original = asset.stat()
    asset.write_text('<svg xmlns="http://www.w3.org/2000/svg" >')
    assert asset.stat().st_size == original.st_size
    os.utime(asset, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert cover_token(registry.store, registry.get_artifact(folder.name)) != before


def test_notes_do_not_change_the_cover_but_audience_root_settings_do(
    registry: ArtifactStore,
) -> None:
    folder = registry.store.root / "fictional-deck"
    make_design(folder)
    token = cover_token(registry.store, registry.get_artifact(folder.name))
    deck = registry.store.read(folder.name)
    deck.slides[0].notes = "A private note that should never appear in a cover."
    deck.slides[0].notes_revision += 1
    registry.store.write(folder.name, deck)
    assert cover_token(registry.store, registry.get_artifact(folder.name)) == token
    source = folder / "index.html"
    source.write_text(source.read_text().replace('<html lang="en">', '<html lang="de" dir="rtl">'))
    assert cover_token(registry.store, registry.get_artifact(folder.name)) != token


def test_disk_full_failure_remains_cooled_down_in_memory(
    registry: ArtifactStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker: CoverWorker
) -> None:
    view = register(registry, tmp_path)
    token = cover_token(registry.store, view)
    calls: list[bool] = []

    def failed(view: ArtifactView, output: Path) -> Path:
        calls.append(True)
        raise ValueError("Fictional rendering failure")

    def no_space(*args: object, **kwargs: object) -> None:
        raise OSError("No space left on fixture disk")

    monkeypatch.setattr(hub_previews, "replace_text", no_space)
    assert hub_previews.request_cover(registry.store, view.id, token, failed).status == 202
    worker.jobs.join()
    for _ in range(10):
        assert hub_previews.request_cover(registry.store, view.id, token, failed).status == 503
    assert len(calls) == 1


def test_cache_prunes_by_count_and_bytes_without_deleting_current(
    registry: ArtifactStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker: CoverWorker
) -> None:
    monkeypatch.setattr(hub_previews, "MAX_COVERS", 2)
    monkeypatch.setattr(hub_previews, "MAX_CACHE_BYTES", 120)
    views = [register(registry, tmp_path, f"item-{index}") for index in range(3)]
    for view in views:
        token = cover_token(registry.store, view)
        assert (
            hub_previews.request_cover(registry.store, view.id, token, image_render).status == 202
        )
        worker.jobs.join()
    images = list(hub_previews.cache_folder(registry.store).glob("*.png"))
    assert len(images) == 1 and sum(path.stat().st_size for path in images) <= 120
    last = views[-1]
    assert images == [cover_path(registry.store, last.id, cover_token(registry.store, last))]


def test_raster_cover_is_bounded_and_does_not_start_chromium(
    registry: ArtifactStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker: CoverWorker
) -> None:
    from fastapi.testclient import TestClient

    from artifact_studio import app

    source = tmp_path / "picture.png"
    data = io.BytesIO()
    Image.new("RGBA", (2400, 1200), (15, 130, 210, 170)).save(data, format="PNG")
    original = data.getvalue()
    source.write_bytes(original)
    project = registry.create_project("Images")
    view = registry.register_artifact(project.id, "Picture", source, "picture.png")
    monkeypatch.setattr(app, "store", registry.store)

    def forbidden() -> None:
        raise AssertionError("Raster library cards must not launch Chromium")

    monkeypatch.setattr(hub_previews, "ensure_renderer_ready", forbidden)
    url = cover_url(view.id, cover_token(registry.store, view))
    client = TestClient(app.app, base_url="http://127.0.0.1")
    try:
        assert client.get(url).status_code == 202
        worker.jobs.join()
        response = client.get(url)
        assert response.status_code == 200 and len(response.content) < 8 * 1024 * 1024
        with Image.open(io.BytesIO(response.content)) as preview:
            assert preview.size == (1280, 640)
            assert preview.mode == "RGBA" and preview.getpixel((500, 300)) == (15, 130, 210, 170)
        assert client.get(f"/api/artifacts/{view.id}/files/picture.png").content == original
    finally:
        client.close()
    assert source.read_bytes() == original
