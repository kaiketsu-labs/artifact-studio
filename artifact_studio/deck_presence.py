"""Short-lived viewing and editing activity for the shared presentation editor."""

import re
import time
from dataclasses import dataclass

from pydantic import Field

from .models import StrictModel
from .multiplayer_access import AccessDenied, Actor, Scope, Sharing

PRESENCE_TTL = 45
AGENT_ACTIVITY_TTL = 90


class PresenceInput(StrictModel):
    """Identify a browser tab and its selected slide, or release its activity."""

    tab: str = Field(pattern=r"^[0-9a-f]{32}$")
    slide_id: str | None = Field(default=None, min_length=1, max_length=100)


class DeckViewer(StrictModel):
    """Expose human names or an anonymous agent marker to other readers."""

    name: str
    slide_id: str
    agent: bool = False


@dataclass(frozen=True)
class Viewing:
    """Track the most recent active tab for one person in one deck."""

    tab: str
    slide_id: str
    expires: float


@dataclass(frozen=True)
class Editing:
    """Identify an authorized agent's explicitly shared slide activity."""

    actor: Actor
    scope: Scope
    slide_id: str
    expires: float


class DeckPresence:
    """Keep transient activity in the single hosted server process."""

    def __init__(self, sharing: Sharing) -> None:
        self.sharing = sharing
        self.active: dict[tuple[Scope, str], Viewing] = {}
        self.editing: dict[str, Editing] = {}

    def _expire_editing(self, now: float) -> None:
        """Prune stale signals on the next update or edit."""
        for key in [key for key, entry in self.editing.items() if entry.expires <= now]:
            del self.editing[key]

    def set_editing(self, actor: Actor, scope: Scope, slide_id: str, active: bool) -> None:
        """Publish or clear a short-lived marker after validating the target and edit grant."""
        if not actor.agent or scope.kind != "artifact":
            raise AccessDenied
        with self.sharing.guard(actor, scope, "edit") as store:
            now = time.monotonic()
            self._expire_editing(now)
            current = self.editing.get(actor.agent)
            if not active:
                if current is not None and (current.scope, current.slide_id) == (scope, slide_id):
                    del self.editing[actor.agent]
                return
            try:
                exists = any(slide.id == slide_id for slide in store.read(scope.resource).slides)
            except FileNotFoundError, ValueError:
                raise AccessDenied from None
            if not exists:
                raise AccessDenied
            self.editing[actor.agent] = Editing(actor, scope, slide_id, now + AGENT_ACTIVITY_TTL)

    def update(self, actor: Actor, scope: Scope, body: PresenceInput) -> list[DeckViewer]:
        """Refresh or release a tab and return other people with current view access."""
        if actor.agent:
            raise AccessDenied
        with self.sharing.locked():
            now = time.monotonic()
            self._expire_editing(now)
            for expired in [key for key, view in self.active.items() if view.expires <= now]:
                del self.active[expired]
            nearby = {person for target, person in self.active if target == scope}
            contacts = self.sharing.human_contacts(scope, nearby | {actor.person})
            if actor.person not in contacts:
                raise AccessDenied
            store = self.sharing.store(scope.workspace)
            try:
                deck = store.read(scope.resource)
            except FileNotFoundError, ValueError:
                raise AccessDenied from None
            slides = {slide.id for slide in deck.slides}
            if body.slide_id is not None and body.slide_id not in slides:
                raise AccessDenied
            key = (scope, actor.person)
            if body.slide_id is None:
                current = self.active.get(key)
                if current is not None and current.tab == body.tab:
                    del self.active[key]
                return []
            self.active[key] = Viewing(body.tab, body.slide_id, now + PRESENCE_TTL)
            viewers: list[DeckViewer] = []
            for person, email in contacts.items():
                if person == actor.person:
                    continue
                view = self.active[scope, person]
                if view.slide_id not in slides:
                    continue
                local = email.partition("@")[0].partition("+")[0]
                name = re.sub(r"[._-]+", " ", local).strip().title()
                viewers.append(DeckViewer(name=name[:60] or "Collaborator", slide_id=view.slide_id))
            editing_slides: set[str] = set()
            for key, edit in list(self.editing.items()):
                if edit.scope != scope:
                    continue
                try:
                    self.sharing.require(edit.actor, scope, "edit")
                except AccessDenied:
                    del self.editing[key]
                    continue
                if edit.slide_id not in slides:
                    del self.editing[key]
                else:
                    editing_slides.add(edit.slide_id)
            viewers.extend(
                DeckViewer(name="Agent working", slide_id=slide_id, agent=True)
                for slide_id in editing_slides
            )
            return sorted(viewers, key=lambda viewer: (viewer.name, viewer.slide_id))
