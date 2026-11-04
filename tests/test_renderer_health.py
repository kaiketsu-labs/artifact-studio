"""Renderer launch failures are actionable, bounded and independent of slide failures."""

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace
from typing import Never, cast

import pytest
from playwright.sync_api import Browser, Playwright
from playwright.sync_api import Error as BrowserError

from artifact_studio import renderer_health, visuals
from artifact_studio.renderer_health import RendererHealth, sandboxed_browser


def test_concurrent_launch_failure_is_shared_and_sanitized() -> None:
    """One broken infrastructure launch does not spawn a browser for each queued slide."""
    health = RendererHealth()
    attempts = 0

    def fail() -> Never:
        nonlocal attempts
        attempts += 1
        raise BrowserError("Chromium sandboxing failed! Try chromium_sandbox=False. /private/path")

    def request(_index: int) -> str:
        with pytest.raises(ValueError) as error:
            health.attempt(fail)
        return str(error.value)

    with ThreadPoolExecutor(max_workers=6) as pool:
        messages = list(pool.map(request, range(6)))
    assert attempts == 1
    assert all("sandbox support" in text for text in messages)
    assert all("False" not in text and "/private" not in text for text in messages)
    status = health.status()
    assert status["status"] == "unavailable" and status["reason"] == "sandbox_unavailable"
    assert status["retry_after_seconds"] == 30 and status["checked_at"]


def test_status_does_not_block_while_launching() -> None:
    """Reading last observations remains cheap while a bounded browser launch is active."""
    health = RendererHealth()
    started, release = Event(), Event()

    def launch() -> bool:
        started.set()
        assert release.wait(2)
        return True

    with ThreadPoolExecutor() as pool:
        pending = pool.submit(health.attempt, launch)
        assert started.wait(1)
        status = health.status()
        assert status["status"] == "unknown" and status["launching"] is True
        release.set()
        assert pending.result(timeout=2)
    assert health.status()["status"] == "ready"


def test_failure_cooldown_expires_and_explicit_retry_recovers() -> None:
    """Recover after installation without restarting the application or weakening isolation."""
    health = RendererHealth()

    def missing() -> Never:
        raise BrowserError("Executable doesn't exist")

    with pytest.raises(ValueError, match="playwright install chromium"):
        health.attempt(missing)
    with pytest.raises(ValueError, match="cached"):
        health.attempt(lambda: "must not run")
    assert health.attempt(lambda: "installed", force=True) == "installed"
    assert health.status()["status"] == "ready"
    assert health.status()["retry_after_seconds"] == 0
    with pytest.raises(ValueError):
        health.attempt(missing)
    health.retry_at = 0
    assert health.attempt(lambda: "automatic retry") == "automatic retry"


def test_probe_checks_startup_only_and_page_failure_keeps_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed slide does not disable other captures; probing never loads slide content."""
    health = RendererHealth()
    monkeypatch.setattr(renderer_health, "HEALTH", health)
    broken = True
    calls: list[str] = []

    def launch(*, headless: bool, chromium_sandbox: bool, timeout: int) -> Browser:
        calls.append("launch")
        assert headless and chromium_sandbox and timeout == 15_000
        if broken:
            raise BrowserError("No usable sandbox! disable sandbox")

        def close() -> None:
            calls.append("close")

        return cast(Browser, SimpleNamespace(close=close))

    @contextmanager
    def factory() -> Generator[Playwright]:
        yield cast(Playwright, SimpleNamespace(chromium=SimpleNamespace(launch=launch)))

    monkeypatch.setattr(visuals, "sync_playwright", factory)
    assert visuals.capture_queue_status()["renderer"] == health.status()
    assert calls == []
    unavailable = visuals.capture_queue_status(probe_renderer=True)
    assert unavailable["renderer"] == health.status()
    assert unavailable["running"] is False and unavailable["queued"] == 0
    assert health.status()["status"] == "unavailable"
    with pytest.raises(ValueError, match="cached"), sandboxed_browser(factory):
        pytest.fail("The cached failure must stop another launch.")
    assert calls == ["launch"]
    broken = False
    ready = visuals.capture_queue_status(probe_renderer=True)
    assert "probe_error" not in ready and health.status()["status"] == "ready"
    assert calls == ["launch", "launch", "close"]
    with pytest.raises(BrowserError, match="slide script"), sandboxed_browser(factory):
        raise BrowserError("The slide script timed out")
    assert health.status()["status"] == "ready"
    assert calls[-1] == "close"
