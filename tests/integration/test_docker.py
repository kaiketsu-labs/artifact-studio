"""Exercise the built image through its published port and a disposable named volume."""

import base64
import io
import json
import os
import socket
import subprocess
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from mcp.types import CallToolResult, ImageContent, TextContent
from PIL import Image

from artifact_studio.agent_client import Client, Invocation, object_value

PROJECT = Path(__file__).resolve().parents[2]
DECK = "2026-11-07-docker-example"
pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_DOCKER_TESTS") != "1", reason="Opt-in Docker integration"
    ),
]


@dataclass
class Container:
    """Use the shipped Compose settings, with an isolated name and available host port."""

    command: list[str]
    environment: dict[str, str]
    http: httpx.Client
    mcp: Client

    def compose(self, *arguments: str) -> str:
        result = subprocess.run(
            [*self.command, *arguments],
            cwd=PROJECT,
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    def call(self, tool: str, **arguments: object) -> dict[str, object]:
        return object_value(self.mcp.call(Invocation(tool=tool, arguments=arguments)))

    def post(self, path: str, **body: object) -> dict[str, object]:
        response = self.http.post(path, json=body)
        response.raise_for_status()
        return object_value(response.json())


@pytest.fixture(scope="module")
def container() -> Iterator[Container]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    with httpx.Client(base_url=url, trust_env=False, timeout=45) as http:
        app = Container(
            command=[
                "docker",
                "compose",
                "--project-name",
                "artifact-docker-test-" + uuid.uuid4().hex[:12],
                "--file",
                str(PROJECT / "compose.yaml"),
            ],
            environment={**os.environ, "ARTIFACT_STUDIO_PORT": str(port)},
            http=http,
            mcp=Client(url + "/mcp"),
        )
        try:
            _ = app.compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "90")
            app.mcp.initialize()
            yield app
        finally:
            try:
                print(app.compose("logs", "--no-color", "--tail=80", "studio"))
            finally:
                # Only this random test project's resources are removed.
                _ = app.compose("down", "--volumes", "--remove-orphans")


def test_image_has_runtime_without_private_build_context(container: Container) -> None:
    health = container.http.get("/api/health")
    health.raise_for_status()
    assert health.json()["service"] == "artifact-studio"
    assert health.json()["status"] == "ready"
    for path in ("/", "/web/hub.js", "/runtime/presentation.js"):
        response = container.http.get(path)
        assert response.status_code == 200 and len(response.content) > 100
    assert container.http.get("/api/app-info").json()["update_check"] == "disabled"
    assert container.compose("exec", "-T", "studio", "id", "-u") == "1000"
    for command in ("git", "gh", "tesseract"):
        assert container.compose("exec", "-T", "studio", command, "--version")
    assert "gh auth git-credential" in container.compose(
        "exec", "-T", "studio", "git", "config", "--get", "credential.https://github.com.helper"
    )
    _ = container.compose(
        "exec",
        "-T",
        "studio",
        "python",
        "-c",
        "from pathlib import Path; "
        "assert all(not Path('/app', p).exists() for p in "
        "['.git', '.env', '.state', 'tests', 'docs/assets']); "
        "assert Path('/app/agent/guides/artifacts.md').is_file(); "
        "assert Path('/app/docs/AGENT-GUIDE.md').is_file()",
    )


def test_mcp_authoring_sandboxed_capture_and_recreation(container: Container) -> None:
    _ = container.call(
        "create_design_pack",
        design_id="docker-blue",
        name="Fictional blue design",
        css=".slide { background: #edf5ff; color: #123456; font-family: sans-serif; }",
    )
    _ = container.call(
        "create_presentation",
        deck_id=DECK,
        title="Docker example",
        design_id="docker-blue",
        brief="Fictional fixture. Explain a reliable deployment.",
        request_id="docker-create-deck",
    )
    context = container.call("get_working_context", deck_id=DECK)
    assert "docker-blue" in json.dumps(context)
    before = container.call("get_slide", deck_id=DECK, slide_id="cover")
    html = (
        '<article class="slide" style="padding:6cqw">'
        '<h1 style="font-size:5cqw" data-element-id="title">Reliable deployment</h1></article>'
    )
    changed = container.call(
        "revise_slide",
        deck_id=DECK,
        slide_id="cover",
        expected_hash=before["expected_hash"],
        request_id="docker-revise-cover",
        patch={"html": html, "notes": "Fictional speaker note"},
    )
    assert changed["expected_hash"] != before["expected_hash"]
    status = container.call("get_capture_status", probe_renderer=True)
    assert object_value(status["renderer"])["status"] == "ready", status
    result = CallToolResult.model_validate(
        container.mcp.rpc(
            "tools/call",
            {"name": "capture_slide", "arguments": {"deck_id": DECK, "slide_id": "cover"}},
        )
    )
    assert not result.is_error, [
        item.text for item in result.content if isinstance(item, TextContent)
    ]
    pixels = next(item for item in result.content if isinstance(item, ImageContent))
    with Image.open(io.BytesIO(base64.b64decode(pixels.data))) as rendered:
        assert rendered.size == (1600, 900)
        assert rendered.convert("RGB").getpixel((10, 10)) == (237, 245, 255)
    manifest = object_value(result.structured_content)
    text = container.call(
        "check_slide_text",
        deck_id=DECK,
        slide_id="cover",
        capture_id=manifest["capture_id"],
        expected_text="Reliable deployment",
    )
    assert text["status"] == "no_mismatch_detected", text
    assert text["word_count"] == 2
    _ = container.post(
        f"/api/decks/{DECK}/feedback",
        message="Fictional human review survives recreation.",
        request_id="docker-human-feedback",
    )
    _ = container.post("/api/app-info/settings", include_nightly=True)
    before_container = container.compose("ps", "--quiet", "studio")
    _ = container.compose(
        "up", "-d", "--no-build", "--force-recreate", "--wait", "--wait-timeout", "90"
    )
    assert container.compose("ps", "--quiet", "studio") != before_container
    after = container.call("get_slide", deck_id=DECK, slide_id="cover")
    assert after["expected_hash"] == changed["expected_hash"]
    assert "Fictional speaker note" in json.dumps(after)
    feedback = container.call("read_feedback", deck_id=DECK)
    assert "Fictional human review survives recreation." in json.dumps(feedback)
    assert container.http.get("/api/app-info").json()["include_nightly"] is True
    # Existing captures and their manifests must survive along with the SQLite file.
    text_after = container.call(
        "check_slide_text",
        deck_id=DECK,
        slide_id="cover",
        capture_id=manifest["capture_id"],
        expected_text="Reliable deployment",
    )
    assert text_after["image_sha256"] == text["image_sha256"]


def test_host_files_import_through_container_path(container: Container, tmp_path: Path) -> None:
    source = tmp_path / "example.md"
    _ = source.write_text("# Fictional import\n\nCreated on the host.\n")
    source.chmod(0o600)
    _ = container.compose("cp", str(source), "studio:/data/imports/example.md")
    project = container.call("create_project", title="Docker import project")
    item = container.call(
        "register_artifact",
        project_id=project["id"],
        title="Imported example",
        source_path="/data/imports/example.md",
        entrypoint="example.md",
    )
    content = container.mcp.call(
        Invocation(
            tool="read_artifact_files",
            arguments={"artifact_id": item["id"], "paths": ["example.md"]},
        )
    )
    assert "Created on the host." in json.dumps(content)
