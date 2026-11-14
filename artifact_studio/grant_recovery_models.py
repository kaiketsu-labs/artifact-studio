"""Portable, credential-free human grant archive models."""

from typing import Literal

from pydantic import Field

from .models import StrictModel


class HumanGrant(StrictModel):
    """A human identity and one scope; no invitation or agent credentials."""

    person: str = Field(pattern=r"^[0-9a-f]{64}$")
    email: str = Field(min_length=3, max_length=254)
    kind: Literal["workspace", "project", "artifact"]
    resource: str = Field(max_length=200)
    role: Literal["view", "comment", "edit"]


class GrantSnapshot(StrictModel):
    """Portable sharing state, bound to the original verified identity context."""

    version: Literal[1] = 1
    issuer: str = Field(min_length=1, max_length=512)
    audience: str = Field(min_length=1, max_length=512)
    workspace: str = Field(min_length=1, max_length=100)
    owner: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_email: str = Field(min_length=3, max_length=254)
    grants: list[HumanGrant] = Field(max_length=10000)


class StagedGrants(StrictModel):
    """Grant data staged by a verified restore, bound to the source archive."""

    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot: GrantSnapshot
