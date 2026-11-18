"""PowerPoint import via the local MCP protocol."""

import base64
import io
import zipfile
from pathlib import Path

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
    arguments = {
        "project_id": project.id,
        "title": "Garden plan",
        "data_base64": base64.b64encode(source.getvalue()).decode(),
        "request_id": "fictional-import-1",
    }
    with TestClient(service.app, base_url="http://127.0.0.1:8791") as client:

        def invoke() -> dict[str, object]:
            response = client.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "import_powerpoint", "arguments": arguments},
                },
            )
            assert response.status_code == 200, response.text
            return response.json()["result"]

        first = invoke()
        assert not first.get("isError"), first
        assert len(str(first)) < 4096
        assert invoke() == first
