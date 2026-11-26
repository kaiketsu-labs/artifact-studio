"""Exercise the container startup policy against actual Uvicorn access logging."""

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from starlette.responses import PlainTextResponse
from starlette.types import Receive, Scope, Send

from artifact_studio.request_diagnostics import RequestDiagnostics
from tests.integration.support import bound_server_url


async def endpoint(scope: Scope, receive: Receive, send: Send) -> None:
    await PlainTextResponse("not found", status_code=404)(scope, receive, send)


app = RequestDiagnostics(endpoint, "preview.example.test")


@pytest.mark.parametrize("mode,expect_access_log", [("hosted", False), ("local", True)])
def test_real_uvicorn_access_log_obeys_mode(
    tmp_path: Path, mode: str, expect_access_log: bool
) -> None:
    """The hosted container suppresses URLs while local operators retain access logs."""
    log = tmp_path / "server.log"
    source = (
        "from artifact_studio.server import run; "
        'run("tests.test_server_startup:app", host="127.0.0.1", port=0)'
    )
    with log.open("w") as output:
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", source],
            stdout=output,
            stderr=subprocess.STDOUT,
            env={**os.environ, "ARTIFACT_STUDIO_MODE": mode},
        )
        try:
            deadline = time.monotonic() + 15
            url = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(log.read_text())
                url = bound_server_url(log)
                if url:
                    break
                time.sleep(0.05)
            assert url is not None
            response = httpx.get(
                url + "/_preview/fictional-path-secret/resource?token=fictional-query-secret",
                headers={"host": "preview.example.test"},
            )
            assert response.status_code == 404
            request_id = response.headers["x-request-id"]
        finally:
            process.terminate()
            process.wait(timeout=10)
    text = log.read_text()
    assert ("fictional-path-secret" in text) is expect_access_log
    assert ("fictional-query-secret" in text) is expect_access_log
    assert f"id={request_id}" in text
    assert "outcome=http_error" in text


def test_stateless_mcp_client_does_not_hold_up_sigterm(tmp_path: Path) -> None:
    """An initialized client can stay connected while an ordinary Uvicorn launch exits."""
    log = tmp_path / "mcp.log"
    with log.open("w") as output, httpx.Client(timeout=5) as client:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "artifact_studio.app:app", "--port", "0"],
            stdout=output,
            stderr=subprocess.STDOUT,
            env={
                **os.environ,
                "ARTIFACT_STUDIO_MODE": "local",
                "ARTIFACT_STUDIO_COLLECTION": str(tmp_path / "collection"),
                "ARTIFACT_STUDIO_DATABASE": str(tmp_path / "state" / "review.sqlite3"),
            },
        )
        try:
            deadline = time.monotonic() + 20
            while not (base := bound_server_url(log)):
                if process.poll() is not None or time.monotonic() >= deadline:
                    pytest.fail(log.read_text())
                time.sleep(0.05)
            headers = {"Accept": "application/json, text/event-stream"}
            initialized = client.post(
                base + "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "id": 1,
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "fixture", "version": "1"},
                    },
                },
            )
            assert initialized.status_code == 200 and "result" in initialized.json()
            with client.stream("GET", base + "/mcp", headers={"Accept": "text/event-stream"}) as r:
                assert r.status_code == 405 and "POST" in r.headers["allow"]
                process.terminate()
                _ = process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                _ = process.wait(timeout=5)
    assert "Application shutdown complete" in log.read_text()
