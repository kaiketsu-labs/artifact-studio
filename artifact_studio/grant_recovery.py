"""Explicit, identity-bound recovery of human sharing grants."""

import hashlib
import re
import secrets
from typing import cast

from .deployment import DeploymentConfig
from .grant_recovery_models import GrantSnapshot, HumanGrant, StagedGrants
from .multiplayer_access import AccessDenied, Actor, Scope, Sharing, email_address
from .storage import ConflictError


def identity_context(config: DeploymentConfig) -> tuple[str, str]:
    """Only use providers with a known, stable issuer and audience contract."""
    options = config.auth_options or {}
    domain, audience = options.get("team_domain"), options.get("audience")
    if (
        config.mode != "hosted"
        or config.auth_provider != "cloudflare"
        or not isinstance(domain, str)
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.cloudflareaccess\.com", domain)
        or not isinstance(audience, str)
        or not audience
    ):
        raise ValueError("Grant recovery requires a verified Cloudflare Access identity context.")
    return f"https://{domain}", audience


def snapshot_grants(
    hub: Sharing, actor: Actor, workspace: str, config: DeploymentConfig
) -> GrantSnapshot:
    """Capture only this owner's workspace and its current human grants."""
    issuer, audience = identity_context(config)
    with hub.locked(), hub.connect() as db:
        if not hub.owner(actor, workspace):
            raise AccessDenied
        owner = db.execute("SELECT email FROM people WHERE id=?", (actor.person,)).fetchone()
        if owner is None:
            raise AccessDenied
        rows = db.execute(
            "SELECT g.person,p.email,g.kind,g.resource,g.role FROM grants g "
            "LEFT JOIN people p ON p.id=g.person WHERE g.workspace=? "
            "ORDER BY g.person,g.kind,g.resource",
            (workspace,),
        ).fetchall()
        return GrantSnapshot(
            issuer=issuer,
            audience=audience,
            workspace=workspace,
            owner=actor.person,
            owner_email=cast(str, owner["email"]),
            grants=[HumanGrant.model_validate(dict(row)) for row in rows],
        )


def _validate(
    hub: Sharing, actor: Actor, snapshot: GrantSnapshot, config: DeploymentConfig
) -> None:
    """Validate context, identity uniqueness, resource existence and an unused target."""
    if (snapshot.issuer, snapshot.audience) != identity_context(config):
        raise ValueError("The backup belongs to a different identity provider or audience.")
    if actor.agent or actor.person != snapshot.owner or not hub.owner(actor, snapshot.workspace):
        raise AccessDenied
    with hub.connect() as db:
        owner = db.execute("SELECT email FROM people WHERE id=?", (actor.person,)).fetchone()
        if not owner or owner["email"] != snapshot.owner_email:
            raise AccessDenied
        for table in ("grants", "invitations", "agents"):
            if db.execute(
                f"SELECT 1 FROM {table} WHERE workspace=? LIMIT 1", (snapshot.workspace,)
            ).fetchone():
                raise ConflictError("Sharing must be unused in the restored workspace.")
        seen: set[tuple[str, str, str]] = set()
        identities: dict[str, str] = {}
        emails: dict[str, str] = {}
        for grant in snapshot.grants:
            if email_address(grant.email) != grant.email or grant.person == snapshot.owner:
                raise ValueError("Invalid grant identity.")
            if (
                identities.setdefault(grant.person, grant.email) != grant.email
                or emails.setdefault(grant.email, grant.person) != grant.person
            ):
                raise ValueError("Conflicting grant identities.")
            pair = (grant.person, grant.kind, grant.resource)
            if pair in seen:
                raise ValueError("Duplicate grant.")
            seen.add(pair)
            existing = db.execute(
                "SELECT id,email FROM people WHERE id=? OR email=?", (grant.person, grant.email)
            ).fetchall()
            if any(row["id"] != grant.person or row["email"] != grant.email for row in existing):
                raise ConflictError("A grant identity conflicts with the current registry.")
            hub.ancestors(Scope(snapshot.workspace, grant.kind, grant.resource))


def preview_grants(
    hub: Sharing, actor: Actor, snapshot: GrantSnapshot, config: DeploymentConfig
) -> str:
    """Validate and bind an owner-visible preview to the exact sharing state."""
    with hub.locked():
        _validate(hub, actor, snapshot, config)
        return hashlib.sha256(snapshot.model_dump_json().encode()).hexdigest()


def restore_grants(
    hub: Sharing,
    actor: Actor,
    snapshot: GrantSnapshot,
    config: DeploymentConfig,
    expected_hash: str,
) -> int:
    """Apply exactly the approved human grants atomically after revalidation."""
    with hub.locked():
        actual = preview_grants(hub, actor, snapshot, config)
        if not secrets.compare_digest(actual, expected_hash):
            raise ConflictError("The sharing preview changed. Review it again.")
        with hub.connect() as db:
            for grant in snapshot.grants:
                db.execute("INSERT OR IGNORE INTO people VALUES (?,?)", (grant.person, grant.email))
                db.execute(
                    "INSERT INTO grants VALUES (?,?,?,?,?,?)",
                    (
                        secrets.token_hex(16),
                        snapshot.workspace,
                        grant.kind,
                        grant.resource,
                        grant.person,
                        grant.role,
                    ),
                )
    return len(snapshot.grants)


def staged_grants(hub: Sharing, workspace: str) -> StagedGrants:
    """Read only the fixed private state file created by a verified archive restore."""
    import os
    import stat

    from .workspace_recovery import MAX_METADATA_BYTES, STAGED_GRANTS

    path = hub.store(workspace).database.parent / STAGED_GRANTS
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise ValueError("The staged human grants file is unsafe.")
        data = stream.read(MAX_METADATA_BYTES + 1)
    if len(data) > MAX_METADATA_BYTES:
        raise ValueError("The staged human grants file is too large.")
    return StagedGrants.model_validate_json(data, strict=True)


def preview_staged_grants(
    hub: Sharing, actor: Actor, workspace: str, config: DeploymentConfig
) -> tuple[StagedGrants, str]:
    """Expose the fixed staging file to its owner and bind preview to archive bytes."""
    with hub.locked():
        if not hub.owner(actor, workspace):
            raise AccessDenied
        staged = staged_grants(hub, workspace)
        if staged.snapshot.workspace != workspace:
            raise ValueError("The staged grants belong to another workspace.")
        grant_hash = preview_grants(hub, actor, staged.snapshot, config)
        return staged, hashlib.sha256((staged.archive_sha256 + grant_hash).encode()).hexdigest()


def restore_staged_grants(
    hub: Sharing, actor: Actor, workspace: str, config: DeploymentConfig, expected_hash: str
) -> int:
    """Re-read exactly the reviewed fixed staged file before applying its grants."""
    with hub.locked():
        staged, current_hash = preview_staged_grants(hub, actor, workspace, config)
        if not secrets.compare_digest(current_hash, expected_hash):
            raise ConflictError("The sharing preview changed. Review it again.")
        grant_hash = preview_grants(hub, actor, staged.snapshot, config)
        return restore_grants(hub, actor, staged.snapshot, config, grant_hash)
