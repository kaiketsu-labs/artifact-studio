"""Durable, inherited sharing. Every operation uses an explicit workspace and actor."""

import fcntl
import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from .artifacts import ArtifactStore
from .authentication import Principal
from .storage import Store

Role = Literal["view", "comment", "edit"]
Kind = Literal["workspace", "project", "artifact"]
LEVEL = {"view": 1, "comment": 2, "edit": 3}
WORKSPACE_ID = re.compile(r"[0-9a-f]{24}")


class AccessDenied(FileNotFoundError):
    """Do not reveal whether an inaccessible object exists."""


def workspace_editor_path(workspace: str) -> str:
    """Return the native editor root for a registered workspace identifier."""
    if workspace == "default":
        return "/"
    if not WORKSPACE_ID.fullmatch(workspace):
        raise AccessDenied
    return f"/workspaces/{workspace}/"


@dataclass(frozen=True)
class Scope:
    workspace: str
    kind: Kind = "workspace"
    resource: str = ""


@dataclass(frozen=True)
class Actor:
    person: str
    agent: str = ""

    @property
    def key(self) -> str:
        return self.agent or self.person


@dataclass(frozen=True)
class ProjectMoveConfirmation:
    """Describe inherited access affected by moving an artifact."""

    access_hash: str
    source_shared: bool
    destination_shared: bool


def checksum(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def email_address(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("Enter an email address.")
    return value


class Sharing:
    """One private registry, independent stores, and linearizable access/write decisions.

    The file lock also coordinates multiple server processes. Local filesystem editors
    are trusted operators; do not run a second unscoped server against these collections.
    """

    def __init__(
        self,
        root: Path,
        default: Store,
        owner_email: str,
        *,
        personal_workspace_emails: tuple[str, ...] = (),
    ) -> None:
        self.root = root.resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.database = self.root / "sharing.sqlite3"
        self.owner_email = email_address(owner_email)
        self._personal_emails = frozenset(map(email_address, personal_workspace_emails))
        self._lock = threading.RLock()
        self._depth = threading.local()
        self._stores: dict[str, Store] = {"default": default}
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS people (
                  id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE);
                CREATE TABLE IF NOT EXISTS workspaces (
                  id TEXT PRIMARY KEY, title TEXT NOT NULL, owner TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS grants (
                  id TEXT PRIMARY KEY, workspace TEXT NOT NULL, kind TEXT NOT NULL,
                  resource TEXT NOT NULL, person TEXT NOT NULL, role TEXT NOT NULL,
                  UNIQUE(workspace,kind,resource,person));
                CREATE TABLE IF NOT EXISTS invitations (
                  id TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE, workspace TEXT NOT NULL,
                  kind TEXT NOT NULL, resource TEXT NOT NULL, email TEXT NOT NULL,
                  role TEXT NOT NULL, expires REAL NOT NULL, accepted INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS agents (
                  id TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE, person TEXT NOT NULL,
                  workspace TEXT NOT NULL, kind TEXT NOT NULL, resource TEXT NOT NULL,
                  name TEXT NOT NULL, expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
                  last_seen REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS preview_tickets (
                  token TEXT PRIMARY KEY, person TEXT NOT NULL, workspace TEXT NOT NULL,
                  artifact TEXT NOT NULL, revision INTEGER NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS opened_revisions (
                  workspace TEXT NOT NULL, person TEXT NOT NULL, artifact TEXT NOT NULL,
                  slide TEXT NOT NULL, content_hash TEXT NOT NULL,
                  PRIMARY KEY(workspace, person, artifact, slide));
            """)
        self.database.chmod(0o600)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def opened_revisions(
        self, actor: Actor, workspace: str, artifact: str = ""
    ) -> dict[str, dict[str, str]]:
        """Return only this human's opened revision hashes in the selected workspace."""
        if actor.agent:
            raise AccessDenied
        by_artifact: dict[str, dict[str, str]] = {}
        where = " AND artifact=?" if artifact else ""
        params = (workspace, actor.person, artifact) if artifact else (workspace, actor.person)
        with self.connect() as db:
            for row in db.execute(
                "SELECT artifact,slide,content_hash FROM opened_revisions "
                "WHERE workspace=? AND person=?" + where,
                params,
            ):
                by_artifact.setdefault(cast(str, row["artifact"]), {})[cast(str, row["slide"])] = (
                    cast(str, row["content_hash"])
                )
        return by_artifact

    def record_opened(self, actor: Actor, scope: Scope, slide: str, content_hash: str) -> None:
        """Persist an opened hash validated by the caller under the current grant lock."""
        if actor.agent:
            raise AccessDenied
        with self.connect() as db:
            db.execute(
                "INSERT INTO opened_revisions VALUES (?,?,?,?,?) "
                "ON CONFLICT(workspace,person,artifact,slide) DO UPDATE "
                "SET content_hash=excluded.content_hash "
                "WHERE opened_revisions.content_hash!=excluded.content_hash",
                (scope.workspace, actor.person, scope.resource, slide, content_hash),
            )

    @contextmanager
    def locked(self) -> Generator[None]:
        """Use the same lock for authorization, mutations and revocation."""
        with self._lock:
            depth = cast(int, getattr(self._depth, "value", 0))
            self._depth.value = depth + 1
            try:
                if depth:
                    yield
                else:
                    with (self.root / "access.lock").open("a+b") as lock:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                        yield
            finally:
                self._depth.value = depth

    def human(self, principal: Principal) -> Actor:
        """Bind a verified identity and provision its configured personal workspace once."""
        if principal.service or not principal.subject or not principal.email:
            raise AccessDenied
        email = email_address(principal.email)
        person = checksum(principal.subject)
        personal = email in self._personal_emails
        with self.connect() as db:
            existing = db.execute("SELECT email FROM people WHERE id=?", (person,)).fetchone()
            if (
                existing
                and existing["email"] == email
                and (not personal or self._owns_workspace(db, person))
            ):
                return Actor(person)
        with self.locked(), self.connect() as db:
            prior = db.execute("SELECT id FROM people WHERE email=?", (email,)).fetchone()
            if prior and prior["id"] != person:
                raise AccessDenied
            prior = db.execute("SELECT email FROM people WHERE id=?", (person,)).fetchone()
            if prior and prior["email"] != email:
                raise AccessDenied
            db.execute("INSERT OR IGNORE INTO people VALUES (?,?)", (person, email))
            if email == self.owner_email:
                db.execute(
                    "INSERT OR IGNORE INTO workspaces VALUES ('default','My workspace',?)",
                    (person,),
                )
            elif personal and not self._owns_workspace(db, person):
                db.execute(
                    "INSERT INTO workspaces VALUES (?, 'Personal workspace', ?)",
                    (secrets.token_hex(12), person),
                )
        return Actor(person)

    @staticmethod
    def _owns_workspace(db: sqlite3.Connection, person: str) -> bool:
        """Check for a workspace already provisioned to this identity."""
        return bool(db.execute("SELECT 1 FROM workspaces WHERE owner=?", (person,)).fetchone())

    def personal_home(self, actor: Actor) -> str | None:
        """Open the personal library when the default library would be empty."""
        if actor.agent:
            return None
        with self.connect() as db:
            row = db.execute(
                """SELECT id FROM workspaces
                   WHERE owner=? AND id<>'default'
                     AND NOT EXISTS (SELECT 1 FROM grants WHERE workspace='default' AND person=?)
                   ORDER BY id LIMIT 1""",
                (actor.person, actor.person),
            ).fetchone()
        return cast(str, row["id"]) if row else None

    def agent(self, token: str) -> Actor:
        if not 32 <= len(token) <= 200:
            raise AccessDenied
        with self.connect() as db:
            row = db.execute(
                "SELECT id,person FROM agents WHERE token=? AND revoked=0 AND expires>?",
                (checksum(token), time.time()),
            ).fetchone()
        if not row:
            raise AccessDenied
        return Actor(cast(str, row["person"]), cast(str, row["id"]))

    def agent_record(self, actor: Actor) -> sqlite3.Row:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM agents WHERE id=? AND person=? AND revoked=0 AND expires>?",
                (actor.agent, actor.person, time.time()),
            ).fetchone()
        if not row:
            raise AccessDenied
        return row

    def store(self, workspace: str) -> Store:
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM workspaces WHERE id=?", (workspace,)).fetchone():
                raise AccessDenied
        if workspace not in self._stores:
            path = self.root / "workspaces" / workspace
            self._stores[workspace] = Store(path / "collection", path / "state/review.sqlite3")
        return self._stores[workspace]

    def ancestors(self, scope: Scope) -> list[tuple[str, str]]:
        store = self.store(scope.workspace)
        ancestors: list[tuple[str, str]] = [("workspace", "")]
        if scope.kind == "workspace":
            if scope.resource:
                raise AccessDenied
        elif scope.kind == "project":
            if scope.resource not in {item.id for item in ArtifactStore(store).list_projects()}:
                raise AccessDenied
            ancestors.append(("project", scope.resource))
        elif scope.kind == "artifact":
            try:
                if (store.folder(scope.resource) / "design.json").exists():
                    raise AccessDenied
                item = ArtifactStore(store).get_artifact(scope.resource)
            except ValueError, FileNotFoundError:
                raise AccessDenied from None
            ancestors.extend([("project", item.project_id), ("artifact", item.id)])
        else:
            raise AccessDenied
        return ancestors

    def owner(self, actor: Actor, workspace: str) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT owner FROM workspaces WHERE id=?", (workspace,)).fetchone()
        return bool(row and row["owner"] == actor.person and not actor.agent)

    def has_workspace_entry(self, actor: Actor, workspace: str) -> bool:
        """Check whether a human owns or has been invited into the workspace."""
        if actor.agent:
            return False
        with self.connect() as db:
            return bool(
                db.execute(
                    "SELECT 1 FROM workspaces WHERE id=? AND "
                    "(owner=? OR EXISTS (SELECT 1 FROM grants "
                    "WHERE grants.workspace=workspaces.id AND person=?))",
                    (workspace, actor.person, actor.person),
                ).fetchone()
            )

    def sources(self, actor: Actor, scope: Scope) -> list[dict[str, str]]:
        ancestors = self.ancestors(scope)
        if actor.agent:
            connection = self.agent_record(actor)
            if (
                connection["workspace"] != scope.workspace
                or (connection["kind"], connection["resource"]) not in ancestors
            ):
                return []
        with self.connect() as db:
            workspace = db.execute(
                "SELECT owner FROM workspaces WHERE id=?", (scope.workspace,)
            ).fetchone()
            if workspace and workspace["owner"] == actor.person:
                return [{"kind": "workspace", "resource": "", "role": "edit", "id": "owner"}]
            rows = db.execute(
                "SELECT id,kind,resource,role FROM grants WHERE workspace=? AND person=?",
                (scope.workspace, actor.person),
            ).fetchall()
        return [
            {key: cast(str, row[key]) for key in ("id", "kind", "resource", "role")}
            for row in rows
            if (row["kind"], row["resource"]) in ancestors
        ]

    def level(self, actor: Actor, scope: Scope) -> int:
        return max((LEVEL[source["role"]] for source in self.sources(actor, scope)), default=0)

    def require(self, actor: Actor, scope: Scope, role: Role = "view") -> None:
        if self.level(actor, scope) < LEVEL[role]:
            raise AccessDenied

    @contextmanager
    def guard(self, actor: Actor, scope: Scope, role: Role = "view") -> Generator[Store]:
        with self.locked():
            self.require(actor, scope, role)
            yield self.store(scope.workspace)

    def manage(self, actor: Actor, scope: Scope) -> None:
        if not self.owner(actor, scope.workspace):
            raise AccessDenied
        self.ancestors(scope)

    def project_move_confirmation(
        self, workspace: str, artifact: str, source: str, destination: str, expected: str
    ) -> ProjectMoveConfirmation | None:
        """Check the project sharing state while the caller holds the collaboration lock."""
        if source == destination:
            return None
        params = (workspace, source, destination)
        now = time.time()
        with self.connect() as db:
            grants = db.execute(
                "SELECT resource,id,role FROM grants WHERE workspace=? AND kind='project' "
                "AND resource IN (?,?) ORDER BY id",
                params,
            ).fetchall()
            invites = db.execute(
                "SELECT resource,id FROM invitations WHERE workspace=? AND kind='project' "
                "AND resource IN (?,?) AND accepted=0 AND expires>? ORDER BY id",
                (*params, now),
            ).fetchall()
            agents = db.execute(
                "SELECT resource,id FROM agents WHERE workspace=? AND kind='project' "
                "AND resource IN (?,?) AND revoked=0 AND expires>? ORDER BY id",
                (*params, now),
            ).fetchall()
        if not (grants or invites or agents or expected):
            return None
        payload = [
            workspace,
            artifact,
            source,
            destination,
            [[list(row) for row in rows] for rows in (grants, invites, agents)],
        ]
        current = checksum(json.dumps(payload))
        if expected == current:
            return None
        shared = {cast(str, row["resource"]) for rows in (grants, invites, agents) for row in rows}
        return ProjectMoveConfirmation(current, source in shared, destination in shared)

    def create_workspace(self, actor: Actor, title: str) -> str:
        title = title.strip()
        if not title or len(title) > 160:
            raise ValueError("Use a workspace name of 1–160 characters.")
        with self.locked(), self.connect() as db:
            person = db.execute("SELECT email FROM people WHERE id=?", (actor.person,)).fetchone()
            if actor.agent or not person or person["email"] != self.owner_email:
                raise AccessDenied
            workspace = secrets.token_hex(12)
            db.execute("INSERT INTO workspaces VALUES (?,?,?)", (workspace, title, actor.person))
        return workspace

    def invite(self, actor: Actor, scope: Scope, email: str, role: Role) -> dict[str, str]:
        with self.locked(), self.connect() as db:
            self.manage(actor, scope)
            # A newer invitation supersedes older links for this recipient/scope.
            db.execute(
                "DELETE FROM invitations WHERE workspace=? AND kind=? AND resource=? AND email=?",
                (scope.workspace, scope.kind, scope.resource, email_address(email)),
            )
            token, identifier = secrets.token_urlsafe(32), secrets.token_hex(12)
            db.execute(
                "INSERT INTO invitations VALUES (?,?,?,?,?,?,?,?,0)",
                (
                    identifier,
                    checksum(token),
                    scope.workspace,
                    scope.kind,
                    scope.resource,
                    email_address(email),
                    role,
                    time.time() + 7 * 86400,
                ),
            )
        return {"id": identifier, "token": token}

    def accept(self, actor: Actor, token: str) -> Scope:
        with self.locked(), self.connect() as db:
            if actor.agent:
                raise AccessDenied
            row = db.execute(
                "SELECT * FROM invitations WHERE token=? AND expires>? AND accepted=0",
                (checksum(token), time.time()),
            ).fetchone()
            person = db.execute("SELECT email FROM people WHERE id=?", (actor.person,)).fetchone()
            if not row or not person or row["email"] != person["email"]:
                raise AccessDenied
            scope = Scope(
                cast(str, row["workspace"]), cast(Kind, row["kind"]), cast(str, row["resource"])
            )
            self.ancestors(scope)
            db.execute(
                "INSERT INTO grants VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(workspace,kind,resource,person)"
                " DO UPDATE SET role=excluded.role",
                (
                    secrets.token_hex(12),
                    scope.workspace,
                    scope.kind,
                    scope.resource,
                    actor.person,
                    row["role"],
                ),
            )
            db.execute("UPDATE invitations SET accepted=1 WHERE id=?", (row["id"],))
        return scope

    def access(self, actor: Actor, scope: Scope) -> dict[str, object]:
        with self.locked(), self.connect() as db:
            self.require(actor, scope)
            sources = self.sources(actor, scope)
            result: dict[str, object] = {
                "sources": sources,
                "can_manage": self.owner(actor, scope.workspace),
                "role": max((item["role"] for item in sources), key=lambda r: LEVEL[r]),
            }
            if not result["can_manage"]:
                return result
            ancestors = self.ancestors(scope)
            grants = db.execute(
                "SELECT g.*,p.email FROM grants g JOIN people p ON p.id=g.person WHERE workspace=?",
                (scope.workspace,),
            ).fetchall()
            result["grants"] = [
                {
                    **dict(row),
                    "inherited": (row["kind"], row["resource"]) != (scope.kind, scope.resource),
                }
                for row in grants
                if (row["kind"], row["resource"]) in ancestors
            ]
            result["invitations"] = [
                dict(row)
                for row in db.execute(
                    "SELECT id,email,role,expires FROM invitations WHERE workspace=? AND kind=? "
                    "AND resource=? AND accepted=0 AND expires>?",
                    (scope.workspace, scope.kind, scope.resource, time.time()),
                )
            ]
            return result

    def revoke(self, actor: Actor, scope: Scope, identifier: str) -> None:
        with self.locked(), self.connect() as db:
            self.manage(actor, scope)
            grant = db.execute(
                "SELECT p.email FROM grants g JOIN people p ON p.id=g.person "
                "WHERE g.id=? AND g.workspace=? AND g.kind=? AND g.resource=?",
                (identifier, scope.workspace, scope.kind, scope.resource),
            ).fetchone()
            if grant:
                db.execute(
                    "DELETE FROM invitations WHERE workspace=? AND kind=? "
                    "AND resource=? AND email=?",
                    (scope.workspace, scope.kind, scope.resource, grant["email"]),
                )
            for table in ("grants", "invitations"):
                db.execute(
                    f"DELETE FROM {table} WHERE id=? AND workspace=? AND kind=? AND resource=?",
                    (identifier, scope.workspace, scope.kind, scope.resource),
                )

    def connect_agent(self, actor: Actor, scope: Scope, name: str) -> dict[str, str]:
        name = name.strip()
        if not name or len(name) > 80:
            raise ValueError("Use an agent name of 1–80 characters.")
        with self.guard(actor, scope), self.connect() as db:
            if actor.agent:
                raise AccessDenied
            token, identifier = secrets.token_urlsafe(32), secrets.token_hex(12)
            db.execute(
                "INSERT INTO agents (id,token,person,workspace,kind,resource,name,expires) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    checksum(token),
                    actor.person,
                    scope.workspace,
                    scope.kind,
                    scope.resource,
                    name,
                    time.time() + 30 * 86400,
                ),
            )
        return {"id": identifier, "token": token, "name": name}

    def own_agents(self, actor: Actor, workspace: str) -> list[dict[str, object]]:
        if actor.agent:
            raise AccessDenied
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,name,kind,resource,expires,last_seen FROM agents "
                    "WHERE person=? AND workspace=? AND revoked=0 AND expires>?",
                    (actor.person, workspace, time.time()),
                )
            ]

    def revoke_agent(self, actor: Actor, identifier: str) -> None:
        if actor.agent:
            raise AccessDenied
        with self.locked(), self.connect() as db:
            db.execute(
                "UPDATE agents SET revoked=1 WHERE id=? AND person=?", (identifier, actor.person)
            )

    def request_id(self, actor: Actor, request_id: str) -> str:
        if not 8 <= len(request_id) <= 100:
            raise ValueError("Use a request_id of 8–100 characters.")
        return checksum(actor.key + ":" + request_id)
