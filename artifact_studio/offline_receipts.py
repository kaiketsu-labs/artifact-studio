"""Deduplicate retained offline sources without invalidating previously issued receipts.

Receipt tokens are replay handles, not authorization: every use still checks the
current identity and grant. Keep old aliases indefinitely because an offline
device may still have queued work referencing them.
"""

import hashlib
import json
import secrets
import sqlite3
from dataclasses import astuple, dataclass
from typing import cast


@dataclass(frozen=True)
class ReceiptSource:
    namespace: str
    person: str
    workspace: str
    deck: str
    slide: str
    content_hash: str
    expected_hash: str
    source: str
    channel: str
    aggregate_hash: str

    @property
    def key(self) -> str:
        # Include the whole original anchor, including the aggregate comment hash.
        return hashlib.sha256(json.dumps(astuple(self), ensure_ascii=False).encode()).hexdigest()


_COLUMNS = (
    "namespace,person,workspace,deck,slide,content_hash,expected_hash,source,channel,aggregate_hash"
)


def _insert(db: sqlite3.Connection, entry: ReceiptSource) -> str:
    row = db.execute(
        "SELECT token FROM offline_receipt_sources WHERE id=?", (entry.key,)
    ).fetchone()
    if row:
        return cast(str, row[0])
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO offline_receipt_sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (entry.key, token, *astuple(entry)),
    )
    db.execute(
        "INSERT INTO offline_receipt_tokens VALUES (?,?)",
        (hashlib.sha256(token.encode()).hexdigest(), entry.key),
    )
    return token


def initialize(db: sqlite3.Connection) -> None:
    """Migrate duplicate blobs atomically; preserve token hashes and operation references."""
    db.execute("BEGIN IMMEDIATE")
    db.execute(
        "CREATE TABLE IF NOT EXISTS offline_receipt_sources ("
        "id TEXT PRIMARY KEY, token TEXT NOT NULL, namespace TEXT NOT NULL, person TEXT NOT NULL,"
        "workspace TEXT NOT NULL, deck TEXT NOT NULL, slide TEXT NOT NULL,"
        "content_hash TEXT NOT NULL, expected_hash TEXT NOT NULL, source TEXT NOT NULL,"
        "channel TEXT NOT NULL, aggregate_hash TEXT NOT NULL)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS offline_receipt_identity "
        "ON offline_receipt_sources(namespace,person)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS offline_receipt_tokens ("
        "token_hash TEXT PRIMARY KEY, source_id TEXT NOT NULL "
        "REFERENCES offline_receipt_sources(id))"
    )
    prior = db.execute("SELECT type FROM sqlite_master WHERE name='offline_receipts'").fetchone()
    if prior and prior[0] == "table":
        for row in db.execute("SELECT token_hash," + _COLUMNS + " FROM offline_receipts"):
            entry = ReceiptSource(*(cast(str, row[name]) for name in _COLUMNS.split(",")))
            _insert(db, entry)
            db.execute(
                "INSERT OR IGNORE INTO offline_receipt_tokens VALUES (?,?)",
                (row["token_hash"], entry.key),
            )
        db.execute("DROP TABLE offline_receipts")
    if not prior or prior[0] == "table":
        # Preserve read contracts and every existing offline_edits.receipt join.
        db.execute(
            "CREATE VIEW offline_receipts AS SELECT t.token_hash,"
            + ",".join("s." + name for name in _COLUMNS.split(","))
            + " FROM offline_receipt_tokens t JOIN offline_receipt_sources s ON s.id=t.source_id"
        )


def retain(db: sqlite3.Connection, entries: list[ReceiptSource]) -> list[str]:
    """Reuse exact snapshots before quota checks; a failed batch retains nothing new.

    The caller holds Sharing's cross-process lock and current-access guard.
    Sources with unknown device ownership cannot be deleted safely on refresh.
    """
    if not entries:
        return []
    namespace, person = entries[0].namespace, entries[0].person
    if any((entry.namespace, entry.person) != (namespace, person) for entry in entries):
        raise ValueError("Offline snapshots must belong to one identity.")
    existing = {
        cast(str, row[0])
        for row in db.execute(
            "SELECT id FROM offline_receipt_sources WHERE namespace=? AND person=?",
            (namespace, person),
        )
    }
    new = {entry.key: entry for entry in entries if entry.key not in existing}
    if new:
        size = db.execute(
            "SELECT COALESCE(SUM(length(CAST(source AS BLOB))),0) "
            "FROM offline_receipt_sources WHERE namespace=? AND person=?",
            (namespace, person),
        ).fetchone()[0]
        if (
            len(existing) + len(new) > 500
            or size + sum(len(entry.source.encode()) for entry in new.values()) > 20_000_000
        ):
            raise ValueError(
                "Offline receipt storage is full. Existing saved revisions and drafts remain safe."
            )
    return [_insert(db, entry) for entry in entries]
