"""Exercise updates and recovery against real disposable Git checkouts."""

import json
import sys
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

    @property
    def author(self) -> Path:
        return self.project.parent / "author"


def publish_tag(author: Path, name: str, commit: str = "HEAD", *, annotated: bool = False) -> None:
    """Publish a fictional release tag through the same Git transport as the updater."""
    if annotated:
        updater.git(author, "tag", "-a", name, commit, "-m", name)
    else:
        updater.git(author, "tag", name, commit)
    updater.git(author, "push", "origin", f"refs/tags/{name}")


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
    publish_tag(author, "v0.1.0")
    updater.git(tmp_path, "clone", str(remote), str(installed))
    (installed / ".venv").mkdir()
    (installed / ".venv/dependency.txt").write_text("old dependency")
    (installed / ".state").mkdir()
    (installed / ".state/review.sqlite3").write_bytes(b"private review sentinel")
    (author / "app.txt").write_text("new version")
    updater.git(author, "commit", "-am", "feat(test): publish new version")
    target = updater.git(author, "rev-parse", "HEAD")
    updater.git(author, "push", "origin", "main")
    publish_tag(author, "v0.2.0")
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
    assert plan.release_tag == "v0.2.0" and not plan.ahead
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


def test_release_selection_ignores_main_tip_prereleases_and_local_tags(checkout: Checkout) -> None:
    """Install a stable tagged snapshot, keeping newer source commits out of the update."""
    (checkout.author / "app.txt").write_text("unreleased work")
    updater.git(checkout.author, "commit", "-am", "feat(test): unreleased work")
    updater.git(checkout.author, "push", "origin", "main")
    publish_tag(checkout.author, "v9.0.0-rc.1")
    publish_tag(checkout.author, "backup-2026")
    updater.git(checkout.project, "tag", "v99.0.0")
    plan = updater.check_update(checkout.project)
    assert plan.target == checkout.target and plan.release_tag == "v0.2.0"
    assert updater.git(checkout.project, "rev-parse", "refs/tags/v99.0.0") == checkout.current


def test_annotated_tags_sort_by_version_not_creation_or_text_order(checkout: Checkout) -> None:
    """A later-created 0.9 release must not displace an annotated 0.10 release."""
    publish_tag(checkout.author, "v0.10.0", annotated=True)
    publish_tag(checkout.author, "v0.9.0")
    plan = updater.check_update(checkout.project)
    assert plan.target == checkout.target and plan.release_tag == "v0.10.0"


def test_unreleased_checkout_ahead_of_latest_release_is_not_downgraded(checkout: Checkout) -> None:
    """A developer checkout on origin/main stays put until a newer release appears."""
    updater.git(checkout.author, "push", "origin", ":refs/tags/v0.2.0")
    updater.git(checkout.project, "fetch", "origin", "main")
    updater.git(checkout.project, "merge", "--ff-only", checkout.target)
    plan = updater.check_update(checkout.project)
    assert plan.ahead and plan.release_tag == "v0.1.0"
    assert plan.current == plan.target == checkout.target
    assert updater.apply_update(checkout.project, plan) == checkout.target
    assert_private_state(checkout)


def test_no_stable_release_fails_without_falling_back_to_main(checkout: Checkout) -> None:
    """Do not install an arbitrary commit if no stable release is published."""
    updater.git(checkout.author, "push", "origin", ":refs/tags/v0.1.0", ":refs/tags/v0.2.0")
    publish_tag(checkout.author, "v3.0.0-beta.1")
    with pytest.raises(updater.UpdateError, match="No stable release"):
        updater.check_update(checkout.project)
    assert updater.git(checkout.project, "rev-parse", "HEAD") == checkout.current


def test_remote_tag_deletion_prunes_private_refs_without_deleting_local_tags(
    checkout: Checkout,
) -> None:
    """Refresh remote release inventory without trusting or overwriting local tags."""
    updater.git(checkout.project, "tag", "v0.2.0", checkout.current)
    assert updater.check_update(checkout.project).release_tag == "v0.2.0"
    updater.git(checkout.author, "push", "origin", ":refs/tags/v0.2.0")
    assert updater.check_update(checkout.project).release_tag == "v0.1.0"
    assert updater.git(checkout.project, "rev-parse", "refs/tags/v0.2.0") == checkout.current


@pytest.mark.parametrize("change", ["move", "delete", "new_release"])
def test_release_changes_between_check_and_apply_are_rejected(
    checkout: Checkout, change: str
) -> None:
    """Never install a different release than the one the user just reviewed."""
    plan = updater.check_update(checkout.project)
    if change == "move":
        updater.git(checkout.author, "tag", "-f", "v0.2.0", checkout.current)
        updater.git(checkout.author, "push", "--force", "origin", "refs/tags/v0.2.0")
    elif change == "delete":
        updater.git(checkout.author, "push", "origin", ":refs/tags/v0.2.0")
    else:
        publish_tag(checkout.author, "v0.3.0")
    with pytest.raises(updater.UpdateError, match="changed since"):
        updater.apply_update(checkout.project, plan)
    assert updater.git(checkout.project, "rev-parse", "HEAD") == checkout.current
    assert (checkout.project / ".venv/dependency.txt").read_text() == "old dependency"


def test_release_outside_origin_main_is_refused(checkout: Checkout) -> None:
    """An unrelated stable tag must not replace the installed application."""
    updater.git(checkout.author, "checkout", "--orphan", "other")
    updater.git(checkout.author, "commit", "-m", "chore(test): unrelated history")
    publish_tag(checkout.author, "v1.0.0")
    with pytest.raises(updater.UpdateError, match="not part of origin/main"):
        updater.check_update(checkout.project)
    assert updater.git(checkout.project, "rev-parse", "HEAD") == checkout.current


def test_apply_cannot_install_an_untagged_main_commit(checkout: Checkout) -> None:
    """The apply command validates releases independently of caller-supplied commits."""
    (checkout.author / "app.txt").write_text("not released")
    updater.git(checkout.author, "commit", "-am", "feat(test): not released")
    unreleased = updater.git(checkout.author, "rev-parse", "HEAD")
    updater.git(checkout.author, "push", "origin", "main")
    with pytest.raises(updater.UpdateError, match="changed since"):
        updater.apply_update(checkout.project, updater.UpdatePlan(checkout.current, unreleased, 0))


@pytest.mark.parametrize("include_nightly", [False, True])
def test_native_check_apply_protocol_carries_the_selected_release(
    checkout: Checkout,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    include_nightly: bool,
) -> None:
    """The native controller receives a release label and can apply that exact plan."""
    target = publish_nightly(checkout) if include_nightly else checkout.target
    tag = "nightly-20261106.123" if include_nightly else "v0.2.0"
    channel = ["--include-nightly"] if include_nightly else []
    monkeypatch.chdir(checkout.project)
    monkeypatch.setattr(sys, "argv", ["updater", "check", *channel])
    assert updater.main() == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["release_tag"] == tag and plan["ahead"] is False
    assert plan["include_nightly"] is include_nightly
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "updater",
            "apply",
            "--current",
            plan["current"],
            "--target",
            plan["target"],
            "--release-tag",
            plan["release_tag"],
            *channel,
        ],
    )
    monkeypatch.setattr(updater, "sync_environment", install_dependencies)
    assert updater.main() == 0
    assert json.loads(capsys.readouterr().out) == {"target": target, "updated": True}
    assert_private_state(checkout)


def test_release_badge_discovery_is_read_only_on_dirty_feature_branches(checkout: Checkout) -> None:
    """Version checks may refresh private refs but preserve user tags, branches and edits."""
    updater.git(checkout.project, "checkout", "-b", "fictional-work")
    source = checkout.project / "app.txt"
    source.write_text("unfinished work")
    refs = updater.git(checkout.project, "show-ref", "--tags")
    assert updater.discover_release(checkout.project) == "v0.2.0"
    assert updater.git(checkout.project, "branch", "--show-current") == "fictional-work"
    assert updater.git(checkout.project, "show-ref", "--tags") == refs
    assert source.read_text() == "unfinished work"


def publish_nightly(checkout: Checkout, tag: str = "nightly-20261106.123") -> str:
    (checkout.author / "app.txt").write_text(tag)
    updater.git(checkout.author, "commit", "-am", "feat(test): nightly change")
    updater.git(checkout.author, "push", "origin", "main")
    publish_tag(checkout.author, tag, annotated=True)
    return updater.git(checkout.author, "rev-parse", "HEAD")


def test_nightly_channel_installs_once_and_opt_out_never_downgrades(
    checkout: Checkout, monkeypatch: pytest.MonkeyPatch
) -> None:
    nightly = publish_nightly(checkout)
    assert updater.check_update(checkout.project).release_tag == "v0.2.0"
    assert updater.discover_update(checkout.project).latest_tag == "v0.2.0"
    status = updater.discover_update(checkout.project, True)
    assert status.latest_tag == "nightly-20261106.123" and status.update_available
    plan = updater.check_update(checkout.project, True)
    assert plan.target == nightly and plan.include_nightly
    with pytest.raises(updater.UpdateError, match="changed since"):
        updater.apply_update(checkout.project, updater.UpdatePlan(plan.current, nightly, 0))
    monkeypatch.setattr(updater, "sync_environment", install_dependencies)
    updater.apply_update(checkout.project, plan)
    # pyproject need not change for the installed nightly to stop advertising itself.
    installed = updater.discover_update(checkout.project, True)
    assert not installed.update_available and installed.installed_tag == plan.release_tag
    assert not updater.discover_update(checkout.project).update_available
    stable = updater.check_update(checkout.project)
    assert stable.ahead and stable.target == nightly
    assert_private_state(checkout)


def test_stable_release_supersedes_older_nightly_by_source_ancestry(checkout: Checkout) -> None:
    _ = publish_nightly(checkout)
    # A stable tag at the same commit is preferred over its nightly alias.
    publish_tag(checkout.author, "v0.3.0")
    assert updater.check_update(checkout.project, True).release_tag == "v0.3.0"
    # An even newer stable commit wins without relying on incompatible tag strings.
    (checkout.author / "app.txt").write_text("stable after nightly")
    updater.git(checkout.author, "commit", "-am", "fix(test): stable after nightly")
    updater.git(checkout.author, "push", "origin", "main")
    publish_tag(checkout.author, "v0.3.1")
    assert updater.check_update(checkout.project, True).release_tag == "v0.3.1"


def test_nightly_without_stable_release_is_explicitly_opt_in(checkout: Checkout) -> None:
    _ = publish_nightly(checkout)
    updater.git(checkout.author, "push", "origin", ":refs/tags/v0.1.0", ":refs/tags/v0.2.0")
    assert updater.discover_update(checkout.project).latest_tag is None
    with pytest.raises(updater.UpdateError, match="No stable release"):
        updater.check_update(checkout.project)
    assert updater.check_update(checkout.project, True).release_tag == "nightly-20261106.123"


def test_changed_nightly_is_rejected_and_failure_restores_installation(
    checkout: Checkout, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = publish_nightly(checkout)
    plan = updater.check_update(checkout.project, True)
    _ = publish_nightly(checkout, "nightly-20261107.124")
    with pytest.raises(updater.UpdateError, match="changed since"):
        updater.apply_update(checkout.project, plan)
    plan = updater.check_update(checkout.project, True)

    def fail(_: Path) -> None:
        raise updater.UpdateError("fictional install failure")

    monkeypatch.setattr(updater, "sync_environment", fail)
    with pytest.raises(updater.UpdateError, match="previous installation was restored"):
        updater.apply_update(checkout.project, plan)
    assert updater.git(checkout.project, "rev-parse", "HEAD") == checkout.current
    assert_private_state(checkout)


@pytest.mark.parametrize(
    "tag",
    ["nightly-20260230.1", "nightly-20261106.0", "nightly-20261106.01", "nightly", "v1.0.0-beta.1"],
)
def test_malformed_nightly_tags_are_not_releases(tag: str) -> None:
    assert updater.nightly_version(tag) is None
