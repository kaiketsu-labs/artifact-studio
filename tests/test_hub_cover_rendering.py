"""Pixel fidelity and persistent cover reuse through the production sandboxed renderer."""

import io
import os
import time
from pathlib import Path
from typing import Never

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from artifact_studio import app as application
from artifact_studio import renderer_health
from artifact_studio.artifact_models import ArtifactFileInput, ArtifactView
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.models import SlidePatch
from artifact_studio.storage import Store, digest
from tests.design_factory import TEMPLATE_ID, make_design

pytestmark = pytest.mark.skipif(
    os.environ.get("ARTIFACT_STUDIO_RENDER_TESTS") != "1",
    reason="Production Chromium sandbox required for exact cover rendering",
)


def cover_record(client: TestClient, artifact_id: str) -> dict[str, str]:
    """Discover the exact preview identity that the visible library receives."""
    response = client.get("/api/hub")
    assert response.status_code == 200, response.text
    return next(item for item in response.json()["artifacts"] if item["id"] == artifact_id)


def wait_for_cover(client: TestClient, url: str) -> bytes:
    """Wait for the normal background job, without replacing rendering or cache contents."""
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        response = client.get(url)
        if response.status_code == 200:
            assert response.headers["content-type"] == "image/png"
            return response.content
        assert response.status_code == 202, response.text
        assert response.headers["cache-control"] == "no-store"
        time.sleep(0.05)
    raise AssertionError("The real background cover did not become available")


@pytest.fixture
def cover_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    """Keep rendering, cache and private review data inside one fictional workspace."""
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(application, "store", store)
    monkeypatch.setattr(renderer_health, "HEALTH", renderer_health.RendererHealth())
    return store


def test_persistent_html_cover_keeps_js_asset_fidelity_and_prior_version(
    cover_workspace: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the real viewport, authored JS/local assets and old image during replacement."""
    artifacts = ArtifactStore(cover_workspace)
    project = artifacts.create_project("Fictional cover fidelity")
    bundle = tmp_path / "owned-html"
    bundle.mkdir()
    _ = (bundle / "index.html").write_text(
        "<html><head><style>html,body{margin:0;width:100%;height:100%;"
        "background:rgb(205,70,45)}img{position:absolute;left:50%;top:0;"
        "width:50%;height:100%;object-fit:fill}</style></head><body>"
        '<img src="brand.png" alt="Fictional brand color"><script src="local.js"></script>'
        "</body></html>"
    )
    Image.new("RGB", (32, 32), (28, 92, 185)).save(bundle / "brand.png")
    _ = (bundle / "local.js").write_text("document.body.style.background='rgb(30,150,75)';")
    artifact = artifacts.register_artifact(project.id, "Live authored page", bundle, "index.html")
    client = TestClient(application.app, base_url="http://127.0.0.1")
    first = cover_record(client, artifact.id)
    original = wait_for_cover(client, first["preview_url"])
    with Image.open(io.BytesIO(original)) as image:
        assert image.size == (1280, 800)
        pixels = image.convert("RGB")
        assert pixels.getpixel((320, 400)) == (30, 150, 75)
        assert pixels.getpixel((960, 400)) == (28, 92, 185)

    updated = artifacts.revise_artifact(
        artifact.id,
        [
            ArtifactFileInput(
                path="local.js", text="document.body.style.background='rgb(185,65,100)';"
            )
        ],
        artifact.content_hash,
        "fictional-cover-update",
    )
    assert isinstance(updated, ArtifactView)
    second = cover_record(client, artifact.id)
    assert second["preview_url"] != first["preview_url"]
    assert second["preview_fallback_url"] == first["preview_url"]
    assert client.get(second["preview_fallback_url"]).content == original
    replacement = wait_for_cover(client, second["preview_url"])
    with Image.open(io.BytesIO(replacement)) as image:
        assert image.size == (1280, 800)
        pixels = image.convert("RGB")
        assert pixels.getpixel((320, 400)) == (185, 65, 100)
        assert pixels.getpixel((960, 400)) == (28, 92, 185)
    assert replacement != original

    def no_launch(*args: object, **kwargs: object) -> Never:
        raise AssertionError("Warm covers must not launch Chromium")

    monkeypatch.setattr(renderer_health.HEALTH, "attempt", no_launch)
    for _ in range(5):
        assert client.get(second["preview_url"]).content == replacement
        assert client.get(first["preview_url"]).content == original
    assert client.get(second["preview_url"]).headers["cache-control"] == "private, max-age=3600"


def test_legacy_cover_retains_full_resolution_and_live_speaker_notes(
    cover_workspace: Store,
) -> None:
    """A persisted card uses the unchanged full slide viewport and never rewrites source."""
    make_design(cover_workspace.root / TEMPLATE_ID)
    deck = cover_workspace.create("2026-11-06-preview-fidelity", "Fictional slide cover")
    original = deck.slides[0]
    _ = cover_workspace.revise(
        deck.id,
        original.id,
        digest(original.model_dump_json()),
        "fictional-slide-cover",
        SlidePatch(
            html='<article class="slide" style="background:rgb(32,70,105)">'
            '<div style="position:absolute;left:50%;top:0;width:50%;height:100%;'
            'background:rgb(230,185,75)"></div></article>',
            notes="Private fictional notes stay in the source and never become a cover.",
        ),
    )
    source = (cover_workspace.folder(deck.id) / "index.html").read_bytes()
    client = TestClient(application.app, base_url="http://127.0.0.1")
    item = cover_record(client, deck.id)
    data = wait_for_cover(client, item["preview_url"])
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (1600, 900)
        pixels = image.convert("RGB")
        assert pixels.getpixel((400, 450)) == (32, 70, 105)
        assert pixels.getpixel((1200, 450)) == (230, 185, 75)
    assert (cover_workspace.folder(deck.id) / "index.html").read_bytes() == source
    assert client.get(item["preview_url"]).content == data
