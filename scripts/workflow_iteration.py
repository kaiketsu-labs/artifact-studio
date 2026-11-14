"""Persistent hosted MCP recorder and fictional review fixture.

Private JSONL includes fictional source/arguments; publish only aggregate summaries.
Run fixture: uv run python -m scripts.workflow_iteration serve --root /tmp/studio-workflow-iteration
Run calls: uv run python -m scripts.workflow_iteration calls --root ROOT --input calls.json
"""

import argparse
import base64
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from mcp.types import LATEST_PROTOCOL_VERSION


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class RecordedPeer:
    """One HTTP client, initialize and catalog per logical editing session.

    The JSONL records transport, tool failures and image payload sizes separately.
    Credentials are sent in headers only and are never recorded.
    """

    def __init__(
        self, root: Path, *, phase: str = "editor", transport: httpx.BaseTransport | None = None
    ) -> None:
        self.root = root
        self.phase = phase
        self.sequence = 0
        config = json.loads((root / "connection.json").read_text())
        self.deck_id = config["deck_id"]
        self.client = httpx.Client(
            base_url=config["url"],
            transport=transport,
            timeout=90,
            headers={
                "host": "studio.example.test",
                "authorization": "Bearer " + (root / "agent-token").read_text(),
                "accept": "application/json, text/event-stream",
            },
        )
        self.rpc(
            "initialize",
            {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "workflow-iteration", "version": "1"},
            },
        )
        self.rpc("notifications/initialized", None)
        self.catalog = self.rpc("tools/list", {})
        self.schemas = {tool["name"]: tool["inputSchema"] for tool in self.catalog["tools"]}
        (root / "tool-schemas.json").write_text(json.dumps(self.schemas, indent=2))

    def rpc(self, method: str, params: dict[str, Any] | None) -> dict[str, Any]:
        self.sequence += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body.update(id=self.sequence, params=params)
        request = self.client.build_request("POST", "/mcp", json=body)
        started = time.perf_counter()
        try:
            response = self.client.send(request)
        except httpx.HTTPError as error:
            record = {
                "sequence": self.sequence,
                "phase": self.phase,
                "method": method,
                "parameters": params,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "status": None,
                "transport_error": type(error).__name__,
                "request_http_body_utf8_bytes": len(request.content),
                "response_http_body_utf8_bytes": 0,
                "jsonrpc_error": None,
                "isError": False,
                "response_text": [],
                "request_parameters_utf8_bytes": len(compact(params).encode()) if params else 0,
            }
            with (self.root / "calls.jsonl").open("a") as stream:
                stream.write(compact(record) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            raise
        elapsed = time.perf_counter() - started
        try:
            packet = response.json() if response.content else {}
        except ValueError:
            packet = {
                "error": {"message": "Non-JSON HTTP response", "status": response.status_code}
            }

        result = packet.get("result", {})
        parts = result.get("content", [])
        texts = [p["text"] for p in parts if p.get("type") == "text"]
        images = [p for p in parts if p.get("type") == "image"]
        record = {
            "sequence": self.sequence,
            "phase": self.phase,
            "method": method,
            "parameters": params,
            "elapsed_ms": round(elapsed * 1000, 3),
            "status": response.status_code,
            "jsonrpc_error": packet.get("error"),
            "isError": bool(result.get("isError")),
            "response_text": texts,
            "request_parameters_utf8_bytes": len(compact(params).encode()) if params else 0,
            "request_http_body_utf8_bytes": len(response.request.content),
            "response_http_body_utf8_bytes": len(response.content),
            "response_mcp_json_utf8_bytes": len(compact(result).encode()),
            "response_text_utf8_bytes": sum(len(t.encode()) for t in texts),
            "image_blocks": len(images),
            "image_decoded_bytes": sum(len(base64.b64decode(p["data"])) for p in images),
            "image_base64_utf8_bytes": sum(len(p["data"].encode()) for p in images),
            "response_nonimage_json_utf8_bytes": len(
                compact(
                    {**result, "content": [p for p in parts if p.get("type") != "image"]}
                ).encode()
            ),
        }
        # One append + fsync per completed call, including errors and initialization.
        with (self.root / "calls.jsonl").open("a") as stream:
            stream.write(compact(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        response.raise_for_status()
        if packet.get("error"):
            raise ValueError(packet["error"])
        return result

    def call(self, name: str, *, expect_error: bool = False, **arguments: Any) -> Any:
        if name not in self.schemas:
            raise ValueError(f"Tool not in connected schema: {name}")
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        assert bool(result.get("isError")) == expect_error, result
        if expect_error:
            return result
        value = result.get("structuredContent")
        if value is None:
            texts = [p["text"] for p in result.get("content", []) if p["type"] == "text"]
            value = json.loads(texts[0]) if texts else result
        return value["result"] if isinstance(value, dict) and set(value) == {"result"} else value

    def close(self) -> None:
        self.client.close()


def serve(root: Path, system_browser: bool = False) -> None:
    """Keep an isolated hosted server alive for independent browser personas."""
    from tests.integration.multiplayer_fixture import running_multiplayer_app
    from tests.test_multiplayer_server import accept, grant, human

    if system_browser:
        import subprocess
        from unittest.mock import patch

        original_popen = subprocess.Popen

        def fixture_popen(command: Any, **kwargs: Any) -> Any:
            if isinstance(command, list) and len(command) > 1:
                runner = Path(command[1])
                if runner.name == "server.py" and runner.is_file():
                    source = runner.read_text()
                    adapter = (
                        "from playwright.sync_api import BrowserType\n"
                        "original_launch = BrowserType.launch\n"
                        "def system_launch(self, **kwargs):\n"
                        "    kwargs['executable_path'] = '/usr/bin/chromium'\n"
                        "    return original_launch(self, **kwargs)\n"
                        "BrowserType.launch = system_launch\n"
                    )
                    runner.write_text(
                        source.replace("uvicorn.run(app,", adapter + "uvicorn.run(app,")
                    )
            return original_popen(command, **kwargs)

        with patch("subprocess.Popen", fixture_popen):
            serve(root, system_browser=False)
        return
    root.mkdir(parents=True, exist_ok=True)
    with (
        running_multiplayer_app(root / "app", root / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as reviewer,
    ):
        accept(reviewer, grant(owner, "alice", app.deck_id, role="edit"))
        response = owner.post(
            "/api/workspaces/default/agents",
            json={
                "kind": "artifact",
                "resource": app.deck_id,
                "name": "Iteration editor",
            },
        )
        response.raise_for_status()
        token = root / "agent-token"
        token.write_text(response.json()["token"])
        token.chmod(0o600)
        (root / "connection.json").write_text(
            json.dumps(
                {
                    "url": app.url,
                    "deck_id": app.deck_id,
                    "project_id": app.project_id,
                    "artifact_id": app.artifact_id,
                    "sibling_id": app.sibling_id,
                    "workspace_origin": "https://studio.example.test",
                    "preview_origin": "https://preview.example.test",
                    "identities": ["owner", "alice"],
                },
                indent=2,
            )
        )
        print(f"Fictional hosted fixture ready: {root / 'connection.json'}", flush=True)
        while True:
            time.sleep(1)


def summarize(root: Path) -> dict[str, Any]:
    rows = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
    keys = sorted({key for row in rows for key in row if key.endswith("_bytes")})
    phases = sorted({row["phase"] for row in rows})
    return {
        "scope": "Actual recorded hosted MCP transport; provider usage unavailable",
        "calls": len(rows),
        "tool_calls": sum(r["method"] == "tools/call" for r in rows),
        "initializations": sum(r["method"] == "initialize" for r in rows),
        "schema_discoveries": sum(r["method"] == "tools/list" for r in rows),
        "transport_errors": sum(bool(r.get("transport_error")) for r in rows),
        "tool_errors": sum(r["isError"] for r in rows),
        "jsonrpc_errors": sum(r["jsonrpc_error"] is not None for r in rows),
        "totals": {key: sum(r.get(key, 0) for r in rows) for key in keys},
        "phases": {
            phase: {
                "calls": sum(r["phase"] == phase for r in rows),
                "rpc_wall_ms": sum(r["elapsed_ms"] for r in rows if r["phase"] == phase),
            }
            for phase in phases
        },
        "tokens": {
            "provider_usage": None,
            "text_estimate": None,
            "reason": "No legitimate local tokenizer resource established",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["serve", "calls", "summary"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument(
        "--system-browser", action="store_true", help="Test-only system Chromium adapter"
    )
    args = parser.parse_args()
    if args.command == "serve":
        serve(args.root, args.system_browser)
    elif args.command == "summary":
        print(json.dumps(summarize(args.root), indent=2))
    else:
        if args.input is None:
            parser.error("calls requires --input")
        peer = RecordedPeer(args.root)
        try:
            for invocation in json.loads(args.input.read_text()):
                peer.phase = invocation.get("phase", "editor")
                value = peer.call(invocation["tool"], **invocation.get("arguments", {}))
                print(compact({"tool": invocation["tool"], "result": value}))
        finally:
            peer.close()


if __name__ == "__main__":
    main()
