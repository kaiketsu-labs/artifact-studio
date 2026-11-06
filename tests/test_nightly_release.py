"""Simulate nightly publication boundaries without publishing any real tags or releases."""

import json

import pytest

from scripts.publish_nightly import publish

SHA = "a" * 40


class GitHub:
    """A fictional GitHub and checkout that records all attempted writes."""

    def __init__(self) -> None:
        self.head = SHA
        self.main = SHA
        self.ci = "success"
        self.ci_sha = SHA
        self.tags: list[str] = []
        self.published: list[str] = []
        self.created: list[tuple[str, ...]] = []
        self.conflict = False
        self.move_main = False
        self.main_reads = 0

    def run(self, *args: str) -> str:
        if args == ("git", "rev-parse", "HEAD"):
            return self.head
        if args[:2] == ("gh", "api") and args[2].endswith("/commits/main"):
            self.main_reads += 1
            return "b" * 40 if self.move_main and self.main_reads > 1 else self.main
        if args[:3] == ("gh", "run", "list"):
            return json.dumps([{"conclusion": self.ci, "headSha": self.ci_sha}])
        if args[:3] == ("git", "tag", "--points-at"):
            return "\n".join(self.tags)
        if args[:4] == ("gh", "api", "--paginate", "--slurp"):
            return json.dumps([[{"tag_name": tag, "draft": False} for tag in self.published]])
        if args[:3] == ("git", "tag", "--list"):
            return args[3] if args[3] in self.tags or self.conflict else ""
        if args[:2] == ("git", "rev-parse"):
            return "b" * 40 if self.conflict else SHA
        if args[:3] == ("gh", "release", "create"):
            self.created.append(args)
            return "https://example.invalid/nightly"
        raise AssertionError(args)

    def publish(self) -> str:
        return publish("example/studio", SHA, "123", date="20261106", run=self.run)


def test_nightly_publication_is_immutable_and_never_latest_stable() -> None:
    github = GitHub()
    assert github.publish().startswith("Published nightly-20261106.123")
    args = github.created[0]
    assert args[3] == "nightly-20261106.123"
    assert args[args.index("--target") + 1] == SHA
    assert "--prerelease" in args and "--latest=false" in args


@pytest.mark.parametrize("ci", ["failure", "cancelled", "", "in_progress"])
def test_no_nightly_without_successful_ci(ci: str) -> None:
    github = GitHub()
    github.ci = ci
    assert github.publish().startswith("Skipped: CI")
    assert not github.created


@pytest.mark.parametrize("boundary", ["ci", "main", "moves"])
def test_other_commits_cannot_substitute_for_selected_source(boundary: str) -> None:
    github = GitHub()
    if boundary == "ci":
        github.ci_sha = "b" * 40
    elif boundary == "main":
        github.main = "b" * 40
    else:
        github.move_main = True
    assert github.publish().startswith("Skipped:")
    assert not github.created


def test_unchanged_source_is_skipped_and_interrupted_release_reuses_tag() -> None:
    github = GitHub()
    github.tags = ["nightly-20261105.122"]
    github.published = github.tags.copy()
    assert "already has a nightly" in github.publish()
    assert not github.created
    github.published.clear()
    assert github.publish().startswith("Published nightly-20261105.122")
    assert github.created[0][3] == github.tags[0]


def test_existing_nightly_tag_is_never_moved() -> None:
    github = GitHub()
    github.conflict = True
    with pytest.raises(ValueError, match="another commit"):
        github.publish()
    assert not github.created
