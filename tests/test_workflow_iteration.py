"""Measurement must retain failed calls and keep image bytes out of text totals."""

import base64
import json
from pathlib import Path

import httpx
import pytest

from scripts.workflow_iteration import RecordedPeer, compact, summarize


def test_recorder_initializes_once_and_records_errors_and_images(tmp_path: Path) -> None:
    (tmp_path / "connection.json").write_text(
        json.dumps({"url": "http://127.0.0.1:1", "deck_id": "fictional"})
    )
    (tmp_path / "agent-token").write_text("fictional-token-never-log-this")
    methods: list[str] = []
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        body = json.loads(request.content)
        method = body["method"]
        methods.append(method)
        if method == "notifications/initialized":
            return httpx.Response(202)
        result: object = {}
        if method == "tools/list":
            schema: dict[str, object] = {}
            result = {"tools": [{"name": "sample", "inputSchema": schema}]}
        elif method == "tools/call":
            calls += 1
            if calls == 1:
                result = {"isError": True, "content": [{"type": "text", "text": "Conflict é"}]}
            elif calls == 2:
                result = {
                    "content": [
                        {"type": "text", "text": "é"},
                        {
                            "type": "image",
                            "data": base64.b64encode(b"fictional-image").decode(),
                            "mimeType": "image/png",
                        },
                    ]
                }
            elif calls == 3:
                raise httpx.ConnectError("Fictional refused connection", request=request)
            else:
                return httpx.Response(502, text="fictional non-JSON gateway response")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body.get("id"), "result": result})

    peer = RecordedPeer(tmp_path, transport=httpx.MockTransport(handle))
    try:
        assert peer.call("sample", expect_error=True)["isError"]
        result = peer.rpc("tools/call", {"name": "sample", "arguments": {}})
        assert len(result["content"]) == 2
        with pytest.raises(httpx.ConnectError):
            peer.call("sample")
        with pytest.raises(httpx.HTTPStatusError):
            peer.call("sample")
    finally:
        peer.close()
    rows = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert len(rows) == len(methods) == 7
    assert methods.count("initialize") == methods.count("tools/list") == 1
    image = rows[4]
    assert image["response_text_utf8_bytes"] == len("é".encode())
    assert image["image_decoded_bytes"] == len(b"fictional-image")
    assert image["response_mcp_json_utf8_bytes"] == len(compact(result).encode())
    assert rows[5]["request_http_body_utf8_bytes"] > 0
    assert rows[5]["request_http_body_utf8_bytes"] == rows[6]["request_http_body_utf8_bytes"]
    summary = summarize(tmp_path)
    assert summary["tool_errors"] == summary["transport_errors"] == summary["jsonrpc_errors"] == 1
    assert "fictional-token-never-log-this" not in (tmp_path / "calls.jsonl").read_text()
