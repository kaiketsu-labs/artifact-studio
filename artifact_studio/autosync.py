"""Debounced, opt-in workspace snapshots while the local server is running."""

import asyncio
import fcntl
import json
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Literal

from .libraries import Libraries, file_hash
from .models import StrictModel
from .storage import Store, digest

POLL_SECONDS = 3
QUIET_SECONDS = 8


class SyncStatus(StrictModel):
    """Persist observations and confirmed delivery separately from repository permissions."""

    library_id: str
    phase: Literal["disabled", "pending", "syncing", "synced", "error"] = "pending"
    observed: str = ""
    synced: str = ""
    changed_at: float = 0
    last_synced_at: str | None = None
    commit: str = ""
    error: str = ""


class AutoSync:
    """Resume pending changes without requiring a browser tab or active agent."""

    def __init__(self, store: Store) -> None:
        """Keep durable checkpoints in local review state, outside presentation files."""
        self.store = store
        self.libraries = Libraries(store)
        with store.connect() as db:
            _ = db.execute(
                "CREATE TABLE IF NOT EXISTS library_auto_sync "
                "(id TEXT PRIMARY KEY, document TEXT NOT NULL)"
            )

    def state(self, identifier: str) -> SyncStatus:
        """Read a checkpoint without creating an implicit sync permission."""
        with self.store.connect() as db:
            row = db.execute(
                "SELECT document FROM library_auto_sync WHERE id=?", (identifier,)
            ).fetchone()
        return (
            SyncStatus.model_validate_json(str(row[0]))
            if row
            else SyncStatus(library_id=identifier)
        )

    def save(self, state: SyncStatus) -> None:
        """Persist a checkpoint only after its phase or delivery outcome is known."""
        with self.store.connect() as db:
            _ = db.execute(
                "INSERT OR REPLACE INTO library_auto_sync VALUES (?, ?)",
                (state.library_id, state.model_dump_json()),
            )

    def statuses(self) -> list[SyncStatus]:
        """Report disabled references separately from pending and completed uploads."""
        result: list[SyncStatus] = []
        for library in self.libraries.list():
            if library.kind != "github":
                continue
            state = self.state(library.id)
            if not library.auto_sync_enabled:
                state.phase = "disabled"
            result.append(state)
        return result

    def fingerprint(self) -> str:
        """Observe saved-file replacements while excluding private state and reference caches."""
        files = self.libraries.snapshot_files()
        return digest(
            json.dumps(
                sorted((item.path, file_hash(self.store.root / item.path)) for item in files)
            )
        )

    def queue(self, identifier: str) -> SyncStatus:
        """Retry a user-selected destination after checking its current opt-in flag."""
        if not self.libraries.get(identifier).auto_sync_enabled:
            raise ValueError("Enable automatic sync for this repository first.")
        state = self.state(identifier)
        state.phase = "pending"
        state.observed = ""
        state.synced = ""
        state.error = ""
        self.save(state)
        return state

    def tick(self, now: float | None = None) -> None:
        """Publish stable saved changes once, with one worker across server processes."""
        with (self.store.database.parent / ".auto-sync.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            try:
                self._tick(time.time() if now is None else now)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _tick(self, now: float) -> None:
        """Wait for a quiet interval and preserve failed deliveries for explicit attention."""
        enabled = [item for item in self.libraries.list() if item.auto_sync_enabled]
        if not enabled:
            return
        for library in enabled:
            state = self.state(library.id)
            if state.phase == "syncing":
                # The process-wide lock is ours, so this checkpoint predates a crash.
                # Reconcile delivery rather than trusting an unacknowledged push.
                state.phase = "pending"
                state.observed = state.synced = ""
                self.save(state)
        try:
            fingerprint = self.fingerprint()
        except FileNotFoundError:
            # A saved file may be atomically replaced or removed during enumeration.
            # Observe the settled tree on the next tick without pausing the destination.
            for library in enabled:
                state = self.state(library.id)
                if state.phase == "error":
                    continue
                state.phase = "pending"
                state.observed = ""
                state.changed_at = now
                self.save(state)
            return
        except (OSError, ValueError) as error:
            for library in enabled:
                state = self.state(library.id)
                state.phase = "error"
                state.error = f"Cannot read saved workspace files: {error}. Resolve this and retry."
                self.save(state)
            return
        for library in enabled:
            state = self.state(library.id)
            if state.phase == "error":
                continue
            if state.synced == fingerprint:
                if state.phase != "synced":
                    state.phase = "synced"
                    state.observed = fingerprint
                    self.save(state)
                continue
            if state.observed != fingerprint:
                state.observed = fingerprint
                state.changed_at = now
                state.phase = "pending"
                state.error = ""
                self.save(state)
            if now - state.changed_at < QUIET_SECONDS:
                continue
            state.phase = "syncing"
            self.save(state)
            try:
                result = self.libraries.sync(library.id, automatic=True)
            except FileNotFoundError:
                state.phase = "pending"
                state.observed = ""
                state.changed_at = now
                state.error = ""
            except (ValueError, OSError) as error:
                state.phase = "pending" if "operation is running" in str(error) else "error"
                state.error = str(error)
            else:
                state.synced = fingerprint
                state.last_synced_at = datetime.now(UTC).isoformat()
                state.commit = result.commit
                state.error = ""
                state.phase = "synced"
            self.save(state)

    async def run(self, stop: asyncio.Event) -> None:
        """Finish any in-flight operation before stopping the local background worker."""
        while not stop.is_set():
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                logging.getLogger(__name__).exception("Could not inspect automatic sync inputs")
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
