"""Opt-in checks for the local presentation PDF renderer."""

import os
from pathlib import Path

import pytest

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.sharing import SharingService
from artifact_studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_render_presentation_pdf(tmp_path: Path) -> None:
    """Produce actual PDF bytes through Chromium from a sanitized audience document."""
    make_design(tmp_path / "workspace" / TEMPLATE_ID)
    store = Store(tmp_path / "workspace", tmp_path / "state" / "review.sqlite3")
    deck = store.create("2026-11-02-fictional", "Fictional")
    slide = store.view(deck.id).slides[0]
    store.save_notes(deck.id, slide.id, "PRIVATE_NOTE_SENTINEL", slide.notes)
    result = SharingService(store, ArtifactStore(store)).prepare_artifact(deck.id, "pdf")
    assert result.data.startswith(b"%PDF-")
    assert b"PRIVATE_NOTE_SENTINEL" not in result.data
    assert len(result.data) > 1000
