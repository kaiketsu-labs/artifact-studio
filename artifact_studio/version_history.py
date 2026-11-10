"""Compact version timelines and explicit restores of retained local source."""

import json
import re
import sqlite3
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast

from pydantic import Field

from .models import Slide, SlideView, StrictModel
from .storage import SAFE_ID, ConflictError, Store, digest
from .version_authors import current_revision_author

if TYPE_CHECKING:
    from .artifact_models import ArtifactView
    from .artifacts import ArtifactStore

GROUP_SECONDS = 180
SLIDE_FIDELITY = (
    "Slide source is retained, but shared theme files and assets are current. "
    "This preview may differ from the original appearance."
)
ARTIFACT_FIDELITY = (
    "Bundled files are retained exactly. External resources, if any, may have changed."
)
VERSION_HASH = re.compile(r"^slide:([a-f0-9]{64})$")


class VersionSummary(StrictModel):
    """Bounded metadata for one save, without source or binary payloads."""

    version_id: str
    title: str
    revision: int
    created_at: str | None
    group_id: str
    is_current: bool = False
    size_bytes: int = 0
    file_count: int = 1
    content_changed: bool = True
    author: str | None = None


class HistoryGroup(StrictModel):
    """A rapid-save session, including individual saves on the current page."""

    id: str
    created_at: str | None
    latest_at: str | None
    count: int
    version_ids: list[str] = Field(default_factory=list)


class HistoryPage(StrictModel):
    """A source-free page whose cursor remains stable as newer saves arrive."""

    items: list[VersionSummary]
    groups: list[HistoryGroup]
    next_cursor: str | None
    current_version_id: str
    expected_hash: str
    continues_group: str | None = None
    fidelity_warning: str
    total_saves: int = 0
    hidden_saves: int = 0


class SlideVersion(StrictModel):
    """One selected slide's source, fetched only when previewing or restoring."""

    version: VersionSummary
    slide: Slide
    fidelity_warning: str = SLIDE_FIDELITY


def _text_column(db: sqlite3.Connection, table: str, column: str) -> None:
    """Add a nullable metadata column once, even during concurrent initialization."""

    def missing() -> bool:
        """Check schema again after acquiring the lock if another process initialized it."""
        return column not in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}

    if missing():
        if not db.in_transaction:
            _ = db.execute("BEGIN IMMEDIATE")
        if missing():
            _ = db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")


def _content_column(db: sqlite3.Connection, table: str) -> None:
    """Add a small signature cache and index uncached rows without duplicating source."""
    _text_column(db, table, "content_key")
    columns = "deck,slide" if table == "slide_version_metadata" else "artifact_id"
    _ = db.execute(
        f"CREATE INDEX IF NOT EXISTS {table}_missing_content ON {table}({columns}) "
        "WHERE content_key IS NULL"
    )


def _slide_content_key(slide: Slide) -> str:
    """Identify authored audience content, excluding private and source-panel metadata."""
    return digest(json.dumps([slide.html, slide.interactive_html], ensure_ascii=False))


def install_slide_history(db: sqlite3.Connection) -> None:
    """Add metadata and capture future retained revisions without dating old saves."""
    existing = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='slide_version_metadata'"
    ).fetchone()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS slide_version_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT, deck TEXT NOT NULL, slide TEXT NOT NULL,
            hash TEXT NOT NULL, revision INTEGER NOT NULL, title TEXT NOT NULL,
            created_at TEXT, group_id INTEGER NOT NULL DEFAULT 0, size_bytes INTEGER NOT NULL,
            UNIQUE(deck,slide,revision));
        CREATE INDEX IF NOT EXISTS slide_version_timeline
            ON slide_version_metadata(deck,slide,id DESC);
        CREATE INDEX IF NOT EXISTS slide_version_groups
            ON slide_version_metadata(deck,slide,group_id);
        CREATE TRIGGER IF NOT EXISTS retain_slide_version_metadata AFTER INSERT ON revisions
        BEGIN
            INSERT OR IGNORE INTO slide_version_metadata
                (deck,slide,hash,revision,title,size_bytes)
            VALUES (NEW.deck,NEW.slide,NEW.hash,json_extract(NEW.document,'$.revision'),
                json_extract(NEW.document,'$.title'),length(CAST(NEW.document AS BLOB)));
            UPDATE slide_version_metadata SET group_id=id
                WHERE deck=NEW.deck AND slide=NEW.slide AND group_id=0;
        END;
    """)
    if existing is None:
        _ = db.execute("""
            INSERT OR IGNORE INTO slide_version_metadata
                (deck,slide,hash,revision,title,size_bytes)
            SELECT deck,slide,hash,json_extract(document,'$.revision'),
                json_extract(document,'$.title'),length(CAST(document AS BLOB))
            FROM revisions ORDER BY rowid
        """)
        _ = db.execute("UPDATE slide_version_metadata SET group_id=id WHERE group_id=0")
    _content_column(db, "slide_version_metadata")
    _text_column(db, "slide_version_metadata", "author")


def capture_slide_version(
    db: sqlite3.Connection,
    deck_id: str,
    slide: Slide,
    *,
    saved_at: str | None = None,
    authored: bool = False,
) -> None:
    """Record a retained save and attribute only an original write by the current actor."""
    document = slide.model_dump_json()
    source_hash = digest(document)
    inserted = db.execute(
        "INSERT OR IGNORE INTO revisions VALUES (?,?,?,?)",
        (deck_id, slide.id, source_hash, document),
    ).rowcount
    _ = db.execute(
        "UPDATE slide_version_metadata SET content_key=? WHERE deck=? AND slide=? "
        "AND revision=? AND content_key IS NULL",
        (_slide_content_key(slide), deck_id, slide.id, slide.revision),
    )
    if saved_at is None:
        return
    row = db.execute(
        "SELECT id,hash,created_at FROM slide_version_metadata "
        "WHERE deck=? AND slide=? AND revision=?",
        (deck_id, slide.id, slide.revision),
    ).fetchone()
    if row is None or row["created_at"] is not None:
        return
    prior = db.execute(
        "SELECT group_id,created_at FROM slide_version_metadata "
        "WHERE deck=? AND slide=? AND created_at IS NOT NULL AND id<? ORDER BY id DESC LIMIT 1",
        (deck_id, slide.id, row["id"]),
    ).fetchone()
    group_id = cast(int, row["id"])
    if prior is not None:
        elapsed = (
            datetime.fromisoformat(saved_at)
            - datetime.fromisoformat(cast(str, prior["created_at"]))
        ).total_seconds()
        if 0 <= elapsed <= GROUP_SECONDS:
            group_id = cast(int, prior["group_id"])
    author = (
        current_revision_author()
        if authored and inserted == 1 and row["hash"] == source_hash
        else None
    )
    _ = db.execute(
        "UPDATE slide_version_metadata SET created_at=?,group_id=?,author=? WHERE id=?",
        (saved_at, group_id, author, row["id"]),
    )


def install_artifact_history(db: sqlite3.Connection) -> None:
    """Index manifest metadata once so timeline pagination never scans entire bundles."""
    existing = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifact_version_metadata'"
    ).fetchone()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS artifact_version_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT, artifact_id TEXT NOT NULL,
            revision INTEGER NOT NULL, title TEXT NOT NULL, created_at TEXT,
            group_id INTEGER NOT NULL DEFAULT 0, size_bytes INTEGER NOT NULL,
            file_count INTEGER NOT NULL, UNIQUE(artifact_id,revision));
        CREATE INDEX IF NOT EXISTS artifact_version_timeline
            ON artifact_version_metadata(artifact_id,id DESC);
        CREATE INDEX IF NOT EXISTS artifact_version_groups
            ON artifact_version_metadata(artifact_id,group_id);
        CREATE TRIGGER IF NOT EXISTS retain_artifact_version_metadata
            AFTER INSERT ON artifact_revisions
        BEGIN
            INSERT OR IGNORE INTO artifact_version_metadata
                (artifact_id,revision,title,created_at,size_bytes,file_count)
            VALUES (NEW.artifact_id,NEW.revision,json_extract(NEW.document,'$.title'),
                json_extract(NEW.document,'$.created_at'),
                (SELECT COALESCE(SUM(json_extract(value,'$.size')),0)
                    FROM json_each(NEW.document,'$.files')),
                json_array_length(NEW.document,'$.files'));
            UPDATE artifact_version_metadata SET group_id=COALESCE(
                (SELECT prior.group_id FROM artifact_version_metadata prior
                    WHERE prior.artifact_id=NEW.artifact_id AND prior.revision<NEW.revision
                    AND (julianday(json_extract(NEW.document,'$.created_at'))
                        -julianday(prior.created_at))*86400 BETWEEN 0 AND 180
                    ORDER BY prior.revision DESC LIMIT 1),id)
                WHERE artifact_id=NEW.artifact_id AND revision=NEW.revision;
        END;
    """)
    if existing is None:
        _ = db.execute("""
            INSERT OR IGNORE INTO artifact_version_metadata
                (artifact_id,revision,title,created_at,size_bytes,file_count)
            SELECT artifact_id,revision,json_extract(document,'$.title'),
                json_extract(document,'$.created_at'),
                (SELECT COALESCE(SUM(json_extract(value,'$.size')),0)
                    FROM json_each(document,'$.files')),
                json_array_length(document,'$.files')
            FROM artifact_revisions ORDER BY artifact_id,revision
        """)
        _ = db.execute("""
            WITH sessions AS (
                SELECT id,artifact_id,revision,CASE WHEN
                    (julianday(created_at)-julianday(LAG(created_at) OVER
                        (PARTITION BY artifact_id ORDER BY revision)))*86400
                        BETWEEN 0 AND 180 THEN 0 ELSE 1 END AS start
                FROM artifact_version_metadata
            ), grouped AS (
                SELECT *,SUM(start) OVER (PARTITION BY artifact_id ORDER BY revision) AS session
                FROM sessions
            ), identities AS (
                SELECT id,MIN(id) OVER (PARTITION BY artifact_id,session) AS group_id FROM grouped
            )
            UPDATE artifact_version_metadata SET group_id=
                (SELECT group_id FROM identities WHERE identities.id=artifact_version_metadata.id)
        """)
    _content_column(db, "artifact_version_metadata")


class VersionHistory:
    """Expose small paginated lists and separate source retrieval and restore operations."""

    def __init__(self, store: Store, artifacts: ArtifactStore) -> None:
        """Use existing stores so history shares their ownership and locking rules."""
        self.store = store
        self.artifacts = artifacts

    @staticmethod
    def _page_bounds(cursor: str | None, limit: int) -> int:
        """Validate bounded keyset cursors before passing them into SQL."""
        if not 1 <= limit <= 100:
            raise ValueError("Read between 1 and 100 versions per page.")
        if cursor is None:
            return 9_223_372_036_854_775_807
        if not cursor.isdecimal() or len(cursor) > 19 or not 0 < int(cursor) < 2**63:
            raise ValueError("Invalid version-history cursor.")
        return int(cursor)

    @staticmethod
    def _slide_summary(row: sqlite3.Row, current_revision: int) -> VersionSummary:
        """Convert only metadata columns, keeping source out of list responses."""
        return VersionSummary(
            version_id="slide:" + cast(str, row["hash"]),
            title=cast(str, row["title"]),
            revision=cast(int, row["revision"]),
            created_at=cast(str | None, row["created_at"]),
            group_id="slide-group-" + str(row["group_id"]),
            is_current=row["revision"] == current_revision,
            size_bytes=cast(int, row["size_bytes"]),
            content_changed=bool(dict(row).get("content_changed", True)),
            author=cast(str | None, row["author"]),
        )

    @staticmethod
    def _groups(items: list[VersionSummary], counts: dict[str, int]) -> list[HistoryGroup]:
        """Keep session members expandable without duplicating their source or metadata."""
        grouped: defaultdict[str, list[VersionSummary]] = defaultdict(list)
        for item in items:
            grouped[item.group_id].append(item)
        return [
            HistoryGroup(
                id=group_id,
                created_at=min(times)
                if (times := [v.created_at for v in saves if v.created_at])
                else None,
                latest_at=max(times) if times else None,
                count=counts[group_id],
                version_ids=[version.version_id for version in saves],
            )
            for group_id, saves in grouped.items()
        ]

    @staticmethod
    def _slide_keys(db: sqlite3.Connection, deck_id: str, slide_id: str) -> None:
        """Backfill old signatures one source at a time; subsequent reads use only the index."""
        rows = db.execute(
            "SELECT m.id,r.document FROM slide_version_metadata m JOIN revisions r "
            "ON r.deck=m.deck AND r.slide=m.slide AND r.hash=m.hash "
            "WHERE m.deck=? AND m.slide=? AND m.content_key IS NULL ORDER BY m.id",
            (deck_id, slide_id),
        )
        for row in rows:
            slide = Slide.model_validate_json(cast(str, row["document"]))
            _ = db.execute(
                "UPDATE slide_version_metadata SET content_key=? WHERE id=?",
                (_slide_content_key(slide), row["id"]),
            )

    @staticmethod
    def _rows(
        db: sqlite3.Connection,
        kind: Literal["slide", "artifact"],
        owner: tuple[str, ...],
        current_revision: int,
        before_id: int,
        limit: int,
        include_all: bool,
    ) -> tuple[list[sqlite3.Row], dict[str, int], int, int]:
        """Page indexed states, keeping the newest save in each consecutive identical run."""
        table = kind + "_version_metadata"
        columns = ("deck", "slide") if kind == "slide" else ("artifact_id",)
        identity = " AND ".join(f"m.{column}=?" for column in columns)
        related = " AND ".join(f"n.{column}=m.{column}" for column in columns)
        neighbor = (
            f"SELECT n.content_key FROM {table} n WHERE {related} AND n.id>m.id "
            "ORDER BY n.id LIMIT 1"
        )
        distinct = f"(m.revision=? OR m.content_key IS NOT ({neighbor}))"
        previous = (
            f"SELECT n.content_key FROM {table} n WHERE {related} AND n.id<m.id "
            "ORDER BY n.id DESC LIMIT 1"
        )
        condition = identity if include_all else identity + " AND " + distinct
        parameters = owner if include_all else (*owner, current_revision)
        rows = db.execute(
            f"SELECT m.*,m.content_key IS NOT ({previous}) AS content_changed "
            f"FROM {table} m WHERE {condition} AND m.id<? ORDER BY m.id DESC LIMIT ?",
            (*parameters, before_id, limit + 1),
        ).fetchall()
        group_ids = {cast(int, row["group_id"]) for row in rows[:limit]}
        counts = {
            f"{kind}-group-{group_id}": cast(
                int,
                db.execute(
                    f"SELECT COUNT(*) FROM {table} m WHERE {condition} AND m.group_id=?",
                    (*parameters, group_id),
                ).fetchone()[0],
            )
            for group_id in group_ids
        }
        total = cast(
            int,
            db.execute(
                f"SELECT COUNT(*) FROM {table} m WHERE {identity}",
                owner,
            ).fetchone()[0],
        )
        visible = cast(
            int,
            db.execute(
                f"SELECT COUNT(*) FROM {table} m WHERE {identity} AND {distinct}",
                (*owner, current_revision),
            ).fetchone()[0],
        )
        return rows, counts, total, total - visible

    def list_slide(
        self,
        deck_id: str,
        slide_id: str,
        *,
        cursor: str | None = None,
        limit: int = 30,
        include_all: bool = False,
    ) -> HistoryPage:
        """List distinct audience states by default while retaining every saved revision."""
        before_id = self._page_bounds(cursor, limit)
        with self.store.editing(deck_id) as db:
            current = self.store.locate(self.store.read(deck_id), slide_id)
            current_row = db.execute(
                "SELECT hash FROM slide_version_metadata WHERE deck=? AND slide=? AND revision=?",
                (deck_id, slide_id, current.revision),
            ).fetchone()
            if current_row is None:
                capture_slide_version(db, deck_id, current)
                current_row = db.execute(
                    "SELECT hash FROM slide_version_metadata "
                    "WHERE deck=? AND slide=? AND revision=?",
                    (deck_id, slide_id, current.revision),
                ).fetchone()
            self._slide_keys(db, deck_id, slide_id)
            rows, counts, total, hidden = self._rows(
                db,
                "slide",
                (deck_id, slide_id),
                current.revision,
                before_id,
                limit,
                include_all,
            )
            items = [self._slide_summary(row, current.revision) for row in rows[:limit]]
        return HistoryPage(
            items=items,
            groups=self._groups(items, counts),
            next_cursor=str(rows[limit - 1]["id"]) if len(rows) > limit else None,
            current_version_id="slide:" + cast(str, current_row[0]) if current_row else "",
            expected_hash=digest(current.model_dump_json()),
            continues_group=("slide-group-" + str(rows[limit]["group_id"]))
            if len(rows) > limit and rows[limit]["group_id"] == rows[limit - 1]["group_id"]
            else None,
            fidelity_warning=SLIDE_FIDELITY,
            total_saves=total,
            hidden_saves=hidden,
        )

    def get_slide_version(self, deck_id: str, slide_id: str, version_id: str) -> SlideVersion:
        """Fetch one retained source after validating ownership and its original digest."""
        match = VERSION_HASH.fullmatch(version_id)
        if match is None:
            raise ValueError("Invalid slide version ID.")
        current = self.store.locate(self.store.read(deck_id), slide_id)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT m.*,r.document FROM slide_version_metadata m JOIN revisions r "
                "ON r.deck=m.deck AND r.slide=m.slide AND r.hash=m.hash "
                "WHERE m.deck=? AND m.slide=? AND m.hash=?",
                (deck_id, slide_id, match[1]),
            ).fetchone()
        if row is None:
            raise FileNotFoundError("Slide version does not exist.")
        document = cast(str, row["document"])
        if digest(document) != match[1]:
            raise ConflictError("The retained slide version was modified outside the workspace.")
        return SlideVersion(
            version=self._slide_summary(row, current.revision),
            slide=Slide.model_validate_json(document),
        )

    def restore_slide(
        self,
        deck_id: str,
        slide_id: str,
        version_id: str,
        expected_hash: str,
        request_id: str,
        *,
        validate_audience: Callable[[Slide], None] | None = None,
    ) -> SlideView:
        """Restore audience source as a fresh save, preserving current human presentation state."""
        if not 8 <= len(request_id) <= 100:
            raise ValueError("Use a unique request_id of 8–100 characters.")
        payload_hash = digest(
            "restore-slide:"
            + slide_id
            + version_id
            + expected_hash
            + (":scoped-audience" if validate_audience else "")
        )
        with self.store.editing(deck_id) as db:
            prior = db.execute(
                "SELECT payload_hash,result FROM operations WHERE deck=? AND request_id=?",
                (deck_id, request_id),
            ).fetchone()
            if prior is not None:
                if prior[0] != payload_hash:
                    raise ConflictError("This request_id was already used with another payload.")
                return SlideView.model_validate_json(cast(str, prior[1]))
            deck = self.store.read(deck_id)
            current = self.store.locate(deck, slide_id)
            if digest(current.model_dump_json()) != expected_hash:
                raise ConflictError("Slide changed. Read it again before restoring a version.")
            historical = self.get_slide_version(deck_id, slide_id, version_id).slide
            if validate_audience:
                validate_audience(historical)
                historical = current.model_copy(
                    update={
                        "title": historical.title,
                        "html": historical.html,
                        "interactive_html": historical.interactive_html,
                    }
                )
            restored = historical.model_copy(
                update={
                    "notes": current.notes,
                    "notes_revision": current.notes_revision,
                    "hidden": current.hidden,
                    "revision": current.revision + 1,
                }
            )
            capture_slide_version(db, deck_id, current)
            deck.slides[deck.slides.index(current)] = restored
            self.store.write(deck_id, deck)
            capture_slide_version(
                db, deck_id, restored, saved_at=datetime.now(UTC).isoformat(), authored=True
            )
            _ = db.execute("DELETE FROM approvals WHERE deck=? AND slide=?", (deck_id, slide_id))
            seen = db.execute(
                "SELECT content_hash FROM slide_views WHERE deck=? AND slide=?", (deck_id, slide_id)
            ).fetchone()
            result = self.store.view_slide(
                deck_id,
                restored,
                feedback=[],
                review_hashes=({}, {slide_id: cast(str, seen[0])} if seen else {}),
            )
            _ = db.execute(
                "INSERT INTO operations VALUES (?,?,?,?)",
                (deck_id, request_id, payload_hash, result.model_dump_json()),
            )
        return result

    @staticmethod
    def _artifact_revision(version_id: str) -> int:
        """Accept only canonical positive revision IDs, independent of filesystem paths."""
        prefix, separator, number = version_id.partition(":")
        if prefix != "artifact" or not separator or not number.isdecimal() or len(number) > 18:
            raise ValueError("Invalid artifact version ID.")
        revision = int(number)
        if revision < 1 or str(revision) != number:
            raise ValueError("Invalid artifact version ID.")
        return revision

    def list_artifact(
        self,
        artifact_id: str,
        *,
        cursor: str | None = None,
        limit: int = 30,
        include_all: bool = False,
    ) -> HistoryPage:
        """Read compact manifest fields in SQLite without opening any snapshot files."""
        from .artifacts import artifact_revision_hash

        before_id = self._page_bounds(cursor, limit)
        if not SAFE_ID.fullmatch(artifact_id):
            raise ValueError("Invalid artifact ID.")
        with self.store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT json_extract(document,'$.revision'),"
                "json_extract(document,'$.content_hash') FROM artifact_items WHERE id=?",
                (artifact_id,),
            ).fetchone()
            if current is None:
                raise FileNotFoundError(
                    "Owned artifact does not exist. Use slide history for decks."
                )
            _ = db.execute(
                "UPDATE artifact_version_metadata AS m SET content_key=(SELECT "
                "json_extract(r.document,'$.content_hash') FROM artifact_revisions r "
                "WHERE r.artifact_id=m.artifact_id AND r.revision=m.revision) "
                "WHERE m.artifact_id=? AND m.content_key IS NULL",
                (artifact_id,),
            )
            rows, counts, total, hidden = self._rows(
                db,
                "artifact",
                (artifact_id,),
                cast(int, current[0]),
                before_id,
                limit,
                include_all,
            )
        current_revision = cast(int, current[0])
        items = [
            VersionSummary(
                version_id="artifact:" + str(row["revision"]),
                title=cast(str, row["title"]),
                revision=cast(int, row["revision"]),
                created_at=cast(str | None, row["created_at"]),
                group_id="artifact-group-" + str(row["group_id"]),
                is_current=row["revision"] == current_revision,
                size_bytes=cast(int, row["size_bytes"]),
                file_count=cast(int, row["file_count"]),
                content_changed=bool(row["content_changed"]),
            )
            for row in rows[:limit]
        ]
        return HistoryPage(
            items=items,
            groups=self._groups(items, counts),
            next_cursor=str(rows[limit - 1]["id"]) if len(rows) > limit else None,
            current_version_id="artifact:" + str(current_revision),
            expected_hash=artifact_revision_hash(
                artifact_id, current_revision, cast(str, current[1])
            ),
            continues_group=("artifact-group-" + str(rows[limit]["group_id"]))
            if len(rows) > limit and rows[limit]["group_id"] == rows[limit - 1]["group_id"]
            else None,
            fidelity_warning=ARTIFACT_FIDELITY,
            total_saves=total,
            hidden_saves=hidden,
        )

    def restore_artifact(
        self, artifact_id: str, version_id: str, expected_hash: str, request_id: str
    ) -> ArtifactView:
        """Delegate an explicit snapshot restore to the artifact store's atomic write path."""
        return self.artifacts.restore_artifact(
            artifact_id, self._artifact_revision(version_id), expected_hash, request_id
        )
