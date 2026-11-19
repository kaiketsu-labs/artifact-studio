"""Imported text revisions preserve surrounding source and reject stale edits."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from artifact_studio import app as service
from artifact_studio.models import SlidePatch
from artifact_studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design


def test_imported_text_revision_and_stale_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    original = store.view(TEMPLATE_ID).slides[0]
    markup = (
        '<article class="slide" data-pptx-width="100" data-pptx-height="50">'
        "<div><span>Old</span></div></article>"
    )
    store.revise(
        TEMPLATE_ID, original.id, original.expected_hash, "initial-import", SlidePatch(html=markup)
    )
    imported = store.view(TEMPLATE_ID).slides[0]
    client = TestClient(service.app)
    endpoint = f"/api/decks/{TEMPLATE_ID}/slides/{original.id}/imported-text"
    body = {
        "expected_hash": imported.expected_hash,
        "request_id": "human-edit-1",
        "path": [0, 0],
        "text": "New & safe",
    }
    assert client.post(endpoint, json=body).status_code == 200
    saved = Store(store.root, store.database).view(TEMPLATE_ID).slides[0]
    assert saved.html == markup.replace("Old", "New &amp; safe")
    assert saved.revision == imported.revision + 1
    assert client.post(endpoint, json=body).status_code == 200
    assert client.post(endpoint, json={**body, "text": "Different"}).status_code == 409
    assert (
        client.post(
            endpoint, json={**body, "request_id": "human-edit-2", "text": "Stale"}
        ).status_code
        == 409
    )
    assert store.view(TEMPLATE_ID).slides[0].html == saved.html


def test_title_sync_keeps_custom_names() -> None:
    from artifact_studio.powerpoint.edit import edit_pptx_text, synchronized_title

    source = (
        '<article class="slide" data-pptx-width="100" data-pptx-height="50">'
        '<div data-pptx-title="title"><div><div><span>Hello</span><span>world</span>'
        "</div></div></div></article>"
    )
    path = [0, 0, 0, 1]
    updated = edit_pptx_text(source, path, "there")
    assert synchronized_title(source, updated, path, "Hello world") == "Hello there"
    assert synchronized_title(source, updated, path, "My custom name") is None


def test_imported_shape_transform_revision_and_group_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    first = store.view(TEMPLATE_ID).slides[0]
    source = (
        '<article class="slide" data-pptx-width="100" data-pptx-height="50">'
        '<div data-pptx-object="Shape" style="left:10%;top:20%;width:30%;height:40%;'
        'transform:rotate(10deg) scale(-1,1);"><span>Keep me</span></div>'
        '<div data-pptx-object="Group" data-pptx-group="true" '
        'style="left:0%;top:0%;"><div data-pptx-object="Nested"></div></div></article>'
    )
    store.revise(
        TEMPLATE_ID, first.id, first.expected_hash, "initial-shape", SlidePatch(html=source)
    )
    imported = store.view(TEMPLATE_ID).slides[0]
    client = TestClient(service.app)
    endpoint = f"/api/decks/{TEMPLATE_ID}/slides/{first.id}/imported-transform"
    body = {
        "expected_hash": imported.expected_hash,
        "request_id": "transform-1",
        "index": 0,
        "dx": 0.05,
        "dy": -0.1,
        "rotation": 30,
    }
    assert client.post(endpoint, json={**body, "index": 1}).status_code == 400
    assert client.post(endpoint, json=body).status_code == 200
    saved = Store(store.root, store.database).view(TEMPLATE_ID).slides[0]
    assert "left:15%;top:10%" in saved.html
    assert "transform:rotate(10deg) scale(-1,1) rotate(30deg);" in saved.html
    assert "<span>Keep me</span>" in saved.html
    assert saved.revision == imported.revision + 1
    assert client.post(endpoint, json=body).status_code == 200
    assert store.view(TEMPLATE_ID).slides[0].html == saved.html
    assert client.post(endpoint, json={**body, "dx": 0.2}).status_code == 409
    assert client.post(endpoint, json={**body, "request_id": "transform-2"}).status_code == 409


def test_clustered_value_edit_preserves_other_bars_and_fixed_scale() -> None:
    from artifact_studio.powerpoint.edit import edit_pptx_text

    def group(value: int, height: str) -> str:
        return (
            '<div data-chart-fixed-max="40">'
            f'<div data-chart-bar="" style="height:{height}%"></div>'
            f'<span data-chart-value="{value}" aria-label="Chart value {value}" '
            f'style="height:{height}%">{value}</span></div>'
        )

    def slide(first_group: str) -> str:
        return (
            '<article data-pptx-width="100" data-pptx-height="50">'
            '<div data-pptx-chart="column" data-chart-max="40">'
            + first_group
            + group(32, "80")
            + "</div></article>"
        )

    source = slide(group(24, "60"))
    target = [0, 0, 1]
    for value, height in ((29, "72.5000"), (0, "0.0000")):
        assert edit_pptx_text(source, target, str(value)) == slide(group(value, height))
    with pytest.raises(ValueError, match="fixed axis"):
        edit_pptx_text(source, target, "41")
