"""Per-person conversations and explicit, retryable delivery to one connected agent."""

import json
import re
import secrets
import shutil
import sqlite3
import time
from hashlib import sha256
from typing import Literal, TypedDict, cast
from urllib.parse import quote

from pydantic import Field

from .artifact_models import ArtifactTarget
from .artifacts import ArtifactStore
from .element_alternatives import ElementAlternatives
from .models import FileInput, StrictModel
from .multiplayer_access import LEVEL, AccessDenied, Actor, Scope, Sharing, checksum
from .review import ReviewHookInput
from .slide_designs import SlideDesigns
from .storage import ConflictError, Store, decode_attachments

Channel = Literal["shared", "private"]
CollaboratorRequestMode = Literal["off", "direct", "suggest"]
_WORKSPACE_FILE_LIMIT = 1024**3
_HOST_FILE_LIMIT = 4 * 1024**3
_MINIMUM_DISK_FREE = 1024**3


class ChatProposal(StrictModel):
    """Identify the immutable proposal displayed while writing a comment."""

    kind: Literal["design", "element"]
    option_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    context_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def proposal_anchor(
    store: Store, deck_id: str, slide_id: str, proposal: ChatProposal, source_hash: str
) -> dict[str, object]:
    """Verify the frozen source and resolve its label without accepting client display data."""
    if proposal.kind == "design":
        if proposal.context_hash is not None:
            raise ValueError("Only element alternatives have a separate preview context.")
        option = SlideDesigns(store, scoped=True).read(deck_id, slide_id, proposal.option_id).option
        context_hash = None
    else:
        source = ElementAlternatives(store, scoped=True).read(
            deck_id, slide_id, proposal.option_id, proposal.context_hash
        )
        option, context_hash = source.option, source.context_hash
    if option.source_hash != source_hash:
        raise ConflictError("The comment must reference the reviewed proposal source.")
    return {
        **proposal.model_dump(),
        "name": option.name,
        "source_hash": source_hash,
        "context_hash": context_hash,
    }


class ChatFile(TypedDict):
    """Attachment metadata exposed with a visible conversation message."""

    name: str
    size: int
    sha256: str
    index: int


def chat_file_url(workspace: str, artifact: str, event: int, index: int) -> str:
    """Build a download URL on the authenticated workspace origin."""
    root = f"/api/workspaces/{quote(workspace, safe='')}/artifacts/{quote(artifact, safe='')}"
    return f"{root}/chat/{event}/files/{index}"


class Conversations:
    def __init__(self, sharing: Sharing) -> None:
        self.sharing = sharing
        with sharing.locked(), sharing.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, workspace TEXT NOT NULL,
                  artifact TEXT NOT NULL, channel TEXT NOT NULL, person TEXT NOT NULL,
                  author TEXT NOT NULL, message TEXT NOT NULL, target TEXT NOT NULL,
                  content_hash TEXT NOT NULL, assigned TEXT NOT NULL DEFAULT '',
                  released INTEGER NOT NULL, reply_to INTEGER,
                  request_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
                  created REAL NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
                  include_shared_context INTEGER NOT NULL DEFAULT 0,
                  claim TEXT NOT NULL DEFAULT '', claimant TEXT NOT NULL DEFAULT '',
                  claim_expires REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS chat_attachments (
                  message INTEGER NOT NULL, file_index INTEGER NOT NULL, content BLOB NOT NULL,
                  PRIMARY KEY(message,file_index));
                CREATE TABLE IF NOT EXISTS chat_attachment_usage (
                  workspace TEXT PRIMARY KEY, bytes INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS chat_mentions (
                  message INTEGER NOT NULL, person TEXT NOT NULL, seen INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY(message,person));
                CREATE INDEX IF NOT EXISTS chat_mentions_unread
                  ON chat_mentions(person,message) WHERE seen=0;
                CREATE TABLE IF NOT EXISTS agent_shared_history (
                  workspace TEXT NOT NULL, artifact TEXT NOT NULL, agent TEXT NOT NULL,
                  PRIMARY KEY(workspace,artifact,agent));
                CREATE TABLE IF NOT EXISTS agent_collaborator_requests (
                  workspace TEXT NOT NULL, artifact TEXT NOT NULL, agent TEXT NOT NULL,
                  mode TEXT NOT NULL, generation INTEGER NOT NULL,
                  PRIMARY KEY(workspace,artifact,agent));
                CREATE INDEX IF NOT EXISTS conversation_scope
                  ON conversations(workspace,artifact,id);
                CREATE TABLE IF NOT EXISTS chat_delivery (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, message INTEGER NOT NULL UNIQUE);
                INSERT OR IGNORE INTO chat_delivery(message)
                  SELECT id FROM conversations ORDER BY id;
                CREATE TRIGGER IF NOT EXISTS chat_created AFTER INSERT ON conversations BEGIN
                  INSERT INTO chat_delivery(message) VALUES (new.id);
                END;
                CREATE TRIGGER IF NOT EXISTS chat_released AFTER UPDATE OF released ON conversations
                  WHEN old.released=0 AND new.released=1 BEGIN
                  DELETE FROM chat_delivery WHERE message=new.id;
                  INSERT INTO chat_delivery(message) VALUES (new.id);
                END;
                CREATE TABLE IF NOT EXISTS multiplayer_hooks (
                  agent TEXT NOT NULL, session TEXT NOT NULL, workspace TEXT NOT NULL,
                  artifact TEXT NOT NULL, expires REAL NOT NULL,
                  PRIMARY KEY(agent,session));
            """)
            if not any(
                row["name"] == "files" for row in db.execute("PRAGMA table_info(conversations)")
            ):
                _ = db.execute(
                    "ALTER TABLE conversations ADD COLUMN files TEXT NOT NULL DEFAULT '[]'"
                )

            if not any(
                row["name"] == "proposal" for row in db.execute("PRAGMA table_info(conversations)")
            ):
                _ = db.execute(
                    "ALTER TABLE conversations ADD COLUMN proposal TEXT NOT NULL DEFAULT 'null'"
                )
            if not any(
                row["name"] == "slide_content_hash"
                for row in db.execute("PRAGMA table_info(conversations)")
            ):
                _ = db.execute(
                    "ALTER TABLE conversations ADD COLUMN slide_content_hash "
                    "TEXT NOT NULL DEFAULT ''"
                )
            if not any(
                row["name"] == "include_shared_context"
                for row in db.execute("PRAGMA table_info(conversations)")
            ):
                _ = db.execute(
                    "ALTER TABLE conversations ADD COLUMN include_shared_context "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            if not any(
                row["name"] == "collaborator_request_generation"
                for row in db.execute("PRAGMA table_info(conversations)")
            ):
                _ = db.execute(
                    "ALTER TABLE conversations ADD COLUMN collaborator_request_generation "
                    "INTEGER NOT NULL DEFAULT 0"
                )
                _ = db.execute(
                    "ALTER TABLE conversations ADD COLUMN collaborator_request_mode "
                    "TEXT NOT NULL DEFAULT 'off'"
                )

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, object]:
        files = cast(list[ChatFile], json.loads(cast(str, row["files"])))
        workspace, artifact = cast(str, row["workspace"]), cast(str, row["artifact"])
        event = cast(int, row["id"])
        public: dict[str, object] = {
            key: cast(object, row[key])
            for key in (
                "id",
                "channel",
                "person",
                "author",
                "message",
                "content_hash",
                "slide_content_hash",
                "assigned",
                "released",
                "reply_to",
                "created",
                "completed",
            )
        }
        if row["proposal"] != "null":
            public["proposal"] = json.loads(cast(str, row["proposal"]))
        if row["collaborator_request_generation"]:
            public["collaborator_request_mode"] = row["collaborator_request_mode"]
        public.update(
            {
                "target": json.loads(cast(str, row["target"])),
                "cursor": dict(row).get("cursor", row["id"]),
                "files": [
                    dict(
                        file, download_url=chat_file_url(workspace, artifact, event, file["index"])
                    )
                    for file in files
                ],
            }
        )
        return public

    @staticmethod
    def _collaborator_setting(
        db: sqlite3.Connection, scope: Scope, agent: str
    ) -> tuple[CollaboratorRequestMode, int]:
        """Read the mode and generation for this presentation connection."""
        row = db.execute(
            "SELECT mode,generation FROM agent_collaborator_requests "
            "WHERE workspace=? AND artifact=? AND agent=?",
            (scope.workspace, scope.resource, agent),
        ).fetchone()
        if row is None:
            return "off", 0
        return cast(CollaboratorRequestMode, row["mode"]), cast(int, row["generation"])

    def collaborator_requests(
        self, actor: Actor, scope: Scope, agent: str, mode: CollaboratorRequestMode | None = None
    ) -> CollaboratorRequestMode:
        """Manage how other editors can address a human's own presentation agent."""
        with self.sharing.guard(actor, scope, "edit") as store, self.sharing.connect() as db:
            if (
                actor.agent
                or ArtifactStore(store).get_artifact(scope.resource).legacy_deck_id is None
            ):
                raise AccessDenied
            self.sharing.require(Actor(actor.person, agent), scope, "edit")
            current, generation = self._collaborator_setting(db, scope, agent)
            if mode is None or mode == current:
                return current
            _ = db.execute(
                "INSERT INTO agent_collaborator_requests VALUES (?,?,?,?,?) "
                "ON CONFLICT(workspace,artifact,agent) DO UPDATE "
                "SET mode=excluded.mode,generation=excluded.generation",
                (scope.workspace, scope.resource, agent, mode, generation + 1),
            )
            return mode

    def _request_is_current(
        self, db: sqlite3.Connection, scope: Scope, recipient: Actor, row: sqlite3.Row
    ) -> bool:
        """Check the sender's current access and the consent that authorized delivery."""
        sender = Actor(cast(str, row["person"]))
        if self.sharing.level(sender, scope) < LEVEL["edit"]:
            return False
        if sender.person == recipient.person:
            return True
        mode, generation = self._collaborator_setting(db, scope, recipient.agent)
        return (
            row["channel"] == "shared"
            and mode != "off"
            and generation == row["collaborator_request_generation"]
        )

    def require_direct_edit(self, actor: Actor, scope: Scope) -> None:
        """Guard canonical presentation writes while holding the sharing lock."""
        with self.sharing.connect() as db:
            mode, _ = self._collaborator_setting(db, scope, actor.agent)
        if mode == "suggest":
            raise ConflictError(
                "This connection can only suggest changes on this presentation. "
                "Use a slide, element, or structure proposal tool."
            )

    @staticmethod
    def _visible(actor: Actor, row: sqlite3.Row) -> bool:
        return bool(
            (row["channel"] == "shared" or row["person"] == actor.person)
            and (row["released"] or (not actor.agent and row["person"] == actor.person))
        )

    def history(
        self, actor: Actor, scope: Scope, channel: Channel, before: int = 0
    ) -> list[dict[str, object]]:
        with self.sharing.guard(actor, scope) as store, self.sharing.connect() as db:
            restricted = self._restrict_shared_agent(actor, scope, store, db, channel)
            rows = db.execute(
                "SELECT c.*,p.email AS author_name,d.id AS cursor, "
                "EXISTS(SELECT 1 FROM chat_mentions m WHERE m.message=c.id AND m.person=?) "
                "AS mentioned FROM conversations c "
                "JOIN people p ON p.id=c.person JOIN chat_delivery d ON d.message=c.id "
                "WHERE workspace=? AND artifact=? AND channel=? "
                "AND (?=0 OR d.id<?) AND (channel='shared' OR person=?) "
                "AND (released=1 OR (person=? AND ?=0)) "
                "AND (?=0 OR c.assigned=? OR c.author=?) ORDER BY d.id DESC LIMIT 100",
                (
                    actor.person,
                    scope.workspace,
                    scope.resource,
                    channel,
                    before,
                    before,
                    actor.person,
                    actor.person,
                    bool(actor.agent),
                    restricted,
                    actor.agent,
                    actor.agent,
                ),
            ).fetchall()
            return [
                self._public(row)
                | {"author_name": row["author_name"], "mentioned": bool(row["mentioned"])}
                for row in reversed(rows)
            ]

    def _restrict_shared_agent(
        self, actor: Actor, scope: Scope, store: Store, db: sqlite3.Connection, channel: Channel
    ) -> bool:
        """Limit presentation discussion to the agent's own requests and replies by default."""
        if not actor.agent or channel != "shared":
            return False
        if ArtifactStore(store).get_artifact(scope.resource).legacy_deck_id is None:
            return False
        return (
            db.execute(
                "SELECT 1 FROM agent_shared_history WHERE workspace=? AND artifact=? AND agent=?",
                (scope.workspace, scope.resource, actor.agent),
            ).fetchone()
            is None
        )

    def shared_history_access(
        self, actor: Actor, scope: Scope, agent_id: str, enabled: bool | None = None
    ) -> bool:
        """Read or change one owner's agent access to one presentation discussion."""
        with self.sharing.guard(actor, scope, "edit") as store, self.sharing.connect() as db:
            self.sharing.manage(actor, scope)
            if ArtifactStore(store).get_artifact(scope.resource).legacy_deck_id is None:
                raise AccessDenied
            connection = Actor(actor.person, agent_id)
            self.sharing.require(connection, scope)
            key = (scope.workspace, scope.resource, agent_id)
            if enabled is True:
                _ = db.execute("INSERT OR IGNORE INTO agent_shared_history VALUES (?,?,?)", key)
            elif enabled is False:
                _ = db.execute(
                    "DELETE FROM agent_shared_history WHERE workspace=? AND artifact=? AND agent=?",
                    key,
                )
            return (
                db.execute(
                    "SELECT 1 FROM agent_shared_history "
                    "WHERE workspace=? AND artifact=? AND agent=?",
                    key,
                ).fetchone()
                is not None
            )

    def contacts(self, actor: Actor, scope: Scope) -> list[dict[str, str]]:
        """List only verified people who currently have access to this artifact."""
        if actor.agent:
            raise AccessDenied
        with self.sharing.guard(actor, scope):
            return [
                {"id": person, "email": email}
                for person, email in sorted(self.sharing.human_contacts(scope).items())
                if person != actor.person
            ]

    def incoming_cursors(self, actor: Actor, scope: Scope) -> dict[str, dict[str, int]]:
        """Return the latest visible incoming message for each conversation and slide."""
        if actor.agent:
            raise AccessDenied
        with self.sharing.guard(actor, scope), self.sharing.connect() as db:
            rows = db.execute(
                "SELECT c.channel, COALESCE(json_extract(c.target, '$.slide_id'), '') AS slide, "
                "MAX(d.id) AS cursor FROM conversations c JOIN chat_delivery d ON d.message=c.id "
                "WHERE c.workspace=? AND c.artifact=? AND c.released=1 AND c.author<>? "
                "AND (c.channel='shared' OR c.person=?) GROUP BY c.channel, slide",
                (scope.workspace, scope.resource, actor.person, actor.person),
            )
            result: dict[str, dict[str, int]] = {"shared": {}, "private": {}}
            for row in rows:
                result[cast(str, row["channel"])][cast(str, row["slide"])] = cast(
                    int, row["cursor"]
                )
            return result

    def mentions(self, actor: Actor, scope: Scope) -> list[dict[str, object]]:
        """Return released unread mentions for this person in this artifact."""
        if actor.agent:
            raise AccessDenied
        with self.sharing.guard(actor, scope), self.sharing.connect() as db:
            rows = db.execute(
                "SELECT c.id,c.target FROM chat_mentions m JOIN conversations c ON c.id=m.message "
                "WHERE m.person=? AND m.seen=0 AND c.workspace=? AND c.artifact=? "
                "AND c.channel='shared' AND c.released=1 ORDER BY c.id",
                (actor.person, scope.workspace, scope.resource),
            ).fetchall()
            return [{"id": row["id"], "target": json.loads(row["target"])} for row in rows]

    def read_mention(self, actor: Actor, scope: Scope, event: int) -> None:
        """Acknowledge only a visible mention addressed to this person."""
        if actor.agent:
            raise AccessDenied
        with self.sharing.guard(actor, scope), self.sharing.connect() as db:
            _ = db.execute(
                "UPDATE chat_mentions SET seen=1 WHERE person=? AND message IN "
                "(SELECT id FROM conversations WHERE id=? AND workspace=? AND artifact=? "
                "AND channel='shared' AND released=1)",
                (actor.person, event, scope.workspace, scope.resource),
            )

    def _reserve_attachments(self, db: sqlite3.Connection, workspace: str, total: int) -> None:
        """Reserve bounded storage in the same transaction as the message."""
        if not total:
            return
        row = db.execute(
            "SELECT COALESCE(SUM(bytes),0) AS used, "
            "COALESCE(SUM(CASE WHEN workspace=? THEN bytes END),0) AS workspace_used "
            "FROM chat_attachment_usage",
            (workspace,),
        ).fetchone()
        assert row is not None
        if (
            cast(int, row["workspace_used"]) + total > _WORKSPACE_FILE_LIMIT
            or cast(int, row["used"]) + total > _HOST_FILE_LIMIT
            or shutil.disk_usage(self.sharing.root).free < _MINIMUM_DISK_FREE + 2 * total
        ):
            raise ConflictError("Shared chat storage is full.")
        _ = db.execute(
            "INSERT INTO chat_attachment_usage VALUES (?,?) ON CONFLICT(workspace) "
            "DO UPDATE SET bytes=bytes+excluded.bytes",
            (workspace, total),
        )

    def post(
        self,
        actor: Actor,
        scope: Scope,
        channel: Channel,
        message: str,
        request_id: str,
        assigned: str = "",
        released: bool = True,
        target: ArtifactTarget | None = None,
        content_hash: str = "",
        files: list[FileInput] | None = None,
        *,
        slide_content_hash: str = "",
        proposal: ChatProposal | None = None,
        include_shared_context: bool = False,
        offline_anchor: tuple[str, str, str] | None = None,
        collaborator_request_generation: int = 0,
    ) -> dict[str, object]:
        if (not message.strip() and not files) or len(message) > 10000:
            raise ValueError("Write a message or attach a file.")
        if include_shared_context and (channel != "shared" or not assigned):
            raise ValueError("Shared context requires an assigned Shared request.")
        with self.sharing.guard(actor, scope, "edit" if assigned else "comment") as store:
            if actor.agent:
                raise AccessDenied
            item = ArtifactStore(store).get_artifact(scope.resource)
            attachments = decode_attachments(files or [])
            metadata: list[ChatFile] = [
                {
                    "name": name,
                    "size": len(data),
                    "sha256": sha256(data).hexdigest(),
                    "index": index,
                }
                for index, (name, data) in enumerate(attachments)
            ]
            operation = self.sharing.request_id(actor, request_id)
            payload = checksum(
                json.dumps(
                    [
                        scope.workspace,
                        scope.resource,
                        channel,
                        message,
                        assigned,
                        released,
                        target.model_dump() if target else None,
                        content_hash,
                        *([metadata] if metadata else []),
                        *([proposal.model_dump(), slide_content_hash] if proposal else []),
                        *(["include_shared_context"] if include_shared_context else []),
                        *([list(offline_anchor)] if offline_anchor else []),
                        *(
                            [collaborator_request_generation]
                            if collaborator_request_generation
                            else []
                        ),
                    ],
                    sort_keys=True,
                )
            )
            with self.sharing.connect() as db:
                prior = db.execute(
                    "SELECT c.*,d.id AS cursor FROM conversations c "
                    "JOIN chat_delivery d ON d.message=c.id WHERE request_id=?",
                    (operation,),
                ).fetchone()
                if prior:
                    if prior["payload"] != payload:
                        raise ConflictError("This request_id already belongs to another message.")
                    return self._public(prior)
                if offline_anchor and (
                    proposal or offline_anchor[1:] != (content_hash, slide_content_hash)
                ):
                    raise ValueError("Invalid offline anchor.")
                if (
                    not offline_anchor
                    and not proposal
                    and content_hash
                    and content_hash != item.content_hash
                ):
                    raise ConflictError("The artifact changed. Review the current version first.")
                slide_id = target.slide_id if target else None
                version = item.presentation_versions.get(slide_id) if slide_id else None
                if slide_id and item.legacy_deck_id and version is None:
                    raise ConflictError(
                        "This slide was removed. Choose another slide to send your message."
                    )
                anchor = None
                if proposal:
                    if (
                        not item.legacy_deck_id
                        or not target
                        or target.kind != "slide"
                        or not slide_id
                    ):
                        raise ValueError("A proposal comment must reference a presentation slide.")
                    anchor = proposal_anchor(
                        store, item.legacy_deck_id, slide_id, proposal, slide_content_hash
                    )
                elif (
                    not offline_anchor
                    and slide_content_hash
                    and (version is None or version[0] != slide_content_hash)
                ):
                    raise ConflictError("The slide changed. Review the current version first.")
                mode, generation = "off", 0
                if assigned:
                    recipient = self._recipient(db, assigned)
                    self.sharing.require(recipient, scope, "edit")
                    if recipient.person != actor.person:
                        if channel != "shared" or item.legacy_deck_id is None:
                            raise AccessDenied
                        mode, generation = self._collaborator_setting(db, scope, assigned)
                        if mode == "off":
                            raise AccessDenied
                        if generation != collaborator_request_generation:
                            raise ConflictError(
                                "The agent's request setting changed. Choose a recipient again."
                            )
                        if include_shared_context:
                            raise ValueError(
                                "Collaborator requests can only include the selected message."
                            )
                self._reserve_attachments(
                    db, scope.workspace, sum(file["size"] for file in metadata)
                )
                cursor = db.execute(
                    "INSERT INTO conversations (workspace,artifact,channel,person,author,message,"
                    "target,content_hash,assigned,released,request_id,payload,created,"
                    "files,proposal,slide_content_hash,include_shared_context,"
                    "collaborator_request_generation,collaborator_request_mode) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        scope.workspace,
                        scope.resource,
                        channel,
                        actor.person,
                        actor.person,
                        message,
                        target.model_dump_json() if target else "null",
                        content_hash if offline_anchor else item.content_hash,
                        assigned,
                        released,
                        operation,
                        payload,
                        time.time(),
                        json.dumps(metadata),
                        json.dumps(anchor),
                        slide_content_hash,
                        include_shared_context,
                        generation,
                        mode,
                    ),
                )
                if channel == "shared":
                    contacts = self.sharing.human_contacts(scope)
                    recipients = [
                        person
                        for person, email in contacts.items()
                        if person != actor.person
                        and re.search(
                            rf"(?<![\w@])@{re.escape(email)}(?![\w@-]|\.[\w])", message, re.I
                        )
                    ]
                    _ = db.executemany(
                        "INSERT INTO chat_mentions(message,person) VALUES (?,?)",
                        [(cursor.lastrowid, person) for person in recipients],
                    )
                _ = db.executemany(
                    "INSERT INTO chat_attachments VALUES (?,?,?)",
                    [
                        (cursor.lastrowid, index, data)
                        for index, (_, data) in enumerate(attachments)
                    ],
                )
                row = db.execute(
                    "SELECT c.*,d.id AS cursor FROM conversations c "
                    "JOIN chat_delivery d ON d.message=c.id WHERE c.id=?",
                    (cursor.lastrowid,),
                ).fetchone()
                assert row is not None
                return self._public(row)

    def attachment(
        self,
        actor: Actor,
        scope: Scope,
        event: int,
        index: int,
        offset: int = 0,
        maximum: int = 25_000_000,
    ) -> tuple[ChatFile, bytes]:
        """Read only the requested bytes of a currently visible message attachment."""
        if not 1 <= event <= 2**63 - 1 or not 0 <= index < 5 or not 0 <= offset <= 25_000_000:
            raise AccessDenied
        with self.sharing.guard(actor, scope) as store, self.sharing.connect() as db:
            message = db.execute(
                "SELECT channel,person,author,assigned,released,files FROM conversations "
                "WHERE id=? AND workspace=? AND artifact=?",
                (event, scope.workspace, scope.resource),
            ).fetchone()
            if message is None or not self._visible(actor, message):
                raise AccessDenied
            if self._restrict_shared_agent(actor, scope, store, db, message["channel"]) and (
                actor.agent not in (message["assigned"], message["author"])
            ):
                raise AccessDenied
            files = cast(list[ChatFile], json.loads(cast(str, message["files"])))
            if index >= len(files):
                raise AccessDenied
            metadata = files[index]
            if offset > metadata["size"]:
                raise ValueError("Attachment offset is out of range.")
            row = db.execute(
                "SELECT substr(content,?,?) AS page FROM chat_attachments "
                "WHERE message=? AND file_index=?",
                (offset + 1, maximum, event, index),
            ).fetchone()
            if row is None:
                raise AccessDenied
            return metadata, cast(bytes, row["page"])

    def release(self, actor: Actor, scope: Scope, channel: Channel) -> None:
        with self.sharing.guard(actor, scope, "comment"), self.sharing.connect() as db:
            if actor.agent:
                raise AccessDenied
            # Recheck assigned agents: a revoked or downgraded collaborator cannot
            # publish a formerly eligible edit request using Send batch.
            rows = db.execute(
                "SELECT assigned,channel,person,collaborator_request_generation "
                "FROM conversations WHERE workspace=? AND artifact=? "
                "AND channel=? AND person=? AND released=0 AND assigned<>''",
                (scope.workspace, scope.resource, channel, actor.person),
            ).fetchall()
            for row in rows:
                recipient = self._recipient(db, cast(str, row["assigned"]))
                self.sharing.require(recipient, scope, "edit")
                if not self._request_is_current(db, scope, recipient, row):
                    raise AccessDenied
            db.execute(
                "UPDATE conversations SET released=1 WHERE workspace=? AND artifact=? "
                "AND channel=? AND person=? AND released=0",
                (scope.workspace, scope.resource, channel, actor.person),
            )

    @staticmethod
    def _shared_context(db: sqlite3.Connection, row: sqlite3.Row) -> list[dict[str, object]]:
        """Include nearby released human discussion from the same slide or artifact."""
        if not row["include_shared_context"]:
            return []
        rows = db.execute(
            "SELECT c.id, substr(c.message,1,401) AS excerpt FROM conversations c "
            "JOIN chat_delivery d ON d.message=c.id "
            "WHERE c.workspace=? AND c.artifact=? AND c.id<? AND d.id<? "
            "AND c.channel='shared' AND c.released=1 AND c.assigned='' AND c.author=c.person "
            "AND json_extract(c.target,'$.slide_id') IS json_extract(?,'$.slide_id') "
            "AND length(trim(c.message))>0 ORDER BY d.id DESC LIMIT 4",
            (row["workspace"], row["artifact"], row["id"], row["cursor"], row["target"]),
        ).fetchall()
        return [
            {
                "id": cast(int, context["id"]),
                "message": excerpt[:400] + ("…" if len(excerpt) > 400 else ""),
            }
            for context in reversed(rows)
            if (excerpt := cast(str, context["excerpt"]).strip())
        ]

    def tasks(self, actor: Actor, scope: Scope) -> dict[str, object]:
        with self.sharing.guard(actor, scope, "edit"), self.sharing.connect() as db:
            if not actor.agent:
                raise AccessDenied
            cursor = db.execute(
                "SELECT c.*,d.id AS cursor FROM conversations c "
                "JOIN chat_delivery d ON d.message=c.id "
                "WHERE workspace=? AND artifact=? AND assigned=? "
                "AND released=1 AND completed=0 ORDER BY c.id",
                (scope.workspace, scope.resource, actor.agent),
            )
            rows: list[sqlite3.Row] = []
            for row in cursor:
                if self._visible(actor, row) and self._request_is_current(db, scope, actor, row):
                    rows.append(row)
                    if len(rows) == 100:
                        break
            # A cursor is informational, never acknowledgement: lost responses and
            # late-released drafts remain pending until a successful claimed reply.
            events: list[dict[str, object]] = []
            seen_context: set[int] = set()
            for row in rows:
                event = self._public(row)
                if row["include_shared_context"]:
                    if len(seen_context) >= 12:
                        event["shared_context_on_claim"] = True
                    elif context := self._shared_context(db, row):
                        excerpts: list[dict[str, object]] = []
                        for excerpt in context:
                            identifier = cast(int, excerpt["id"])
                            excerpts.append(
                                {"id": identifier} if identifier in seen_context else excerpt
                            )
                            seen_context.add(identifier)
                        event["shared_context"] = excerpts
                events.append(event)
            return {
                "events": events,
                "next_cursor": max((cast(int, row["id"]) for row in rows), default=0),
                "delivery": "Claim each event before acting; reply acknowledges it.",
            }

    def claim(
        self,
        actor: Actor,
        scope: Scope,
        event_id: int,
        session_id: str,
        *,
        include_message: bool = True,
    ) -> dict[str, object]:
        """Claim a request, optionally omitting a message the agent already read."""
        with self.sharing.guard(actor, scope, "edit"), self.sharing.connect() as db:
            row = self._assigned(db, actor, scope, event_id)
            if row["completed"]:
                return {"completed": True}
            if row["claim_expires"] > time.time() and row["claimant"] != session_id:
                raise ConflictError("Another session is working on this request.")
            claim = cast(str, row["claim"]) if row["claimant"] == session_id else ""
            claim = claim or secrets.token_urlsafe(24)
            expires = time.time() + 600
            db.execute(
                "UPDATE conversations SET claim=?,claimant=?,claim_expires=? WHERE id=?",
                (claim, session_id, expires, event_id),
            )
            result: dict[str, object] = {
                "event_id": event_id,
                "claim": claim,
                "expires": expires,
            }
            if include_message:
                message = self._public(row)
                if context := self._shared_context(db, row):
                    message["shared_context"] = context
                result["message"] = message
            return result

    def _assigned(
        self, db: sqlite3.Connection, actor: Actor, scope: Scope, event_id: int
    ) -> sqlite3.Row:
        row = db.execute(
            "SELECT c.*,d.id AS cursor FROM conversations c "
            "JOIN chat_delivery d ON d.message=c.id WHERE c.id=? AND workspace=? AND artifact=? "
            "AND assigned=? AND released=1",
            (event_id, scope.workspace, scope.resource, actor.agent),
        ).fetchone()
        if not actor.agent or not row or not self._visible(actor, row):
            raise AccessDenied
        if not self._request_is_current(db, scope, actor, row):
            raise AccessDenied
        return row

    @staticmethod
    def _recipient(db: sqlite3.Connection, identifier: str) -> Actor:
        row = db.execute("SELECT person FROM agents WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise AccessDenied
        return Actor(cast(str, row["person"]), identifier)

    def reply(
        self, actor: Actor, scope: Scope, event_id: int, claim: str, message: str, request_id: str
    ) -> dict[str, object]:
        with self.sharing.guard(actor, scope, "edit"), self.sharing.connect() as db:
            parent = self._assigned(db, actor, scope, event_id)
            operation = self.sharing.request_id(actor, request_id)
            payload = checksum(json.dumps([scope.workspace, scope.resource, event_id, message]))
            prior = db.execute(
                "SELECT c.*,d.id AS cursor FROM conversations c "
                "JOIN chat_delivery d ON d.message=c.id WHERE request_id=?",
                (operation,),
            ).fetchone()
            if prior:
                if prior["payload"] != payload:
                    raise ConflictError("This request_id already belongs to another reply.")
                return self._public(prior)
            if (
                parent["completed"]
                or not claim
                or parent["claim"] != claim
                or parent["claim_expires"] <= time.time()
            ):
                raise ConflictError("Claim this request before replying; renew expired claims.")
            cursor = db.execute(
                "INSERT INTO conversations (workspace,artifact,channel,person,author,message,"
                "target,content_hash,released,reply_to,request_id,payload,created,proposal) "
                "VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?,?)",
                (
                    scope.workspace,
                    scope.resource,
                    parent["channel"],
                    actor.person,
                    actor.agent,
                    message,
                    parent["target"],
                    parent["content_hash"],
                    event_id,
                    operation,
                    payload,
                    time.time(),
                    parent["proposal"],
                ),
            )
            db.execute("UPDATE conversations SET completed=1 WHERE id=?", (event_id,))
            row = db.execute(
                "SELECT c.*,d.id AS cursor FROM conversations c "
                "JOIN chat_delivery d ON d.message=c.id WHERE c.id=?",
                (cursor.lastrowid,),
            ).fetchone()
            assert row is not None
            return self._public(row)

    def _has_pending(self, db: sqlite3.Connection, actor: Actor, scope: Scope) -> bool:
        """Check for released requests assigned by people who still have edit access."""
        pending = db.execute(
            "SELECT DISTINCT person,channel,collaborator_request_generation "
            "FROM conversations WHERE workspace=? "
            "AND artifact=? AND assigned=? "
            "AND released=1 AND completed=0",
            (scope.workspace, scope.resource, actor.agent),
        ).fetchall()
        return any(self._request_is_current(db, scope, actor, row) for row in pending)

    def hook(self, actor: Actor, body: ReviewHookInput) -> dict[str, object]:
        """Check assigned feedback and track leases for active host listeners."""
        with self.sharing.locked(), self.sharing.connect() as db:
            connection = self.sharing.agent_record(actor)
            workspace = cast(str, connection["workspace"])
            if body.passive:
                scope = Scope(workspace, "artifact", body.artifact_id or body.deck_id or "")
                self.sharing.require(actor, scope, "edit")
                available = self._has_pending(db, actor, scope)
                return {
                    "listening": False,
                    "feedback_available": available,
                    "reason": (
                        f"Artifact Studio has assigned feedback. Use read_feedback with "
                        f"artifact_id={scope.resource!r}, then claim each request before acting."
                        if available
                        else ""
                    ),
                }
            session = checksum(body.harness + ":" + body.session_id)
            existing = db.execute(
                "SELECT artifact,expires FROM multiplayer_hooks WHERE agent=? AND session=?",
                (actor.agent, session),
            ).fetchone()
            artifact = (
                body.artifact_id
                or body.deck_id
                or (cast(str, existing["artifact"]) if existing else "")
            )
            if body.event in {"SessionEnd", "Interrupt", "StopFailure"}:
                db.execute(
                    "INSERT OR REPLACE INTO multiplayer_hooks VALUES (?,?,?,?,0)",
                    (actor.agent, session, connection["workspace"], artifact),
                )
                return {"listening": False, "feedback_available": False, "reason": "Session ended"}
            if existing and existing["expires"] == 0 and not body.start_review:
                return {"listening": False, "feedback_available": False, "reason": "Review paused"}
            if not existing and not (body.start_review or body.auto_start):
                return {
                    "listening": False,
                    "feedback_available": False,
                    "reason": "Call start_live_review to connect this session.",
                }
            if not artifact:
                return {
                    "listening": False,
                    "feedback_available": False,
                    "reason": "Open an artifact or start_live_review first.",
                }
            scope = Scope(workspace, "artifact", artifact)
            self.sharing.require(actor, scope, "edit")
            now = time.time()
            if not existing or existing["artifact"] != artifact or existing["expires"] <= now + 60:
                db.execute(
                    "INSERT OR REPLACE INTO multiplayer_hooks VALUES (?,?,?,?,?)",
                    (actor.agent, session, scope.workspace, artifact, now + 120),
                )
            available = body.event == "Stop" and self._has_pending(db, actor, scope)
            reason = ""
            if available:
                reason = f"Studio feedback: call read_feedback(artifact_id={artifact!r})."
            elif body.event == "Stop":
                reason = "Studio is idle. End this turn to keep listening."
            return {
                "listening": True,
                "feedback_available": available,
                "reason": reason,
            }

    def agents(self, actor: Actor, scope: Scope) -> list[dict[str, object]]:
        with self.sharing.guard(actor, scope), self.sharing.connect() as db:
            rows = db.execute(
                "SELECT a.id,a.person,a.name,a.last_seen,p.email FROM agents a "
                "JOIN people p ON p.id=a.person WHERE workspace=? AND revoked=0 AND expires>?",
                (scope.workspace, time.time()),
            ).fetchall()
            result: list[dict[str, object]] = []
            for row in rows:
                recipient = Actor(cast(str, row["person"]), cast(str, row["id"]))
                if self.sharing.level(recipient, scope) < LEVEL["edit"]:
                    continue
                mine = recipient.person == actor.person
                mode, generation = self._collaborator_setting(db, scope, recipient.agent)
                listening = scope.kind == "artifact" and bool(
                    db.execute(
                        "SELECT 1 FROM multiplayer_hooks WHERE agent=? AND workspace=? "
                        "AND artifact=? AND expires>?",
                        (recipient.agent, scope.workspace, scope.resource, time.time()),
                    ).fetchone()
                )
                result.append(
                    {
                        "id": row["id"],
                        "email": row["email"],
                        "name": row["name"],
                        "mine": mine,
                        "collaborator_request_mode": mode,
                        "collaborator_request_generation": generation,
                        "status": "listening"
                        if listening
                        else (
                            "connected"
                            if mine and row["last_seen"] > time.time() - 120
                            else "offline"
                        ),
                    }
                )
            return result
