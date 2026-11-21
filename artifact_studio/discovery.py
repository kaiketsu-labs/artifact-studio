"""Shared matching and pagination for agent project and artifact discovery."""

from collections.abc import Iterable, Iterator
from itertools import islice
from typing import Annotated

from pydantic import Field

DiscoveryQuery = Annotated[str, Field(max_length=256)]
DiscoveryOffset = Annotated[int, Field(ge=0, le=2_147_483_647)]
DiscoveryLimit = Annotated[int, Field(ge=0, le=1000)]


def matches(query: str, identifier: str, title: str) -> bool:
    """Match a literal, case-insensitive substring of the title or ID."""
    needle = query.strip().casefold()
    return not needle or needle in title.casefold() or needle in identifier.casefold()


def page[T](rows: Iterable[T], offset: int, limit: int) -> Iterator[T]:
    """Page visible matches; zero limit preserves the complete default listing."""
    return islice(rows, offset, offset + limit if limit else None)
