"""Preview admission waits for normal asset bursts and bounds active send buffers."""

import asyncio

import pytest
from starlette.types import Message, Receive, Scope, Send

from tests.test_hosted_limits import boundary


def preview_scope() -> Scope:
    return {
        "type": "http",
        "method": "GET",
        "path": "/_preview/fictional/resource",
        "headers": [(b"host", b"preview.example.com")],
        "client": ("127.0.0.1", 1),
    }


async def receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


@pytest.mark.anyio
async def test_preview_burst_waits_and_holds_slots_through_sending() -> None:
    release = asyncio.Event()
    four_started = asyncio.Event()
    active = peak = 0

    async def app(_scope: Scope, _receive: Receive, send: Send) -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 4:
            four_started.set()
        try:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await release.wait()  # Model a response awaiting downstream backpressure.
            await send({"type": "http.response.body", "body": b"ok"})
        finally:
            active -= 1

    middleware = boundary(app)
    statuses: list[int] = []

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            statuses.append(message["status"])

    tasks = [asyncio.create_task(middleware(preview_scope(), receive, send)) for _ in range(12)]
    try:
        await asyncio.wait_for(four_started.wait(), 1)
        assert statuses == [200] * 4
        assert not any(task.done() for task in tasks)
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert peak == 4
    assert statuses == [200] * 12


@pytest.mark.anyio
async def test_preview_wait_timeout_and_cancel_do_not_leak_capacity() -> None:
    release = asyncio.Event()
    four_started = asyncio.Event()
    started = 0

    async def app(_scope: Scope, _receive: Receive, send: Send) -> None:
        nonlocal started
        started += 1
        if started == 4:
            four_started.set()
        await release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = boundary(app)
    middleware.preview_wait_timeout = 0.02
    messages: list[Message] = []

    async def send(message: Message) -> None:
        messages.append(message)

    tasks = [asyncio.create_task(middleware(preview_scope(), receive, send)) for _ in range(4)]
    try:
        await asyncio.wait_for(four_started.wait(), 1)
        await middleware(preview_scope(), receive, send)
        assert messages[0]["status"] == 429
        assert (b"retry-after", b"2") in messages[0]["headers"]
        cancelled = asyncio.create_task(middleware(preview_scope(), receive, send))
        await asyncio.sleep(0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert started == 4
    finally:
        release.set()
        await asyncio.gather(*tasks)
    messages.clear()
    await middleware(preview_scope(), receive, send)
    assert messages[0]["status"] == 200
