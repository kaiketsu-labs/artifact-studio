"""Isolated server/browser fixtures with retained CI diagnostics."""

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from tests.integration.support import RunningApp, running_app


@pytest.fixture
def artifacts(tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    """Keep screenshots, traces and server logs separate for each test."""
    root = Path(os.environ.get("SLIDE_STUDIO_ARTIFACTS", str(tmp_path / "artifacts")))
    path = root / re.sub(r"[^A-Za-z0-9_-]", "_", request.node.name)
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def live_app(tmp_path: Path, artifacts: Path) -> Iterator[RunningApp]:
    """Run the checked-out application with no personal or corporate data."""
    with running_app(tmp_path, artifacts / "server.log") as app:
        yield app


@pytest.fixture
def browser() -> Iterator[Browser]:
    """Close Playwright's event loop per test so renderer tests can start their own."""
    with sync_playwright() as renderer:
        browser = renderer.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def page(browser: Browser, artifacts: Path, live_app: RunningApp) -> Iterator[Page]:
    """Keep the server alive through diagnostics; reject browser/network errors."""
    context = browser.new_context(viewport={"width": 1600, "height": 1000})
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = context.new_page()
    page.set_default_timeout(10_000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "response",
        lambda response: (
            errors.append(f"HTTP {response.status}: {response.url}")
            if response.status >= 400
            else None
        ),
    )
    page.on(
        "requestfailed",
        lambda request: (
            errors.append(f"Request failed: {request.failure}: {request.url}")
            if request.failure != "net::ERR_ABORTED"
            else None
        ),
    )
    try:
        yield page
    finally:
        try:
            _ = page.screenshot(path=str(artifacts / "browser.png"), full_page=True)
            context.tracing.stop(path=str(artifacts / "trace.zip"))
            _ = (artifacts / "browser-errors.txt").write_text("\n".join(errors))
        finally:
            context.close()
    assert not errors, "\n".join(errors)
