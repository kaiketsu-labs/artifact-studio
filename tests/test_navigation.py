"""Shared recency and live navigation must preserve source and review state."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from artifact_studio import app as service
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.navigation import Navigation, NavigationVisit
from artifact_studio.review import ReviewHookInput, Reviews
from artifact_studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def navigation(tmp_path: Path) -> Navigation:
    """Use fictional projects and an isolated local database."""
    store = Store(tmp_path / "collection", tmp_path / "review.sqlite3")
    make_design(store.root / TEMPLATE_ID)
    _ = store.create("2026-11-04-navigation", "Board review")
    return Navigation(store)


def test_recents_are_shared_deduplicated_bounded_and_use_current_titles(
    navigation: Navigation,
) -> None:
    """Remember genuine visits across clients while ignoring views and renames as activity."""
    artifacts = ArtifactStore(navigation.store)
    projects = [artifacts.create_project(f"Project {index}") for index in range(7)]
    for project in projects:
        _ = navigation.visit(NavigationVisit(project_id=project.id))
    assert [entry.id for entry in navigation.snapshot().recent] == [
        p.id for p in reversed(projects[2:])
    ]
    reopened = Navigation(navigation.store).visit(NavigationVisit(project_id=projects[3].id))
    assert [entry.id for entry in reopened.recent] == [projects[i].id for i in [3, 6, 5, 4, 2]]
    assert navigation.snapshot() == reopened
    with navigation.store.connect() as db:
        renamed = projects[3].model_copy(update={"title": "Renamed project"})
        _ = db.execute(
            "UPDATE artifact_projects SET document=? WHERE id=?",
            (renamed.model_dump_json(), renamed.id),
        )
        _ = db.execute("DELETE FROM artifact_projects WHERE id=?", (projects[6].id,))
    assert navigation.snapshot().recent[0].title == "Renamed project"
    assert projects[6].id not in [entry.id for entry in navigation.snapshot().recent]


def test_connected_work_requires_a_live_lease_and_never_renews_it(navigation: Navigation) -> None:
    """Ignore waiting-only presence, expired connections, design examples and the design inbox."""
    store = navigation.store
    reviews = Reviews(store)
    deck = "2026-11-04-navigation"
    body = ReviewHookInput(
        harness="codex", session_id="reviewer", event="PostToolUse", deck_id=deck, auto_start=True
    )
    assert reviews.bind(body).listening
    before = store.workflow(deck)
    active = navigation.snapshot().connected
    assert len(active) == 1 and active[0].title == "Board review"
    assert active[0].url == "/?deck=" + deck and active[0].project_title == "Presentations"
    assert active[0].harness == "codex" and not navigation.snapshot().recent
    with store.connect() as db:
        expiry = db.execute("SELECT expires_at FROM review_sessions").fetchone()[0]
    _ = navigation.snapshot()
    with store.connect() as db:
        assert db.execute("SELECT expires_at FROM review_sessions").fetchone()[0] == expiry
        _ = db.execute("UPDATE review_sessions SET expires_at=?", (time.time() - 1,))
        _ = db.execute(
            "INSERT OR REPLACE INTO workflow(deck,waiting_until) VALUES (?,?)",
            (deck, time.time() + 100),
        )
    assert not navigation.snapshot().connected
    assert not navigation.visit(NavigationVisit(artifact_id=TEMPLATE_ID)).recent
    assert before.review_connected
    assert reviews.bind(body).listening
    reviews.disconnect(deck)
    assert not navigation.snapshot().connected


def test_artifact_visits_follow_membership_without_reading_bundle_bodies(
    navigation: Navigation, tmp_path: Path
) -> None:
    """Record the owner of native and moved legacy artifacts, without changing their sources."""
    artifacts = ArtifactStore(navigation.store)
    project = artifacts.create_project("Team launch")
    source = tmp_path / "source"
    source.mkdir()
    _ = (source / "index.html").write_text("<h1>Original</h1>")
    artifact = artifacts.register_artifact(project.id, "Launch page", str(source), "index.html")
    result = navigation.visit(NavigationVisit(artifact_id=artifact.id))
    assert result.recent[0].id == project.id
    assert (source / "index.html").read_text() == "<h1>Original</h1>"
    deck = "2026-11-04-navigation"
    _ = artifacts.set_artifact_project(deck, project.id)
    assert navigation.visit(NavigationVisit(artifact_id=deck)).recent[0].id == project.id


def test_moving_the_last_legacy_deck_removes_its_virtual_project_shortcut(
    navigation: Navigation,
) -> None:
    """Keep recent project destinations consistent with actual hub membership."""
    artifacts = ArtifactStore(navigation.store)
    deck = "2026-11-04-navigation"
    _ = navigation.visit(NavigationVisit(artifact_id=deck))
    project = artifacts.create_project("Team launch")
    _ = artifacts.set_artifact_project(deck, project.id)
    assert [entry.id for entry in artifacts.list_projects()] == [project.id]
    assert not navigation.snapshot().recent
    plain = navigation.store.root / "plain-page"
    plain.mkdir()
    _ = (plain / "index.html").write_text("<h1>A page, not a presentation</h1>")
    assert not navigation.snapshot().recent
    assert navigation.visit(NavigationVisit(artifact_id=deck)).recent[0].id == project.id


def test_navigation_http_keeps_invalid_and_cross_origin_visits_out(
    navigation: Navigation, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expose one safe shared contract to both menus without accepting synthetic targets."""
    monkeypatch.setattr(service, "store", navigation.store)
    client = TestClient(service.app, base_url="http://127.0.0.1")
    assert client.get("/api/navigation").json() == {"connected": [], "recent": []}
    assert client.post("/api/navigation/visit", json={"project_id": "all"}).status_code == 404
    assert client.post("/api/navigation/visit", json={}).status_code == 422
    response = client.post("/api/navigation/visit", json={"artifact_id": "2026-11-04-navigation"})
    assert (
        response.status_code == 200 and response.json()["recent"][0]["id"] == "legacy-presentations"
    )
    assert (
        client.post(
            "/api/navigation/visit",
            json={"project_id": "legacy-presentations"},
            headers={"origin": "https://other.invalid"},
        ).status_code
        == 403
    )
