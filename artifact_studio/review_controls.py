"""Bounded, ordered start/stop receipts for native host review sessions."""

import secrets
import sqlite3
from typing import Literal, cast

Action = Literal["start", "stop"]
SCHEMA = """
    CREATE TABLE IF NOT EXISTS review_control_requests (
      id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT NOT NULL UNIQUE,
      owner TEXT NOT NULL, action TEXT NOT NULL, target TEXT);
    CREATE INDEX IF NOT EXISTS review_control_owner ON review_control_requests(owner,id);
    CREATE TABLE IF NOT EXISTS review_control_state (
      owner TEXT NOT NULL, session TEXT NOT NULL, last_control INTEGER NOT NULL,
      paused INTEGER NOT NULL, PRIMARY KEY(owner,session));
"""
RETRY = (
    "Review control was superseded or expired. Call start_live_review or stop_live_review again."
)


def issue(db: sqlite3.Connection, owner: str, action: Action, target: str | None) -> str:
    """Issue a server receipt without assuming the tool transport is a native host session."""
    token = secrets.token_hex(32)
    _ = db.execute(
        "INSERT INTO review_control_requests(token,owner,action,target) VALUES (?,?,?,?)",
        (token, owner, action, target),
    )
    _ = db.execute(
        "DELETE FROM review_control_requests WHERE owner=? AND id NOT IN "
        "(SELECT id FROM review_control_requests WHERE owner=? ORDER BY id DESC LIMIT 256)",
        (owner, owner),
    )
    return token


def paused(db: sqlite3.Connection, owner: str, session: str) -> bool:
    """Read the native session's explicit pause inside the caller's lease transaction."""
    row = db.execute(
        "SELECT paused FROM review_control_state WHERE owner=? AND session=?", (owner, session)
    ).fetchone()
    return bool(row and row["paused"])


def apply(
    db: sqlite3.Connection,
    owner: str,
    session: str,
    action: Action,
    target: str | None,
    token: str | None,
    *,
    receipt_owner: str | None = None,
    preserve_pause: bool = False,
) -> bool:
    """Fence reordered controls atomically with the lease; legacy starts cannot clear a fence."""
    state = db.execute(
        "SELECT last_control,paused FROM review_control_state WHERE owner=? AND session=?",
        (owner, session),
    ).fetchone()
    if token is None:
        return action == "start" and state is None
    request = db.execute(
        "SELECT id FROM review_control_requests WHERE token=? AND owner=? AND action=? "
        "AND target IS ?",
        (token, owner if receipt_owner is None else receipt_owner, action, target),
    ).fetchone()
    if request is None or (state and request["id"] <= state["last_control"]):
        return False
    new_pause = bool(state and state["paused"]) if preserve_pause else action == "stop"
    _ = db.execute(
        "INSERT INTO review_control_state VALUES (?,?,?,?) ON CONFLICT(owner,session) "
        "DO UPDATE SET last_control=excluded.last_control,paused=excluded.paused",
        (owner, session, cast(int, request["id"]), int(new_pause)),
    )
    return True
