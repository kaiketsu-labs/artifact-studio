"""Update a local checkout and its Mac controller without touching workspace data."""

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

RELEASE_REFS = "refs/artifact-studio/releases/"


def stable_version(tag: str) -> tuple[int, ...] | None:
    """Parse the stable release tag format used by the update channel."""
    if len(tag) > 80:
        return None
    match = re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", tag)
    return tuple(int(part) for part in match.groups()) if match else None


def latest_release_tag(tags: list[str]) -> str | None:
    """Ignore prereleases and order versions numerically."""
    releases = [(version, tag) for tag in tags if (version := stable_version(tag)) is not None]
    return max(releases)[1] if releases else None


def nightly_version(tag: str) -> tuple[int, int] | None:
    """Recognize only the immutable nightly tags produced by our release workflow."""
    match = re.fullmatch(r"nightly-([0-9]{8})\.([1-9][0-9]{0,11})", tag)
    if match is None:
        return None
    try:
        _ = datetime.strptime(match[1], "%Y%m%d")
    except ValueError:
        return None
    return int(match[1]), int(match[2])


def discover_release(project: Path) -> str | None:
    """Discover stable updates without changing checked-out source or local tags."""
    return discover_update(project).latest_tag


class UpdateError(RuntimeError):
    """Report an update failure and whether the previous server can restart."""

    def __init__(self, message: str, *, restart_safe: bool = True) -> None:
        super().__init__(message)
        self.restart_safe = restart_safe


@dataclass(frozen=True)
class UpdatePlan:
    """Pin an update to an inspected pair of commits."""

    current: str
    target: str
    commits: int
    release_tag: str = ""
    ahead: bool = False
    include_nightly: bool = False


@dataclass(frozen=True)
class ReleaseStatus:
    """Compare exact commits, including nightlies whose project version is unchanged."""

    latest_tag: str | None = None
    installed_tag: str | None = None
    update_available: bool = False


@dataclass(frozen=True)
class AppSettings:
    """Preserve the installed controller's existing local configuration."""

    application: Path
    collection: Path
    database: Path
    port: int
    launch_at_login: bool
    qa: bool = False


def command(project: Path, *arguments: str, timeout: int = 300) -> str:
    """Run a bounded noninteractive tool and keep its output out of the JSON protocol."""
    environment = os.environ | {
        "GIT_TERMINAL_PROMPT": "0",
        "PLAYWRIGHT_SKIP_BROWSER_GC": "1",
    }
    try:
        result = subprocess.run(
            arguments,
            cwd=project,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateError(f"{Path(arguments[0]).name}: {error}") from error
    if result.returncode:
        detail = (result.stderr.strip() or result.stdout.strip())[-2000:]
        raise UpdateError(detail or f"{Path(arguments[0]).name} failed.")
    return result.stdout.strip()


def git(project: Path, *arguments: str) -> str:
    """Run Git against the existing checkout with prompts disabled."""
    return command(project, "/usr/bin/git", *arguments, timeout=60)


@contextmanager
def update_lock(project: Path) -> Generator[None]:
    """Allow only one menu-bar updater to operate on this checkout at a time."""
    path = Path(git(project, "rev-parse", "--git-path", "artifact-studio-update.lock"))
    if not path.is_absolute():
        path = project / path
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise UpdateError("Another update is already in progress.") from error
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def clean_checkout(project: Path) -> str:
    """Require main and preserve unfinished tracked or untracked source changes."""
    if git(project, "branch", "--show-current") != "main":
        raise UpdateError("Switch this checkout to main before updating.")
    if git(project, "status", "--porcelain", "--untracked-files=normal"):
        raise UpdateError("Commit or move your local source changes before updating.")
    return git(project, "rev-parse", "HEAD")


def release_inventory(project: Path) -> list[str]:
    """Refresh private refs only; never replace the user's tags or checked-out files."""
    git(
        project,
        "fetch",
        "--prune",
        "--no-tags",
        "origin",
        "+refs/heads/main:refs/remotes/origin/main",
        f"+refs/tags/*:{RELEASE_REFS}*",
    )
    return git(project, "for-each-ref", "--format=%(refname:strip=3)", RELEASE_REFS).splitlines()


def release_target(project: Path, tags: list[str], include_nightly: bool) -> tuple[str, str] | None:
    """Choose the newest eligible source, preferring stable when both tag the same commit."""
    stable = latest_release_tag(tags)
    nightlies = [(version, tag) for tag in tags if (version := nightly_version(tag))]
    nightly = max(nightlies)[1] if nightlies and include_nightly else None
    selected: tuple[str, str] | None = None
    for tag in (stable, nightly):
        if tag is None:
            continue
        target = git(project, "rev-parse", "--verify", f"{RELEASE_REFS}{tag}^{{commit}}")
        try:
            git(project, "merge-base", "--is-ancestor", target, "origin/main")
        except UpdateError as error:
            raise UpdateError(
                f"Release {tag} is not part of origin/main; no update was installed."
            ) from error
        if selected is None:
            selected = tag, target
        elif selected[1] != target:
            try:
                git(project, "merge-base", "--is-ancestor", target, selected[1])
            except UpdateError:
                git(project, "merge-base", "--is-ancestor", selected[1], target)
                selected = tag, target
    return selected


def installed_release_tag(project: Path) -> str | None:
    """Identify the current source using commit-matching release refs, without networking."""
    try:
        # --points-at also peels annotated tags. Local tags are display metadata only.
        refs = git(
            project,
            "for-each-ref",
            "--points-at=HEAD",
            "--format=%(refname)",
            RELEASE_REFS,
            "refs/tags/",
        )
    except UpdateError:
        return None
    tags = [ref.rsplit("/", 1)[-1] for ref in refs.splitlines()]
    stable = latest_release_tag(tags)
    nightlies = [(version, tag) for tag in tags if (version := nightly_version(tag))]
    return stable or (max(nightlies)[1] if nightlies else None)


def discover_update(project: Path, include_nightly: bool = False) -> ReleaseStatus:
    """Refresh cached release metadata without moving HEAD, source files or local tags."""
    with update_lock(project):
        selected = release_target(project, release_inventory(project), include_nightly)
        installed = installed_release_tag(project)
        if selected is None:
            return ReleaseStatus(installed_tag=installed)
        tag, target = selected
        current = git(project, "rev-parse", "HEAD")
        try:
            git(project, "merge-base", "--is-ancestor", target, current)
        except UpdateError:
            git(project, "merge-base", "--is-ancestor", current, target)
            return ReleaseStatus(tag, installed, True)
        return ReleaseStatus(tag, installed)


def release_plan(project: Path, include_nightly: bool = False) -> UpdatePlan:
    """Resolve the selected channel while retaining clean-main and ancestry guards."""
    current = clean_checkout(project)
    selected = release_target(project, release_inventory(project), include_nightly)
    if selected is None:
        channel = "stable or nightly release" if include_nightly else "stable release tag (vX.Y.Z)"
        raise UpdateError(f"No {channel} has been published on origin yet.")
    tag, target = selected
    try:
        git(project, "merge-base", "--is-ancestor", current, "origin/main")
    except UpdateError as error:
        raise UpdateError("Local main has commits that need reconciling with GitHub.") from error
    try:
        git(project, "merge-base", "--is-ancestor", current, target)
    except UpdateError:
        try:
            git(project, "merge-base", "--is-ancestor", target, current)
        except UpdateError as error:
            raise UpdateError(f"This checkout cannot fast-forward to release {tag}.") from error
        # Older controllers also see a no-op rather than attempting a downgrade.
        return UpdatePlan(current, current, 0, tag, ahead=True, include_nightly=include_nightly)
    return UpdatePlan(
        current,
        target,
        int(git(project, "rev-list", "--count", f"{current}..{target}")),
        tag,
        include_nightly=include_nightly,
    )


def check_update(project: Path, include_nightly: bool = False) -> UpdatePlan:
    """Offer only an explicitly selected channel that can be fast-forwarded safely."""
    with update_lock(project):
        return release_plan(project, include_nightly)


def sync_environment(project: Path) -> None:
    """Install the checked-in dependencies and the renderer used by local exports."""
    uv = shutil.which("uv")
    if uv is None:
        raise UpdateError("uv could not be found. Install uv before updating.")
    print("Installing locked dependencies…", file=sys.stderr, flush=True)
    command(project, uv, "sync", "--locked", "--python", "3.14")
    print("Checking Chromium…", file=sys.stderr, flush=True)
    command(project, uv, "run", "--no-sync", "playwright", "install", "chromium", "--only-shell")


def build_controller(project: Path, settings: AppSettings, destination: Path) -> None:
    """Build and sign the updated controller before replacing the installed app."""
    print("Building the menu-bar app…", file=sys.stderr, flush=True)
    arguments = [
        str(project / ".venv/bin/python"),
        str(project / "scripts/install_macos.py"),
        "--destination",
        str(destination),
        "--collection",
        str(settings.collection),
        "--database",
        str(settings.database),
        "--port",
        str(settings.port),
    ]
    if settings.launch_at_login:
        arguments.append("--launch-at-login")
    if settings.qa:
        arguments.append("--qa")
    command(project, *arguments)


def apply_update(project: Path, plan: UpdatePlan, settings: AppSettings | None = None) -> str:
    """Install a pinned update, restoring code and dependencies if installation fails."""
    with update_lock(project):
        checked = release_plan(project, plan.include_nightly)
        if (
            checked.current != plan.current
            or checked.target != plan.target
            or (plan.release_tag and checked.release_tag != plan.release_tag)
        ):
            raise UpdateError(
                "The checkout changed since the update was checked. Try Update again."
            )
        git(project, "merge-base", "--is-ancestor", plan.current, plan.target)
        if plan.current == plan.target:
            return plan.current
        environment = project / ".venv"
        if environment.is_symlink() or not environment.is_dir():
            raise UpdateError("The checkout needs its own .venv before it can be updated.")
        with tempfile.TemporaryDirectory(
            prefix="artifact-studio-update-", dir=project.parent
        ) as temp:
            backup = Path(temp) / "environment"
            print("Preserving the current installation…", file=sys.stderr, flush=True)
            shutil.copytree(environment, backup, symlinks=True)
            changed = False
            previous_app: Path | None = None
            try:
                git(project, "merge", "--ff-only", plan.target)
                changed = True
                sync_environment(project)
                if settings is not None:
                    staged_app = Path(temp) / settings.application.name
                    build_controller(project, settings, staged_app)
                    previous_app = settings.application.with_name(
                        settings.application.name + ".previous"
                    )
                    if previous_app.exists():
                        raise UpdateError(
                            f"Move the previous app backup aside first: {previous_app}"
                        )
                    with tempfile.TemporaryDirectory(
                        prefix=".artifact-studio-install-", dir=settings.application.parent
                    ) as install:
                        candidate = Path(install) / settings.application.name
                        shutil.copytree(staged_app, candidate, symlinks=True)
                        settings.application.rename(previous_app)
                        try:
                            candidate.rename(settings.application)
                        except OSError:
                            previous_app.rename(settings.application)
                            previous_app = None
                            raise
                if previous_app is not None:
                    try:
                        shutil.rmtree(previous_app)
                    except OSError:
                        print(f"Previous app retained at {previous_app}.", file=sys.stderr)
                return plan.target
            except (OSError, UpdateError) as error:
                if not changed:
                    raise UpdateError(str(error)) from error
                try:
                    if clean_checkout(project) != plan.target:
                        raise UpdateError("Source changed during installation.")
                    git(project, "reset", "--keep", plan.current)
                    if environment.exists():
                        shutil.rmtree(environment)
                    shutil.move(str(backup), str(environment))
                except (OSError, UpdateError) as recovery:
                    retained = project.parent / f"artifact-studio-recovery-{plan.current[:12]}"
                    if not retained.exists():
                        shutil.copytree(backup, retained, symlinks=True)
                    raise UpdateError(
                        f"Update failed: {error}\nRecovery needs attention: {recovery}\n"
                        f"The old environment is preserved at {retained}.",
                        restart_safe=False,
                    ) from error
                raise UpdateError(
                    f"Update failed; the previous installation was restored.\n{error}"
                ) from error


def main() -> int:
    """Serve the menu controller's small check/apply JSON protocol."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("check", "apply"))
    parser.add_argument("--current")
    parser.add_argument("--target")
    parser.add_argument("--release-tag", default="")
    parser.add_argument("--include-nightly", action="store_true")
    parser.add_argument("--application", type=Path)
    parser.add_argument("--collection", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--launch-at-login", action="store_true")
    parser.add_argument("--qa", action="store_true")
    args = parser.parse_args()
    project = Path.cwd()
    try:
        if args.operation == "check":
            print(json.dumps(asdict(check_update(project, args.include_nightly))))
        else:
            if not args.current or not args.target:
                raise UpdateError("The checked current and target commits are required.")
            settings = None
            if args.application:
                if not args.collection or not args.database:
                    raise UpdateError("The installed app's collection and database are required.")
                settings = AppSettings(
                    args.application,
                    args.collection,
                    args.database,
                    args.port,
                    args.launch_at_login,
                    args.qa,
                )
            target = apply_update(
                project,
                UpdatePlan(
                    args.current,
                    args.target,
                    0,
                    args.release_tag,
                    include_nightly=args.include_nightly,
                ),
                settings,
            )
            print(json.dumps({"target": target, "updated": True}))
        return 0
    except (OSError, UpdateError) as error:
        print(
            json.dumps({"error": str(error), "restart_safe": getattr(error, "restart_safe", True)})
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
