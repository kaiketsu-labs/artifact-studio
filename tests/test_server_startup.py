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
