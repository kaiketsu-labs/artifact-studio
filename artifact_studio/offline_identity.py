"""Identity partitioning for experimental offline data, with no credentials in the browser."""

import hashlib
import json
import os

from .authentication import Principal
from .deployment import DeploymentConfig


def cache_scope(
    config: DeploymentConfig, principal: Principal | None, workspace: str
) -> str | None:
    """Bind a browser partition to a verified human, identity provider and workspace."""
    if (
        os.environ.get("ARTIFACT_STUDIO_EXPERIMENTAL_OFFLINE") != "1"
        or config.mode != "hosted"
        or config.auth_provider != "cloudflare"
        or principal is None
        or principal.service
        or not principal.subject
        or not workspace
    ):
        return None
    options = config.auth_options or {}
    domain, audience = options.get("team_domain"), options.get("audience")
    if not isinstance(domain, str) or not domain or not isinstance(audience, str) or not audience:
        return None
    payload = [
        "offline-v1",
        config.auth_provider,
        domain,
        audience,
        config.public_url,
        principal.subject,
        workspace,
    ]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
