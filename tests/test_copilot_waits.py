"""Real TCP acceptance checks for independent, cancellable gateway wait admission."""

import asyncio
import json
import socket
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import cast

import httpx2
import pytest
import uvicorn

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.copilot import KEY_HEADER, GatewayBoundary, create_gateway, read_key
from artifact_studio.copilot_tools import ScopedTools
from tests.test_copilot import DECK, GatewayFixture, data, object_value
from tests.test_copilot import gateway as gateway


async def until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0.01)


@asynccontextmanager
async def running(
    fixture: GatewayFixture, *, timeout: float = 90, waits: int = 4
) -> AsyncGenerator[tuple[GatewayBoundary, httpx2.AsyncClient]]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        address = cast(tuple[str, int], listener.getsockname())
        config = replace(
            fixture.config,
            public_host=f"127.0.0.1:{address[1]}",
            request_timeout_seconds=timeout,
            max_concurrent_waits=waits,
        )
        app = create_gateway(config, store=fixture.store)
        server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="error"))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            await until(lambda: server.started)
            async with httpx2.AsyncClient(
                base_url=f"http://{config.public_host}",
                headers={
                    KEY_HEADER: read_key(config.key_file).decode(),
                    "Accept": "application/json, text/event-stream",
                },
                timeout=8,
            ) as client:
                yield app, client
        finally:
            server.should_exit = True
            await asyncio.wait_for(serving, 5)


async def call(
    client: httpx2.AsyncClient, name: str, arguments: dict[str, object] | None = None
) -> httpx2.Response:
    return await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments or {},
            },
        },
    )


def result(response: httpx2.Response) -> dict[str, object]:
    assert response.status_code == 200, response.text
    return object_value(object_value(response.json())["result"])


@pytest.mark.anyio
async def test_idle_wait_capacity_preserves_short_reads_writes_and_feedback(
    gateway: GatewayFixture,
) -> None:
    """Four waiters neither occupy ordinary HTTP slots nor ordinary tool workers."""
    _ = gateway.store.set_mode(DECK, "batch")
    _ = gateway.store.add_feedback(DECK, None, "Unreleased fictional draft", "", "pending-test")
    async with running(gateway) as (app, client):
        # Include the SDK's coerced integer representation in admission coverage.
        waiting = [
            asyncio.create_task(
                call(
                    client,
                    "read_feedback",
                    {
                        "deck_id": DECK,
                        "wait_seconds": value,
                    },
                )
            )
            for value in (25, "25", 25.0, "25.0")
        ]
        try:
            await until(app.wait_slots.locked)
            assert not app.slots.locked()
            excess = await call(client, "read_feedback", {"deck_id": DECK, "wait_seconds": 25})
            assert excess.status_code == 429
            immediate = data(result(await call(client, "read_feedback", {"deck_id": DECK})))
            assert object_value(immediate)["events"] == []
            # Authentication and browser/host restrictions precede the wait budget.
            for headers, status in (
                ({KEY_HEADER: "wrong"}, 401),
                ({"Origin": "null"}, 403),
                ({"Host": "wrong.example.test"}, 421),
            ):
                denied = await client.post("/mcp", json={}, headers=headers)
                assert denied.status_code == status
            latencies: list[float] = []
            for _ in range(5):
                started = time.monotonic()
                projects = data(result(await call(client, "list_projects")))
                latencies.append(time.monotonic() - started)
                assert gateway.selected.project_id in json.dumps(projects)
            started = time.monotonic()
            created = data(
                result(
                    await call(
                        client,
                        "create_artifact",
                        {
                            "project_id": gateway.selected.project_id,
                            "title": "Concurrent fictional write",
                            "entrypoint": "index.html",
                            "files_json": json.dumps(
                                [
                                    {
                                        "path": "index.html",
                                        "text": "<h1>Written during idle waits</h1>",
                                    }
                                ]
                            ),
                            "request_id": "write-with-four-waiters",
                        },
                    )
                )
            )
            write_seconds = time.monotonic() - started
            assert "Concurrent fictional write" in json.dumps(created)
            assert all(not task.done() for task in waiting)
            print(
                json.dumps(
                    {
                        "read_seconds": latencies,
                        "write_seconds": write_seconds,
                        "active_waiters": len(waiting),
                    }
                )
            )
            # Withheld feedback never ends a wait. Releasing it wakes every waiter.
            _ = gateway.store.release_batch(DECK)
            delivered = await asyncio.wait_for(asyncio.gather(*waiting), 5)
            assert all(
                "Unreleased fictional draft" in json.dumps(data(result(item))) for item in delivered
            )
            assert not app.wait_slots.locked()
        finally:
            for task in waiting:
                task.cancel()
            await asyncio.gather(*waiting, return_exceptions=True)


@pytest.mark.anyio
async def test_wait_timeout_disconnect_and_request_deadline_stop_polling(
    gateway: GatewayFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    polls: list[float] = []
    original = ScopedTools.feedback_poll

    def observed(self: ScopedTools, deck: str, cursor: int) -> dict[str, object]:
        polls.append(time.monotonic())
        return original(self, deck, cursor)

    monkeypatch.setattr(ScopedTools, "feedback_poll", observed)
    async with running(gateway, waits=1) as (app, client):
        started = time.monotonic()
        timed = data(
            result(await call(client, "read_feedback", {"deck_id": DECK, "wait_seconds": 1}))
        )
        assert object_value(timed)["events"] == []
        assert time.monotonic() - started >= 0.9
        waiting = asyncio.create_task(
            call(client, "read_feedback", {"deck_id": DECK, "wait_seconds": 25})
        )
        await until(app.wait_slots.locked)
        before = len(polls)
        await until(lambda: len(polls) > before)
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        await until(lambda: not app.wait_slots.locked())
        # Let a poll already running at disconnect finish before checking no new polls.
        await asyncio.sleep(0.1)
        after = len(polls)
        await asyncio.sleep(0.4)
        assert len(polls) == after
        assert data(result(await call(client, "list_projects")))
    async with running(gateway, waits=1, timeout=1) as (app, client):
        timed = await call(client, "read_feedback", {"deck_id": DECK, "wait_seconds": 25})
        assert timed.status_code == 504
        assert not app.wait_slots.locked()
        await asyncio.sleep(0.1)
        after = len(polls)
        await asyncio.sleep(0.4)
        assert len(polls) == after
        assert data(result(await call(client, "list_projects")))


@pytest.mark.anyio
async def test_wait_rechecks_scope_after_project_move(gateway: GatewayFixture) -> None:
    async with running(gateway, waits=1) as (app, client):
        waiting = asyncio.create_task(
            call(client, "read_feedback", {"deck_id": DECK, "wait_seconds": 25})
        )
        try:
            await until(app.wait_slots.locked)
            _ = ArtifactStore(gateway.store).set_artifact_project(
                DECK, gateway.unrelated.project_id
            )
            denied = result(await asyncio.wait_for(waiting, 5))
            assert denied["isError"] is True
            assert "UNRELATED" not in json.dumps(denied)
            _ = gateway.store.add_feedback(
                DECK, None, "Out-of-scope private content", "", "outside-feedback"
            )
            denied = result(
                await call(client, "read_feedback", {"deck_id": DECK, "wait_seconds": 25})
            )
            assert denied["isError"] is True
            assert "Out-of-scope private content" not in json.dumps(denied)
            assert not app.wait_slots.locked()
        finally:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
