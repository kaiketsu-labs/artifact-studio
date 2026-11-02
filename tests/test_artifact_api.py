"""End-to-end local HTTP boundaries and scoped agent context for Artifact Studio."""

import base64
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError

from artifact_studio import app as service
from artifact_studio.artifact_models import ArtifactFileInput, ArtifactView, ProjectView
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.design_library import DesignCreate, DesignLibrary
from artifact_studio.models import FileInput
from artifact_studio.storage import Store
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
    assert "sources" not in cast(dict[str, object], context["receipt"])
    assert context["receipt_sources"] == "sources"
    http_receipt = client.get("/api/hub/context", params={"artifact_id": artifact.id}).json()[
        "receipt"
    ]
    assert http_receipt["sources"] == retained["sources"]
    with pytest.raises(ToolError):
        _ = service.get_working_context(artifact_id=artifact.id, project_id="different-project")


def test_scoped_context_discovery_and_selected_reference(client: TestClient) -> None:
    """Discovery can be skipped without losing scoped context or selected references."""
    artifact = import_page(client)
    design = DesignLibrary(service.store).create(
        DesignCreate(
            name="Reference",
            files=[
                FileInput(name="guide.txt", data_base64=base64.b64encode(b"reference").decode())
            ],
        )
    )
    for artifact_id, project_id in ((artifact.id, ""), ("", artifact.project_id), ("", "")):
        selected = service.get_working_context(
            artifact_id=artifact_id,
            project_id=project_id,
            design_id=design.id,
            include_discovery=False,
        )
        assert selected["discovery_included"] is False and selected["available_designs"] == []
        assert selected["design_context"] == design.model_dump()
        assert selected["rules_included"] and selected["guide_included"]
        assert "pointer returned" in str(selected["sources"])
        if artifact_id or project_id:
            assert (
                cast(dict[str, object], selected["project"])["context"] == "Project private brief"
            )
        if artifact_id:
            assert (
                cast(dict[str, object], selected["artifact"])["context"]
                == "Artifact private context"
            )
    discovery = service.get_working_context(project_id=artifact.project_id)
    assert discovery["discovery_included"] is True
    assert all(
        "files" not in item
        for item in cast(list[dict[str, object]], discovery["available_designs"])
    )
    assert service.get_working_context(project_id=artifact.project_id, include_discovery=False)[
        "related_artifacts"
    ]


def test_legacy_artifact_retains_distinct_guides(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retained artifact guide never suppresses a different nested presentation guide."""
    make_design(service.store.root / TEMPLATE_ID)
    deck = service.store.create("2026-11-02-guides", "Legacy")
    first = service.get_working_context(artifact_id=deck.id, include_discovery=False)
    nested = cast(dict[str, object], first["presentation_context"])
    assert first["guide_included"] and nested["guide_included"]
    retained = service.get_working_context(
        artifact_id=deck.id,
        known_guide_hash=str(first["guide_hash"]),
        known_presentation_guide_hash=str(nested["guide_hash"]),
        known_rules_hash=str(first["rules_hash"]),
        include_discovery=False,
    )
    assert not retained["guide_included"]
    assert not cast(dict[str, object], retained["presentation_context"])["guide_included"]
    unknown = service.get_working_context(
        artifact_id=deck.id, known_guide_hash="unknown", known_presentation_guide_hash="unknown"
    )
    assert unknown["guide_included"]
    assert cast(dict[str, object], unknown["presentation_context"])["guide_included"]
    lost = service.get_working_context(artifact_id=deck.id)
    assert lost["guide"] == first["guide"]
    assert cast(dict[str, object], lost["presentation_context"])["guide"] == nested["guide"]
    docs = tmp_path / "app" / "docs"
    docs.mkdir(parents=True)
    (docs / "AGENT-QUICKSTART.md").write_text("Updated presentation guide")
    monkeypatch.setattr(service, "PROJECT", docs.parent)
    changed = service.get_working_context(
        artifact_id=deck.id,
        known_guide_hash=str(first["guide_hash"]),
        known_presentation_guide_hash=str(nested["guide_hash"]),
    )
    assert not changed["guide_included"]
    assert (
        cast(dict[str, object], changed["presentation_context"])["guide"]
        == "Updated presentation guide"
    )


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


def test_design_examples_are_separate_from_work_and_project_counts(client: TestClient) -> None:
    """Classify registered designs, preserving branded decks and direct template editing."""
    make_design(service.store.root / TEMPLATE_ID)
    design_bytes = (service.store.folder(TEMPLATE_ID) / "index.html").read_bytes()
    deck = service.store.create("2026-11-02-design-review", "Default design discussion")
    library = client.get("/api/hub").json()
    assert [item["id"] for item in library["artifacts"]] == [deck.id]
    assert library["projects"][0]["artifact_count"] == 1
    assert library["designs"][0]["folder_id"] == TEMPLATE_ID
    assert library["designs"][0]["is_default"] is True
    assert client.get(f"/api/artifacts/{TEMPLATE_ID}").status_code == 200
    assert (service.store.folder(TEMPLATE_ID) / "index.html").read_bytes() == design_bytes
    service.store.set_archived(deck.id, archived=True)
    library = client.get("/api/hub").json()
    assert library["artifacts"] == [] and library["projects"] == []
    assert len(library["designs"]) == 1
