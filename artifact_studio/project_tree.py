"""Portable sidebar folders and project order, independent of artifact storage paths."""

import uuid
from collections import defaultdict
from typing import Literal

from pydantic import Field, model_validator

from .artifacts import ArtifactStore
from .models import StrictModel
from .storage import ConflictError, Store, digest, replace_text


class TreeNode(StrictModel):
    """One folder or project reference in sibling display order."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")
    kind: Literal["folder", "project"]
    parent_id: str | None = None
    title: str = Field(default="", max_length=200)


class TreeDocument(StrictModel):
    """Readable workspace navigation metadata without copied project contents."""

    version: Literal[1] = 1
    nodes: list[TreeNode] = Field(default_factory=list, max_length=10_000)

    @model_validator(mode="after")
    def valid_hierarchy(self) -> TreeDocument:
        """Reject missing parents, duplicate references and folder cycles."""
        nodes = {node.id: node for node in self.nodes}
        if len(nodes) != len(self.nodes):
            raise ValueError("Each project or folder must appear exactly once.")
        for node in self.nodes:
            if node.kind == "folder" and not node.title.strip():
                raise ValueError("Give the folder a name.")
            current = node
            visited = {node.id}
            while current.parent_id is not None:
                parent = nodes.get(current.parent_id)
                if parent is None or parent.kind != "folder":
                    raise ValueError("The destination folder does not exist.")
                if parent.id in visited:
                    raise ValueError("A folder cannot be placed inside itself.")
                visited.add(parent.id)
                if len(visited) > 32:
                    raise ValueError("Use at most 32 nested folder levels.")
                current = parent
        return self


class TreeView(TreeDocument):
    """The resolved sidebar with its optimistic concurrency token."""

    content_hash: str


class TreeChange(StrictModel):
    """A small version-bound navigation change from the human-facing sidebar."""

    action: Literal["create", "move", "rename", "remove"]
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    id: str | None = None
    title: str = Field(default="", max_length=200)
    parent_id: str | None = None
    before_id: str | None = None


class ProjectTree:
    """Keep grouping portable while serializing writes with the workspace database."""

    def __init__(self, store: Store) -> None:
        """Reuse the collection and database without adding a second source of project data."""
        self.store = store
        self.path = store.root / "project-tree.json"

    def view(self, project_ids: list[str] | None = None) -> TreeView:
        """Append newly discovered projects and omit stale project references on read."""
        if self.path.is_symlink():
            raise ValueError("Project navigation cannot be a linked file.")
        document = (
            TreeDocument.model_validate_json(self.path.read_text())
            if self.path.exists()
            else TreeDocument()
        )
        ids = project_ids
        if ids is None:
            ids = [project.id for project in ArtifactStore(self.store).list_projects()]
        projects = set(ids)
        if any(node.kind == "folder" and node.id in projects for node in document.nodes):
            raise ValueError("A folder cannot use an existing project's ID.")
        nodes = [node for node in document.nodes if node.kind == "folder" or node.id in projects]
        registered = {node.id for node in nodes}
        nodes.extend(TreeNode(id=id, kind="project") for id in ids if id not in registered)
        current = TreeDocument(nodes=nodes)
        return TreeView(nodes=nodes, content_hash=digest(current.model_dump_json()))

    def change(self, change: TreeChange) -> TreeView:
        """Save one guarded move or folder edit without moving any artifact source files."""
        registry = ArtifactStore(self.store)
        with self.store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            current = self.view([project.id for project in registry.list_projects()])
            if current.content_hash != change.expected_hash:
                raise ConflictError("Projects changed in another window. Try again.")
            nodes = current.nodes.copy()
            node = next((item for item in nodes if item.id == change.id), None)
            if change.action == "create":
                node = TreeNode(
                    id="folder-" + uuid.uuid4().hex[:16],
                    kind="folder",
                    title=change.title.strip(),
                    parent_id=change.parent_id,
                )
            elif node is None:
                raise FileNotFoundError("The project or folder no longer exists.")
            elif change.action in {"rename", "remove"} and node.kind != "folder":
                raise ValueError("Only folders can be renamed or removed here.")
            if change.action == "rename":
                nodes[nodes.index(node)] = node.model_copy(update={"title": change.title.strip()})
            elif change.action == "remove":
                siblings: defaultdict[str | None, list[TreeNode]] = defaultdict(list)
                for item in nodes:
                    siblings[item.parent_id].append(item)
                children = siblings[node.id]
                nodes = [item for item in nodes if item.id != node.id and item not in children]
                position = sum(
                    1 for item in current.nodes[: current.nodes.index(node)] if item in nodes
                )
                nodes[position:position] = [
                    item.model_copy(update={"parent_id": node.parent_id}) for item in children
                ]
            else:
                if change.before_id == node.id:
                    return current
                nodes = [item for item in nodes if item.id != node.id]
                node = node.model_copy(update={"parent_id": change.parent_id})
                if change.before_id is None:
                    nodes.append(node)
                else:
                    before = next((item for item in nodes if item.id == change.before_id), None)
                    if before is None or before.parent_id != change.parent_id:
                        raise ValueError("Choose a position inside the destination folder.")
                    nodes.insert(nodes.index(before), node)
            document = TreeDocument.model_validate({"nodes": nodes})
            replace_text(self.path, document.model_dump_json(indent=2) + "\n")
        return self.view()
