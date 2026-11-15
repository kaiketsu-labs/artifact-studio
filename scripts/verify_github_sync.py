"""Opt-in fictional acceptance for a dedicated private GitHub source-sync repository.

The operator creates a new private repository and branch with MARKER first. This
program never creates repositories, changes grants or deletes remote history.
Run as a module so the checkout supplies Artifact Studio's normal implementation.
"""

import argparse
import json
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from unittest.mock import patch

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.authentication import Principal
from artifact_studio.design_authoring import blank_presentation
from artifact_studio.models import AgentContext, Slide, SlidePatch, Source
from artifact_studio.multiplayer_access import AccessDenied, Scope, Sharing
from artifact_studio.project_sync import (
    GitHubProject,
    GitTree,
    ProjectGitHubSync,
    ProjectSyncResolution,
    SyncReport,
    _verify_scratch,  # pyright: ignore[reportPrivateUsage]
)
from artifact_studio.storage import Store

MARKER = b'{"purpose":"artifact-studio-sync-acceptance","fictional_only":true}\n'
MARKER_PREFIX = "acceptance-marker"
DECK = "fictional-sync-acceptance"
PREFIX = "fictional/presentation"
PRIVATE = "FICTIONAL PRIVATE SENTINEL"


@dataclass
class Acceptance:
    root: Path
    scratch: Path
    store: Store
    sharing: Sharing
    binding: GitHubProject
    measurements: list[dict[str, object]]

    @classmethod
    def create(cls, root: Path, scratch: Path, repository: str, branch: str) -> Acceptance:
        """Create new fictional state; refuse to touch an existing workspace folder."""
        root.mkdir(mode=0o700, parents=False, exist_ok=False)
        store = Store(root / "collection", root / "state/review.sqlite3")
        project = ArtifactStore(store).create_project("Fictional source-sync acceptance")
        folder = store.root / DECK
        folder.mkdir()
        deck = blank_presentation(folder, DECK, "Fictional acceptance deck")
        deck.slides += [
            Slide(id="second", title="Second", html="<article>Second baseline</article>"),
            Slide(id="third", title="Third", html="<article>Third baseline</article>"),
        ]
        for slide in deck.slides:
            slide.notes = f"{PRIVATE} notes {slide.id}"
            slide.agent = AgentContext(summary=f"{PRIVATE} agent {slide.id}")
            slide.sources = [Source(label=f"{PRIVATE} source {slide.id}")]
        store.write(DECK, deck)
        (folder / "README.md").write_text(f"{PRIVATE} presentation context")
        (folder / "assets/example.txt").write_text("Fictional initial asset")
        _ = ArtifactStore(store).set_artifact_project(DECK, project.id)
        _ = store.set_mode(DECK, "batch")
        _ = store.add_feedback(DECK, None, f"{PRIVATE} unreleased review", "", "private-review-001")
        sharing = Sharing(root / "sharing", store, "owner@example.test")
        binding = GitHubProject(
            workspace="default",
            project=project.id,
            repository=repository,
            branch=branch,
            artifacts={DECK: PREFIX},
        )
        return cls(root, scratch, store, sharing, binding, [])

    @property
    def scope(self) -> Scope:
        return Scope("default", "project", self.binding.project)

    @property
    def sync(self) -> ProjectGitHubSync:
        return ProjectGitHubSync(self.sharing, [self.binding], scratch=self.scratch)

    @contextmanager
    def peer(self) -> Generator[GitTree]:
        """A second Git writer uses the same kernel-bounded staging mount."""
        with tempfile.TemporaryDirectory(prefix="acceptance-peer-", dir=self.scratch) as name:
            yield GitTree(Path(name), self.binding)

    def remote(self) -> dict[str, bytes]:
        with self.peer() as tree:
            files = tree.read(PREFIX)
            assert PRIVATE.encode() not in b"\n".join(files.values())
            assert tree.read(MARKER_PREFIX) == {"fixture.json": MARKER}
            return files

    def publish(self, change: Callable[[dict[str, bytes]], object]) -> None:
        with self.peer() as tree:
            before = tree.read(PREFIX)
            after = before.copy()
            change(after)
            tree.publish(PREFIX, before, after)

    def check_private(self) -> None:
        deck = self.store.read(DECK)
        for slide in deck.slides:
            assert slide.notes == f"{PRIVATE} notes {slide.id}"
            assert slide.agent.summary == f"{PRIVATE} agent {slide.id}"
            assert slide.sources[0].label == f"{PRIVATE} source {slide.id}"
        assert (
            self.store.folder(DECK) / "README.md"
        ).read_text() == f"{PRIVATE} presentation context"
        comments = self.store.comments(DECK)
        assert any(
            item.message == f"{PRIVATE} unreleased review" and not item.released
            for item in comments
        )

    def run(
        self,
        name: str,
        expected: str,
        *,
        resolution: ProjectSyncResolution | None = None,
        sync: ProjectGitHubSync | None = None,
    ) -> SyncReport:
        started = time.monotonic()
        actor = self.sharing.human(Principal("owner", "owner@example.test"))
        report = (
            (sync or self.sync)
            .run(actor, self.scope, DECK if resolution else "", resolution)
            .reports[0]
        )
        assert report.action == expected, (name, report.model_dump())
        self.check_private()
        self.measurements.append(
            {
                "scenario": name,
                "action": report.action,
                "seconds": round(time.monotonic() - started, 4),
                "changed_files": report.changed_files,
                "commit": report.commit,
            }
        )
        return report

    def edit(self, slide_id: str, html: str) -> None:
        slide = self.store.view(DECK).slides[
            [s.id for s in self.store.read(DECK).slides].index(slide_id)
        ]
        self.store.revise_view(
            DECK,
            slide_id,
            slide.expected_hash,
            f"acceptance-{len(self.measurements)}-{slide_id}",
            SlidePatch(html=html),
        )

    def choose(self, report: SyncReport, choice: Literal["studio", "github"], name: str) -> None:
        assert report.conflict
        resolution = ProjectSyncResolution(conflict=report.conflict, choice=choice)
        self.run(name, "upload" if choice == "studio" else "download", resolution=resolution)
        self.remote()

    def exercise(self) -> dict[str, object]:
        """Exercise normal API guards and real Git operations with no transport bypass."""
        guest = self.sharing.human(Principal("guest", "guest@example.test"))
        for operation in (
            lambda: self.sync.status(guest, self.scope),
            lambda: self.sync.run(guest, self.scope),
            lambda: self.sync.configure(guest, self.scope, True),
        ):
            try:
                operation()
            except AccessDenied:
                pass
            else:
                raise AssertionError("A non-owner accessed source sync")
        self.run("initial_upload", "upload")
        self.remote()
        self.run("unchanged", "unchanged")
        folder = self.store.folder(DECK)
        with self.store.editing(DECK):
            (folder / "theme.css").write_text("/* Fictional independent local theme */")
        self.publish(lambda files: update_slide(files, "second", title="Independent GitHub edit"))
        self.run("independent_merge", "merge")
        assert self.store.read(DECK).slides[1].title == "Independent GitHub edit"
        assert self.remote()["theme.css"] == b"/* Fictional independent local theme */"

        self.edit("cover", "<article>Local content choice</article>")
        self.publish(
            lambda files: update_slide(
                files, "cover", html="<article>Remote content choice</article>"
            )
        )
        conflict = self.run("content_conflict", "conflict")
        assert "slides/cover.json" in conflict.changed_files
        self.choose(conflict, "github", "choose_remote_content")
        assert self.store.read(DECK).slides[0].html == "<article>Remote content choice</article>"

        self.store.reorder(
            DECK, ["third", "cover", "second"], expected_slide_ids=["cover", "second", "third"]
        )
        self.publish(lambda files: update_order(files, ["second", "third", "cover"]))
        conflict = self.run("order_conflict", "conflict")
        assert "presentation.json" in conflict.changed_files
        self.choose(conflict, "studio", "choose_local_order")
        assert json.loads(self.remote()["presentation.json"])["slides"] == [
            "third",
            "cover",
            "second",
        ]

        self.edit("second", "<article>Keep local slide edit</article>")

        def delete_slide(files: dict[str, bytes]) -> None:
            update_order(files, ["third", "cover"])
            del files["slides/second.json"]

        self.publish(delete_slide)
        conflict = self.run("slide_delete_edit_conflict", "conflict")
        self.choose(conflict, "studio", "keep_local_slide")
        assert "slides/second.json" in self.remote()

        with self.store.editing(DECK):
            (folder / "assets/example.txt").write_text("Fictional local asset edit")
        self.publish(lambda files: files.pop("assets/example.txt"))
        conflict = self.run("asset_delete_edit_conflict", "conflict")
        assert "assets/example.txt" in conflict.changed_files
        self.choose(conflict, "github", "accept_asset_deletion")
        assert not (folder / "assets/example.txt").exists()

        with self.store.editing(DECK):
            (folder / "theme.css").write_text("/* Fictional theme before interrupted apply */")
        self.publish(
            lambda files: update_slide(files, "cover", title="Remote title before interruption")
        )
        service = self.sync
        with patch.object(
            service, "_apply_source", side_effect=OSError("Fictional interruption after push")
        ):
            self.run("interrupted_after_successful_push", "blocked", sync=service)
        merged = self.remote()
        assert merged["theme.css"] == b"/* Fictional theme before interrupted apply */"
        assert (
            json.loads(merged["slides/cover.json"])["title"] == "Remote title before interruption"
        )
        self.store = Store(self.store.root, self.store.database)
        self.sharing = Sharing(self.root / "sharing", self.store, "owner@example.test")
        self.run("restart_after_push", "merge")
        assert self.remote() == merged
        self.run("restart_unchanged", "unchanged")

        self.edit("cover", "<article>Studio race candidate</article>")
        publish = GitTree.publish
        competed = False

        def race(
            tree: GitTree, prefix: str, previous: dict[str, bytes], source: dict[str, bytes]
        ) -> str:
            nonlocal competed
            if not competed:
                competed = True
                self.publish(
                    lambda files: update_slide(
                        files, "cover", html="<article>Other editor wins push</article>"
                    )
                )
            return publish(tree, prefix, previous, source)

        with patch.object(GitTree, "publish", race):
            self.run("competing_push_rejected", "blocked")
        assert (
            json.loads(self.remote()["slides/cover.json"])["html"]
            == "<article>Other editor wins push</article>"
        )
        conflict = self.run("competing_edit_conflict", "conflict")
        self.choose(conflict, "github", "retain_competing_edit")
        self.run("final_unchanged", "unchanged")
        return {
            "fictional_only": True,
            "repository": self.binding.repository,
            "branch": self.binding.branch,
            "scenarios": self.measurements,
            "non_owner_denied": True,
            "private_state_preserved": True,
            "transport": "production GitTree; kernel-bounded staging required",
            "limits": [
                "No live identity-provider/browser test",
                "GitHub denied-credential behavior not exercised",
                "Not a production throughput benchmark",
            ],
        }


def update_slide(files: dict[str, bytes], slide: str, **changes: str) -> None:
    path = f"slides/{slide}.json"
    value = json.loads(files[path])
    value.update(changes)
    files[path] = json.dumps(value).encode()


def update_order(files: dict[str, bytes], order: list[str]) -> None:
    value = json.loads(files["presentation.json"])
    value["slides"] = order
    files["presentation.json"] = json.dumps(value).encode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument(
        "--workspace", type=Path, required=True, help="A NEW fictional workspace directory"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-private-fixture-writes", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(
        r"[a-zA-Z0-9_-]+/artifact-studio-sync-acceptance-[a-zA-Z0-9_-]+", args.repository
    ):
        parser.error("Use a new dedicated artifact-studio-sync-acceptance-* repository.")
    if not re.fullmatch(r"acceptance/[a-zA-Z0-9_-]+", args.branch):
        parser.error("Use an isolated acceptance/<run-id> branch.")
    if not args.allow_private_fixture_writes:
        parser.error("Use --allow-private-fixture-writes to authorize fictional commits.")
    _verify_scratch(args.scratch)
    permission = subprocess.run(
        ["gh", "repo", "view", args.repository, "--json", "isPrivate,viewerPermission"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    metadata = json.loads(permission.stdout)
    if not metadata.get("isPrivate") or metadata.get("viewerPermission") not in {
        "ADMIN",
        "MAINTAIN",
        "WRITE",
    }:
        parser.error("The dedicated fixture repository must be private and writable.")
    binding = GitHubProject(
        workspace="default",
        project="preflight",
        repository=args.repository,
        branch=args.branch,
        artifacts={DECK: PREFIX},
    )
    with tempfile.TemporaryDirectory(prefix="acceptance-preflight-", dir=args.scratch) as name:
        tree = GitTree(Path(name), binding)
        if tree.read(MARKER_PREFIX) != {"fixture.json": MARKER} or tree.read(PREFIX):
            parser.error("The fixture marker must match and the presentation folder must be empty.")
    run = Acceptance.create(args.workspace, args.scratch, args.repository, args.branch)
    report = run.exercise()
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": len(run.measurements), "output": str(args.output)}))


if __name__ == "__main__":
    main()
