"""Human-selected, revision-frozen audience examples for a deck or its project."""

import io
import json
import zlib
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Literal, cast

from fastapi import APIRouter, Request
from PIL import Image
from pydantic import Field

from ..artifacts import ArtifactStore, legacy_project
from ..deck_edit_api import DeckID
from ..interactive import prepare_document
from ..models import StrictModel
from ..storage import ConflictError, Store, digest
from ..visuals import CaptureOptions, ScopedDeckEmbedder, Snapshot, capture_snapshot, snapshot

ReferenceScope = Literal["deck", "project"]
OPTIONS = CaptureOptions(reduced_motion=True, resolution="draft")
METADATA = "scope, pin_id, deck_id, slide_id, title, content_hash"


class PinnedSlide(StrictModel):
    """A compact pointer to the exact slide a human selected."""

    scope: ReferenceScope
    pin_id: str
    deck_id: str
    slide_id: str
    title: str
    content_hash: str


class PinChange(StrictModel):
    """Replace a viewed pin, or clear it by omitting slide_id and content_hash."""

    scope: ReferenceScope
    expected_pin_id: str = Field(pattern=r"^(?:[0-9a-f]{64})?$")
    slide_id: str = Field(default="", pattern=r"^(?:[a-z0-9][a-z0-9-]{0,79})?$")
    content_hash: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")


class PinSlot(StrictModel):
    """Current visible selection and permission to change it."""

    pin: PinnedSlide | None
    editable: bool


class PinState(StrictModel):
    deck: PinSlot
    project: PinSlot


@dataclass
class PinAccess:
    """Live grant predicates for the duration of an already authorized deck access."""

    store: Store
    deck_editable: bool = True
    can_view: Callable[[str], bool] | None = None
    project_level: Callable[[str], int] | None = None


@dataclass
class Selection:
    reference: PinnedSlide
    source: str
    image: Snapshot


def render_thumbnail(image: Snapshot) -> bytes:
    """Render once, preserving the actual canvas ratio and its final reveal."""
    rendered = capture_snapshot(image, OPTIONS)
    with Image.open(io.BytesIO(rendered)) as original:
        original.thumbnail((480, 300), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        original.save(output, format="PNG")
        return output.getvalue()


class DesignReferences:
    """Store exactly one immutable selection per deck or project."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def presentation_project(self, deck_id: str) -> str:
        if (self.store.root / deck_id).is_symlink() or (
            self.store.folder(deck_id) / "index.html"
        ).is_symlink():
            raise FileNotFoundError("Presentation unavailable.")
        return ArtifactStore(self.store).presentation_project(deck_id)

    def _row(self, scope: ReferenceScope, target: str) -> PinnedSlide | None:
        with self.store.connect() as db:
            row = db.execute(
                f"SELECT {METADATA} FROM pinned_slides WHERE scope=? AND target=?", (scope, target)
            ).fetchone()
        return PinnedSlide.model_validate(dict(row)) if row else None

    def _visible(
        self, pin: PinnedSlide | None, project: str, can_view: Callable[[str], bool] | None
    ) -> PinnedSlide | None:
        if pin is None:
            return None
        try:
            if (can_view and not can_view(pin.deck_id)) or self.presentation_project(
                pin.deck_id
            ) != project:
                return None
            with self.store.connect() as db:
                if db.execute(
                    "SELECT 1 FROM archived_decks WHERE deck=?", (pin.deck_id,)
                ).fetchone():
                    return None
            _ = self.store.locate(self.store.read(pin.deck_id), pin.slide_id)
        except ValueError, FileNotFoundError:
            return None
        return pin

    def resolve(
        self,
        *,
        deck_id: str = "",
        project_id: str = "",
        can_view: Callable[[str], bool] | None = None,
        can_view_project: Callable[[str], bool] | None = None,
    ) -> PinnedSlide | None:
        """Choose the deck override, then the accessible project reference."""
        with self.store.connect() as db:
            if db.execute("SELECT 1 FROM pinned_slides LIMIT 1").fetchone() is None:
                return None
        if deck_id:
            if can_view and not can_view(deck_id):
                return None
            try:
                project_id = self.presentation_project(deck_id)
            except ValueError, FileNotFoundError:
                return None
            pin = self._visible(self._row("deck", deck_id), project_id, can_view)
            if pin:
                return pin
        if not project_id or (can_view_project and not can_view_project(project_id)):
            return None
        return self._visible(self._row("project", project_id), project_id, can_view)

    def source(self, pin_id: str) -> dict[str, str]:
        """Read only the editable audience fields of the authorized selection."""
        with self.store.connect() as db:
            row = db.execute(
                "SELECT source FROM pinned_slides WHERE pin_id=?", (pin_id,)
            ).fetchone()
        if row is None:
            raise FileNotFoundError("The design reference changed. Read working context again.")
        return cast(dict[str, str], json.loads(cast(str, row[0])))

    def thumbnail(self, pin_id: str) -> bytes:
        """Lazily render from frozen bytes. A failed capture leaves the selection usable."""
        with self.store.connect() as db:
            row = db.execute(
                "SELECT thumbnail FROM pinned_slides WHERE pin_id=?", (pin_id,)
            ).fetchone()
            if row is None:
                raise FileNotFoundError("The design reference changed. Read working context again.")
            if row[0] is not None:
                return cast(bytes, row[0])
            frozen = db.execute(
                "SELECT render_document FROM pinned_slides WHERE pin_id=?", (pin_id,)
            ).fetchone()
            if frozen is None:
                raise FileNotFoundError("The design reference changed. Read working context again.")
        image = Snapshot.model_validate_json(zlib.decompress(cast(bytes, frozen[0])))
        data = render_thumbnail(image)
        with self.store.connect() as db:
            saved = db.execute(
                "UPDATE pinned_slides SET thumbnail=? WHERE pin_id=?", (data, pin_id)
            )
            if saved.rowcount != 1:
                raise FileNotFoundError("The design reference changed. Read working context again.")
        return data

    def state(self, deck_id: str, project: str, access: PinAccess) -> PinState:
        level = access.project_level(project) if access.project_level else 3
        return PinState(
            deck=PinSlot(
                pin=self._visible(self._row("deck", deck_id), project, access.can_view),
                editable=access.deck_editable,
            ),
            project=PinSlot(
                pin=self._visible(self._row("project", project), project, access.can_view)
                if level
                else None,
                editable=level >= 3,
            ),
        )

    def prepare(self, deck_id: str, target: str, body: PinChange) -> Selection:
        """Freeze the audience markup and the exact document passed to Chromium."""
        embedder = ScopedDeckEmbedder(self.store, deck_id)
        deck = self.store.read(deck_id)
        slide = self.store.locate(deck, body.slide_id)
        if self.store.content_hash(deck_id, slide) != body.content_hash:
            raise ConflictError("This slide changed. Reopen it before pinning.")
        source = json.dumps(
            {
                "html": slide.html,
                "interactive_html": slide.interactive_html,
                "theme_css": embedder.local_path("theme.css").read_text(),
            },
            ensure_ascii=False,
        )
        visual = slide
        if slide.interactive_html:
            document = prepare_document(self.store, deck_id, slide.interactive_html, scoped=True)
            visual = slide.model_copy(update={"interactive_html": document})
        image = snapshot(
            self.store,
            deck_id,
            visual,
            deck.slides.index(slide) + 1,
            OPTIONS,
            scoped_embedder=embedder,
            prepared_interactive=True,
        )
        reference = PinnedSlide(
            scope=body.scope,
            deck_id=deck_id,
            slide_id=slide.id,
            title=slide.title,
            content_hash=body.content_hash,
            pin_id=digest(json.dumps([body.scope, target, deck_id, slide.id, body.content_hash])),
        )
        return Selection(reference, source, image)

    def change_check(
        self, deck_id: str, project: str, body: PinChange, access: PinAccess
    ) -> tuple[str, str]:
        """Validate the edit scope and return the raw token for the final compare-and-swap."""
        if body.scope == "project":
            if access.project_level and access.project_level(project) < 3:
                raise FileNotFoundError("Project unavailable.")
            target = project
        else:
            if not access.deck_editable:
                raise FileNotFoundError("Presentation unavailable.")
            target = deck_id
        row = self._row(body.scope, target)
        current = self._visible(row, project, access.can_view)
        desired = bool(
            current
            and current.deck_id == deck_id
            and current.slide_id == body.slide_id
            and current.content_hash == body.content_hash
        )
        if (current.pin_id if current else "") != body.expected_pin_id and not (
            desired or (not body.slide_id and current is None)
        ):
            raise ConflictError("The design reference changed. Reopen the menu to review it.")
        return target, row.pin_id if row else ""

    def save(
        self,
        deck_id: str,
        project: str,
        body: PinChange,
        target: str,
        previous: str,
        selection: Selection | None,
    ) -> None:
        """Replace pointer and bytes together after revalidating membership and slide revision."""
        # Compress outside the write transaction so unrelated project edits can proceed.
        document = (
            zlib.compress(selection.image.model_dump_json().encode(), level=1)
            if selection
            else None
        )
        with self.store.editing(deck_id) as db:
            if legacy_project(db, deck_id) != project:
                raise ConflictError("The presentation moved. Reopen it before pinning.")
            row = db.execute(
                "SELECT pin_id FROM pinned_slides WHERE scope=? AND target=?", (body.scope, target)
            ).fetchone()
            actual = cast(str, row[0]) if row else ""
            if selection:
                current = self.store.locate(self.store.read(deck_id), body.slide_id)
                if self.store.content_hash(deck_id, current) != body.content_hash:
                    raise ConflictError("This slide changed. Reopen it before pinning.")
                if db.execute("SELECT 1 FROM archived_decks WHERE deck=?", (deck_id,)).fetchone():
                    raise FileNotFoundError("Presentation unavailable.")
            if actual == (selection.reference.pin_id if selection else ""):
                return
            if actual != previous:
                raise ConflictError("The design reference changed. Reopen the menu to review it.")
            if selection is None:
                _ = db.execute(
                    "DELETE FROM pinned_slides WHERE scope=? AND target=?", (body.scope, target)
                )
            else:
                pin = selection.reference
                _ = db.execute(
                    "INSERT OR REPLACE INTO pinned_slides(scope,target,pin_id,deck_id,slide_id,"
                    "title,content_hash,source,render_document) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        body.scope,
                        target,
                        pin.pin_id,
                        deck_id,
                        pin.slide_id,
                        pin.title,
                        pin.content_hash,
                        selection.source,
                        document,
                    ),
                )


def pinned_routes(access: Callable[[Request, str], AbstractContextManager[PinAccess]]) -> APIRouter:
    """Human-only controls shared by the desktop and grant-scoped browser editors."""
    router = APIRouter(prefix="/api/decks/{deck_id}/design-reference")

    @router.get("")
    def state(request: Request, deck_id: DeckID) -> PinState:
        with access(request, deck_id) as grant:
            pins = DesignReferences(grant.store)
            return pins.state(deck_id, pins.presentation_project(deck_id), grant)

    @router.post("")
    def change(request: Request, deck_id: DeckID, body: PinChange) -> PinState:
        if bool(body.slide_id) != bool(body.content_hash):
            raise ValueError("Select a slide and its displayed version.")
        with access(request, deck_id) as grant:
            pins = DesignReferences(grant.store)
            project = pins.presentation_project(deck_id)
            target, previous = pins.change_check(deck_id, project, body, grant)
            selection = pins.prepare(deck_id, target, body) if body.slide_id else None
            pins.save(deck_id, project, body, target, previous, selection)
            return pins.state(deck_id, project, grant)

    return router
