"""Exact design references are selected by humans and rechecked against current access."""

import json
from contextlib import nullcontext
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.designs import pinned
from artifact_studio.designs.pinned import DesignReferences, PinAccess, pinned_routes
from artifact_studio.models import SlidePatch
from artifact_studio.storage import ConflictError, Store, digest
from tests.design_factory import TEMPLATE_ID, make_design
from tests.integration.multiplayer_fixture import running_multiplayer_app
from tests.test_multiplayer_server import accept, grant, human

DECK = "2026-11-21-clinic-review"


def test_reference_is_frozen_switches_by_scope_and_respects_source_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "review.sqlite3")
    artifacts = ArtifactStore(store)
    project = artifacts.create_project("Fictional clinic").id
    other = artifacts.create_project("Second fictional clinic").id
    deck = store.create(DECK, "Clinic review", project_id=project)
    sibling = store.create("2026-11-21-clinic-next", "Extension", project_id=project)
    slide = deck.slides[0]
    _ = store.revise(
        DECK,
        slide.id,
        digest(slide.model_dump_json()),
        "set-slide-for-review",
        SlidePatch(
            notes="PRIVATE PRESENTER TEXT", html='<article class="slide">Eight minutes</article>'
        ),
    )
    service = DesignReferences(store)
    app = FastAPI()
    app.include_router(pinned_routes(lambda request, deck_id: nullcontext(PinAccess(store))))
    client = TestClient(app)
    path = f"/api/decks/{DECK}/design-reference"
    body = {
        "scope": "project",
        "slide_id": slide.id,
        "expected_pin_id": "",
        "content_hash": store.content_hash(DECK, store.read(DECK).slides[0]),
    }
    assert client.get(path).json()["project"] == {"editable": True, "pin": None}
    rendered: list[str] = []

    def fake_render(image: pinned.Snapshot) -> bytes:
        rendered.append(image.document)
        return b"fixture-pixels"

    monkeypatch.setattr(pinned, "render_thumbnail", fake_render)
    result = client.post(path, json=body).raise_for_status().json()["project"]["pin"]
    pin_id = result["pin_id"]
    source = service.source(pin_id)
    assert not rendered
    assert "Eight minutes" in source["html"]
    assert "PRIVATE PRESENTER TEXT" not in json.dumps([result, source])
    assert client.post(path, json=body).json()["project"]["pin"] == result
    found = service.resolve(deck_id=sibling.id)
    assert found is not None and found.pin_id == pin_id
    assert service.resolve(project_id=project, can_view=lambda deck: False) is None
    assert service.resolve(deck_id=DECK, can_view_project=lambda project: False) is None
    current = store.read(DECK).slides[0]
    _ = store.revise(
        DECK,
        slide.id,
        digest(current.model_dump_json()),
        "new-content-revision",
        SlidePatch(html='<article class="slide">A later result</article>'),
    )
    (store.folder(DECK) / "theme.css").write_text("/* updated clinic theme */")
    found = service.resolve(project_id=project)
    assert found is not None and found.pin_id == pin_id
    assert service.source(pin_id) == source
    assert service.thumbnail(pin_id) == service.thumbnail(pin_id) == b"fixture-pixels"
    assert len(rendered) == 1 and "Eight minutes" in rendered[0]
    assert "updated clinic theme" not in rendered[0] and "PRIVATE PRESENTER TEXT" not in rendered[0]
    with pytest.raises(ConflictError, match="slide changed"):
        _ = client.post(path, json={**body, "scope": "deck"})
    current_hash = store.content_hash(DECK, store.read(DECK).slides[0])
    override = client.post(
        path, json={**body, "scope": "deck", "content_hash": current_hash}
    ).json()["deck"]["pin"]
    found = service.resolve(deck_id=DECK)
    assert found is not None and found.pin_id == override["pin_id"]
    with pytest.raises(ConflictError, match="reference changed"):
        _ = client.post(path, json={"scope": "deck", "expected_pin_id": pin_id})
    _ = client.post(
        path, json={"scope": "deck", "expected_pin_id": override["pin_id"]}
    ).raise_for_status()
    found = service.resolve(deck_id=DECK)
    assert found is not None and found.pin_id == pin_id
    _ = artifacts.set_artifact_project(DECK, other)
    assert service.resolve(project_id=project) is None
    assert service.resolve(project_id=other) is None
    assert service.resolve(deck_id=sibling.id) is None
    assert service.resolve(deck_id="missing") is None
    # Replace the now unavailable project reference from a remaining member.
    s = sibling.slides[0]
    replacement = (
        client.post(
            path.replace(DECK, sibling.id),
            json={**body, "slide_id": s.id, "content_hash": store.content_hash(sibling.id, s)},
        )
        .raise_for_status()
        .json()["project"]["pin"]
    )
    assert replacement["deck_id"] == sibling.id
    store.set_archived(sibling.id, archived=True)
    assert service.resolve(project_id=project) is None


def test_shared_human_pin_roles(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(
            tmp_path / "app", tmp_path / "server.log", unified=True, startup_timeout=30
        ) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        human(app, "bob") as bob,
    ):
        _ = ArtifactStore(app.store).set_artifact_project(app.deck_id, app.project_id)
        accept(alice, grant(owner, "alice", app.deck_id, "edit"))
        accept(bob, grant(owner, "bob", app.deck_id, "view"))
        path = f"/api/decks/{app.deck_id}/design-reference"
        slide = owner.get(f"/api/decks/{app.deck_id}").raise_for_status().json()["slides"][0]
        change = {
            "scope": "project",
            "slide_id": slide["id"],
            "content_hash": slide["content_hash"],
            "expected_pin_id": "",
        }
        selected = owner.post(path, json=change)
        assert selected.status_code == 200, selected.text
        assert selected.json()["project"]["pin"]["slide_id"] == slide["id"]
        assert alice.get(path).json()["project"] == {"pin": None, "editable": False}
        assert alice.post(path, json=change).status_code == 404
        assert bob.get(path).json()["deck"] == {"pin": None, "editable": False}
        assert bob.post(path, json={**change, "scope": "deck"}).status_code == 404
        deck_pin = (
            alice.post(path, json={**change, "scope": "deck"})
            .raise_for_status()
            .json()["deck"]["pin"]
        )
        assert bob.get(path).json()["deck"]["pin"] == deck_pin
        assert alice.post(
            path, json={"scope": "deck", "expected_pin_id": deck_pin["pin_id"]}
        ).is_success
        accept(alice, grant(owner, "alice", app.project_id, "edit", kind="project"))
        project_pin = alice.get(path).json()["project"]["pin"]
        assert project_pin == selected.json()["project"]["pin"]
        assert alice.post(
            path, json={"scope": "project", "expected_pin_id": project_pin["pin_id"]}
        ).is_success
