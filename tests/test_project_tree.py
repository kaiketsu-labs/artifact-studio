"""Persistent nested project navigation, source safety and concurrent editing."""

from concurrent.futures import ThreadPoolExecutor
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
