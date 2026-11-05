"""Installed version and bounded, read-only release discovery for local clients."""

import time
from collections.abc import Callable
from pathlib import Path
from threading import Lock, Thread

from .updater import UpdateError, discover_release, stable_version


class AppInfo:
    """Serve cached metadata without making UI requests wait on Git or a network."""

    def __init__(
        self,
        project: Path,
        version: str,
        *,
        enabled: bool = True,
        discover: Callable[[Path], str | None] = discover_release,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.project, self.version = project, version
        self.enabled, self.discover, self.clock = enabled, discover, clock
        self.lock = Lock()
        self.next_check = 0.0
        self.running = False
        self.latest: str | None = None
        self.state = "checking" if enabled else "disabled"

    def status(self) -> dict[str, str | bool | None]:
        """Schedule at most one hourly check while returning immediately."""
        with self.lock:
            start = self.enabled and not self.running and self.clock() >= self.next_check
            if start:
                self.running = True
                self.state = "checking"
                self.next_check = self.clock() + 3600
            installed = stable_version("v" + self.version)
            latest = stable_version(self.latest) if self.latest else None
            result: dict[str, str | bool | None] = {
                "version": self.version,
                "latest_tag": self.latest,
                "latest_version": self.latest[1:] if self.latest else None,
                "update_available": bool(installed and latest and latest > installed),
                "update_check": self.state,
            }
        if start:
            Thread(target=self.refresh, daemon=True, name="app-release-check").start()
        return result

    def refresh(self) -> None:
        """Keep release failures local; they never hide the installed version."""
        try:
            latest = self.discover(self.project)
            state = "ok"
        except OSError, UpdateError:
            latest, state = None, "unavailable"
        with self.lock:
            self.latest, self.state, self.running = latest, state, False
