"""Sandboxed capture pipelines recover after hostile authored readiness programs."""

import os
import re
import time
from pathlib import Path

import pytest
from PIL import Image

from artifact_studio import renderer_health
from artifact_studio.artifact_capture import capture_artifact_image
from artifact_studio.artifact_models import ArtifactFileInput
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.storage import Store
from artifact_studio.visuals import (
    CaptureOptions,
    capture_queue_status,
    capture_snapshot,
    snapshot,
)
from tests.design_factory import TEMPLATE_ID, make_design

pytestmark = pytest.mark.skipif(
    os.environ.get("ARTIFACT_STUDIO_RENDER_TESTS") != "1", reason="Opt-in sandboxed Chromium"
)


@pytest.mark.parametrize(
    "script",
    [
        "Promise.all = () => new Promise(() => {});",
        # Let the preview bridge finish booting before stalling capture readiness.
        # Stalling its initial font read tests goto's existing 15-second deadline.
        "addEventListener('load', () => {"
        "Object.defineProperty(document.fonts, 'ready', {get() {while (true) {}}});});",
    ],
    ids=["pending-images", "busy-fonts"],
)
def test_artifact_readiness_timeout_releases_capture_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    """The complete production capture closes its browser and can render the next revision."""
    store = ArtifactStore(Store(tmp_path / "collection", tmp_path / "state/review.sqlite3"))
    project = store.create_project("Fictional renderer deadline")
    source = tmp_path / "source"
    source.mkdir()
    _ = (source / "index.html").write_text(f"<h1>Pending</h1><script>{script}</script>")
    artifact = store.register_artifact(project.id, "Pending", source, "index.html")
    monkeypatch.setattr(renderer_health, "SCRIPT_TIMEOUT_MS", 300)
    started = time.monotonic()
    with pytest.raises(ValueError, match="Page.wait_for_function: Timeout"):
        _ = capture_artifact_image(store, artifact.id, tmp_path / "captures")
    assert time.monotonic() - started < 10
    assert capture_queue_status()["running"] is False

    _ = store.revise_artifact(
        artifact.id,
        [
            ArtifactFileInput(
                path="index.html",
                text="<style>body{background:rgb(25,140,70)}</style><h1>Recovered</h1>",
            )
        ],
        artifact.content_hash,
        "deadline-safe-revision",
    )
    monkeypatch.setattr(renderer_health, "SCRIPT_TIMEOUT_MS", 15_000)
    result = capture_artifact_image(store, artifact.id, tmp_path / "captures")
    with Image.open(result.path) as image:
        assert image.convert("RGB").getpixel((200, 200)) == (25, 140, 70)


def test_slide_readiness_timeout_releases_capture_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stalled slide readiness cannot retain the renderer through a pending promise."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "review.sqlite3")
    deck_id = "2026-11-08-deadline-slide"
    store.create(deck_id, "Fictional slide readiness")
    slide = store.read(deck_id).slides[0]
    options = CaptureOptions()
    prepared = snapshot(store, deck_id, slide, 1, options)
    nonce = re.search(r'<script nonce="([^"]+)">', prepared.document)
    assert nonce is not None
    # Exercise the readiness boundary without being blocked by the static slide's
    # script policy. Keep the policy intact and use the fixture's trusted nonce.
    pending = prepared.model_copy(
        update={
            "document": prepared.document + f'<script nonce="{nonce[1]}">'
            "window.ArtifactStudioCaptureReady = new Promise(() => {});</script>"
        }
    )
    monkeypatch.setattr(renderer_health, "SCRIPT_TIMEOUT_MS", 300)
    with pytest.raises(ValueError, match="Page.wait_for_function: Timeout"):
        _ = capture_snapshot(pending, options)
    assert capture_queue_status()["running"] is False
    monkeypatch.setattr(renderer_health, "SCRIPT_TIMEOUT_MS", 15_000)
    assert capture_snapshot(prepared, options).startswith(b"\x89PNG")
    assert prepared.frame.diagnostics is not None
