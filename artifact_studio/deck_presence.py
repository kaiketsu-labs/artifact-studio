"""Short-lived human viewing activity for the shared presentation editor."""

import re
import time
from dataclasses import dataclass

from pydantic import Field

from .models import StrictModel
from .multiplayer_access import AccessDenied, Actor, Scope, Sharing

PRESENCE_TTL = 45


class PresenceInput(StrictModel):
    """Identify a browser tab and its selected slide, or release its activity."""

    tab: str = Field(pattern=r"^[0-9a-f]{32}$")
    slide_id: str | None = Field(default=None, min_length=1, max_length=100)


class DeckViewer(StrictModel):
    """Expose a readable name and a slide ID to other readers of this deck."""

    name: str
    slide_id: str


@dataclass(frozen=True)
class Viewing:
    """Track the most recent active tab for one person in one deck."""

    tab: str
    slide_id: str
    expires: float


class DeckPresence:
    """Keep transient activity in the single hosted server process."""

    def __init__(self, sharing: Sharing) -> None:
        self.sharing = sharing
        self.active: dict[tuple[Scope, str], Viewing] = {}

    def update(self, actor: Actor, scope: Scope, body: PresenceInput) -> list[DeckViewer]:
        """Refresh or release a tab and return other people with current view access."""
        if actor.agent:
            raise AccessDenied
        with self.sharing.locked():
            now = time.monotonic()
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
            return sorted(viewers, key=lambda viewer: (viewer.name, viewer.slide_id))
