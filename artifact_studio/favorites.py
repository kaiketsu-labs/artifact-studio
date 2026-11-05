"""Private bookmarks and exact, audience-only slide references for chat."""

import base64
import json
from collections.abc import Callable
from typing import Literal, cast

from fastapi import APIRouter
from pydantic import Field

from .artifacts import ArtifactStore
from .interactive import prepare_document
from .models import FileInput, Slide, StrictModel
from .storage import Store, digest
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


class FavoriteSlide(StrictModel):
    """Discover references without sending their source or notes into startup context."""

    deck_id: str
    slide_id: str
    deck_title: str
    title: str
    number: int
    content_hash: str
    reference_error: str = ""


class Favorites:
    """Persist human bookmarks independently of authored source and review state."""

    def __init__(self, store: Store) -> None:
        self.store = store
        with store.connect() as db:
            _ = db.execute(
                "CREATE TABLE IF NOT EXISTS favorites ("
                "kind TEXT NOT NULL, artifact TEXT NOT NULL, slide TEXT NOT NULL, "
                "PRIMARY KEY(kind, artifact, slide))"
            )

    def index(self) -> dict[str, list[str]]:
        """Return only bookmark identifiers, including temporarily unavailable slides."""
        result: dict[str, list[str]] = {"artifacts": [], "slides": []}
        with self.store.connect() as db:
            for row in db.execute(
                "SELECT kind, artifact, slide FROM favorites ORDER BY artifact, slide"
            ):
                kind, artifact, slide = cast(tuple[str, str, str], tuple(row))
                result["artifacts" if kind == "artifact" else "slides"].append(
                    artifact if kind == "artifact" else artifact + "/" + slide
                )
        return result

    def set(self, body: StarInput) -> dict[str, list[str]]:
        """Validate additions; allow removal even after the source has disappeared."""
        if (body.kind == "slide") != bool(body.slide_id):
            raise ValueError("A slide star needs a slide ID; an artifact star does not.")
        if body.starred:
            artifact = ArtifactStore(self.store).get_artifact(body.artifact_id)
            if body.kind == "slide":
                if not artifact.legacy_deck_id:
                    raise ValueError("Only presentation slides can be starred.")
                _ = self.store.locate(self.store.read(body.artifact_id), body.slide_id)
        with self.store.connect() as db:
            if body.starred:
                _ = db.execute(
                    "INSERT OR IGNORE INTO favorites VALUES (?,?,?)",
                    (body.kind, body.artifact_id, body.slide_id),
                )
            else:
                _ = db.execute(
                    "DELETE FROM favorites WHERE kind=? AND artifact=? AND slide=?",
                    (body.kind, body.artifact_id, body.slide_id),
                )
        return self.index()

    def slides(self) -> list[FavoriteSlide]:
        """Resolve bookmarks by stable IDs, keeping presentation order within each deck."""
        wanted = set(self.index()["slides"])
        result: list[FavoriteSlide] = []
        for item in self.store.list_decks():
            deck_id = item["id"]
            if not any(key.startswith(deck_id + "/") for key in wanted):
                continue
            deck = self.store.read(deck_id)
            for number, slide in enumerate(deck.slides, 1):
                if deck_id + "/" + slide.id in wanted:
                    token, error = "", ""
                    try:
                        _, token = self._prepare(deck_id, slide, number)
                    except (ValueError, FileNotFoundError) as reason:
                        error = str(reason)
                    result.append(
                        FavoriteSlide(
                            deck_id=deck_id,
                            slide_id=slide.id,
                            deck_title=deck.title,
                            title=slide.title,
                            number=number,
                            content_hash=token,
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

    def reference(self, deck_id: str, slide_id: str, expected: str) -> FileInput:
        """Freeze one chosen visual as an owned chat attachment, without private notes."""
        if deck_id + "/" + slide_id not in self.index()["slides"]:
            raise ValueError("This slide is no longer a favorite. Reopen Favorite slides.")
        deck = self.store.read(deck_id)
        slide = self.store.locate(deck, slide_id)
        original_hash = self.store.content_hash(deck_id, slide)
        document, token = self._prepare(deck_id, slide, deck.slides.index(slide) + 1)
        if token != expected:
            raise ValueError(
                "This slide changed. Reopen Favorite slides to see the latest version."
            )
        provenance = json.dumps(
            {
                "role": "read-only visual reference",
                "deck_id": deck_id,
                "slide_id": slide_id,
                "deck_title": deck.title,
                "title": slide.title,
                "content_hash": original_hash,
                "reference_hash": expected,
            }
        ).replace("<", r"\u003c")
        document = document.replace(
            "</head>",
            '<script type="application/json" id="artifact-studio-slide-reference">'
            + provenance
            + "</script></head>",
            1,
        )
        current = self.store.locate(self.store.read(deck_id), slide_id)
        if self.store.content_hash(deck_id, current) != original_hash:
            raise ValueError("This slide changed while attaching. Reopen Favorite slides.")
        data = document.encode()
        if len(data) > 25_000_000:
            raise ValueError(
                "This slide exceeds the 25 MB attachment limit. Reduce its assets first."
            )
        return FileInput(
            name=f"Slide reference - {slide_id}.html", data_base64=base64.b64encode(data).decode()
        )


def favorite_routes(get_store: Callable[[], Store]) -> APIRouter:
    """Use the existing human-only HTTP surface and feedback attachment delivery path."""
    router = APIRouter(tags=["Favorites"])

    @router.get("/api/favorites")
    def index() -> dict[str, list[str]]:
        return Favorites(get_store()).index()

    @router.post("/api/favorites")
    def set_star(body: StarInput) -> dict[str, list[str]]:
        return Favorites(get_store()).set(body)

    @router.get("/api/favorites/slides")
    def slides() -> list[FavoriteSlide]:
        return Favorites(get_store()).slides()

    @router.post("/api/favorites/slides/{deck_id}/{slide_id}/reference")
    def reference(deck_id: str, slide_id: str, body: ReferenceInput) -> FileInput:
        return Favorites(get_store()).reference(deck_id, slide_id, body.content_hash)

    return router
