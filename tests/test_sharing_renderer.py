"""PDF exports share the bounded sandboxed renderer lifecycle with visual captures."""

import subprocess
import threading
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from typing import cast

import pytest
from playwright.sync_api import Browser, Playwright
from playwright.sync_api import Error as BrowserError

from artifact_studio import renderer_health, sharing, visuals
from artifact_studio.renderer_health import RendererHealth, RendererUnavailable

render_pdf = sharing._pdf  # pyright: ignore[reportPrivateUsage]
render_hosted_pdf = sharing._scoped_pdf  # pyright: ignore[reportPrivateUsage]


def test_hosted_pdf_worker_deadline_releases_the_render_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop the worker and let later jobs enter after a print deadline."""
    queue = visuals.RenderQueue()
    monkeypatch.setattr(visuals, "_RENDER_QUEUE", queue)
    monkeypatch.setattr(renderer_health, "HEALTH", RendererHealth())
    stopped: list[int] = []
    timeouts: list[float | None] = []

    def wait(timeout: float | None = None) -> int:
        timeouts.append(timeout)
        if timeout is not None:
            raise subprocess.TimeoutExpired("fictional print", timeout)
        return -9

    @contextmanager
    def worker(*_args: object, **_options: object) -> Generator[SimpleNamespace]:
        yield SimpleNamespace(wait=wait, pid=1234, returncode=None)

    def kill(pid: int, _signal: int) -> None:
        stopped.append(pid)

    monkeypatch.setattr(sharing.subprocess, "Popen", worker)
    monkeypatch.setattr(sharing.os, "killpg", kill)
    with pytest.raises(ValueError, match="timed out"):
        _ = render_hosted_pdf("<h1>Fictional print job</h1>", 100_000)
    assert timeouts == [45, None]
    assert stopped == [1234]
    assert queue.status()["running"] is False


def test_pdf_waits_for_other_captures_and_serializes_exports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PDF request queues before browser startup and never overlaps another renderer."""
    queue = visuals.RenderQueue(timeout=5)
    monkeypatch.setattr(visuals, "_RENDER_QUEUE", queue)
    monkeypatch.setattr(renderer_health, "HEALTH", RendererHealth())
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def pdf(**_options: object) -> bytes:
        started.set()
        assert release.wait(5)
        return b"%PDF-fictional"

    def accept(*_args: object, **_kwargs: object) -> None:
        """Accept page setup without starting Chromium in this lifecycle test."""

    page = SimpleNamespace(
        route=accept,
        route_web_socket=accept,
        set_content=accept,
        emulate_media=accept,
        wait_for_function=accept,
        pdf=pdf,
    )

    def new_page(**options: object) -> SimpleNamespace:
        """Verify the print context isolates requests before rendering."""
        assert options == {"offline": True, "service_workers": "block", "accept_downloads": False}
        return page

    def launch(*, headless: bool, chromium_sandbox: bool, timeout: int) -> Browser:
        assert headless and chromium_sandbox and timeout == 15_000
        calls.append("launch")
        return cast(
            Browser,
            SimpleNamespace(new_page=new_page, close=lambda: calls.append("close")),
        )

    @contextmanager
    def factory() -> Generator[Playwright]:
        yield cast(Playwright, SimpleNamespace(chromium=SimpleNamespace(launch=launch)))

    monkeypatch.setattr(sharing, "sync_playwright", factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            with visuals.render_slot():
                first = pool.submit(render_pdf, "<p>First fictional export</p>")
                second = pool.submit(render_pdf, "<p>Second fictional export</p>")
                with queue.condition:
                    assert queue.condition.wait_for(lambda: len(queue.waiters) == 2, timeout=5)
                assert calls == []
            assert started.wait(5)
            assert calls == ["launch"]
        finally:
            release.set()
        assert first.result(timeout=5) == b"%PDF-fictional"
        assert second.result(timeout=5) == b"%PDF-fictional"
    assert calls == ["launch", "close", "launch", "close"]
    assert queue.status()["running"] is False and queue.status()["queued"] == 0


def test_pdf_launch_failures_share_backoff_and_release_the_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing sandbox is attempted once; repeated exports fail before Playwright starts."""
    queue = visuals.RenderQueue()
    health = RendererHealth()
    monkeypatch.setattr(visuals, "_RENDER_QUEUE", queue)
    monkeypatch.setattr(renderer_health, "HEALTH", health)
    calls: list[str] = []

    def launch(**_options: object) -> Browser:
        calls.append("launch")
        raise BrowserError("No usable sandbox")

    @contextmanager
    def factory() -> Generator[Playwright]:
        calls.append("start")
        try:
            yield cast(Playwright, SimpleNamespace(chromium=SimpleNamespace(launch=launch)))
        finally:
            calls.append("stop")

    monkeypatch.setattr(sharing, "sync_playwright", factory)
    for _ in range(2):
        with pytest.raises(RendererUnavailable, match="sandbox"):
            _ = render_pdf("<p>Fictional export</p>")
    assert calls == ["start", "launch", "stop"]
    assert queue.status()["running"] is False and queue.status()["queued"] == 0
    assert health.status()["status"] == "unavailable"


def test_pdf_page_failure_closes_browser_without_disabling_other_captures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Content errors release the shared slot and leave the successful host launch ready."""
    health = RendererHealth()
    queue = visuals.RenderQueue()
    monkeypatch.setattr(renderer_health, "HEALTH", health)
    monkeypatch.setattr(visuals, "_RENDER_QUEUE", queue)
    closed: list[bool] = []

    def page(**_options: object) -> object:
        raise BrowserError("Fictional page failure")

    def launch(**_options: object) -> Browser:
        return cast(Browser, SimpleNamespace(new_page=page, close=lambda: closed.append(True)))

    @contextmanager
    def factory() -> Generator[Playwright]:
        yield cast(Playwright, SimpleNamespace(chromium=SimpleNamespace(launch=launch)))

    monkeypatch.setattr(sharing, "sync_playwright", factory)
    with pytest.raises(ValueError, match="PDF export"):
        _ = render_pdf("<p>Fictional export</p>")
    assert closed == [True]
    assert health.status()["status"] == "ready"
    with visuals.render_slot():
        assert queue.status()["running"] is True
