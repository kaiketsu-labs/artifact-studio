"""Publish one immutable prerelease for a tested main commit; never change version files."""

import argparse
import json
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime

from artifact_studio.updater import nightly_version


def command(*arguments: str) -> str:
    """Use the workflow's GitHub identity without shell interpolation."""
    result = subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=60)
    return result.stdout.strip()


def publish(
    repository: str,
    commit: str,
    run_number: str,
    *,
    date: str,
    run: Callable[..., str] = command,
) -> str:
    """Require exact-commit CI and skip unchanged builds, including manual reruns."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository) or not re.fullmatch(
        r"[a-f0-9]{40}", commit
    ):
        raise ValueError("A repository and full commit SHA are required.")
    tag = f"nightly-{date}.{run_number}"
    if nightly_version(tag) is None:
        raise ValueError("A valid UTC date and positive workflow run number are required.")
    if run("git", "rev-parse", "HEAD") != commit:
        raise ValueError("The checked-out source does not match the requested nightly.")
    main = run("gh", "api", f"repos/{repository}/commits/main", "--jq", ".sha")
    if main != commit:
        return "Skipped: main changed. The next run will check the new commit."
    checks = json.loads(
        run(
            "gh",
            "run",
            "list",
            "--repo",
            repository,
            "--workflow",
            "ci.yml",
            "--commit",
            commit,
            "--branch",
            "main",
            "--event",
            "push",
            "--limit",
            "1",
            "--json",
            "conclusion,headSha",
        )
    )
    if not checks or checks[0].get("headSha") != commit or checks[0].get("conclusion") != "success":
        return "Skipped: CI has not passed for this main commit."
    tags = [
        name
        for name in run("git", "tag", "--points-at", commit).splitlines()
        if nightly_version(name)
    ]
    releases = json.loads(
        run("gh", "api", "--paginate", "--slurp", f"repos/{repository}/releases?per_page=100")
    )
    published = {
        release["tag_name"]
        for page in releases
        for release in page
        if not release.get("draft", False)
    }
    if any(name in published for name in tags):
        return "Skipped: this commit already has a nightly release."
    # Complete an interrupted publication without moving or duplicating its tag.
    if tags:
        tag = max(tags, key=lambda name: nightly_version(name) or (0, 0))
    existing = run("git", "tag", "--list", tag)
    if existing and run("git", "rev-parse", f"{tag}^{{commit}}") != commit:
        raise ValueError("The nightly tag already identifies another commit.")
    if run("gh", "api", f"repos/{repository}/commits/main", "--jq", ".sha") != commit:
        return "Skipped: main changed before publication."
    _ = run(
        "gh",
        "release",
        "create",
        tag,
        "--repo",
        repository,
        "--target",
        commit,
        "--prerelease",
        "--latest=false",
        "--title",
        f"Nightly {date} ({commit[:8]})",
        "--notes",
        f"Preview build from main at {commit}. May be less stable.\n\n"
        "Enable Include nightly builds in Artifact Studio's About dialog or Mac Settings "
        "to receive nightly updates. Stable releases remain the default.",
        "--generate-notes",
    )
    return f"Published {tag} at {commit}."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-number", required=True)
    args = parser.parse_args()
    print(
        publish(
            args.repository, args.commit, args.run_number, date=datetime.now(UTC).strftime("%Y%m%d")
        )
    )


if __name__ == "__main__":
    main()
