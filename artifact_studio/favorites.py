"""Private bookmarks and exact, audience-only slide references for chat."""

import base64
import json
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from typing import Literal, cast

from fastapi import APIRouter, Request
from pydantic import Field

from .artifacts import ArtifactStore
from .interactive import prepare_document
from .models import FileInput, Slide, StrictModel
from .storage import ConflictError, Store, digest
from .visuals import CaptureOptions, snapshot


class StarInput(StrictModel):
    """Set membership explicitly so retrying cannot toggle a star twice."""

    kind: Literal["artifact", "slide"]
    artifact_id: str = Field(min_length=1, max_length=160)
    slide_id: str = Field(default="", max_length=80)
    starred: bool = Field(strict=True)


class ReferenceInput(StrictModel):
    """Attach the version shown in the picker, never an unseen newer version."""

    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class DescriptionInput(StrictModel):
    """A short human description, scoped to a single favorite slide."""

    description: str = Field(max_length=300)
    expected_description: str = Field(max_length=300)


class FavoriteSlide(StrictModel):
    """Discover references without sending their source or notes into startup context."""

    deck_id: str
    slide_id: str
    deck_title: str
    title: str
    number: int
    content_hash: str
    description: str = ""
    reference_error: str = ""


class Favorites:
    """Persist human bookmarks independently of authored source and review state."""

    def __init__(
        self,
        store: Store,
        *,
        connect: Callable[[], sqlite3.Connection] | None = None,
        reader: str = "",
        can_view: Callable[[str], bool] | None = None,
    ) -> None:
        """Open a local bookmark table or an explicitly scoped reader's records."""
        self.store = store
        self.connect = connect or store.connect
        self.reader = reader
        self.can_view = can_view
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            columns = {cast(str, row[1]) for row in db.execute("PRAGMA table_info(favorites)")}
            migrate = bool(columns and "reader" not in columns)
            if migrate:
                _ = db.execute("ALTER TABLE favorites RENAME TO favorites_previous")
            _ = db.execute(
                "CREATE TABLE IF NOT EXISTS favorites ("
                "reader TEXT NOT NULL DEFAULT '', "
                "kind TEXT NOT NULL, artifact TEXT NOT NULL, slide TEXT NOT NULL, "
                "description TEXT NOT NULL DEFAULT '', "
                "PRIMARY KEY(reader, kind, artifact, slide))"
            )
            if migrate:
                description = "description" if "description" in columns else "''"
                _ = db.execute(
                    "INSERT INTO favorites(kind, artifact, slide, description) "
                    f"SELECT kind, artifact, slide, {description} FROM favorites_previous"
                )
                _ = db.execute("DROP TABLE favorites_previous")

    def _visible(self, artifact: str) -> bool:
        """Resolve current authorization without exposing stale bookmark identifiers."""
        return self.can_view is None or self.can_view(artifact)

    def index(self) -> dict[str, list[str]]:
        """Return only bookmark identifiers, including temporarily unavailable slides."""
        result: dict[str, list[str]] = {"artifacts": [], "slides": []}
        with self.connect() as db:
            for row in db.execute(
                "SELECT kind, artifact, slide FROM favorites WHERE reader=? "
                "ORDER BY artifact, slide",
                (self.reader,),
            ):
                kind, artifact, slide = cast(tuple[str, str, str], tuple(row))
                if not self._visible(artifact):
                    continue
                result["artifacts" if kind == "artifact" else "slides"].append(
                    artifact if kind == "artifact" else artifact + "/" + slide
                )
        return result

    def set(self, body: StarInput) -> dict[str, list[str]]:
        """Validate additions; allow removal even after the source has disappeared."""
        if (body.kind == "slide") != bool(body.slide_id):
            raise ValueError("A slide star needs a slide ID; an artifact star does not.")
        if body.starred:
            if not self._visible(body.artifact_id):
                raise FileNotFoundError("This item is unavailable.")
            artifact = ArtifactStore(self.store).get_artifact(body.artifact_id)
            if body.kind == "slide":
                if not artifact.legacy_deck_id:
                    raise ValueError("Only presentation slides can be starred.")
                _ = self.store.locate(self.store.read(body.artifact_id), body.slide_id)
        with self.connect() as db:
            if body.starred:
                _ = db.execute(
                    "INSERT OR IGNORE INTO favorites(reader, kind, artifact, slide) "
                    "VALUES (?,?,?,?)",
                    (self.reader, body.kind, body.artifact_id, body.slide_id),
                )
            else:
                _ = db.execute(
                    "DELETE FROM favorites WHERE reader=? AND kind=? AND artifact=? AND slide=?",
                    (self.reader, body.kind, body.artifact_id, body.slide_id),
                )
        return self.index()

    def descriptions(self) -> dict[str, str]:
        """Read only human bookmark context, never presentation notes or agent guidance."""
        with self.connect() as db:
            return {
                cast(str, row[0]) + "/" + cast(str, row[1]): cast(str, row[2])
                for row in db.execute(
                    "SELECT artifact, slide, description FROM favorites "
                    "WHERE reader=? AND kind='slide'",
                    (self.reader,),
                )
                if self._visible(cast(str, row[0]))
            }

    def describe(self, deck_id: str, slide_id: str, body: DescriptionInput) -> str:
        """Save or clear a description without overwriting another editor's change."""
        if not self._visible(deck_id):
            raise FileNotFoundError("This item is unavailable.")
        description = body.description.strip()
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT description FROM favorites WHERE reader=? AND kind='slide' "
                "AND artifact=? AND slide=?",
                (self.reader, deck_id, slide_id),
            ).fetchone()
            if row is None:
                raise ValueError("This slide is no longer a favorite. Reopen Favorite slides.")
            if row[0] not in {body.expected_description, description}:
                raise ConflictError(
                    "Description changed elsewhere. Reopen it to see the latest version."
                )
            _ = db.execute(
                "UPDATE favorites SET description=? WHERE reader=? AND kind='slide' "
                "AND artifact=? AND slide=?",
                (description, self.reader, deck_id, slide_id),
            )
        return description

    @staticmethod
    def _reference_token(visual_token: str, description: str) -> str:
        """Bind an optional description to the exact source selected in the picker."""
        return digest(visual_token + "\n" + description) if description else visual_token

    def slides(self) -> list[FavoriteSlide]:
        """Resolve bookmarks by stable IDs, keeping presentation order within each deck."""
        descriptions = self.descriptions()
        wanted = {key.split("/", 1)[0] for key in descriptions}
        if not wanted:
            return []
        result: list[FavoriteSlide] = []
        for item in self.store.list_decks():
            deck_id = item["id"]
            if deck_id not in wanted:
                continue
            deck = self.store.read(deck_id)
            for number, slide in enumerate(deck.slides, 1):
                if deck_id + "/" + slide.id in descriptions:
                    description = descriptions[deck_id + "/" + slide.id]
                    token, error = "", ""
                    try:
                        if self.can_view is None:
                            _, token = self._prepare(deck_id, slide, number)
                        else:
                            token = self.store.content_hash(deck_id, slide)
                        token = self._reference_token(token, description)
                    except (ValueError, FileNotFoundError) as reason:
                        error = str(reason) if self.can_view is None else "Preview unavailable."
                    result.append(
                        FavoriteSlide(
                            deck_id=deck_id,
                            slide_id=slide.id,
                            deck_title=deck.title,
                            title=slide.title,
                            number=number,
                            content_hash=token,
                            description=description,
                            reference_error=error,
                        )
                    )
        return result

    def _prepare(self, deck_id: str, slide: Slide, number: int) -> tuple[str, str]:
        """Fingerprint the actual embedded bytes, including deck-local scripts and styles."""
        prepared = snapshot(self.store, deck_id, slide, number, CaptureOptions())
        document, embedded = prepared.document, ""
        if slide.interactive_html:
            embedded = prepare_document(self.store, deck_id, slide.interactive_html)
            document = document.replace(
                json.dumps(slide.interactive_html).replace("<", r"\u003c"),
                json.dumps(embedded).replace("<", r"\u003c"),
                1,
            )
        return document, digest(prepared.frame.render_hash + embedded)

    def selection_token(self, deck_id: str, slide_id: str) -> str:
        """Pin any selected slide's current visual and optional favorite description."""
        deck = self.store.read(deck_id)
        slide = self.store.locate(deck, slide_id)
        _, token = self._prepare(deck_id, slide, deck.slides.index(slide) + 1)
        return self._reference_token(token, self.descriptions().get(deck_id + "/" + slide_id, ""))

    def reference(
        self, deck_id: str, slide_id: str, expected: str, *, require_favorite: bool = True
    ) -> FileInput:
        """Freeze one chosen visual as an owned chat attachment, without private notes."""
        description = self.descriptions().get(deck_id + "/" + slide_id)
        if description is None and require_favorite:
            raise ValueError("This slide is no longer a favorite. Reopen Favorite slides.")
        description = description or ""
        deck = self.store.read(deck_id)
        slide = self.store.locate(deck, slide_id)
        original_hash = self.store.content_hash(deck_id, slide)
        document, token = self._prepare(deck_id, slide, deck.slides.index(slide) + 1)
        if self._reference_token(token, description) != expected:
            raise ValueError("This slide or its description changed. Reopen Favorite slides.")
        provenance = json.dumps(
            {
                "role": "read-only visual reference",
                "deck_id": deck_id,
                "slide_id": slide_id,
                "deck_title": deck.title,
                "title": slide.title,
                "content_hash": original_hash,
                "reference_hash": expected,
                **({"description": description} if description else {}),
            }
        ).replace("<", r"\u003c")
        document = document.replace(
            '<meta charset="utf-8">',
            '<meta charset="utf-8"><script type="application/json" '
            'id="artifact-studio-slide-reference">' + provenance + "</script>",
            1,
        )
        current = self.store.locate(self.store.read(deck_id), slide_id)
        descriptions = self.descriptions()
        if (
            self.store.content_hash(deck_id, current) != original_hash
            or descriptions.get(deck_id + "/" + slide_id, "") != description
            or (require_favorite and deck_id + "/" + slide_id not in descriptions)
        ):
            raise ValueError("This slide changed while attaching. Reopen Favorite slides.")
        data = document.encode()
        if len(data) > 25_000_000:
            raise ValueError(
                "This slide exceeds the 25 MB attachment limit. Reduce its assets first."
            )
        return FileInput(
            name=f"Slide reference - {slide_id}.html", data_base64=base64.b64encode(data).decode()
        )


def favorite_routes(
    get_store: Callable[[], Store],
    *,
    scoped: Callable[[Request], AbstractContextManager[Favorites | None]] | None = None,
) -> APIRouter:
    """Use the existing human-only HTTP surface and feedback attachment delivery path."""
    router = APIRouter(tags=["Favorites"])

    def access(request: Request) -> AbstractContextManager[Favorites | None]:
        """Select the request's private bookmark collection."""
        return scoped(request) if scoped is not None else nullcontext(Favorites(get_store()))

    @router.get("/api/favorites")
    def index(request: Request) -> dict[str, list[str]]:
        with access(request) as favorites:
            return favorites.index() if favorites else {"artifacts": [], "slides": []}

    @router.post("/api/favorites")
    def set_star(request: Request, body: StarInput) -> dict[str, list[str]]:
        with access(request) as favorites:
            if favorites is None:
                raise FileNotFoundError("This item is unavailable.")
            return favorites.set(body)

    @router.get("/api/favorites/slides")
    def slides(request: Request) -> list[FavoriteSlide]:
        with access(request) as favorites:
            return favorites.slides() if favorites else []

    @router.post("/api/favorites/slides/{deck_id}/{slide_id}/description")
    def description(
        request: Request, deck_id: str, slide_id: str, body: DescriptionInput
    ) -> dict[str, str]:
        with access(request) as favorites:
            if favorites is None:
                raise FileNotFoundError("This item is unavailable.")
            return {"description": favorites.describe(deck_id, slide_id, body)}

    if scoped is not None:
        return router

    @router.post("/api/favorites/slides/{deck_id}/{slide_id}/reference")
    def reference(deck_id: str, slide_id: str, body: ReferenceInput) -> FileInput:
        return Favorites(get_store()).reference(deck_id, slide_id, body.content_hash)

    return router
