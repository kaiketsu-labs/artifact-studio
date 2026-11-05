"""Version disclosure and bounded update discovery without workspace content."""

from pathlib import Path
from threading import Event, Thread

import pytest
from fastapi.testclient import TestClient

from artifact_studio import app as service
from artifact_studio import app_info as module
from artifact_studio.app_info import AppInfo
from artifact_studio.updater import UpdateError


def test_app_info_endpoint_is_local_metadata_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    info = AppInfo(tmp_path, "2.3.4", enabled=False)
    monkeypatch.setattr(service, "app_info", info)

    def unexpected(*_: object) -> None:
        pytest.fail("Version information must not read workspace content")

    monkeypatch.setattr(service.store, "list_decks", unexpected)
    monkeypatch.setattr(service.store, "authoring_instructions", unexpected)
    response = TestClient(service.app).get("/api/app-info")
    assert response.status_code == 200
    assert response.json() == {
        "version": "2.3.4",
        "latest_version": None,
        "latest_tag": None,
        "update_available": False,
        "update_check": "disabled",
    }
    assert response.headers["cache-control"] == "no-store"


def test_slow_release_discovery_is_shared_and_nonblocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, finish = Event(), Event()
    calls: list[Path] = []
    now = [100.0]
    threads: list[Thread] = []

    class RecordedThread(Thread):
        def start(self) -> None:
            threads.append(self)
            super().start()

    monkeypatch.setattr(module, "Thread", RecordedThread)

    def discover(project: Path) -> str:
        calls.append(project)
        entered.set()
        assert finish.wait(5)
        return "v0.10.0"

    info = AppInfo(tmp_path, "0.9.0", discover=discover, clock=lambda: now[0])
    try:
        assert info.status()["update_check"] == "checking"
        assert entered.wait(5)
        for _ in range(10):
            assert info.status()["version"] == "0.9.0"
        assert len(calls) == 1
    finally:
        finish.set()
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()
    assert info.status()["latest_tag"] == "v0.10.0"
    assert info.status()["update_available"] is True
    assert len(calls) == 1
    now[0] += 3599
    info.status()
    assert len(calls) == 1
    now[0] += 1
    assert info.status()["update_check"] == "checking"
    threads[-1].join(timeout=5)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "installed,tag,available",
    [("1.0.0", "v0.9.0", False), ("0.2.0", "v0.2.0", False), ("0.2.0", "v0.3.0", True)],
)
def test_update_indicator_compares_installed_and_stable_versions(
    tmp_path: Path, installed: str, tag: str, available: bool
) -> None:
    info = AppInfo(tmp_path, installed, discover=lambda _: tag)
    info.next_check = float("inf")
    info.refresh()
    assert info.status()["update_available"] is available


def test_release_error_keeps_installed_version_and_no_false_badge(tmp_path: Path) -> None:
    def unavailable(_: Path) -> str:
        raise UpdateError("fictional offline transport")

    info = AppInfo(tmp_path, "0.2.0", discover=unavailable)
    info.next_check = float("inf")
    info.refresh()
    status = info.status()
    assert status["version"] == "0.2.0"
    assert status["update_check"] == "unavailable"
    assert status["update_available"] is False
