"""End-to-end local HTTP boundaries and scoped agent context for Artifact Hub."""

import base64
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError

from studio import app as service
from studio.artifact_models import ArtifactFileInput, ArtifactView, ProjectView
from studio.artifacts import ArtifactStore
from studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Bind all HTTP and MCP handlers to an isolated project collection."""
    store = Store(tmp_path / "workspace", tmp_path / "review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    return TestClient(service.app, base_url="http://127.0.0.1")


def import_page(client: TestClient) -> ArtifactView:
    """Import a freeform page and script through the same API used by the browser."""
    project_response = client.post(
        "/api/hub/projects",
        json={"title": "Research", "context": "Project private brief"},
    )
    assert project_response.status_code == 200, project_response.text
    project = ProjectView.model_validate_json(project_response.text)
    response = client.post(
        "/api/artifacts/import",
        json={
            "project_id": project.id,
            "title": "Page",
            "entrypoint": "index.html",
            "context": "Artifact private context",
            "files": [
                {
                    "name": "index.html",
                    "data_base64": base64.b64encode(
                        b"<html><head></head><body><h1>Hello</h1>"
                        b'<script src="app.js"></script></body></html>'
                    ).decode(),
                },
                {"name": "app.js", "data_base64": base64.b64encode(b"window.example = 1").decode()},
            ],
        },
    )
    assert response.status_code == 200, response.text
    return ArtifactView.model_validate_json(response.text)


def test_import_preview_and_immutable_source_routes(client: TestClient) -> None:
    """Preserve authored JS while isolating it from private metadata and workspace APIs."""
    artifact = import_page(client)
    listing = client.get("/api/hub")
    assert listing.status_code == 200
    assert listing.json()["artifacts"][0]["id"] == artifact.id
    preview = client.get(f"/api/artifacts/{artifact.id}/preview")
    assert preview.status_code == 200, preview.text
    assert '<h1>Hello</h1><script src="app.js"></script>' in preview.text
    assert "artifact-bridge.js" in preview.text
    assert f"/revisions/{artifact.revision}/files/" in preview.text
    assert "private brief" not in preview.text and "private context" not in preview.text
    policy = preview.headers["content-security-policy"]
    assert "sandbox allow-scripts" in policy and "allow-same-origin" not in policy
    resource = client.get(f"/api/artifacts/{artifact.id}/revisions/1/files/app.js")
    assert resource.status_code == 200 and resource.text == "window.example = 1"
    assert resource.headers["access-control-allow-origin"] == "null"
    assert resource.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in resource.headers["content-security-policy"]
    assert client.get(f"/api/artifacts/{artifact.id}/files/artifact.json").status_code == 404
    assert client.get(f"/api/artifacts/{artifact.id}/files/%2e%2e/INSTRUCTIONS.md").status_code in {
        400,
        404,
    }
    store = ArtifactStore(service.store)
    _ = store.revise_artifact(
        artifact.id,
        [ArtifactFileInput(path="app.js", text="window.example = 2")],
        1,
        "http-revision",
    )
    assert (
        client.get(f"/api/artifacts/{artifact.id}/revisions/1/files/app.js").text
        == "window.example = 1"
    )
    assert client.get(f"/api/artifacts/{artifact.id}/files/app.js").text == "window.example = 2"


def test_cross_origin_writes_and_unsafe_upload_paths_are_rejected(client: TestClient) -> None:
    """Block cross-origin mutations and traversal before publishing imported content."""
    assert (
        client.post(
            "/api/hub/projects", json={"title": "Blocked"}, headers={"Origin": "null"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/hub/projects", json={"title": "Blocked"}, headers={"Origin": "https://other.test"}
        ).status_code
        == 403
    )
    artifact = import_page(client)
    response = client.post(
        "/api/artifacts/import",
        json={
            "project_id": artifact.project_id,
            "title": "Unsafe",
            "files": [
                {"name": "../escape.html", "data_base64": base64.b64encode(b"unsafe").decode()}
            ],
        },
    )
    assert response.status_code == 400
    assert len(ArtifactStore(service.store).list_artifacts()) == 1
    assert (
        client.post(
            f"/api/artifacts/{artifact.id}/approve",
            json={
                "content_hash": artifact.content_hash,
                "approved": True,
            },
            headers={"Origin": "null"},
        ).status_code
        == 403
    )


def test_http_batch_groups_and_private_attachment_downloads(client: TestClient) -> None:
    """Keep batch discussion out of agent reads while allowing human draft downloads."""
    artifact = import_page(client)
    assert (
        client.post(f"/api/artifacts/{artifact.id}/mode", json={"mode": "batch"}).status_code == 200
    )
    group = {
        "kind": "group",
        "elements": [
            {"kind": "element", "selector": "h1", "quote": "Hello", "label": "Heading"},
            {"kind": "text", "quote": "Hello", "start": 0, "end": 5},
        ],
    }
    response = client.post(
        f"/api/artifacts/{artifact.id}/feedback",
        json={
            "message": "Change these together",
            "content_hash": artifact.content_hash,
            "request_id": "http-feedback",
            "target": group,
            "files": [
                {
                    "name": "private.html",
                    "data_base64": base64.b64encode(b"<script>private</script>").decode(),
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["released"] is False and len(body["target"]["elements"]) == 2
    human = client.get(f"/api/artifacts/{artifact.id}").json()
    assert human["feedback"][0]["message"] == "Change these together"
    assert human["workflow"]["pending"] == 1
    assert not ArtifactStore(service.store).feedback_feed(artifact.id).events
    download = client.get(body["files"][0]["download_url"])
    assert download.status_code == 200, download.text
    assert download.content == b"<script>private</script>"
    assert "attachment" in download.headers["content-disposition"]
    assert "application/octet-stream" in download.headers["content-type"]
    assert client.post(f"/api/artifacts/{artifact.id}/release").status_code == 200
    feed = ArtifactStore(service.store).feedback_feed(artifact.id)
    assert len(feed.events) == 1 and feed.events[0].released
    assert feed.events[0].target and len(feed.events[0].target.elements) == 2


def test_context_receipt_distinguishes_available_and_returned_sources(client: TestClient) -> None:
    """Only actual scoped agent context reads produce returned-source receipts."""
    artifact = import_page(client)
    inspected = client.get("/api/hub/context", params={"artifact_id": artifact.id})
    assert inspected.status_code == 200, inspected.text
    assert inspected.json()["receipt"] is None
    assert all(source["delivery"] == "available" for source in inspected.json()["sources"])
    context = service.get_working_context(artifact_id=artifact.id)
    assert cast(dict[str, object], context["artifact"])["id"] == artifact.id
    assert cast(dict[str, object], context["project"])["id"] == artifact.project_id
    sources = cast(list[dict[str, str]], context["sources"])
    assert all(source["delivery"] == "returned" for source in sources)
    assert context["receipt"] is not None
    retained = service.get_working_context(
        artifact_id=artifact.id,
        known_rules_hash=str(context["rules_hash"]),
        known_guide_hash=str(context["guide_hash"]),
    )
    assert retained["rules_included"] is False and retained["guide_included"] is False
    assert (
        cast(list[dict[str, str]], retained["sources"])[0]["delivery"] == "omitted because retained"
    )
    assert client.get("/api/hub/context", params={"artifact_id": artifact.id}).json()["receipt"]
    with pytest.raises(ToolError):
        _ = service.get_working_context(artifact_id=artifact.id, project_id="different-project")


def test_legacy_deep_links_and_project_assignment_remain_compatible(client: TestClient) -> None:
    """Open existing deck editor links while assigning related artifacts without relocation."""
    make_design(service.store.root / TEMPLATE_ID)
    deck = service.store.create("2026-11-02-api-legacy", "Legacy")
    artifact = import_page(client)
    source_path = Path(artifact.source_path)
    project = ProjectView.model_validate_json(
        client.post("/api/hub/projects", json={"title": "Related"}).text
    )
    for artifact_id in (deck.id, artifact.id):
        response = client.post(
            f"/api/artifacts/{artifact_id}/project", json={"project_id": project.id}
        )
        assert response.status_code == 200, response.text
        assert response.json()["project_id"] == project.id
    assert ArtifactStore(service.store).get_artifact(artifact.id).source_path == str(source_path)
    legacy = client.get("/", params={"deck": deck.id})
    assert legacy.status_code == 200 and "hub.js" not in legacy.text
    library = client.get("/")
    assert library.status_code == 200 and "hub.js" in library.text
    assert client.get(f"/api/decks/{deck.id}").status_code == 200
