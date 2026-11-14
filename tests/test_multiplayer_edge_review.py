"""Regression journeys for isolated edits and simultaneous live-review hooks."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from fastapi import FastAPI
from starlette.datastructures import Headers

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.authentication import Principal
from artifact_studio.deck_presence import DeckPresence
from artifact_studio.deployment import DeploymentConfig
from artifact_studio.models import Deck
from artifact_studio.multiplayer import create_app
from artifact_studio.multiplayer_access import Scope, Sharing
from artifact_studio.multiplayer_chat import Conversations
from artifact_studio.multiplayer_http import MultiplayerAuth
from artifact_studio.multiplayer_tools import Ctx, MultiplayerTools
from artifact_studio.shared_editor import create_shared_editor
from artifact_studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design


class FixtureProvider:
    async def authenticate(self, headers: Headers) -> Principal:
        name = headers.get("x-fixture-auth", "owner")
        return Principal(name, f"{name}@example.test")


def test_hook_capacity_does_not_block_human_actions(tmp_path: Path) -> None:
    async def journey() -> None:
        store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
        hub = Sharing(tmp_path / "sharing", store, "owner@example.test")
        owner = hub.human(Principal("owner", "owner@example.test"))
        service = ArtifactStore(store)
        project = service.create_project("Fictional project")
        source = tmp_path / "fiction.md"
        source.write_text("Fictional content")
        item = service.register_artifact(project.id, "Shared", source, "fiction.md")
        scope = Scope("default", "artifact", item.id)
        token = hub.connect_agent(owner, scope, "Fictional agent")["token"]
        config = DeploymentConfig(
            "hosted",
            "https://studio.example.test",
            "https://preview.example.test",
            "fixture",
            {},
            ("owner@example.test",),
            (),
        )
        app = create_app(config, FixtureProvider(), hub)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=config.public_url, timeout=30
        ) as client:
            headers = {"authorization": "Bearer " + token}
            body = {"harness": "codex", "event": "Stop", "artifact_id": item.id, "auto_start": True}
            tasks = [
                asyncio.create_task(
                    client.post(
                        "/api/review/hooks",
                        headers=headers,
                        json={**body, "session_id": f"fictional-session-{index}"},
                    )
                )
                for index in range(8)
            ]
            try:
                async with asyncio.timeout(10):
                    while True:
                        with hub.connect() as db:
                            count = cast(
                                int,
                                db.execute(
                                    "SELECT count(*) FROM multiplayer_hooks WHERE expires>0"
                                ).fetchone()[0],
                            )
                        if count == 8:
                            break
                        await asyncio.sleep(0.02)
                assert all(not task.done() for task in tasks)
                extra = await client.post(
                    "/api/review/hooks",
                    headers=headers,
                    json={**body, "session_id": "fictional-extra"},
                )
                assert extra.status_code == 429
                assert (await client.get("/api/session")).status_code == 200
                response = await client.post(
                    f"/api/workspaces/default/artifacts/{item.id}/chat",
                    headers={"origin": config.public_url},
                    json={"message": "Fictional comment", "request_id": "fictional-message"},
                )
                assert response.status_code == 200
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(journey())


def test_artifact_only_edit_receipt_hides_parent_project(tmp_path: Path) -> None:
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    hub = Sharing(tmp_path / "sharing", store, "owner@example.test")
    owner = hub.human(Principal("owner", "owner@example.test"))
    guest = hub.human(Principal("guest", "guest@example.test"))
    service = ArtifactStore(store)
    project = service.create_project("Fictional confidential project")
    source = tmp_path / "fiction.md"
    source.write_text("Fictional original")
    item = service.register_artifact(project.id, "Shared", source, "fiction.md")
    scope = Scope("default", "artifact", item.id)
    invite = hub.invite(owner, scope, "guest@example.test", "edit")
    hub.accept(guest, invite["token"])
    token = hub.connect_agent(guest, scope, "Fictional agent")["token"]
    actor = hub.agent(token)
    ctx = cast(
        Ctx,
        cast(
            object,
            SimpleNamespace(
                request_context=SimpleNamespace(
                    request=SimpleNamespace(state=SimpleNamespace(actor=actor))
                )
            ),
        ),
    )
    tools = MultiplayerTools(
        hub, Conversations(hub), "https://studio.example.test", DeckPresence(hub)
    )
    result = tools.revise_artifact(
        ctx,
        item.id,
        json.dumps([{"path": "fiction.md", "text": "Fictional revised"}]),
        item.revision,
        "fictional-edit-request",
    )
    assert "project_id" not in result
    assert "project_title" not in result
    assert service.get_artifact(item.id).revision == item.revision + 1


def test_native_editor_assets_have_separate_capacity(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Keep editor assets available and diagnose overload without request data."""

    async def journey() -> None:
        store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
        hub = Sharing(tmp_path / "sharing", store, "owner@example.test")
        config = DeploymentConfig(
            "hosted", "https://studio.example.test", "https://preview.example.test"
        )
        app = FastAPI()
        release, occupied = asyncio.Event(), asyncio.Event()
        waiting = 0

        @app.get("/api/workspaces/fictional-workspace/hub")
        async def library() -> dict[str, bool]:
            nonlocal waiting
            waiting += 1
            if waiting == 8:
                occupied.set()
            await release.wait()
            return {"ok": True}

        @app.get("/web/session.js")
        async def script() -> str:
            return "fictional script"

        boundary = MultiplayerAuth(app, config, FixtureProvider(), hub, shared_shell=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=boundary), base_url=config.public_url
        ) as client:
            route = "/api/workspaces/fictional-workspace/hub"
            requests = [
                asyncio.create_task(client.get(route, headers={"x-fixture-auth": f"user-{index}"}))
                for index in range(8)
            ]
            try:
                await asyncio.wait_for(occupied.wait(), timeout=5)
                result = await client.get("/web/session.js")
                assert result.status_code == 200
                assert result.headers["cache-control"] == "no-store"
                extra = await asyncio.gather(
                    client.get(route, headers={"x-fixture-auth": "alice"}),
                    client.get(route, headers={"x-fixture-auth": "bob"}),
                )
                for response in extra:
                    assert response.status_code == 429
                    assert response.headers["retry-after"] == "2"
                    assert response.headers["cache-control"] == "no-store"
                warnings = [
                    record.getMessage()
                    for record in caplog.records
                    if record.name == "artifact_studio.multiplayer_http"
                ]
                assert len(warnings) == 1
                assert "surface=shared_editor pool=data reason=wait_timeout" in warnings[0]
                assert "fictional-workspace" not in warnings[0]
                assert "alice" not in warnings[0] and "bob" not in warnings[0]
            finally:
                release.set()
                await asyncio.gather(*requests)
            assert (await client.get(route)).status_code == 200

    asyncio.run(journey())


def test_shared_deck_assets_reuse_the_grant_checked_deck_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Load preview assets once per request and enforce inherited, revocable grants."""

    async def journey() -> None:
        collection = tmp_path / "collection"
        make_design(collection / TEMPLATE_ID)
        store = Store(collection, tmp_path / "state/review.sqlite3")
        hub = Sharing(tmp_path / "sharing", store, "owner@example.test")
        owner = hub.human(Principal("owner", "owner@example.test"))
        reader = hub.human(Principal("alice", "alice@example.test"))
        service = ArtifactStore(store)
        project = service.create_project("Fictional project")
        deck_id = "2026-11-13-fictional-assets"
        store.create(deck_id, "Fictional deck", "test-brand")
        _ = service.set_artifact_project(deck_id, project.id)
        folder = store.folder(deck_id)
        assets = folder / "assets"
        assets.mkdir(exist_ok=True)
        _ = (assets / "figure.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
        (assets / "link.svg").symlink_to(assets / "figure.svg")
        scope = Scope("default", "project", project.id)
        invite = hub.invite(owner, scope, "alice@example.test", "view")
        _ = hub.accept(reader, invite["token"])
        grants = cast(list[dict[str, str]], hub.access(owner, scope)["grants"])
        grant_id = grants[0]["id"]
        config = DeploymentConfig(
            "hosted", "https://studio.example.test", "https://preview.example.test"
        )
        original_read = Store.read
        read_count = 0

        def count_read(current: Store, identifier: str) -> Deck:
            """Count reads of this fixture deck without modifying the store."""
            nonlocal read_count
            if identifier == deck_id:
                read_count += 1
            return original_read(current, identifier)

        monkeypatch.setattr(Store, "read", count_read)
        app = create_shared_editor(config, FixtureProvider(), hub)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=config.public_url
        ) as client:
            asset = f"/decks/{deck_id}/assets/figure.svg"
            alice = {"x-fixture-auth": "alice"}
            response = await client.get(asset, headers=alice)
            assert response.status_code == 200
            assert response.text == (assets / "figure.svg").read_text()
            assert "sandbox" in response.headers["content-security-policy"]
            assert read_count == 1
            for path in ("index.html", "assets/link.svg", "assets/missing.svg"):
                assert (
                    await client.get(f"/decks/{deck_id}/{path}", headers=alice)
                ).status_code == 404
            assert (await client.get(asset, headers={"x-fixture-auth": "bob"})).status_code == 404
            assert (await client.get(f"/decks/{TEMPLATE_ID}/theme.css")).status_code == 404
            hub.revoke(owner, scope, grant_id)
            assert (await client.get(asset, headers=alice)).status_code == 404

    asyncio.run(journey())
