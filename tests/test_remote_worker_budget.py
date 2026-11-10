"""Cancelled agent requests cannot free capacity held by running workers."""

import asyncio
import threading

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from artifact_studio.copilot_tools import remote_tool


@pytest.mark.anyio
async def test_cancelled_callers_do_not_release_running_worker_capacity() -> None:
    release = threading.Event()
    started = [threading.Event() for _ in range(4)]

    def work(index: int) -> int:
        started[index].set()
        if not release.wait(timeout=10):
            raise RuntimeError("worker timed out")
        return index

    call = remote_tool(work)
    tasks = [asyncio.create_task(call(index)) for index in range(4)]
    try:
        assert all(await asyncio.gather(*(asyncio.to_thread(event.wait, 5) for event in started)))
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with pytest.raises(ToolError, match="busy"):
            await call(0)
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)

    # Completion is signalled from the worker thread; wait for its callback.
    for _ in range(100):
        try:
            assert await call(0) == 0
            break
        except ToolError:
            await asyncio.sleep(0.01)
    else:
        pytest.fail("worker capacity was not released")


@pytest.mark.anyio
async def test_failed_worker_releases_capacity_and_translates_error() -> None:
    def fail() -> None:
        raise ValueError("invalid field")

    for _ in range(5):
        with pytest.raises(ToolError, match="invalid field"):
            await remote_tool(fail)()


@pytest.mark.anyio
async def test_submit_failure_releases_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    from artifact_studio import copilot_tools

    def fail_submit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("executor unavailable")

    def work() -> int:
        return 1

    with monkeypatch.context() as patch:
        patch.setattr(copilot_tools._REMOTE_WORKERS, "submit", fail_submit)  # pyright: ignore[reportPrivateUsage]
        for _ in range(5):
            with pytest.raises(RuntimeError, match="executor unavailable"):
                await remote_tool(work)()
    assert await remote_tool(work)() == 1
