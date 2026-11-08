"""Hosted browser body budgets are enforced before application mutations."""

import asyncio
from collections.abc import Iterator

import pytest
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from artifact_studio.authentication import Principal
from artifact_studio.deployment import DeploymentConfig
from artifact_studio.hosted_auth import HostedAuth


class Provider:
    async def authenticate(self, headers: Headers) -> Principal:
        return Principal("member", "member@example.com")


def boundary(app: ASGIApp) -> HostedAuth:
    config = DeploymentConfig(
        "hosted",
        "https://studio.example.com",
        "https://preview.example.com",
        "test",
        {},
        ("member@example.com",),
        (),
    )
    return HostedAuth(app, config, Provider(), lambda method, path: True)


def scope(
    path: str = "/api/navigation/visit", headers: tuple[tuple[bytes, bytes], ...] = ()
) -> Scope:
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [
            (b"host", b"studio.example.com"),
            (b"origin", b"https://studio.example.com"),
            *headers,
        ],
        "client": ("127.0.0.1", 1),
    }


@pytest.mark.anyio
async def test_chunked_body_is_replayed_and_length_header_is_not_trusted() -> None:
    seen: list[bytes] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        while True:
            msg = await receive()
            seen.append(msg["body"])
            if not msg["more_body"]:
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    incoming: Iterator[Message] = iter(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ]
    )
    output: list[Message] = []

    async def receive() -> Message:
        return next(incoming)

    async def send(msg: Message) -> None:
        output.append(msg)

    await boundary(app)(scope(headers=((b"content-length", b"1"),)), receive, send)
    assert b"".join(seen) == b"abcdef"
    assert output[0]["status"] == 204


@pytest.mark.anyio
async def test_oversize_never_invokes_handler() -> None:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        pytest.fail("oversized request reached handler")

    incoming: Iterator[Message] = iter(
        [{"type": "http.request", "body": b"x" * (1024 * 1024), "more_body": True}] * 17
    )
    output: list[Message] = []

    async def receive() -> Message:
        return next(incoming)

    async def send(msg: Message) -> None:
        output.append(msg)

    await boundary(app)(scope(), receive, send)
    assert output[0]["status"] == 413


@pytest.mark.anyio
async def test_slow_body_times_out_before_handler() -> None:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        pytest.fail("timed out request reached handler")

    handler = boundary(app)
    handler.body_timeout = 0.01
    output: list[Message] = []

    async def receive() -> Message:
        await asyncio.sleep(1)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg: Message) -> None:
        output.append(msg)

    await handler(scope(), receive, send)
    assert output[0]["status"] == 408


@pytest.mark.anyio
async def test_busy_writes_rejected_without_waiting() -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        entered.set()
        await release.wait()

    handler = boundary(app)

    async def receive() -> Message:
        return {"type": "http.request", "body": b"x", "more_body": False}

    async def ignore(msg: Message) -> None:
        pass

    first = asyncio.create_task(handler(scope("/api/artifacts/import"), receive, ignore))
    await entered.wait()
    output: list[Message] = []

    async def send(msg: Message) -> None:
        output.append(msg)

    await handler(scope("/api/artifacts/import"), receive, send)
    assert output[0]["status"] == 429
    release.set()
    await first


@pytest.mark.anyio
@pytest.mark.parametrize("interruption", ["disconnect", "timeout"])
async def test_upload_slot_released_after_interrupted_read(interruption: str) -> None:
    invoked = 0

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal invoked
        invoked += 1
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    handler = boundary(app)
    handler.body_timeout = 0.01
    output: list[Message] = []

    async def interrupted_receive() -> Message:
        if interruption == "timeout":
            await asyncio.sleep(1)
        return {"type": "http.disconnect"}

    async def receive() -> Message:
        return {"type": "http.request", "body": b"ok", "more_body": False}

    async def send(message: Message) -> None:
        output.append(message)

    await handler(scope("/api/artifacts/import"), interrupted_receive, send)
    assert invoked == 0
    assert not output if interruption == "disconnect" else output[0]["status"] == 408
    output.clear()
    await handler(scope("/api/artifacts/import"), receive, send)
    assert invoked == 1
    assert output[0]["status"] == 204
