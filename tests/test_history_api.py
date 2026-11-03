"""HTTP boundaries for private-free history previews and explicit concurrent restores."""

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from artifact_studio import app as service
from artifact_studio import history_thumbnails
from artifact_studio.artifact_models import ArtifactFileInput, ArtifactView
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.models import AgentContext, SlidePatch, SlideView
from artifact_studio.storage import Store, digest
from artifact_studio.version_history import HistoryPage
from artifact_studio.visuals import CaptureOptions, Snapshot
from tests.design_factory import TEMPLATE_ID, make_design

SLIDE_PATH = f"/api/decks/{TEMPLATE_ID}/slides/cover/history"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    """Bind actual application routes and error handlers to an isolated fictional collection."""
    root = tmp_path / "workspace"
    make_design(root / TEMPLATE_ID)
    result = Store(root, tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", result)
    return result


@pytest.fixture
def client(store: Store) -> TestClient:
    """Exercise production HTTP middleware without starting background synchronization."""
    assert store.root.exists()
    return TestClient(service.app, base_url="http://127.0.0.1")


def revise_slide(store: Store) -> None:
    """Retain an audience revision containing unique private data that previews must omit."""
    deck = store.read(TEMPLATE_ID)
    slide = deck.slides[0]
    slide.html = '<article class="slide"><h1>Earlier audience body</h1></article>'
    slide.notes = "PRIVATE-ORIGINAL-SPEAKER-NOTE"
    slide.agent = AgentContext(summary="PRIVATE-ORIGINAL-AGENT-CONTEXT")
    store.write(TEMPLATE_ID, deck)
    _ = store.revise(
        TEMPLATE_ID,
        "cover",
        digest(slide.model_dump_json()),
        "http-history-revision",
        SlidePatch(
            title="Updated audience title", html='<article class="slide">Current body</article>'
        ),
    )


def timeline(client: TestClient, path: str = SLIDE_PATH) -> HistoryPage:
    """Validate the typed metadata contract rather than depending on untyped JSON dictionaries."""
    response = client.get(path)
    assert response.status_code == 200, response.text
    return HistoryPage.model_validate_json(response.text)


def test_slide_history_metadata_and_preview_exclude_all_private_source(
    store: Store, client: TestClient
) -> None:
    """List small metadata and render only the selected historical audience content."""
    revise_slide(store)
    response = client.get(SLIDE_PATH, params={"limit": 1})
    assert response.status_code == 200, response.text
    page = HistoryPage.model_validate_json(response.text)
    assert len(page.items) == 1 and page.next_cursor is not None
    assert "<article" not in response.text and "PRIVATE-" not in response.text
    assert '"html"' not in response.text and '"notes"' not in response.text
    assert len(response.content) < 3000
    older_response = client.get(SLIDE_PATH, params={"cursor": page.next_cursor})
    assert older_response.status_code == 200, older_response.text
    older = HistoryPage.model_validate_json(older_response.text)
    assert older.items[0].revision == 1 and older.items[0].created_at is None
    preview = client.get(SLIDE_PATH + "/preview", params={"version_id": older.items[0].version_id})
    assert preview.status_code == 200, preview.text
    assert "Earlier audience body" in preview.text and "Current body" not in preview.text
    assert "PRIVATE-ORIGINAL-SPEAKER-NOTE" not in preview.text
    assert "PRIVATE-ORIGINAL-AGENT-CONTEXT" not in preview.text
    assert preview.headers["cache-control"] == "no-store"
    assert "sandbox allow-scripts" in preview.headers["content-security-policy"]
    assert "allow-same-origin" not in preview.headers["content-security-policy"]


def test_slide_restore_http_preserves_human_state_and_returns_exact_retry(
    store: Store, client: TestClient
) -> None:
    """Restore creates a new save while keeping current notes, visibility and deck ordering."""
    revise_slide(store)
    current = store.locate(store.read(TEMPLATE_ID), "cover")
    _ = store.save_notes(TEMPLATE_ID, "cover", "Latest human note", current.notes)
    store.set_slide_hidden(TEMPLATE_ID, "cover", hidden=True, expected_hidden=False)
    _ = store.reorder(TEMPLATE_ID, ["message", "cover"])
    page = timeline(client)
    body = {
        "version_id": page.items[-1].version_id,
        "expected_hash": page.expected_hash,
        "request_id": "http-restore-slide-001",
    }
    response = client.post(SLIDE_PATH + "/restore", json=body)
    assert response.status_code == 200, response.text
    restored = SlideView.model_validate_json(response.text)
    assert restored.revision == 3 and not restored.approved
    assert restored.notes == "Latest human note" and restored.hidden
    assert "Earlier audience body" in restored.html
    assert [slide.id for slide in store.read(TEMPLATE_ID).slides] == ["message", "cover"]
    _ = store.save_notes(TEMPLATE_ID, "cover", "Later human note", restored.notes)
    replay = client.post(SLIDE_PATH + "/restore", json=body)
    assert replay.status_code == 200 and replay.text == response.text
    assert store.locate(store.read(TEMPLATE_ID), "cover").notes == "Later human note"
    conflict = client.post(
        SLIDE_PATH + "/restore", json=body | {"request_id": "http-stale-restore"}
    )
    assert conflict.status_code == 409 and "Slide changed" in conflict.text
    reused = client.post(
        SLIDE_PATH + "/restore", json=body | {"version_id": page.items[0].version_id}
    )
    assert reused.status_code == 409 and "another payload" in reused.text


def test_concurrent_human_notes_reject_an_outdated_restore_without_losing_either_edit(
    store: Store, client: TestClient
) -> None:
    """A history dialog opened before a note save must refresh its concurrency baseline."""
    revise_slide(store)
    page = timeline(client)
    current = store.locate(store.read(TEMPLATE_ID), "cover")
    _ = store.save_notes(TEMPLATE_ID, "cover", "Concurrent human note", current.notes)
    response = client.post(
        SLIDE_PATH + "/restore",
        json={
            "version_id": page.items[-1].version_id,
            "expected_hash": page.expected_hash,
            "request_id": "http-notes-race-restore",
        },
    )
    assert response.status_code == 409
    retained = store.locate(store.read(TEMPLATE_ID), "cover")
    assert retained.title == "Updated audience title" and retained.notes == "Concurrent human note"


def test_artifact_http_history_previews_and_restore_pin_the_original_bundle(
    store: Store, client: TestClient, tmp_path: Path
) -> None:
    """Historical previews and fresh restores retain exact binary assets and clear approvals."""
    artifacts = ArtifactStore(store)
    source = tmp_path / "bundle"
    source.mkdir()
    _ = (source / "index.html").write_text("<h1>Earlier artifact body</h1>")
    _ = (source / "asset.bin").write_bytes(b"\x00\xff\x81")
    project = artifacts.create_project("Fictional project", "PRIVATE-PROJECT-CONTEXT")
    original = artifacts.register_artifact(
        project.id, "Fictional artifact", source, "index.html", context="PRIVATE-ARTIFACT-CONTEXT"
    )
    _ = artifacts.approve(original.id, original.content_hash, True)
    _ = artifacts.revise_artifact(
        original.id,
        [ArtifactFileInput(path="index.html", text="Current artifact body")],
        original.content_hash,
        "http-artifact-revision",
    )
    path = f"/api/artifacts/{original.id}/history"
    page = timeline(client, path)
    assert [item.revision for item in page.items] == [2, 1]
    assert page.groups[0].count == 2
    assert page.expected_hash != original.content_hash
    metadata = client.get(path).text
    assert "Earlier artifact body" not in metadata and "PRIVATE-" not in metadata
    preview = client.get(f"/api/artifacts/{original.id}/preview", params={"revision": 1})
    assert preview.status_code == 200, preview.text
    assert "Earlier artifact body" in preview.text and "Current artifact body" not in preview.text
    assert "PRIVATE-" not in preview.text and "/revisions/1/files/" in preview.text
    body = {
        "version_id": "artifact:1",
        "expected_hash": page.expected_hash,
        "request_id": "http-artifact-restore-001",
    }
    response = client.post(path + "/restore", json=body)
    assert response.status_code == 200, response.text
    restored = ArtifactView.model_validate_json(response.text)
    assert restored.revision == 3 and restored.content_hash == original.content_hash
    assert not restored.approved
    assert client.get(f"/api/artifacts/{original.id}/revisions/3/files/asset.bin").content == (
        b"\x00\xff\x81"
    )
    replay = client.post(path + "/restore", json=body)
    assert replay.status_code == 200 and replay.text == response.text
    outdated = client.post(path + "/restore", json=body | {"request_id": "http-artifact-stale"})
    assert outdated.status_code == 409 and "Artifact changed" in outdated.text
    assert timeline(client, path).expected_hash != page.expected_hash


def test_history_validation_and_missing_resource_errors_use_http_statuses(
    store: Store, client: TestClient
) -> None:
    """Invalid query bounds, malformed versions and missing retained sources fail explicitly."""
    revise_slide(store)
    page = timeline(client)
    assert client.get(SLIDE_PATH, params={"limit": 101}).status_code == 422
    assert client.get(SLIDE_PATH, params={"cursor": "not-a-cursor"}).status_code == 400
    assert (
        client.get(SLIDE_PATH + "/preview", params={"version_id": "slide:bad"}).status_code == 400
    )
    assert (
        client.get(SLIDE_PATH + "/preview", params={"version_id": "slide:" + "a" * 64}).status_code
        == 404
    )
    assert client.get("/api/decks/missing/slides/cover/history").status_code == 404
    assert client.get("/api/artifacts/missing/history").status_code == 404
    malformed = client.post(
        SLIDE_PATH + "/restore",
        json={
            "version_id": page.items[-1].version_id,
            "expected_hash": "bad",
            "request_id": "too-short",
        },
    )
    assert malformed.status_code == 422


def test_saved_slide_thumbnails_are_small_cached_and_design_sensitive(
    store: Store, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cache real snapshot identities without exposing private state or reusing a changed design."""
    revise_slide(store)
    documents: list[str] = []

    def render(prepared: Snapshot, options: CaptureOptions) -> bytes:
        """Record audience-only source and return deterministic fixture pixels."""
        assert options.reduced_motion
        documents.append(prepared.document)
        output = io.BytesIO()
        Image.new("RGB", (1600, 900), "#315fce").save(output, format="PNG")
        return output.getvalue()

    monkeypatch.setattr(history_thumbnails, "capture_snapshot", render)
    older = timeline(client).items[-1]
    url = SLIDE_PATH + "/thumbnail"
    first = client.get(url, params={"version_id": older.version_id})
    assert first.status_code == 200 and first.headers["content-type"] == "image/png"
    assert first.headers["cache-control"] == "no-store"
    with Image.open(io.BytesIO(first.content)) as thumbnail:
        assert thumbnail.size == (400, 225)
    assert len(documents) == 1 and "Earlier audience body" in documents[0]
    assert "Current body" not in documents[0] and "PRIVATE-" not in documents[0]
    repeated = client.get(url, params={"version_id": older.version_id})
    assert repeated.content == first.content and len(documents) == 1
    theme = store.folder(TEMPLATE_ID) / "theme.css"
    _ = theme.write_text(theme.read_text() + "\nh1{color:red}")
    assert client.get(url, params={"version_id": older.version_id}).status_code == 200
    assert len(documents) == 2
    assert client.get(url, params={"version_id": "slide:bad"}).status_code == 400
    assert client.get(url, params={"version_id": "slide:" + "a" * 64}).status_code == 404
    assert len(documents) == 2


def test_history_thumbnail_cache_has_a_fixed_limit(
    store: Store, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generating more versions evicts old thumbnails without altering retained source."""
    revise_slide(store)
    monkeypatch.setattr(history_thumbnails, "MAX_THUMBNAILS", 1)

    def render(prepared: Snapshot, options: CaptureOptions) -> bytes:
        """Return deterministic fixture pixels for each saved audience document."""
        assert prepared.document and options.reduced_motion
        output = io.BytesIO()
        Image.new("RGB", (1600, 900), "white").save(output, format="PNG")
        return output.getvalue()

    monkeypatch.setattr(history_thumbnails, "capture_snapshot", render)
    page = timeline(client)
    for item in page.items:
        assert (
            client.get(
                SLIDE_PATH + "/thumbnail", params={"version_id": item.version_id}
            ).status_code
            == 200
        )
    assert len(list((store.database.parent / "history-thumbnails").glob("*.png"))) == 1
    assert timeline(client).items == page.items
