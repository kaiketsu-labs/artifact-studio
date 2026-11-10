"""Disposable hosted server and two-origin browser transport for integration journeys.

The fixture provider is deliberately limited to tests; it is not an authentication
provider suitable for deployment. Browser routing simulates the TLS reverse proxy.
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

from artifact_studio.storage import Store
from tests.integration.support import PROJECT, bound_server_url

WORKSPACE = "https://studio.example.test"
PREVIEW = "https://preview.example.test"


@dataclass
class HostedApp:
    url: str
    store: Store


@contextmanager
def running_hosted_app(root: Path, logs: Path) -> Generator[HostedApp]:
    """Start an isolated server with a fixture-only authentication provider."""
    root.mkdir(parents=True, exist_ok=True)
    collection, database = root / "collection", root / "state/review.sqlite3"
    store = Store(collection, database)
    config = root / "hosted.json"
    config.write_text(
        json.dumps(
            {
                "public_url": WORKSPACE,
                "preview_url": PREVIEW,
                "auth_provider": "cloudflare",
                "auth_options": {},
                "member_emails": ["reviewer@example.test"],
                "agent_subjects": ["fixture-agent", "fixture-agent-two"],
            }
        )
    )
    config.chmod(0o640)
    runner = root / "server.py"
    runner.write_text("""
import sys
import uvicorn
from artifact_studio import authentication
class FixtureProvider:
    async def authenticate(self, headers):
        if headers.get("x-fixture-auth") == "agent-two":
            return authentication.Principal("fixture-agent-two", service=True)
        if headers.get("x-fixture-auth") == "agent":
            return authentication.Principal("fixture-agent", service=True)
        if headers.get("x-fixture-auth") != "member":
            raise authentication.AuthenticationError()
        return authentication.Principal("fixture", "reviewer@example.test")
authentication.load_auth_provider = lambda config: FixtureProvider()
from artifact_studio.app import app
uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), proxy_headers=False, access_log=False)
""")
    env = {
        **os.environ,
        "ARTIFACT_STUDIO_MODE": "hosted",
        "ARTIFACT_STUDIO_HOSTED_CONFIG": str(config),
        "ARTIFACT_STUDIO_COLLECTION": str(collection),
        "ARTIFACT_STUDIO_DATABASE": str(database),
        "ARTIFACT_STUDIO_UPDATE_CHECKS": "0",
        "PYTHONPATH": str(PROJECT),
    }
    logs.parent.mkdir(parents=True, exist_ok=True)
    with logs.open("w") as output:
        process = subprocess.Popen(
            [sys.executable, str(runner), "0"],
            cwd=PROJECT,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError(f"Hosted server exited: {logs.read_text()}")
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
                raise RuntimeError(f"Hosted server startup timed out: {logs.read_text()}")
            yield HostedApp(url, store)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@contextmanager
def routed_hosted_context(
    browser: Browser, app: HostedApp, *, authenticated: bool = True
) -> Generator[BrowserContext]:
    """Route fictional HTTPS hosts to the server, injecting auth only for workspace requests."""
    context = browser.new_context(viewport={"width": 1600, "height": 1000})

    def forward(route: Route) -> None:
        request = route.request
        parts = urlsplit(request.url)
        if parts.hostname not in {"studio.example.test", "preview.example.test"}:
            route.abort()
            return
        headers = {**request.headers, "host": parts.netloc}
        # Playwright intercepts before Chromium adds Fetch Metadata; preserve
        # the browser's navigation destination when simulating the TLS proxy.
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
                # A popup's first navigation arrives before its frame exists.
                destination = "document"
        headers.setdefault("sec-fetch-dest", destination)
        if authenticated and parts.hostname == "studio.example.test":
            headers["x-fixture-auth"] = "member"
        target = app.url + parts.path + ("?" + parts.query if parts.query else "")
        try:
            response = route.fetch(url=target, headers=headers)
            route.fulfill(response=response)
        except Error as error:
            # Closing an audience window cancels its pending proxy response.
            # Keep routing failures observable for every still-attached frame.
            if "Route is already handled" in str(error) and (
                request.frame.is_detached() or request.frame.page.is_closed()
            ):
                return
            raise

    context.route("**/*", forward)
    try:
        yield context
    finally:
        # Stop EventSource retries and polling before removing the fictional-host
        # proxy. Otherwise an open page can reconnect directly while a buffered
        # stream is draining, leaving its old route already handled.
        try:
            for page in context.pages:
                page.close()
            context.unroute_all(behavior="wait")
        finally:
            context.close()
