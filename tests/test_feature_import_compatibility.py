"""Keep historical imports and shipped browser URLs usable during the migration."""

import importlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from artifact_studio import PROJECT_ROOT
from artifact_studio.web_assets import StudioWebFiles


@pytest.mark.parametrize(
    "historical,canonical",
    [
        ("design_authoring", "authoring"),
        ("design_library", "library"),
        ("design_library_api", "library_api"),
        ("design_preview", "preview"),
        ("design_references", "references"),
        ("design_thumbnails", "thumbnails"),
        ("design_transfer", "transfer"),
        ("element_alternative_api", "element_api"),
        ("element_alternatives", "elements"),
        ("slide_design_api", "slide_api"),
        ("slide_designs", "slides"),
    ],
)
def test_historical_import_is_the_same_live_module(historical: str, canonical: str) -> None:
    """Callers and monkeypatches must not retain a detached copy of module globals."""
    before = importlib.import_module("artifact_studio." + historical)
    after = importlib.import_module("artifact_studio.designs." + canonical)
    assert before is after


@pytest.mark.parametrize(
    "historical,canonical",
    [
        ("presentation-launcher.js", "launcher.js"),
        ("presentation-warmup.js", "warmup.js"),
        ("presentations.js", "library-actions.js"),
    ],
)
def test_cached_editor_urls_keep_content_and_cache_validation(
    historical: str, canonical: str
) -> None:
    """A cached editor receives the same bytes and validators without a second request."""
    app = FastAPI()
    app.mount("/web", StudioWebFiles(directory=PROJECT_ROOT / "web"))
    with TestClient(app) as client:
        old = client.get("/web/" + historical)
        current = client.get("/web/presentation/" + canonical)
        assert old.status_code == current.status_code == 200
        assert old.content == current.content
        assert old.headers["etag"] == current.headers["etag"]
        assert "javascript" in old.headers["content-type"]
        assert (
            client.get(
                "/web/" + historical, headers={"If-None-Match": old.headers["etag"]}
            ).status_code
            == 304
        )
        assert client.head("/web/" + historical).status_code == 200
        assert client.get("/web/%2E%2E/pyproject.toml").status_code == 404


def test_historical_monkeypatch_reaches_canonical_preview_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy integrations changing a limit still affect the active preview implementation."""
    from artifact_studio import design_preview
    from artifact_studio.designs import preview
    from artifact_studio.storage import Store
    from tests.design_factory import TEMPLATE_ID, make_design

    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(design_preview, "MAX_PAGE_BYTES", 64)
    result = preview.hosted_design_preview(store, "test-brand", "https://studio.example.test")
    assert b"Preview unavailable" in bytes(result.body)
    assert "sandbox;" in result.headers["content-security-policy"]
