"""Request-scoped display labels for directly authored slide revisions."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

_revision_author: ContextVar[str | None] = ContextVar("revision_author", default=None)


@contextmanager
def revision_author(display_name: str | None) -> Generator[None]:
    """Bind the verified display label for a synchronous write scope."""
    with _revision_author.set((display_name or "").strip() or None):
        yield


def current_revision_author() -> str | None:
    """Return the display label bound to the current write scope."""
    return _revision_author.get()
