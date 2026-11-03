"""Exercise updates and recovery against real disposable Git checkouts."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from artifact_studio import updater


@dataclass(frozen=True)
class Checkout:
    """Hold an isolated installed version and its fictional upstream."""

    project: Path
    current: str
    target: str


@pytest.fixture
def checkout(tmp_path: Path) -> Checkout:
    """Create two published versions while keeping workspace data outside Git."""
    remote, author, installed = (tmp_path / name for name in ("remote.git", "author", "installed"))
    remote.mkdir()
    updater.git(remote, "init", "--bare", "--initial-branch=main")
    author.mkdir()
    updater.git(author, "init", "--initial-branch=main")
    updater.git(author, "config", "user.name", "Updater test")
    updater.git(author, "config", "user.email", "updater@example.invalid")
    (author / ".gitignore").write_text(".venv/\n.state/\n")
    (author / "app.txt").write_text("old version")
    updater.git(author, "add", ".")
    updater.git(author, "commit", "-m", "chore(test): create old version")
    current = updater.git(author, "rev-parse", "HEAD")
    updater.git(author, "remote", "add", "origin", str(remote))
    updater.git(author, "push", "origin", "main")
    updater.git(tmp_path, "clone", str(remote), str(installed))
    (installed / ".venv").mkdir()
    (installed / ".venv/dependency.txt").write_text("old dependency")
    (installed / ".state").mkdir()
    (installed / ".state/review.sqlite3").write_bytes(b"private review sentinel")
    (author / "app.txt").write_text("new version")
    updater.git(author, "commit", "-am", "feat(test): publish new version")
    target = updater.git(author, "rev-parse", "HEAD")
    updater.git(author, "push", "origin", "main")
    return Checkout(installed, current, target)


def install_dependencies(project: Path) -> None:
    """Represent an installed dependency change without downloading real packages."""
    (project / ".venv/dependency.txt").write_text("new dependency")


def assert_private_state(checkout: Checkout) -> None:
    """Keep review data byte-for-byte unchanged through either outcome."""
    assert (checkout.project / ".state/review.sqlite3").read_bytes() == b"private review sentinel"


def test_check_and_apply_keep_workspace_state(
    checkout: Checkout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Install a fast-forward update and preserve ignored workspace files."""
    plan = updater.check_update(checkout.project)
    assert (plan.current, plan.target, plan.commits) == (checkout.current, checkout.target, 1)
    monkeypatch.setattr(updater, "sync_environment", install_dependencies)
    assert updater.apply_update(checkout.project, plan) == checkout.target
    assert (checkout.project / "app.txt").read_text() == "new version"
    assert (checkout.project / ".venv/dependency.txt").read_text() == "new dependency"
    assert_private_state(checkout)
    assert updater.check_update(checkout.project).commits == 0


@pytest.mark.parametrize("untracked", [False, True])
def test_unfinished_edits_are_preserved(checkout: Checkout, untracked: bool) -> None:
    """Refuse an update before changing source or dependencies when work is unfinished."""
    edited = checkout.project / ("new-source.txt" if untracked else "app.txt")
    edited.write_text("unfinished human edit")
    with pytest.raises(updater.UpdateError, match="Commit or move"):
        updater.check_update(checkout.project)
    assert edited.read_text() == "unfinished human edit"
    assert updater.git(checkout.project, "rev-parse", "HEAD") == checkout.current
    assert_private_state(checkout)


def test_local_commits_are_never_reset(checkout: Checkout) -> None:
    """A divergent local commit remains intact instead of being replaced by main."""
    updater.git(checkout.project, "config", "user.name", "Updater test")
    updater.git(checkout.project, "config", "user.email", "updater@example.invalid")
    (checkout.project / "app.txt").write_text("local improvement")
    updater.git(checkout.project, "commit", "-am", "feat(test): keep local work")
    local = updater.git(checkout.project, "rev-parse", "HEAD")
    with pytest.raises(updater.UpdateError, match="reconciling"):
        updater.check_update(checkout.project)
    assert updater.git(checkout.project, "rev-parse", "HEAD") == local


def test_stale_plan_is_rejected(checkout: Checkout) -> None:
    """Do not stop at an outdated version if the checkout changes after confirmation."""
    plan = updater.check_update(checkout.project)
    updater.git(checkout.project, "merge", "--ff-only", checkout.target)
    with pytest.raises(updater.UpdateError, match="changed since"):
        updater.apply_update(checkout.project, plan)
    assert (checkout.project / ".venv/dependency.txt").read_text() == "old dependency"


def test_failed_install_restores_source_and_environment(
    checkout: Checkout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recover from a dependency installation that has already changed the environment."""
    plan = updater.check_update(checkout.project)

    def fail(project: Path) -> None:
        install_dependencies(project)
        (project / ".venv/partial.txt").write_text("partial installation")
        raise updater.UpdateError("fictional network failure")

    monkeypatch.setattr(updater, "sync_environment", fail)
    with pytest.raises(updater.UpdateError, match="previous installation was restored") as failure:
        updater.apply_update(checkout.project, plan)
    assert failure.value.restart_safe
    assert updater.git(checkout.project, "rev-parse", "HEAD") == checkout.current
    assert (checkout.project / "app.txt").read_text() == "old version"
    assert (checkout.project / ".venv/dependency.txt").read_text() == "old dependency"
    assert not (checkout.project / ".venv/partial.txt").exists()
    assert_private_state(checkout)


@pytest.mark.parametrize("fail_build", [False, True])
def test_native_app_replacement_is_prepared_before_install(
    checkout: Checkout, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fail_build: bool
) -> None:
    """Keep the installed app usable if compilation fails and replace it only after success."""
    application = tmp_path / "Artifact Studio.app"
    application.mkdir()
    (application / "binary").write_text("old controller")
    settings = updater.AppSettings(
        application, tmp_path / "collection", tmp_path / "review.db", 8799, False
    )
    plan = updater.check_update(checkout.project)

    def build(project: Path, settings: updater.AppSettings, destination: Path) -> None:
        assert project == checkout.project and settings.application == application
        if fail_build:
            raise updater.UpdateError("fictional compiler failure")
        destination.mkdir()
        (destination / "binary").write_text("new controller")

    monkeypatch.setattr(updater, "sync_environment", install_dependencies)
    monkeypatch.setattr(updater, "build_controller", build)
    if fail_build:
        with pytest.raises(updater.UpdateError, match="previous installation was restored"):
            updater.apply_update(checkout.project, plan, settings)
    else:
        updater.apply_update(checkout.project, plan, settings)
    assert (application / "binary").read_text() == (
        "old controller" if fail_build else "new controller"
    )
    assert not application.with_name(application.name + ".previous").exists()
    assert_private_state(checkout)


def test_concurrent_source_edit_is_not_erased(
    checkout: Checkout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retain a recovery environment instead of resetting work created during installation."""
    plan = updater.check_update(checkout.project)

    def fail(project: Path) -> None:
        install_dependencies(project)
        (project / "app.txt").write_text("concurrent human edit")
        raise updater.UpdateError("fictional install failure")

    monkeypatch.setattr(updater, "sync_environment", fail)
    with pytest.raises(updater.UpdateError, match="Recovery needs attention") as failure:
        updater.apply_update(checkout.project, plan)
    assert not failure.value.restart_safe
    assert (checkout.project / "app.txt").read_text() == "concurrent human edit"
    preserved = checkout.project.parent / f"artifact-studio-recovery-{checkout.current[:12]}"
    assert (preserved / "dependency.txt").read_text() == "old dependency"
    assert_private_state(checkout)


def test_concurrent_update_is_rejected(checkout: Checkout) -> None:
    """Serialize native updater instances without disrupting the first one."""
    with (
        updater.update_lock(checkout.project),
        pytest.raises(updater.UpdateError, match="already in progress"),
    ):
        updater.check_update(checkout.project)
