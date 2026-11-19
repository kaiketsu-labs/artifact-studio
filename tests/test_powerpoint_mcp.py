"""PowerPoint import and export via the local MCP protocol."""

import base64
import io
import zipfile
from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient

from artifact_studio import app as service
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.storage import Store
from tests.pptx_fixture import fictional_powerpoint


def test_local_mcp_import_retry(tmp_path: Path, monkeypatch: object) -> None:
    from pytest import MonkeyPatch

    assert isinstance(monkeypatch, MonkeyPatch)
    store = Store(tmp_path / "collection", tmp_path / "review.sqlite3")
    project = ArtifactStore(store).create_project("Fictional garden")
    monkeypatch.setattr(service, "store", store)
    source = io.BytesIO(fictional_powerpoint())
    with zipfile.ZipFile(source, "a", compression=zipfile.ZIP_STORED) as package:
        package.writestr("fictional-padding.bin", b"x" * (2 * 1024 * 1024))
    arguments: dict[str, object] = {
        "project_id": project.id,
        "title": "Garden plan",
        "data_base64": base64.b64encode(source.getvalue()).decode(),
        "request_id": "fictional-import-1",
    }
    with TestClient(service.app, base_url="http://127.0.0.1:8791") as client:

        def invoke(name: str, tool_arguments: dict[str, object]) -> dict[str, object]:
            response = client.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": tool_arguments},
                },
            )
            assert response.status_code == 200, response.text
            return response.json()["result"]

        first = invoke("import_powerpoint", arguments)
        assert not first.get("isError"), first
        assert len(str(first)) < 4096
        assert invoke("import_powerpoint", arguments) == first
        deck_id = cast(dict[str, object], first["structuredContent"])["id"]
        result = invoke("export_powerpoint", {"deck_id": deck_id})
        assert not result.get("isError"), result
        download_path = f"/api/decks/{deck_id}/exports/pptx"
        assert result["structuredContent"] == {
            "deck_id": deck_id,
            "filename": f"{deck_id}.pptx",
            "download_url": "http://127.0.0.1:8791" + download_path,
        }
        binary = client.get(download_path).raise_for_status().content
        with zipfile.ZipFile(io.BytesIO(binary)) as package:
            assert "ppt/presentation.xml" in package.namelist()
