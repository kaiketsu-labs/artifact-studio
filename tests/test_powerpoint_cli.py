"""Exercise the file helper against the real loopback MCP transport."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

from artifact_studio.artifacts import ArtifactStore
from tests.integration.support import running_app
from tests.pptx_fixture import fictional_powerpoint


def test_local_file_helper_and_mcp_errors(tmp_path: Path) -> None:
    path = tmp_path / "garden.pptx"
    path.write_bytes(fictional_powerpoint())
    with running_app(tmp_path / "app", tmp_path / "server.log") as app:
        project = ArtifactStore(app.store).create_project("Fictional garden")
        command = [
            sys.executable,
            "scripts/import_powerpoint_mcp.py",
            "--local",
            "--url",
            app.url,
            "--project-id",
            project.id,
            "--title",
            "Garden",
            "--file",
            str(path),
            "--request-id",
            "fictional-cli-import",
        ]
        env = {**os.environ, "ARTIFACT_STUDIO_AGENT_TOKEN": "must-not-be-sent"}
        result = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
        assert result.returncode == 0, result.stderr
        document = json.loads(result.stdout)
        assert not document["result"].get("isError")
        assert document["result"]["structuredContent"]["title"] == "Garden"
        path.write_bytes(b"invalid")
        failed = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
        assert failed.returncode != 0
        assert json.loads(failed.stdout)["result"]["isError"]
        command[command.index(app.url)] = "https://example.test"
        refused = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
        assert refused.returncode != 0
        assert "loopback" in refused.stderr


def test_local_helper_sse_and_redirect_do_not_forward(tmp_path: Path) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    requests: list[tuple[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            requests.append((self.path, self.headers.get("Authorization")))
            _ = self.rfile.read(int(self.headers["Content-Length"]))
            if len(requests) == 1:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"jsonrpc":"2.0","id":1,"result":{"isError":true}}\n\n')
            else:
                self.send_response(307)
                self.send_header(
                    "Location",
                    f"http://127.0.0.1:{cast(ThreadingHTTPServer, self.server).server_port}/other",
                )
                self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        path = tmp_path / "garden.pptx"
        path.write_bytes(b"fictional")
        command = [
            sys.executable,
            "scripts/import_powerpoint_mcp.py",
            "--local",
            "--url",
            f"http://127.0.0.1:{server.server_port}",
            "--project-id",
            "project-test",
            "--title",
            "Garden",
            "--file",
            str(path),
            "--request-id",
            "fictional-cli",
        ]
        env = {**os.environ, "ARTIFACT_STUDIO_AGENT_TOKEN": "must-not-be-sent"}
        sse = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
        assert sse.returncode != 0
        assert json.loads(sse.stdout)["result"]["isError"]
        redirect = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
        assert redirect.returncode != 0
        assert requests == [("/mcp", None), ("/mcp", None)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
