"""Short-lived browser change streams over canonical files and durable review state."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from .storage import Store


def change_token(store: Store, deck_id: str) -> tuple[tuple[str, int, int], ...]:
    """Observe replacements and asset changes without reading private slide content."""
    folder = store.folder(deck_id)
    files = [
        folder / "index.html",
        folder / "theme.css",
        folder / "README.md",
        store.root / "INSTRUCTIONS.md",
        store.root / "LEARNED-PREFERENCES.md",
        store.database,
    ]
    files.extend(sorted(path for path in (folder / "assets").rglob("*") if path.is_file()))
    result: list[tuple[str, int, int]] = []
    for path in files:
        try:
            stat = path.stat()
            result.append((str(path), stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            result.append((str(path), 0, 0))
    return tuple(result)


async def deck_events(
    store: Store,
    deck_id: str,
    disconnected: Callable[[], Awaitable[bool]],
    interval: float = 0.35,
) -> AsyncIterator[str]:
    """Push change notices, then reconnect periodically to bound server shutdown time."""
    previous = await asyncio.to_thread(change_token, store, deck_id)
    yield "retry: 500\nevent: change\ndata: ready\n\n"
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline and not await disconnected():
        await asyncio.sleep(interval)
        current = await asyncio.to_thread(change_token, store, deck_id)
        if current != previous:
            previous = current
            yield "event: change\ndata: updated\n\n"
