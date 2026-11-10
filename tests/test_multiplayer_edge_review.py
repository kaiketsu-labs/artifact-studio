"""Regression journeys for isolated edits and simultaneous live-review hooks."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
from fastapi import FastAPI
from starlette.datastructures import Headers

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.authentication import Principal
from artifact_studio.deployment import DeploymentConfig
from artifact_studio.multiplayer import create_app
from artifact_studio.multiplayer_access import Scope, Sharing
from artifact_studio.multiplayer_chat import Conversations
from artifact_studio.multiplayer_http import MultiplayerAuth
from artifact_studio.multiplayer_tools import Ctx, MultiplayerTools
from artifact_studio.storage import Store


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
    tools = MultiplayerTools(hub, Conversations(hub), "https://studio.example.test")
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


def test_native_editor_assets_have_separate_capacity(tmp_path: Path) -> None:
    """A cold editor can still load its assets when shared data slots are in use."""

    async def journey() -> None:
        store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
        hub = Sharing(tmp_path / "sharing", store, "owner@example.test")
        config = DeploymentConfig(
            "hosted", "https://studio.example.test", "https://preview.example.test"
        )
        app = FastAPI()
        release, occupied = asyncio.Event(), asyncio.Event()
        waiting = 0

        @app.get("/api/hub")
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
            requests = [asyncio.create_task(client.get("/api/hub")) for _ in range(8)]
            try:
                await asyncio.wait_for(occupied.wait(), timeout=5)
                result = await client.get("/web/session.js")
                assert result.status_code == 200
                assert result.headers["cache-control"] == "no-store"
            finally:
                release.set()
                await asyncio.gather(*requests)

    asyncio.run(journey())
