"""Observed sandboxed renderer readiness, with bounded launch-failure backoff."""

import math
import threading
import time
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from datetime import UTC, datetime
from typing import Literal

from playwright.sync_api import Browser, Playwright, sync_playwright
from playwright.sync_api import Error as BrowserError


def launch_failure(error: BrowserError) -> tuple[str, str]:
    """Translate infrastructure failures without copying unsafe browser launch advice."""
    detail = str(error).lower()
    if "executable doesn't exist" in detail:
        return "browser_missing", (
            "Install the local renderer: uv run playwright install chromium --only-shell"
        )
    if "sandbox" in detail:
        return "sandbox_unavailable", (
            "The host cannot launch sandboxed Chromium. Use a host with working Chromium "
            "sandbox support, then retry get_capture_status(probe_renderer=True)."
        )
    return "launch_failed", (
        "The local Chromium renderer could not start. Check the browser installation and "
        "host dependencies, then retry get_capture_status(probe_renderer=True)."
    )


class RendererHealth:
    """Serialize launch observations so concurrent callers share a recent failure."""

    def __init__(self, cooldown: float = 30) -> None:
        self.lock = threading.Lock()
        self.launch_lock = threading.Lock()
        self.launching = False
        self.cooldown = cooldown
        self.state: Literal["unknown", "ready", "unavailable"] = "unknown"
        self.reason = ""
        self.message = "No renderer launch has been observed in this process."
        self.checked_at: str | None = None
        self.retry_at = 0.0

    def status(self) -> dict[str, object]:
        """Read the last observation, without launching a browser or assuming readiness."""
        with self.lock:
            return {
                "status": self.state,
                "reason": self.reason,
                "message": self.message,
                "checked_at": self.checked_at,
                "launching": self.launching,
                "retry_after_seconds": max(0, math.ceil(self.retry_at - time.monotonic())),
            }

    def attempt[T](self, launch: Callable[[], T], *, force: bool = False) -> T:
        """Cache only startup failures; page rendering occurs after this guard is released."""
        with self.launch_lock:
            with self.lock:
                if not force and self.state == "unavailable" and time.monotonic() < self.retry_at:
                    raise ValueError(
                        self.message + " Recent launch failure is cached; check get_capture_status."
                    )
                self.launching = True
            try:
                result = launch()
            except BrowserError as error:
                with self.lock:
                    self.state = "unavailable"
                    self.reason, self.message = launch_failure(error)
                    self.checked_at = datetime.now(UTC).isoformat()
                    self.retry_at = time.monotonic() + self.cooldown
                raise ValueError(self.message) from error
            finally:
                with self.lock:
                    self.launching = False
            with self.lock:
                self.state, self.reason = "ready", ""
                self.message = (
                    "Sandboxed Chromium launched successfully; slide content was not checked."
                )
                self.checked_at = datetime.now(UTC).isoformat()
                self.retry_at = 0
            return result


HEALTH = RendererHealth()


def renderer_status() -> dict[str, object]:
    """Read process-local observations without launching Chromium."""
    return HEALTH.status()


@contextmanager
def sandboxed_browser(
    factory: Callable[[], AbstractContextManager[Playwright]] = sync_playwright,
    *,
    force: bool = False,
) -> Generator[Browser]:
    """Share startup diagnostics while keeping every browser isolated and sandboxed."""
    with ExitStack() as stack:

        def launch() -> Browser:
            renderer = stack.enter_context(factory())
            return renderer.chromium.launch(headless=True, chromium_sandbox=True, timeout=15_000)

        browser = HEALTH.attempt(launch, force=force)
        stack.callback(browser.close)
        yield browser
