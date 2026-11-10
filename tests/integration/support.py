"""Start the real server with fictional decks, never a user's collection."""

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

from artifact_studio.agent_client import Client, Invocation, object_value
from artifact_studio.models import AgentContext, Slide, SlidePatch
from artifact_studio.storage import Store, digest
from tests.design_factory import TEMPLATE_ID, make_design

PROJECT = Path(__file__).resolve().parents[2]
DECK_ID = "2026-11-01-ci-workflow"
PRIVATE_NOTE = "PRIVATE_CI_SPEAKER_NOTE_71b03"


def bound_server_url(logs: Path) -> str | None:
    """Read this child's atomically bound port, never probe and release a shared port."""
    match = re.search(r"Uvicorn running on (http://127\.0\.0\.1:[1-9]\d*)\b", logs.read_text())
    return match[1] if match else None


def markup(title: str) -> str:
    """Scale the fictional design equally in thumbnails, editor and slideshow."""
    return (
        '<article class="slide" style="background:#f4f7fb;color:#172b4d;'
        'padding:6.875cqw;font-family:Arial,sans-serif">'
        '<p style="font-size:1.5625cqw">FICTIONAL CI FIXTURE</p>'
        f'<h1 style="font-size:4.625cqw;line-height:1.1;margin:4.6875cqw 0">{title}</h1>'
        '<p style="font-size:2.25cqw">Create, review and present.</p></article>'
    )


@dataclass
class RunningApp:
    """Paths and transports for one independent server process."""

    url: str
    store: Store
    project: Path

    def client(self) -> Client:
        """Negotiate a real stateless MCP connection."""
        client = Client(self.url + "/mcp", timeout=10)
        client.initialize()
        return client

    def call(self, tool: str, **arguments: object) -> dict[str, object]:
        """Decode a real MCP tool result without replacing transport or storage."""
        return object_value(self.client().call(Invocation(tool=tool, arguments=arguments)))

    def get(self, path: str) -> dict[str, object]:
        """Read a browser API result over loopback HTTP."""
        with urlopen(self.url + path, timeout=5) as response:
            return object_value(json.load(response))

    def post(self, path: str, body: dict[str, object]) -> dict[str, object]:
        """Submit browser actions through the application's actual HTTP boundary."""
        request = Request(
            self.url + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return object_value(json.load(response))


@contextmanager
def running_app(root: Path, logs: Path) -> Generator[RunningApp]:
    """Keep the child process, collection, database and agent project isolated."""
    collection = root / "presentations"
    database = root / "state/review.sqlite3"
    make_design(collection / TEMPLATE_ID)
    store = Store(collection, database)
    deck = store.create(DECK_ID, "CI workflow", "test-brand")
    cover = deck.slides[0]
    _ = store.revise(
        DECK_ID,
        cover.id,
        digest(cover.model_dump_json()),
        "ci-fixture",
        SlidePatch(
            html=markup("Review together"),
            notes=PRIVATE_NOTE,
            agent=AgentContext(summary="Disposable integration fixture."),
        ),
    )
    _ = store.add_slide(
        DECK_ID, Slide(id="next", title="Next steps", html=markup("Present with confidence"))
    )
    project = root / "agent-project"
    project.mkdir()
    _ = subprocess.run(["git", "init", "--quiet", str(project)], check=True)
    environment = {
        **os.environ,
        "ARTIFACT_STUDIO_COLLECTION": str(collection),
        "ARTIFACT_STUDIO_DATABASE": str(database),
        "ARTIFACT_STUDIO_UPDATE_CHECKS": "0",
    }
    logs.parent.mkdir(parents=True, exist_ok=True)
    with logs.open("w") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "artifact_studio.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                "0",
            ],
            cwd=PROJECT,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            for _attempt in range(100):
                if process.poll() is not None:
                    raise RuntimeError(f"App exited during startup: {logs.read_text()}")
                url = bound_server_url(logs)
                if url is None:
                    time.sleep(0.1)
                    continue
                try:
                    with urlopen(url + "/api/health", timeout=0.5) as response:
                        if object_value(json.load(response)).get("status") == "ready":
                            break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError(f"App startup timed out: {logs.read_text()}")
            yield RunningApp(url, store, project)
        finally:
            process.terminate()
            try:
                _ = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                _ = process.wait(timeout=5)
