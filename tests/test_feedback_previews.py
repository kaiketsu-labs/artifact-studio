"""Private image previews preserve originals and the existing feedback release boundary."""

import base64
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from artifact_studio import app as service
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.models import FileInput
from artifact_studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.mark.parametrize("kind", ["presentation", "artifact"])
def test_image_preview_preserves_private_original_and_uses_canonical_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Decode bounded PNGs from validated attachments, including relocated artifact metadata."""
    collection = tmp_path / "collection"
    make_design(collection / TEMPLATE_ID)
    store = Store(collection, tmp_path / "private/review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    artifacts = ArtifactStore(store)
    if kind == "presentation":
        target = store.create("2026-11-05-image-feedback", "Image feedback")
        identifier = target.id
        content_hash = store.view(identifier).slides[0].content_hash
        _ = store.set_mode(identifier, "batch")
        route = f"/api/decks/{identifier}/slides/{target.slides[0].id}/feedback"
    else:
        project = artifacts.create_project("Image feedback")
        source = tmp_path / "authored"
        source.mkdir()
        _ = (source / "index.md").write_text("# Example")
        item = artifacts.register_artifact(project.id, "Example", source, "index.md")
        identifier, content_hash = item.id, item.content_hash
        _ = artifacts.set_mode(identifier, "batch")
        route = f"/api/artifacts/{identifier}/feedback"
    encoded = io.BytesIO()
    Image.new("RGB", (2048, 512), "#1964ad").save(encoded, format="PNG")
    original = encoded.getvalue()
    client = TestClient(service.app, base_url="http://127.0.0.1")
    response = client.post(
        route,
        json={
            "content_hash": content_hash,
            "request_id": "image-private-feedback",
            "files": [{"name": "chart.png", "data_base64": base64.b64encode(original).decode()}],
        },
    )
    assert response.is_success, response.text
    if kind == "presentation":
        file = store.comments(identifier)[0].files[0]
        assert not store.feedback_feed(identifier).events
    else:
        event = artifacts.feedback(identifier, include_pending=True)[0]
        file = event.files[0]
        assert not artifacts.feedback_feed(identifier).events
        with store.connect() as db:
            row = db.execute(
                "SELECT document FROM artifact_feedback WHERE id=?", (event.event_id,)
            ).fetchone()
            document = json.loads(str(row[0]))
            document["files"][0]["path"] = str(tmp_path / "old-location/chart.png")
            _ = db.execute(
                "UPDATE artifact_feedback SET document=? WHERE id=?",
                (json.dumps(document), event.event_id),
            )
    preview = client.get(file.download_url + "?preview=true")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"
    assert preview.headers["cache-control"] == "no-store"
    assert preview.headers["cross-origin-resource-policy"] == "same-origin"
    with Image.open(io.BytesIO(preview.content)) as image:
        assert image.size == (640, 160)
        assert image.getpixel((0, 0)) == (25, 100, 173)
    enlarged = client.get(file.download_url + "?preview=true&full=true")
    with Image.open(io.BytesIO(enlarged.content)) as image:
        assert image.size == (2048, 512)
    assert client.get(file.download_url).content == original
    assert client.get(file.download_url.rsplit("/", 1)[0] + "/99?preview=true").status_code == 404


def test_misnamed_active_content_has_no_image_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never turn an uploaded document into active browser content based on its name."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "private/review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    slide = store.view(TEMPLATE_ID).slides[0]
    original = b"<html><script>window.untrusted = true</script></html>"
    event = store.add_feedback(
        TEMPLATE_ID,
        slide.id,
        "",
        slide.content_hash,
        "misnamed-image",
        files=[FileInput(name="image.png", data_base64=base64.b64encode(original).decode())],
    )
    url = f"/api/decks/{TEMPLATE_ID}/feedback/{event}/files/0"
    client = TestClient(service.app, base_url="http://127.0.0.1")
    assert client.get(url + "?preview=true").status_code == 204
    assert client.get(url).content == original
