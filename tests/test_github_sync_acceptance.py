"""Exercise the opt-in GitHub acceptance harness using a disposable real Git origin."""

from pathlib import Path

import pytest

from artifact_studio.multiplayer_access import Sharing
from artifact_studio.project_sync import GitHubProject
from scripts import verify_github_sync
from scripts.verify_github_sync import MARKER, MARKER_PREFIX, Acceptance
from tests.test_project_github_sync import REPOSITORY, git
from tests.test_project_github_sync import sync_fixture as sync_fixture


def test_fictional_acceptance_scenarios_over_real_git(
    tmp_path: Path,
    sync_fixture: tuple[Path, Sharing, str, str, GitHubProject, Path],
) -> None:
    """Local transport validates the harness, not GitHub permissions or kernel quotas."""
    editor, _, _, _, _, scratch = sync_fixture
    (editor / MARKER_PREFIX).mkdir()
    (editor / MARKER_PREFIX / "fixture.json").write_bytes(MARKER)
    git(editor, "add", "--all")
    git(editor, "commit", "-m", "test: dedicated fictional fixture marker")
    git(editor, "push")
    check = Acceptance.create(tmp_path / "acceptance", scratch, REPOSITORY, "main")
    report = check.exercise()
    assert len(check.measurements) == 18
    assert report["non_owner_denied"] is True
    assert report["private_state_preserved"] is True
    names = {item["scenario"] for item in check.measurements}
    assert {
        "independent_merge",
        "content_conflict",
        "order_conflict",
        "slide_delete_edit_conflict",
        "asset_delete_edit_conflict",
        "interrupted_after_successful_push",
        "restart_after_push",
        "competing_push_rejected",
        "competing_edit_conflict",
        "final_unchanged",
    } <= names


@pytest.mark.parametrize(
    ("repository", "branch", "authorize"),
    [
        ("fictional/personal-presentations", "acceptance/fixture", True),
        ("fictional/artifact-studio-sync-acceptance-fixture", "main", True),
        ("fictional/artifact-studio-sync-acceptance-fixture", "acceptance/fixture", False),
    ],
)
def test_cli_refuses_unsafe_targets_before_accessing_network_or_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repository: str, branch: str, authorize: bool
) -> None:
    workspace = tmp_path / "must-not-exist"
    args = [
        "verify_github_sync",
        "--repository",
        repository,
        "--branch",
        branch,
        "--scratch",
        str(tmp_path),
        "--workspace",
        str(workspace),
        "--output",
        str(tmp_path / "result.json"),
    ]
    if authorize:
        args.append("--allow-private-fixture-writes")
    monkeypatch.setattr("sys.argv", args)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Unsafe target reached network/storage preflight")

    monkeypatch.setattr(verify_github_sync, "_verify_scratch", forbidden)
    with pytest.raises(SystemExit) as error:
        verify_github_sync.main()
    assert error.value.code == 2
    assert not workspace.exists()


def test_acceptance_refuses_existing_workspace(tmp_path: Path) -> None:
    root = tmp_path / "existing"
    root.mkdir()
    sentinel = root / "keep.txt"
    sentinel.write_text("Untouched local data")
    with pytest.raises(FileExistsError):
        Acceptance.create(root, tmp_path, "fictional/demo", "acceptance/fixture")
    assert sentinel.read_text() == "Untouched local data"
