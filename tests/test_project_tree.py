"""Persistent nested project navigation, source safety and concurrent editing."""

import sqlite3
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.project_tree import ProjectTree, TreeChange, TreeDocument, TreeNode, TreeView
from artifact_studio.storage import ConflictError, Store


@pytest.fixture
def tree(tmp_path: Path) -> ProjectTree:
    """Create two real projects without using the user's workspace."""
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    registry = ArtifactStore(store)
    _ = registry.create_project("Allianz OCR", "Preserve this brief")
    _ = registry.create_project("Travel", "Separate audience")
    return ProjectTree(store)


def folder(tree: ProjectTree, title: str, parent: str | None = None) -> tuple[str, TreeView]:
    """Create a folder against a current revision and return its assigned identity."""
    before = tree.view()
    after = tree.change(
        TreeChange(
            action="create", title=title, parent_id=parent, expected_hash=before.content_hash
        )
    )
    id = next(node.id for node in after.nodes if node.id not in {item.id for item in before.nodes})
    return id, after


def test_nested_moves_reordering_and_reopen_preserve_project_sources(tree: ProjectTree) -> None:
    """Group and reorder projects durably while their briefs and storage paths stay intact."""
    projects = ArtifactStore(tree.store).list_projects()
    original = {project.id: (project.source_path, project.context) for project in projects}
    outer, _ = folder(tree, "Allianz")
    inner, current = folder(tree, "Documents", outer)
    current = tree.change(
        TreeChange(
            action="move", id=projects[0].id, parent_id=inner, expected_hash=current.content_hash
        )
    )
    current = tree.change(
        TreeChange(
            action="move", id=outer, before_id=projects[1].id, expected_hash=current.content_hash
        )
    )
    assert [node.id for node in current.nodes if node.parent_id is None] == [outer, projects[1].id]
    assert next(node for node in current.nodes if node.id == projects[0].id).parent_id == inner
    assert ProjectTree(tree.store).view() == current
    assert {
        project.id: (project.source_path, project.context)
        for project in ArtifactStore(tree.store).list_projects()
    } == original


def test_folder_removal_keeps_contents_and_relative_position(tree: ProjectTree) -> None:
    """Remove organizational metadata while promoting children beside their former folder."""
    outer, _ = folder(tree, "Allianz")
    inner, current = folder(tree, "Archive", outer)
    project = next(node for node in current.nodes if node.kind == "project")
    current = tree.change(
        TreeChange(
            action="move", id=project.id, parent_id=inner, expected_hash=current.content_hash
        )
    )
    current = tree.change(
        TreeChange(action="rename", id=inner, title="Research", expected_hash=current.content_hash)
    )
    assert next(node for node in current.nodes if node.id == inner).title == "Research"
    current = tree.change(TreeChange(action="remove", id=inner, expected_hash=current.content_hash))
    assert next(node for node in current.nodes if node.id == project.id).parent_id == outer
    assert inner not in {node.id for node in current.nodes}
    assert len(ArtifactStore(tree.store).list_projects()) == 2


def test_cycles_invalid_siblings_and_stale_writes_leave_saved_tree_unchanged(
    tree: ProjectTree,
) -> None:
    """Reject invalid drags and stale windows before replacing the canonical tree."""
    outer, _ = folder(tree, "Allianz")
    inner, current = folder(tree, "Documents", outer)
    saved = tree.path.read_bytes()
    for change in (
        TreeChange(action="move", id=outer, parent_id=inner, expected_hash=current.content_hash),
        TreeChange(action="move", id=outer, parent_id=outer, expected_hash=current.content_hash),
        TreeChange(action="move", id=outer, before_id=inner, expected_hash=current.content_hash),
        TreeChange(action="rename", id=outer, title="  ", expected_hash=current.content_hash),
        TreeChange(action="move", id=outer, expected_hash="0" * 64),
    ):
        with pytest.raises(ValueError):
            _ = tree.change(change)
        assert tree.path.read_bytes() == saved


def test_simultaneous_edits_require_a_fresh_revision(tree: ProjectTree) -> None:
    """Only one client may successfully replace the same sidebar revision."""
    expected = tree.view().content_hash

    def create(title: str) -> bool:
        """Return whether this competing client saved its folder."""
        try:
            _ = tree.change(TreeChange(action="create", title=title, expected_hash=expected))
        except ConflictError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, ["Allianz", "Personal"]))
    assert sorted(results) == [False, True]
    assert len([node for node in tree.view().nodes if node.kind == "folder"]) == 1


def test_discovery_and_validation_do_not_hide_new_projects(tree: ProjectTree) -> None:
    """Append new work while rejecting a malformed saved hierarchy or redirected file."""
    _, current = folder(tree, "Allianz")
    added = ArtifactStore(tree.store).create_project("New project")
    assert tree.view().nodes[-1].id == added.id
    with pytest.raises(ConflictError):
        _ = tree.change(
            TreeChange(action="create", title="Stale", expected_hash=current.content_hash)
        )
    with pytest.raises(ValueError):
        _ = TreeDocument(
            nodes=[TreeNode(id="folder-a", kind="folder", title="A", parent_id="missing")]
        )
    with pytest.raises(ValueError):
        _ = tree.change(
            TreeChange(action="remove", id=added.id, expected_hash=tree.view().content_hash)
        )
    tree.path.unlink()
    target = tree.store.root / "unrelated.json"
    _ = target.write_text("untouched")
    tree.path.symlink_to(target)
    with pytest.raises(ValueError, match="linked"):
        _ = tree.view()
    assert target.read_text() == "untouched"


def test_folder_receipt_keeps_its_committed_identity_after_another_editor_writes(
    tree: ProjectTree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later remove/create may obsolete a receipt, but must not substitute another folder."""
    connect = tree.store.connect
    interleaved = False

    @contextmanager
    def after_commit() -> Generator[sqlite3.Connection]:
        nonlocal interleaved
        with connect() as db:
            yield db
            wrote = db.in_transaction
        if (
            wrote
            and not interleaved
            and tree.path.exists()
            and "Requested folder" in tree.path.read_text()
        ):
            interleaved = True
            other = ProjectTree(tree.store)
            current = other.view()
            requested = next(node for node in current.nodes if node.title == "Requested folder")
            removed = other.change(
                TreeChange(action="remove", id=requested.id, expected_hash=current.content_hash)
            )
            _ = other.change(
                TreeChange(
                    action="create", title="Other editor folder", expected_hash=removed.content_hash
                )
            )

    expected = tree.view().content_hash
    monkeypatch.setattr(tree.store, "connect", after_commit)
    receipt = tree.create_folder("Requested folder", expected)
    assert interleaved
    assert receipt["title"] == "Requested folder"
    current = tree.view()
    assert receipt["id"] not in {node.id for node in current.nodes}
    assert receipt["tree_hash"] != current.content_hash
    assert any(node.title == "Other editor folder" for node in current.nodes)


def test_local_registered_tools_share_project_folder_workflow(tree: ProjectTree) -> None:
    """Local agents use the same hash receipts without losing existing create_project fields."""
    import asyncio

    from mcp.types import CallToolResult

    from artifact_studio.artifact_tools import register_artifact_tools
    from artifact_studio.tool_server import ToolServer

    server = ToolServer[None]("Fictional local project tools")
    register_artifact_tools(server, lambda: tree.store)

    async def call(name: str, arguments: dict[str, object]) -> dict[str, object]:
        result = await server.call_tool(name, arguments)
        assert isinstance(result, CallToolResult)
        assert not result.is_error and result.structured_content is not None
        return result.structured_content

    async def exercise() -> None:
        current = await call("get_project_tree", {})
        folder = await call(
            "create_project_folder",
            {"title": "Fictional collection", "expected_tree_hash": current["tree_hash"]},
        )
        project = await call(
            "create_project",
            {"title": "Fictional project", "context": "Keep local metadata compatible"},
        )
        assert project["context"] == "Keep local metadata compatible"
        assert project["source_path"]
        moved = await call(
            "move_project_to_folder",
            {
                "project_id": project["id"],
                "folder_id": folder["id"],
                "expected_tree_hash": project["tree_hash"],
            },
        )
        assert moved["folder_id"] == folder["id"]

    asyncio.run(exercise())


def test_folder_receipt_rejects_unbound_baseline_before_external_restore(tree: ProjectTree) -> None:
    """An A→B→A external restore must not let a restored old folder masquerade as the new one."""
    original, state_a = folder(tree, "Existing folder")
    saved_a = tree.path.read_bytes()
    _ = tree.change(TreeChange(action="remove", id=original, expected_hash=state_a.content_hash))
    saved_b = tree.path.read_bytes()

    class RestoringTree(ProjectTree):
        def change(self, change: TreeChange) -> TreeView:
            # Simulate portable metadata restored after create_folder's preflight read.
            _ = self.path.write_bytes(saved_a)
            return super().change(change)

    with pytest.raises(ConflictError):
        _ = RestoringTree(tree.store).create_folder("Requested folder", state_a.content_hash)
    assert tree.path.read_bytes() == saved_b
