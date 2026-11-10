"""Version binding and bounded inputs for explicit image exports."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from artifact_studio import app as service
from artifact_studio import image_api
from artifact_studio.artifact_models import ArtifactFileInput, ArtifactView
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.selection_image import (
    ArtifactSelectionImageInput,
    SelectionImage,
    SelectionImageInput,
)
from artifact_studio.visuals import CaptureOptions, Snapshot
from tests.design_factory import TEMPLATE_ID, make_design
from tests.test_artifact_api import client as client
from tests.test_artifact_api import import_page


def test_full_slide_image_is_version_bound_and_excludes_private_context(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Copy saved audience artwork without changing notes, feedback or the source."""
    make_design(service.store.root / TEMPLATE_ID)
    before = service.store.read(TEMPLATE_ID)
    item = service.store.view(TEMPLATE_ID).slides[0]
    calls: list[Snapshot] = []

    def render(prepared: Snapshot, options: CaptureOptions) -> bytes:
        assert options.reduced_motion
        assert prepared.frame.slide_id == item.id
        assert prepared.frame.content_hash == item.content_hash
        for slide in before.slides:
            if slide.notes:
                assert slide.notes not in prepared.document
        calls.append(prepared)
        return b"png-image"

    monkeypatch.setattr(image_api, "capture_snapshot", render)
    endpoint = f"/api/decks/{TEMPLATE_ID}/slides/{item.id}/image"
    result = client.post(endpoint, json={"content_hash": item.content_hash})
    assert result.status_code == 200
    assert result.content == b"png-image"
    assert result.headers["content-type"] == "image/png"
    assert result.headers["content-disposition"] == 'attachment; filename="slide.png"'
    assert client.post(endpoint, json={"content_hash": "0" * 64}).status_code == 409
    assert client.post(endpoint, json={"content_hash": "invalid"}).status_code == 422
    assert len(calls) == 1
    assert service.store.read(TEMPLATE_ID) == before
    assert service.store.feedback_feed(TEMPLATE_ID).events == []


@pytest.mark.parametrize(
    "values",
    [
        {"selectors": []},
        {"selectors": [""]},
        {"selectors": ["#a"] * 101},
        {"selectors": ["#a", "#b"], "format": "original"},
        {"width": 4096, "height": 30000},
        {"content_hash": "stale"},
    ],
)
def test_export_rejects_unbounded_or_ambiguous_input(values: dict[str, object]) -> None:
    """Validate finite selection and source identity before allocating a renderer."""
    with pytest.raises(ValidationError):
        _ = SelectionImageInput.model_validate(
            {"content_hash": "a" * 64, "selectors": ["#a"], **values}
        )


def test_artifact_export_pins_revision_and_never_saves_preview(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Allow an older displayed revision without exporting the newest file or saving drafts."""
    item = import_page(client)
    store = ArtifactStore(service.store)
    newer = store.revise_artifact(
        item.id,
        [ArtifactFileInput(path="index.html", text="<h1>Newer</h1>")],
        item.content_hash,
        "export-concurrent-change",
    )
    calls: list[tuple[int, str, str]] = []

    def render(
        registry: ArtifactStore,
        artifact: ArtifactView,
        request: ArtifactSelectionImageInput,
        origin: str,
    ) -> SelectionImage:
        """Record the immutable source handed to the sandbox boundary."""
        assert registry.get_artifact(item.id).revision == newer.revision
        calls.append((artifact.revision, request.preview_html, origin))
        return SelectionImage(b"original", "image/jpeg", "image.jpg")

    monkeypatch.setattr(image_api, "export_artifact_selection", render)
    body = {
        "revision": item.revision,
        "content_hash": item.content_hash,
        "selectors": ["h1"],
        "preview_html": "<h1>Unsent preview</h1>",
        "format": "original",
    }
    endpoint = f"/api/artifacts/{item.id}/selection-image"
    response = client.post(endpoint, json=body)
    assert response.status_code == 200
    assert response.content == b"original"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["content-disposition"] == 'attachment; filename="image.jpg"'
    assert calls == [(item.revision, body["preview_html"], "http://127.0.0.1")]
    assert store.get_artifact(item.id).content_hash == newer.content_hash
    assert store.feedback(item.id, include_pending=True) == []
    mismatch = client.post(endpoint, json={**body, "content_hash": newer.content_hash})
    assert mismatch.status_code == 409
    assert len(calls) == 1
