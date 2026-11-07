"""Authentication provider contract. Installed providers are trusted server code."""

from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Protocol

from starlette.datastructures import Headers

from .deployment import DeploymentConfig


@dataclass(frozen=True)
class Principal:
    subject: str
    email: str | None = None
    service: bool = False


class AuthenticationError(Exception):
    """Credentials are missing or invalid."""


class AuthenticationUnavailable(Exception):
    """The identity provider cannot currently verify credentials."""


class AuthProvider(Protocol):
    async def authenticate(self, headers: Headers) -> Principal: ...


def load_auth_provider(config: DeploymentConfig) -> AuthProvider:
    options = config.auth_options or {}
    if config.auth_provider == "cloudflare":
        from .cloudflare_auth import CloudflareAuthProvider

        return CloudflareAuthProvider(options)
    matches = entry_points(group="artifact_studio.auth", name=config.auth_provider)
    if len(matches) != 1:
        raise ValueError("Unknown authentication provider")
    return next(iter(matches)).load()(options)
