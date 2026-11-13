"""Version disclosure and bounded update discovery without workspace content."""

from pathlib import Path
from threading import Event, Thread

import pytest
from fastapi.testclient import TestClient

from artifact_studio import app as service
from artifact_studio import app_info as module
from artifact_studio.app_info import AppInfo
from artifact_studio.updater import ReleaseStatus, UpdateError


def test_app_info_endpoint_is_local_metadata_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ARTIFACT_STUDIO_BUILD_REVISION", raising=False)
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
        "build_revision": None,
        "latest_version": None,
        "latest_tag": None,
        "installed_tag": None,
        "update_available": False,
        "update_check": "disabled",
        "include_nightly": False,
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

    def discover(project: Path, _: bool) -> ReleaseStatus:
        calls.append(project)
        entered.set()
        assert finish.wait(5)
        return ReleaseStatus("v0.10.0", update_available=True)

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
def test_update_indicator_uses_verified_release_availability(
    tmp_path: Path, installed: str, tag: str, available: bool
) -> None:
    info = AppInfo(
        tmp_path, installed, discover=lambda _, __: ReleaseStatus(tag, update_available=available)
    )
    info.next_check = float("inf")
    info.refresh()
    assert info.status()["update_available"] is available


def test_release_error_keeps_installed_version_and_no_false_badge(tmp_path: Path) -> None:
    def unavailable(_: Path, __: bool) -> ReleaseStatus:
        raise UpdateError("fictional offline transport")

    info = AppInfo(tmp_path, "0.2.0", discover=unavailable)
    info.next_check = float("inf")
    info.refresh()
    status = info.status()
    assert status["version"] == "0.2.0"
    assert status["update_check"] == "unavailable"
    assert status["update_available"] is False


def test_update_preference_persists_and_default_is_stable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from artifact_studio.storage import Store

    store = Store(tmp_path / "collection", tmp_path / "review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    monkeypatch.setattr(service, "app_info", AppInfo(tmp_path, "0.2.0", enabled=False))
    client = TestClient(service.app)
    assert client.get("/api/app-info").json()["include_nightly"] is False
    assert client.post("/api/app-info/settings", json={"include_nightly": True}).status_code == 200
    monkeypatch.setattr(service, "app_info", AppInfo(tmp_path, "0.2.0", enabled=False))
    assert client.get("/api/app-info").json()["include_nightly"] is True
    assert (
        client.post("/api/app-info/settings", json={"include_nightly": False}).json()[
            "include_nightly"
        ]
        is False
    )


def test_channel_switch_discards_in_flight_nightly_result(tmp_path: Path) -> None:
    entered, finish = Event(), Event()

    def discover(_: Path, nightly: bool) -> ReleaseStatus:
        if nightly:
            entered.set()
            assert finish.wait(5)
            return ReleaseStatus("nightly-20261106.123", update_available=True)
        return ReleaseStatus("v0.2.0")

    info = AppInfo(tmp_path, "0.2.0", discover=discover)
    info.status(include_nightly=True)
    assert entered.wait(5)
    assert info.status(include_nightly=False)["update_available"] is False
    finish.set()
    # Joining the worker gives a deterministic check that its stale result was discarded.
    import threading

    for thread in threading.enumerate():
        if thread.name == "app-release-check":
            thread.join(timeout=5)
    status = info.status()
    assert not status["include_nightly"] and not status["update_available"]
    assert status["latest_tag"] != "nightly-20261106.123"


def test_preferences_and_cache_are_serialized_across_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from artifact_studio.storage import Store

    store = Store(tmp_path / "collection", tmp_path / "review.sqlite3")
    info = AppInfo(tmp_path, "0.2.0", enabled=False)
    info.settings(store, module.UpdatePreferences(include_nightly=True))
    entered, finish, posted = Event(), Event(), Event()
    original = module.update_preferences

    def preferences(
        store: Store, value: module.UpdatePreferences | None = None
    ) -> module.UpdatePreferences:
        result = original(store, value)
        if value is None:
            entered.set()
            assert finish.wait(5)
        return result

    monkeypatch.setattr(module, "update_preferences", preferences)
    read = Thread(target=lambda: info.settings(store))

    def save() -> None:
        info.settings(store, module.UpdatePreferences(include_nightly=False))
        posted.set()

    write = Thread(target=save)
    read.start()
    try:
        assert entered.wait(5)
        write.start()
        assert not posted.wait(0.1), "The write must wait for the read/cache transaction"
    finally:
        finish.set()
        read.join(5)
        if write.ident is not None:
            write.join(5)
    assert posted.is_set()
    assert info.status()["include_nightly"] is False
