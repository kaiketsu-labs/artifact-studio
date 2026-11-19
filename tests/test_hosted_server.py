"""Exercise the complete hosted application boundary and remote MCP transport."""

from pathlib import Path

import httpx

from artifact_studio.artifacts import ArtifactStore
from tests.integration.hosted_fixture import WORKSPACE, running_hosted_app


def test_hosted_app_routes_and_scoped_agent_protocol(tmp_path: Path) -> None:
    with (
        running_hosted_app(tmp_path / "server", tmp_path / "server.log") as server,
        httpx.Client(base_url=server.url, headers={"host": "studio.example.test"}) as client,
    ):
        assert client.get("/").status_code == 401
        assert client.get("/web/hub.js").status_code == 401
        assert client.get("/api/hub").status_code == 401
        member = {"x-fixture-auth": "member"}
        for path in (
            "/",
            "/?editor=1",
            "/web/index.html",
            "/web/hub.html",
            "/web/copilot-setup.html",
        ):
            response = client.get(path, headers=member)
            assert response.status_code == 200
            assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
            assert (
                "frame-src https://studio.example.test/api/artifacts/"
                in response.headers["content-security-policy"]
            )
            assert (
                "https://preview.example.test/_preview/"
                in response.headers["content-security-policy"]
            )
        session = client.get("/api/session", headers=member)
        assert session.status_code == 200
        assert session.json()["mcp_url"] == WORKSPACE + "/mcp"
        assert session.json()["capabilities"]["local_admin"] is False
        checks = client.get("/api/deployment/security", headers=member)
        assert checks.status_code == 200
        assert checks.json() == session.json()["security"]
        assert checks.json()["summary"] == "review"
        assert "reviewer@example.test" not in checks.text
        assert client.get("/api/deployment/security").status_code == 401
        assert (
            client.get("/api/deployment/security", headers={"x-fixture-auth": "agent"}).status_code
            == 403
        )
        for path in ("/api/libraries", "/api/workspace/archive", "/decks/.private/key"):
            assert client.get(path, headers=member).status_code == 404
        assert client.get("/api/hub", headers={"x-fixture-auth": "agent"}).status_code == 403
        assert (
            client.post(
                "/api/hub/projects", headers=member, json={"title": "No origin"}
            ).status_code
            == 403
        )
        created = client.post(
            "/api/hub/projects",
            headers={**member, "origin": WORKSPACE},
            json={"title": "Fictional hosted project"},
        )
        assert created.status_code == 200
        agent = {"x-fixture-auth": "agent", "accept": "application/json, text/event-stream"}
        initialized = client.post(
            "/mcp",
            headers=agent,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "hosted-test", "version": "1"},
                },
            },
        )
        assert initialized.status_code == 200
        assert initialized.json()["result"]["serverInfo"]["name"] == "Artifact Studio Hosted"
        listed = client.post(
            "/mcp",
            headers=agent,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
        )
        assert listed.status_code == 200
        names = {tool["name"] for tool in listed.json()["result"]["tools"]}
        assert {
            "get_working_context",
            "create_artifact",
            "revise_slide",
            "capture_slide",
            "start_live_review",
            "get_live_review_status",
        } <= names
        assert not {"register_artifact", "install_review_hooks", "create_library"} & names
        assert (
            client.post("/mcp", headers={**agent, "origin": WORKSPACE}, json={}).status_code == 403
        )


def test_hosted_download_and_direct_preview_boundary(tmp_path: Path) -> None:
    with running_hosted_app(tmp_path / "server", tmp_path / "server.log") as server:
        registry = ArtifactStore(server.store)
        project = registry.create_project("Fictional document boundary")
        source = tmp_path / "source"
        source.mkdir()
        (source / "index.html").write_text("<h1>Fixture</h1><script>window.fixture=true</script>")
        (source / "preview").write_text("Fictional bundle data with a route-like filename")
        artifact = registry.register_artifact(project.id, "Fixture", source, "index.html")
        with httpx.Client(
            base_url=server.url,
            headers={
                "host": "studio.example.test",
                "x-fixture-auth": "member",
            },
        ) as client:
            download = client.get(f"/api/artifacts/{artifact.id}/files/index.html")
            assert download.status_code == 200
            assert download.headers["content-disposition"].startswith("attachment;")
            assert "sandbox" in download.headers["content-security-policy"]
            url = f"/api/artifacts/{artifact.id}/preview"
            assert client.get(url).status_code == 403
            for destination in ("document", "object", "embed"):
                assert client.get(url, headers={"sec-fetch-dest": destination}).status_code == 403
            assert client.get(url, headers={"sec-fetch-dest": "iframe"}).status_code == 200
            assert client.get(f"/api/artifacts/{artifact.id}/files/preview").status_code == 200
            assert (
                client.get("/api/hub", headers={"host": "preview.example.test"}).status_code == 404
            )


def test_hosted_service_downloads_powerpoint_with_its_existing_credentials(tmp_path: Path) -> None:
    """Service credentials authorize the binary route but not the editor URL."""
    import base64
    from io import BytesIO
    from zipfile import ZipFile

    from artifact_studio.powerpoint import import_powerpoint
    from tests.pptx_fixture import fictional_powerpoint

    with (
        running_hosted_app(tmp_path / "server", tmp_path / "server.log") as server,
        httpx.Client(base_url=server.url, headers={"host": "studio.example.test"}) as client,
    ):
        project = ArtifactStore(server.store).create_project("Fictional garden")
        deck = import_powerpoint(
            server.store,
            project.id,
            "Fictional garden",
            base64.b64encode(fictional_powerpoint()).decode(),
        )
        agent = {"x-fixture-auth": "agent", "accept": "application/json, text/event-stream"}
        receipt = (
            client.post(
                "/mcp",
                headers=agent,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "export_powerpoint", "arguments": {"deck_id": deck.id}},
                },
            )
            .raise_for_status()
            .json()["result"]["structuredContent"]
        )
        download_path = f"/api/agent/decks/{deck.id}/exports/pptx"
        assert receipt["download_url"] == WORKSPACE + download_path
        assert client.get(download_path).status_code == 401
        assert client.get(download_path, headers={"x-fixture-auth": "member"}).status_code == 403
        assert client.get(f"/api/decks/{deck.id}/exports/pptx", headers=agent).status_code == 403
        response = client.get(download_path, headers=agent).raise_for_status()
        with ZipFile(BytesIO(response.content)) as powerpoint:
            assert "ppt/presentation.xml" in powerpoint.namelist()
