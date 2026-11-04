"""Small, shared quick-access lists without loading artifact bodies or feedback."""

import json
import time
from typing import Self, cast
from urllib.parse import urlencode

from pydantic import Field, TypeAdapter, model_validator

from .artifact_models import ArtifactManifest, ProjectView
from .artifacts import LEGACY_PROJECT, ArtifactStore
from .models import StrictModel
from .storage import Store

RECENT_KEY = "navigation:recent-projects"


class NavigationEntry(StrictModel):
    """A verified local destination with optional live-review identity."""

    id: str
    title: str
    url: str
    project_id: str
    project_title: str
    harness: str = ""


class NavigationSnapshot(StrictModel):
    """Connected work and the last five human-opened projects."""

    connected: list[NavigationEntry]
    recent: list[NavigationEntry]


class NavigationVisit(StrictModel):
    """Record one explicitly opened project or artifact, never an agent context read."""

    project_id: str = Field(default="", max_length=100)
    artifact_id: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def one_target(self) -> Self:
        """Require a single unambiguous navigation target."""
        if bool(self.project_id) == bool(self.artifact_id):
            raise ValueError("Choose one project or artifact.")
        return self


class Navigation:
    """Keep human recents durable while reflecting only unexpired agent leases."""

    def __init__(self, store: Store) -> None:
        """Reuse the workspace registry and its transactional settings."""
        self.store = store
        _ = ArtifactStore(store)

    def _projects(self) -> dict[str, ProjectView]:
        with self.store.connect() as db:
            projects = {
                cast(str, row["id"]): ProjectView.model_validate_json(cast(str, row["document"]))
                for row in db.execute("SELECT id,document FROM artifact_projects")
            }
            archived = {cast(str, row[0]) for row in db.execute("SELECT deck FROM archived_decks")}
        if any(
            path.parent.name not in archived and not (path.parent / "design.json").exists()
            for path in self.store.root.glob("*/index.html")
        ):
            projects[LEGACY_PROJECT] = ProjectView(
                id=LEGACY_PROJECT, title="Presentations", legacy=True
            )
        return projects

    def _artifact(self, artifact_id: str) -> tuple[str, str, str]:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT document FROM artifact_items WHERE id=?", (artifact_id,)
            ).fetchone()
            if row:
                artifact = ArtifactManifest.model_validate_json(cast(str, row[0]))
                return artifact.project_id, artifact.title, "artifact"
            if db.execute("SELECT 1 FROM archived_decks WHERE deck=?", (artifact_id,)).fetchone():
                raise FileNotFoundError("Presentation is no longer in the library.")
            binding = db.execute(
                "SELECT project_id FROM artifact_legacy_projects WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
        folder = self.store.folder(artifact_id)
        if (folder / "design.json").exists() or (folder / "design-context.json").exists():
            raise FileNotFoundError("Design examples are not recent projects.")
        deck = self.store.read(artifact_id)
        return cast(str, binding[0]) if binding else LEGACY_PROJECT, deck.title, "deck"

    def snapshot(self) -> NavigationSnapshot:
        """Read compact destinations without changing recency or extending agent leases."""
        projects = self._projects()
        with self.store.connect() as db:
            row = db.execute(
                "SELECT value FROM workspace_settings WHERE key=?", (RECENT_KEY,)
            ).fetchone()
            recent = TypeAdapter(list[str]).validate_json(str(row[0])) if row else []
            leases = db.execute(
                "SELECT deck,harness FROM review_sessions WHERE enabled=1 AND expires_at>? "
                "AND deck<>'' ORDER BY expires_at DESC",
                (time.time(),),
            ).fetchall()
        connected: dict[str, NavigationEntry] = {}
        for lease in leases:
            identifier = cast(str, lease["deck"])
            if identifier in connected:
                continue
            try:
                project_id, title, kind = self._artifact(identifier)
            except FileNotFoundError, ValueError:
                continue
            project = projects.get(project_id)
            if project:
                connected[identifier] = NavigationEntry(
                    id=identifier,
                    title=title,
                    url="/?" + urlencode({kind: identifier}),
                    project_id=project_id,
                    project_title=project.title,
                    harness=cast(str, lease["harness"]),
                )
        return NavigationSnapshot(
            connected=sorted(
                connected.values(),
                key=lambda entry: (entry.project_title.casefold(), entry.title.casefold()),
            ),
            recent=[
                NavigationEntry(
                    id=identifier,
                    title=projects[identifier].title,
                    url="/?" + urlencode({"project": identifier}),
                    project_id=identifier,
                    project_title=projects[identifier].title,
                )
                for identifier in dict.fromkeys(recent)
                if identifier in projects
            ][:5],
        )

    def visit(self, body: NavigationVisit) -> NavigationSnapshot:
        """Move a real, human-opened project to the front without storing file contents."""
        if body.artifact_id and (self.store.folder(body.artifact_id) / "design.json").exists():
            return self.snapshot()
        identifier = self._artifact(body.artifact_id)[0] if body.artifact_id else body.project_id
        projects = self._projects()
        if identifier not in projects:
            raise FileNotFoundError("Project no longer exists.")
        with self.store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value FROM workspace_settings WHERE key=?", (RECENT_KEY,)
            ).fetchone()
            previous = TypeAdapter(list[str]).validate_json(str(row[0])) if row else []
            recent = [
                identifier,
                *(
                    item
                    for item in dict.fromkeys(previous)
                    if item != identifier and item in projects
                ),
            ][:5]
            _ = db.execute(
                "INSERT OR REPLACE INTO workspace_settings VALUES (?,?)",
                (RECENT_KEY, json.dumps(recent)),
            )
        return self.snapshot()
