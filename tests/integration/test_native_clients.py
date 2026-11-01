"""Pinned native CLI configuration checks; no model calls or trust bypasses."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from studio.agent_client import object_value
from tests.integration.support import RunningApp

pytestmark = [
    pytest.mark.native_clients,
    pytest.mark.skipif(
        os.environ.get("SLIDE_STUDIO_NATIVE_CLIENT_TESTS") != "1",
        reason="Opt-in native Codex/Claude CLIs",
    ),
]


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_native_mcp_configuration(live_app: RunningApp, artifacts: Path, harness: str) -> None:
    """Use official CLI commands with isolated or invocation-local configuration."""
    selected = os.environ.get("SLIDE_STUDIO_NATIVE_CLIENT", "")
    assert selected in {"", "codex", "claude-code"}, (
        "SLIDE_STUDIO_NATIVE_CLIENT must be codex or claude-code, or unset for both"
    )
    if selected and selected != harness:
        pytest.skip("Other native client runs in its own matrix job")
    executable = shutil.which("codex" if harness == "codex" else "claude")
    assert executable, f"Install the pinned {harness} CLI before enabling this test"
    model_credentials = {
        "OPENAI_API_KEY",
        "OPENAI_ACCESS_TOKEN",
        "CODEX_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
    }
    environment = {
        **{name: value for name, value in os.environ.items() if name not in model_credentials},
        "CLAUDE_CONFIG_DIR": str(live_app.project / "claude-config"),
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }

    def run(*arguments: str) -> str:
        """Capture only this test's explicit command output, with a bounded timeout."""
        return subprocess.run(
            [executable, *arguments],
            cwd=live_app.project,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=45,
        ).stdout

    version = run("--version")
    _ = (artifacts / "client-version.txt").write_text(version)
    url = live_app.url + "/mcp"
    if harness == "codex":
        details = object_value(
            json.loads(
                run(
                    "-c",
                    f'mcp_servers.slide-studio-ci.url="{url}"',
                    "mcp",
                    "get",
                    "slide-studio-ci",
                    "--json",
                )
            )
        )
        transport = object_value(details["transport"])
        assert details["enabled"] is True
        assert transport["type"] == "streamable_http"
        assert transport["url"] == url
    else:
        _ = run("mcp", "add", "--transport", "http", "--scope", "local", "slide-studio-ci", url)
        details = run("mcp", "get", "slide-studio-ci")
        _ = (artifacts / "mcp-status.txt").write_text(details)
        assert url in details
        assert "Connected" in details, details
