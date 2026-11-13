"""Disposable real multiplayer HTTP server with fictional identities and content.

Use ``running_multiplayer_app(root, logs)`` and route the fictional HTTPS hosts
with ``routed_multiplayer_context(browser, app, identity='alice')``. The fixture
provider and proxy are test-only and must never be used for deployment.
"""

import json
import os
import subprocess
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from playwright.sync_api import Browser, BrowserContext, Error, Route

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.models import Slide
from artifact_studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design
from tests.integration.support import PROJECT, bound_server_url, markup

WORKSPACE = "https://studio.example.test"
PREVIEW = "https://preview.example.test"
DECK_ID = "2026-11-01-fictional-multiplayer"


@dataclass
class MultiplayerApp:
    url: str
    store: Store
    deck_id: str
    project_id: str
    artifact_id: str
    sibling_id: str


@contextmanager
def running_multiplayer_app(
    root: Path,
    logs: Path,
    *,
    unified: bool = False,
    personal_workspace_emails: tuple[str, ...] = (),
) -> Generator[MultiplayerApp]:
    root.mkdir(parents=True, exist_ok=True)
    collection, database = root / "collection", root / "state/review.sqlite3"
    make_design(collection / TEMPLATE_ID)
    store = Store(collection, database)
    store.create(DECK_ID, "Fictional team deck", "test-brand")
    store.add_slide(DECK_ID, Slide(id="next", title="Next", html=markup("Next")))
    artifacts = ArtifactStore(store)
    project = artifacts.create_project("Fictional project")
    source = root / "source.md"
    source.write_text("Fictional shared artifact", encoding="utf-8")
    item = artifacts.register_artifact(project.id, "Shared candidate", source, "source.md")
    sibling = artifacts.register_artifact(project.id, "Private sibling", source, "source.md")
    runner = root / "server.py"
    runner.write_text(
        """\
import os
import sys
from pathlib import Path
import uvicorn
from artifact_studio import authentication
from artifact_studio.authentication import AuthenticationError, Principal
from artifact_studio.deployment import DeploymentConfig
from artifact_studio.multiplayer import create_app
from artifact_studio.multiplayer_access import Sharing
from artifact_studio.storage import Store
class FixtureProvider:
    async def authenticate(self, headers):
        name = headers.get("x-fixture-auth", "")
        if name not in {"owner", "alice", "bob", "carol", "dana"}:
            raise AuthenticationError()
        return Principal(name, name + "@example.test")
root = Path(sys.argv[1])
config = DeploymentConfig("hosted", "https://studio.example.test",
    "https://preview.example.test", "fixture", {}, ("owner@example.test",), ())
sharing = Sharing(root / "sharing", Store(root / "collection",
    root / "state/review.sqlite3"), "owner@example.test")
if os.environ.get("ARTIFACT_STUDIO_FIXTURE_UNIFIED") == "1":
    authentication.load_auth_provider = lambda _: FixtureProvider()
    from artifact_studio.unified import create_app as create_unified
    app = create_unified()
else:
    app = create_app(config, FixtureProvider(), sharing)
uvicorn.run(app, host="127.0.0.1",
    port=int(sys.argv[2]), proxy_headers=False, access_log=False)
""",
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": str(PROJECT)}
    if unified:
        config_path = root / "hosted.json"
        config_path.write_text(
            json.dumps(
                {
                    "public_url": WORKSPACE,
                    "preview_url": PREVIEW,
                    "auth_provider": "fixture",
                    "auth_options": {},
                    "member_emails": ["owner@example.test"],
                    "agent_subjects": [],
                    "personal_workspace_emails": personal_workspace_emails,
                }
            )
        )
        env.update(
            ARTIFACT_STUDIO_FIXTURE_UNIFIED="1",
            ARTIFACT_STUDIO_MODE="hosted",
            ARTIFACT_STUDIO_HOSTED_CONFIG=str(config_path),
            ARTIFACT_STUDIO_MULTIPLAYER_OWNER="owner@example.test",
            ARTIFACT_STUDIO_MULTIPLAYER_STATE=str(root / "sharing"),
            ARTIFACT_STUDIO_COLLECTION=str(collection),
            ARTIFACT_STUDIO_DATABASE=str(database),
        )
    logs.parent.mkdir(parents=True, exist_ok=True)
    with logs.open("w") as output:
        process = subprocess.Popen(
            [sys.executable, str(runner), str(root), "0"],
            cwd=PROJECT,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError(f"Multiplayer server exited: {logs.read_text()}")
                url = bound_server_url(logs)
                if url is None:
                    time.sleep(0.1)
                    continue
                try:
                    with urlopen(
                        Request(url + "/api/health", headers={"Host": "localhost"}), timeout=0.5
                    ) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError(f"Multiplayer startup timed out: {logs.read_text()}")
            yield MultiplayerApp(url, store, DECK_ID, project.id, item.id, sibling.id)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@contextmanager
def routed_multiplayer_context(
    browser: Browser, app: MultiplayerApp, identity: str = "owner"
) -> Generator[BrowserContext]:
    context = browser.new_context(viewport={"width": 1600, "height": 1000})

    def forward(route: Route) -> None:
        request = route.request
        parts = urlsplit(request.url)
        if parts.hostname not in {"studio.example.test", "preview.example.test"}:
            route.abort()
            return
        headers = {**request.headers, "host": parts.netloc}
        destination = {
            "stylesheet": "style",
            "script": "script",
            "image": "image",
            "font": "font",
            "media": "video",
        }.get(request.resource_type, "empty")
        if request.resource_type == "document":
            try:
                destination = "iframe" if request.frame.parent_frame else "document"
            except Error:
                destination = "document"
        headers.setdefault("sec-fetch-dest", destination)
        if parts.hostname == "studio.example.test":
            headers["x-fixture-auth"] = identity
        target = app.url + parts.path + ("?" + parts.query if parts.query else "")
        response = route.fetch(url=target, headers=headers, max_redirects=0)
        route.fulfill(response=response)

    context.route("**/*", forward)
    try:
        yield context
    finally:
        for page in context.pages:
            page.close()
        context.unroute_all(behavior="wait")
        context.close()
