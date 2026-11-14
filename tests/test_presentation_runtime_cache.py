"""Warm static controls without caching private or stale presentation content."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from artifact_studio import app as service
from artifact_studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.mark.parametrize("name", ["presentation.js", "icons.js", "slideshow.css"])
def test_runtime_revalidates_cached_bytes_after_install_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "review.sqlite3")
    monkeypatch.setattr(service, "store", store)

    def unexpected_read(_deck_id: str) -> None:
        pytest.fail("Static controls must not parse presentation content during warmup.")

    monkeypatch.setattr(store, "read", unexpected_read)
    project = tmp_path / "installed"
    runtime = project / "runtime"
    runtime.mkdir(parents=True)
    asset = runtime / name
    _ = asset.write_text("/* First installed runtime */")
    monkeypatch.setattr(service, "PROJECT", project)
    client = TestClient(service.app)
    url = f"/decks/{TEMPLATE_ID}/{name}"
    first = client.get(url)
    assert first.status_code == 200
    assert first.headers["cache-control"] == "private, no-cache"
    warmed = client.get(url, headers={"If-None-Match": first.headers["etag"]})
    assert warmed.status_code == 304
    assert warmed.content == b""
    assert warmed.headers["cache-control"] == "private, no-cache"
    _ = asset.write_text("/* Updated installed runtime, different bytes */")
    updated = client.get(url, headers={"If-None-Match": first.headers["etag"]})
    assert updated.status_code == 200
    assert updated.headers["etag"] != first.headers["etag"]
    assert updated.text == asset.read_text()
    assert client.get("/decks/missing/" + name).status_code == 404
