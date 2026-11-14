"""Opt-in admission provisioning; Studio grants remain the resource authority.

An explicitly selected adapter may reconcile membership of a dedicated
Cloudflare admission group. It must not edit application policies or MFA rules.
No external account mutation is enabled by default.
"""

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Protocol, cast
from urllib.parse import urlencode

from .cloudflare_auth import CloudflareAuthProvider
from .deployment import DeploymentConfig
from .multiplayer_access import (
    AccessDenied,
    Actor,
    Scope,
    Sharing,
    email_address,
    workspace_editor_path,
)


@dataclass(frozen=True)
class AdmissionContext:
    """Public identity configuration; never contains provider credentials."""

    issuer: str
    audience: str
    public_url: str

    @classmethod
    def from_config(cls, config: DeploymentConfig) -> AdmissionContext:
        if config.mode != "hosted" or config.auth_provider != "cloudflare":
            raise ValueError("Guest provisioning requires the configured Cloudflare identity.")
        provider = CloudflareAuthProvider(config.auth_options or {})
        return cls(provider.issuer, provider.audience, config.public_url)


class AdmissionAdapter(Protocol):
    """Trusted operator extension for one existing MFA-protected admission group.

    Reconcile must set this email's membership idempotently, including after a lost
    response, and return only after the provider confirms it. It must honor the
    timeout, preserve other members and never change policy/MFA configuration.
    Credentials belong in the adapter's operator secret store, not this database.
    """

    namespace: str

    def reconcile(
        self, email: str, eligible: bool, operation_id: str, *, timeout: float
    ) -> None: ...


def invitation_destination(scope: Scope, *, unified: bool = True) -> str:
    """Derive a local destination solely from the accepted invitation's scope."""
    root = workspace_editor_path(scope.workspace)
    if not unified and scope.workspace != "default":
        query = {"workspace": scope.workspace}
        if scope.kind != "workspace":
            query[scope.kind] = scope.resource
        return "/?" + urlencode(query)
    return (
        root if scope.kind == "workspace" else root + "?" + urlencode({scope.kind: scope.resource})
    )


class InvitationProvisioning:
    """Persist desired admission with invitation mutations and retry safely."""

    def __init__(
        self, sharing: Sharing, config: DeploymentConfig, adapter: AdmissionAdapter
    ) -> None:
        self.sharing = sharing
        context = AdmissionContext.from_config(config)
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,199}", adapter.namespace):
            raise ValueError("Use a stable, nonsecret admission-group namespace.")
        self.adapter = adapter
        self.protected = frozenset(
            email_address(value)
            for value in (*config.member_emails, *config.personal_workspace_emails)
        )
        self.binding = hashlib.sha256(
            json.dumps(
                [context.issuer, context.audience, context.public_url, adapter.namespace]
            ).encode()
        ).hexdigest()
        with sharing.locked(), sharing.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS guest_admission_binding (
                  id INTEGER PRIMARY KEY CHECK(id=1), binding TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS guest_admission (
                  email TEXT PRIMARY KEY, eligible INTEGER NOT NULL, generation INTEGER NOT NULL,
                  state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                  retry_at REAL NOT NULL DEFAULT 0, updated REAL NOT NULL);
            """)
            prior = db.execute("SELECT binding FROM guest_admission_binding WHERE id=1").fetchone()
            if prior and prior[0] != self.binding:
                raise ValueError(
                    "Guest admission identity or provider binding changed; reconcile separately."
                )
            db.execute(
                "INSERT OR IGNORE INTO guest_admission_binding VALUES (1,?)", (self.binding,)
            )
            emails = db.execute(
                "SELECT email FROM invitations UNION SELECT p.email FROM grants g "
                "JOIN people p ON p.id=g.person UNION SELECT email FROM guest_admission"
            ).fetchall()
            for row in emails:
                self.changed(db, cast(str, row[0]))
        sharing.invitation_provisioning = self

    @classmethod
    def from_env(cls, sharing: Sharing, config: DeploymentConfig) -> InvitationProvisioning | None:
        if os.environ.get("ARTIFACT_STUDIO_EXPERIMENTAL_INVITATIONS") != "1":
            return None
        name = os.environ.get("ARTIFACT_STUDIO_INVITATION_PROVISIONER", "")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", name):
            raise ValueError(
                "Select an installed invitation provisioner before enabling admission."
            )
        context = AdmissionContext.from_config(config)
        if name == "cloudflare-group":
            from .cloudflare_admission import CloudflareGroupAdmission

            return cls(sharing, config, CloudflareGroupAdmission.from_env(context))
        matches = entry_points(group="artifact_studio.admission", name=name)
        if len(matches) != 1:
            raise ValueError("The selected invitation provisioner is not installed.")
        adapter = cast(AdmissionAdapter, next(iter(matches)).load()(context))
        return cls(sharing, config, adapter)

    def _eligible(self, db: sqlite3.Connection, email: str) -> bool:
        if email in self.protected:
            return True
        return bool(
            db.execute(
                "SELECT 1 FROM invitations WHERE email=? AND accepted=0 "
                "AND (expires=0 OR expires>?) UNION ALL "
                "SELECT 1 FROM grants g JOIN people p ON p.id=g.person WHERE p.email=? UNION ALL "
                "SELECT 1 FROM workspaces w JOIN people p ON p.id=w.owner WHERE p.email=? LIMIT 1",
                (email, time.time(), email, email),
            ).fetchone()
        )

    def changed(self, db: sqlite3.Connection, email: str) -> None:
        """Enqueue in the same transaction as the invitation/grant mutation."""
        eligible = self._eligible(db, email)
        db.execute(
            "INSERT INTO guest_admission(email,eligible,generation,state,updated) "
            "VALUES (?,?,1,'pending',?) "
            "ON CONFLICT(email) DO UPDATE SET eligible=excluded.eligible, "
            "generation=guest_admission.generation+1,state='pending',attempts=0,retry_at=0,"
            "updated=excluded.updated "
            "WHERE guest_admission.eligible!=excluded.eligible",
            (email, eligible, time.time()),
        )

    def require_human(self, email: str) -> None:
        """Reject uninvited identities even if their provider session is still valid."""
        with self.sharing.locked(), self.sharing.connect() as db:
            if not self._eligible(db, email):
                raise AccessDenied

    def _workspace_emails(self, db: sqlite3.Connection, workspace: str) -> set[str]:
        return {
            cast(str, row[0])
            for row in db.execute(
                "SELECT email FROM invitations WHERE workspace=? UNION "
                "SELECT p.email FROM grants g JOIN people p ON p.id=g.person WHERE g.workspace=?",
                (workspace, workspace),
            )
        }

    def status(self, actor: Actor, workspace: str) -> list[dict[str, object]]:
        """Expose only recipients already visible to this workspace's human owner."""
        with self.sharing.locked(), self.sharing.connect() as db:
            self.sharing.manage(actor, Scope(workspace))
            emails = self._workspace_emails(db, workspace)
            return [
                {"email": row["email"], "state": row["state"], "updated": row["updated"]}
                for row in db.execute(
                    "SELECT email,state,updated FROM guest_admission ORDER BY email"
                )
                if row["email"] in emails
            ]

    def retry(self, actor: Actor, workspace: str, email: str) -> None:
        with self.sharing.locked(), self.sharing.connect() as db:
            self.sharing.manage(actor, Scope(workspace))
            email = email_address(email)
            if email not in self._workspace_emails(db, workspace):
                raise AccessDenied
            self.changed(db, email)
            db.execute(
                "UPDATE guest_admission SET retry_at=0 WHERE email=? AND state='error'", (email,)
            )

    def tick(self) -> None:
        """Refresh expiry and deliver bounded operations under the grant mutation lock.

        Keeping this lock through reconciliation prevents eligibility removal from
        racing an overlapping new grant/invitation. Adapter calls are bounded to 10s.
        """
        with self.sharing.locked(), self.sharing.connect() as db:
            for row in db.execute(
                "SELECT email FROM guest_admission UNION SELECT email FROM invitations "
                "UNION SELECT p.email FROM grants g JOIN people p ON p.id=g.person"
            ).fetchall():
                self.changed(db, cast(str, row[0]))
        # Persist generation changes before external effects; a crash retries the same key.
        with self.sharing.connect() as db:
            emails = [
                cast(str, row[0])
                for row in db.execute(
                    "SELECT email FROM guest_admission WHERE state IN ('pending','error') "
                    "AND retry_at<=? ORDER BY updated LIMIT 20",
                    (time.time(),),
                )
            ]
        for email in emails:
            with self.sharing.locked(), self.sharing.connect() as db:
                self.changed(db, email)
            with self.sharing.locked(), self.sharing.connect() as db:
                row = db.execute("SELECT * FROM guest_admission WHERE email=?", (email,)).fetchone()
                if (
                    not row
                    or row["state"] not in {"pending", "error"}
                    or row["retry_at"] > time.time()
                ):
                    continue
                operation = hashlib.sha256(
                    json.dumps(
                        [self.binding, email, row["generation"], bool(row["eligible"])]
                    ).encode()
                ).hexdigest()
                try:
                    self.adapter.reconcile(email, bool(row["eligible"]), operation, timeout=10)
                except Exception:
                    # Provider exceptions can contain tokens or response bodies; never persist them.
                    attempts = int(row["attempts"]) + 1
                    db.execute(
                        "UPDATE guest_admission SET state='error',attempts=?,retry_at=?,updated=? "
                        "WHERE email=?",
                        (
                            attempts,
                            time.time() + min(300, 2 ** min(attempts, 8)),
                            time.time(),
                            email,
                        ),
                    )
                else:
                    db.execute(
                        "UPDATE guest_admission SET state=?,attempts=0,retry_at=0,updated=? "
                        "WHERE email=?",
                        ("active" if row["eligible"] else "inactive", time.time(), email),
                    )

    @asynccontextmanager
    async def worker(self) -> AsyncGenerator[None]:
        async def poll() -> None:
            while True:
                await asyncio.to_thread(self.tick)
                await asyncio.sleep(15)

        task = asyncio.create_task(poll())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
