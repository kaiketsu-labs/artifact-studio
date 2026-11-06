"""Installed version and bounded, read-only release discovery for local clients."""

import time
from collections.abc import Callable
from pathlib import Path
from threading import Lock, Thread

from .models import StrictModel
from .storage import Store
from .updater import ReleaseStatus, UpdateError, discover_update, installed_release_tag


class UpdatePreferences(StrictModel):
    """Opt into nightly discovery for this installation's workspace."""

    include_nightly: bool = False


def update_preferences(store: Store, value: UpdatePreferences | None = None) -> UpdatePreferences:
    """Persist one preference without reading artifact content or exposing it to agents."""
    with store.connect() as database:
        if value is not None:
            database.execute(
                "INSERT OR REPLACE INTO workspace_settings VALUES ('include_nightly_updates', ?)",
                ("true" if value.include_nightly else "false",),
            )
        row = database.execute(
            "SELECT value FROM workspace_settings WHERE key='include_nightly_updates'"
        ).fetchone()
    return UpdatePreferences(include_nightly=bool(row and row[0] == "true"))


class AppInfo:
    """Serve cached metadata without making UI requests wait on Git or a network."""

    def __init__(
        self,
        project: Path,
        version: str,
        *,
        enabled: bool = True,
        discover: Callable[[Path, bool], ReleaseStatus] = discover_update,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.project, self.version = project, version
        self.enabled, self.discover, self.clock = enabled, discover, clock
        self.lock = Lock()
        self.settings_lock = Lock()
        self.next_check = 0.0
        self.running = False
        self.latest = ReleaseStatus(installed_tag=installed_release_tag(project))
        self.include_nightly = False
        self.generation = 0
        self.state = "checking" if enabled else "disabled"

    def settings(
        self, store: Store, value: UpdatePreferences | None = None
    ) -> dict[str, str | bool | None]:
        """Serialize preference reads/writes with cache changes across HTTP requests."""
        with self.settings_lock:
            return self.status(include_nightly=update_preferences(store, value).include_nightly)

    def status(self, *, include_nightly: bool | None = None) -> dict[str, str | bool | None]:
        """Schedule at most one hourly check while returning immediately."""
        with self.lock:
            if include_nightly is not None and include_nightly != self.include_nightly:
                self.include_nightly = include_nightly
                self.generation += 1
                self.next_check = 0
                self.latest = ReleaseStatus(installed_tag=self.latest.installed_tag)
                self.state = "checking" if self.enabled else "disabled"
            start = self.enabled and not self.running and self.clock() >= self.next_check
            if start:
                self.running = True
                self.state = "checking"
                self.next_check = self.clock() + 3600
            tag = self.latest.latest_tag
            generation, channel = self.generation, self.include_nightly
            result: dict[str, str | bool | None] = {
                "version": self.version,
                "installed_tag": self.latest.installed_tag,
                "latest_tag": tag,
                "latest_version": tag.removeprefix("v") if tag else None,
                "update_available": self.latest.update_available,
                "update_check": self.state,
                "include_nightly": self.include_nightly,
            }
        if start:
            Thread(
                target=self.refresh,
                args=(generation, channel),
                daemon=True,
                name="app-release-check",
            ).start()
        return result

    def refresh(self, generation: int | None = None, include_nightly: bool | None = None) -> None:
        """Keep release failures local; they never hide the installed version."""
        with self.lock:
            if generation is None:
                generation = self.generation
            if include_nightly is None:
                include_nightly = self.include_nightly
        try:
            latest = self.discover(self.project, include_nightly)
            state = "ok"
        except OSError, UpdateError:
            latest, state = ReleaseStatus(installed_tag=self.latest.installed_tag), "unavailable"
        with self.lock:
            if generation == self.generation:
                self.latest, self.state = latest, state
            self.running = False
