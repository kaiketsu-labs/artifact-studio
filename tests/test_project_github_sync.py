"""Hosted project sync using fictional users and real Git object/ref operations."""

import os
import subprocess
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import Route, expect, sync_playwright
from starlette.datastructures import Headers

from artifact_studio import libraries, project_sync
from artifact_studio.artifact_models import ArtifactFileInput
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.authentication import AuthenticationError, Principal
from artifact_studio.deployment import DeploymentConfig
from artifact_studio.multiplayer import create_app
from artifact_studio.multiplayer_access import Scope, Sharing
from artifact_studio.project_sync import GitHubProject, ProjectGitHubSync
from artifact_studio.storage import ConflictError, Store
from tests.integration.multiplayer_fixture import (
    routed_multiplayer_context,
    running_multiplayer_app,
)
from tests.test_multiplayer_server import accept, grant, human

URL = "https://studio.example.test"
REPOSITORY = "fictional/demo"
OWNER = {"Origin": URL, "X-Fixture-Auth": "owner"}
GUEST = {"Origin": URL, "X-Fixture-Auth": "guest"}


def git(directory: Path, *args: str) -> str:
    """Use ordinary Git in the two disposable test repositories."""
    return libraries.command("git", "-c", "commit.gpgsign=false", *args, cwd=directory)


class FakeAuth:
    """Authenticate only fixed fictional identities."""

    async def authenticate(self, headers: Headers) -> Principal:
        """Allow local test requests without contacting an identity provider."""
        person = headers.get("x-fixture-auth", "")
        if person not in {"owner", "guest"}:
            raise AuthenticationError()
        return Principal(person, f"{person}@example.test")


def setup(root: Path) -> tuple[Path, Sharing, str, str, GitHubProject]:
    """Create an isolated bare origin, source bundles and disposable transport mapping."""
    origin, editor = root / "origin.git", root / "git-editor"
    _ = libraries.command("git", "init", "--bare", "--initial-branch=main", str(origin))
    _ = libraries.command("git", "clone", str(origin), str(editor))
    _ = git(editor, "config", "user.name", "Fictional editor")
    _ = git(editor, "config", "user.email", "fictional@example.test")
    (editor / "other-project.txt").write_text("Outside the Studio binding")
    _ = git(editor, "add", "other-project.txt")
    _ = git(editor, "commit", "-m", "test: initial branch")
    _ = git(editor, "push", "origin", "main")

    store = Store(root / "studio", root / "state" / "review.sqlite")
    service = ArtifactStore(store)
    project = service.create_project("Fictional project")
    source = root / "bundle"
    source.mkdir()
    (source / "index.html").write_text("<h1>Studio original</h1>")
    (source / "asset.txt").write_text("Studio asset")
    artifact = service.register_artifact(project.id, "Fictional page", source, "index.html")
    other = service.create_project("Private other project")
    (source / "index.html").write_text("Other Studio project")
    _ = service.register_artifact(other.id, "Other private artifact", source, "index.html")
    sharing = Sharing(root / "sharing", store, "owner@example.test")
    binding = GitHubProject(
        workspace="default",
        project=project.id,
        repository=REPOSITORY,
        artifacts={artifact.id: "demo/site"},
    )

    return editor, sharing, project.id, artifact.id, binding


def revise(service: ArtifactStore, artifact: str, text: str) -> None:
    """Use Studio's normal revision guard for a fictional author edit."""
    current = service.get_artifact(artifact)
    _ = service.revise_artifact(
        artifact,
        [ArtifactFileInput(path="index.html", text=text)],
        current.content_hash,
        f"fictional-studio-edit-{current.revision}",
    )


def remote(editor: Path, *, title: str | None = None, delete: str | None = None) -> str:
    """Commit a second writer's real Git edit against the latest branch."""
    _ = git(editor, "pull", "--ff-only")
    if title is not None:
        (editor / "demo/site/index.html").write_text(title)
    if delete:
        (editor / f"demo/site/{delete}").unlink()
    _ = git(editor, "add", "--all")
    _ = git(editor, "commit", "-m", "test: update GitHub source")
    _ = git(editor, "push")
    return git(editor, "rev-parse", "HEAD")


def client(sharing: Sharing, sync: ProjectGitHubSync) -> TestClient:
    """Run the normal hosted owner and guest HTTP authorization path."""
    config = DeploymentConfig(
        "hosted", URL, "https://preview.example.test", "fixture", {}, ("owner@example.test",), ()
    )
    return TestClient(
        create_app(config, FakeAuth(), sharing, project_sync=sync),
        base_url=URL,
    )


def assume_test_quota(_folder: Path) -> None:
    """Exercise real Git on macOS while testing the Linux mount check separately."""


@pytest.fixture
def sync_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[Path, Sharing, str, str, GitHubProject, Path]]:
    """Connect an isolated hosted sync service to a real disposable Git origin."""
    editor, sharing, project, artifact, binding = setup(tmp_path)
    scratch = tmp_path / "quota-fixture"
    scratch.mkdir()
    monkeypatch.setattr(project_sync, "_verify_scratch", assume_test_quota)
    original_command = project_sync.GitTree._command  # pyright: ignore[reportPrivateUsage]

    def test_git(
        tree: project_sync.GitTree, command: tuple[str, ...], *, data: bytes | None = None
    ) -> bytes:
        """Route both GitHub checks and Git to fictional, network-free fixtures."""
        assert tree.folder.parent == scratch
        if command[0] == "gh":
            return b"true\n"
        if command[-1] == f"https://github.com/{REPOSITORY}.git":
            command = (*command[:-1], (tmp_path / "origin.git").as_uri())
        return original_command(tree, command, data=data)

    popen = subprocess.Popen

    def local_git(
        command: tuple[str, ...],
        *,
        cwd: Path,
        stdin: int,
        stdout: BinaryIO,
        stderr: int,
        start_new_session: bool,
        env: dict[str, str],
    ) -> subprocess.Popen[bytes]:
        assert env["GIT_ALLOW_PROTOCOL"] == "https"
        assert env["TMPDIR"] == str(cwd)
        return popen(
            tuple(
                "protocol.file.allow=always"
                if argument == "protocol.file.allow=never"
                else argument
                for argument in command
            ),
            cwd=cwd,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            start_new_session=start_new_session,
            env={**env, "GIT_ALLOW_PROTOCOL": "file"},
        )

    monkeypatch.setattr(project_sync.GitTree, "_command", test_git)
    monkeypatch.setattr(project_sync, "Popen", local_git)
    yield editor, sharing, project, artifact, binding, scratch
    assert not list(scratch.glob("studio-github-*"))


def test_hosted_sync_guards_conflicts_and_preserves_history(
    sync_fixture: tuple[Path, Sharing, str, str, GitHubProject, Path],
) -> None:
    """Sync both ways, retain deleted assets, and recheck explicit choices over real Git."""
    editor, sharing, project, artifact, binding, scratch = sync_fixture
    invalid = binding.model_copy(
        update={"project": "removed-project", "artifacts": {"removed-artifact": "removed/source"}}
    )
    sync = ProjectGitHubSync(sharing, [invalid, binding], scratch=scratch)
    endpoint = f"/api/workspaces/default/projects/{project}/github-sync"
    resolve = f"{endpoint}/{artifact}/resolve"
    with client(sharing, sync) as browser:
        discovery = "/api/workspaces/default/github-sync-projects"
        assert browser.get(discovery, headers=OWNER).json() == [invalid.project, project]
        assert browser.get(discovery, headers=GUEST).json() == []
        assert browser.get(endpoint, headers=GUEST).status_code == 404
        invite = browser.post(
            "/api/workspaces/default/invitations",
            headers=OWNER,
            json={
                "kind": "project",
                "resource": project,
                "email": "guest@example.test",
                "role": "edit",
            },
        )
        assert invite.status_code == 200
        token = str(invite.json()["url"]).split("#invite=")[1]
        assert browser.post(
            "/api/invitations/accept", headers=GUEST, json={"token": token}
        ).is_success
        assert browser.put(endpoint, headers=GUEST, json={"enabled": True}).status_code == 404
        assert browser.post(endpoint + "/check", headers=GUEST).status_code == 404
        assert browser.put(endpoint, headers=OWNER, json={"enabled": True}).json()["enabled"]
        load = sync._load  # pyright: ignore[reportPrivateUsage]
        save = sync._save  # pyright: ignore[reportPrivateUsage]
        invalid_state = load(invalid)
        invalid_state.enabled = True
        save(invalid, invalid_state)
        sync.tick()
        result = browser.get(endpoint, headers=OWNER)
        assert result.is_success, result.text
        assert result.json()["reports"][0]["action"] == "upload"
        _ = git(editor, "pull", "--ff-only")
        assert (editor / "demo/site/index.html").read_text() == "<h1>Studio original</h1>"
        assert (editor / "other-project.txt").read_text() == "Outside the Studio binding"
        assert git(editor, "ls-files").splitlines() == [
            "demo/site/asset.txt",
            "demo/site/index.html",
            "other-project.txt",
        ]

        service = ArtifactStore(sharing.store("default"))
        _ = remote(editor, title="<h1>GitHub revision</h1>", delete="asset.txt")
        state = load(binding)
        state.last_checked = 0
        save(binding, state)
        restarted = ProjectGitHubSync(sharing, [invalid, binding], scratch=scratch)
        restarted.tick()
        result = browser.get(endpoint, headers=OWNER)
        assert result.is_success, result.text
        assert result.json()["reports"][0]["action"] == "download"
        assert service.snapshot_files(artifact) == {"index.html": b"<h1>GitHub revision</h1>"}
        assert service.snapshot_files(artifact, 1)["asset.txt"] == b"Studio asset"

        revise(service, artifact, "<h1>Studio conflict</h1>")
        _ = remote(editor, title="<h1>GitHub conflict</h1>")
        result = browser.post(endpoint + "/check", headers=OWNER)
        assert result.json()["repository"] == REPOSITORY
        report = result.json()["reports"][0]
        assert report["action"] == "conflict"
        assert report["changed_files"] == ["index.html"]
        assert report["studio_revision"] == 3
        assert report["baseline_commit"]
        assert service.snapshot_files(artifact)["index.html"] == b"<h1>Studio conflict</h1>"
        _ = remote(editor, title="<h1>GitHub newer</h1>")
        decision = {"conflict": report["conflict"], "choice": "studio"}
        assert browser.post(resolve, headers=OWNER, json=decision).status_code == 409
        assert browser.post(resolve, headers=GUEST, json=decision).status_code == 404
        fresh = browser.post(endpoint + "/check", headers=OWNER).json()["reports"][0]
        result = browser.post(
            resolve,
            headers=OWNER,
            json={"conflict": fresh["conflict"], "choice": "github"},
        )
        assert result.is_success, result.text
        assert service.snapshot_files(artifact)["index.html"] == b"<h1>GitHub newer</h1>"

        _ = remote(editor, delete="index.html")
        missing = browser.post(endpoint + "/check", headers=OWNER).json()["reports"][0]
        assert missing["action"] == "conflict" and missing["github"] is None
        assert service.snapshot_files(artifact)["index.html"] == b"<h1>GitHub newer</h1>"

        assert not browser.put(endpoint, headers=OWNER, json={"enabled": False}).json()["enabled"]


def test_sync_merges_independent_file_changes(
    sync_fixture: tuple[Path, Sharing, str, str, GitHubProject, Path],
) -> None:
    """Transfer edits to different files and keep revisions on both sides."""
    editor, sharing, project, artifact, binding, scratch = sync_fixture
    sync = ProjectGitHubSync(sharing, [binding], scratch=scratch)
    endpoint = f"/api/workspaces/default/projects/{project}/github-sync/check"
    with client(sharing, sync) as api:
        assert api.post(endpoint, headers=OWNER).is_success
        service = ArtifactStore(sharing.store("default"))
        revise(service, artifact, "<h1>Studio independent edit</h1>")
        _ = remote(editor, delete="asset.txt")
        checked = api.post(endpoint, headers=OWNER)
        assert checked.is_success, checked.text
        assert checked.json()["reports"][0]["action"] == "merge"
        expected = {"index.html": b"<h1>Studio independent edit</h1>"}
        assert service.snapshot_files(artifact) == expected
        assert service.snapshot_files(artifact, 2)["asset.txt"] == b"Studio asset"
        _ = git(editor, "pull", "--ff-only")
        assert (editor / "demo/site/index.html").read_bytes() == expected["index.html"]
        assert not (editor / "demo/site/asset.txt").exists()
        assert api.post(endpoint, headers=OWNER).json()["reports"][0]["action"] == "unchanged"


def test_missing_source_does_not_block_other_project_bundles(
    sync_fixture: tuple[Path, Sharing, str, str, GitHubProject, Path],
) -> None:
    """Report a stale mapping while syncing valid bundles from the same project."""
    editor, sharing, project, artifact, binding, scratch = sync_fixture
    paired = binding.model_copy(
        update={
            "artifacts": {"missing-source": "demo/missing", **binding.artifacts},
        }
    )
    sync = ProjectGitHubSync(sharing, [paired], scratch=scratch)
    endpoint = f"/api/workspaces/default/projects/{project}/github-sync"
    with client(sharing, sync) as api:
        result = api.post(endpoint + "/check", headers=OWNER)
        assert result.is_success, result.text
        reports = {report["artifact"]: report for report in result.json()["reports"]}
        assert reports["missing-source"]["action"] == "blocked"
        assert reports["missing-source"]["detail"]
        assert reports[artifact]["action"] == "upload"
        assert not result.json()["error"]
        assert api.get(endpoint, headers=OWNER).json()["reports"] == result.json()["reports"]
        _ = git(editor, "pull", "--ff-only")
        assert not (editor / "demo/missing").exists()
        assert (editor / "demo/site/index.html").read_text() == "<h1>Studio original</h1>"


def test_binding_file_loads_before_owner_sign_in(
    tmp_path: Path,
    sync_fixture: tuple[Path, Sharing, str, str, GitHubProject, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new deployment reads private bindings before any workspace actor exists."""
    _, sharing, project, _, binding, _ = sync_fixture
    config = tmp_path / "github-sync.json"
    config.write_text("[" + binding.model_dump_json() + "]")
    monkeypatch.setenv("ARTIFACT_STUDIO_PROJECT_GITHUB_SYNC", str(config))
    sync = ProjectGitHubSync.from_env(sharing)
    with client(sharing, sync) as api:
        assert api.get("/api/workspaces/default/github-sync-projects", headers=OWNER).json() == [
            project
        ]


@pytest.mark.browser
@pytest.mark.skipif(
    os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
)
def test_owner_can_sync_and_resolve_from_the_project_menu(
    tmp_path: Path,
    sync_fixture: tuple[Path, Sharing, str, str, GitHubProject, Path],
) -> None:
    """A real browser controls hosted sync and sees only its owner's configured project."""
    editor, _, _, _, binding, scratch = sync_fixture
    root = tmp_path / "hosted"
    with (
        running_multiplayer_app(root, tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        sync_playwright() as playwright,
    ):
        # Use the real hosted API, store and Git transport while the existing fixture
        # serves the normal hub shell. The transport never contacts GitHub.
        sharing = Sharing(root / "sharing", app.store, "owner@example.test")
        pair = binding.model_copy(
            update={"project": app.project_id, "artifacts": {app.artifact_id: "demo/site"}}
        )
        sync = ProjectGitHubSync(sharing, [pair], scratch=scratch)
        endpoint = f"/api/workspaces/default/projects/{app.project_id}/github-sync"
        accept(alice, grant(owner, "alice", app.artifact_id, role="edit"))
        with client(sharing, sync) as api:

            def forward(route: Route) -> None:
                """Forward only the sync requests to the real authenticated test service."""
                request = route.request
                headers = {**OWNER}
                if media := request.headers.get("content-type"):
                    headers["Content-Type"] = media
                response = api.request(
                    request.method, request.url, headers=headers, content=request.post_data
                )
                route.fulfill(
                    status=response.status_code, headers=dict(response.headers), body=response.text
                )

            browser = playwright.chromium.launch()
            try:
                with routed_multiplayer_context(browser, app) as context:
                    context.route("**/github-sync-projects", forward)
                    context.route("**/github-sync**", forward)
                    page = context.new_page()
                    page.set_viewport_size({"width": 390, "height": 740})
                    with page.expect_response("**/github-sync-projects"):
                        page.goto(URL)
                    options = page.get_by_role("button", name="Options for Fictional project")
                    options.focus()
                    options.press("Enter")
                    page.get_by_role("menuitem", name="GitHub sync…").press("Enter")
                    dialog = page.get_by_role("dialog", name="GitHub sync")
                    expect(dialog).to_contain_text(REPOSITORY)
                    dialog.get_by_text("Synced sources").click()
                    expect(dialog).to_contain_text("Shared candidate · demo/site")
                    auto = dialog.get_by_role("checkbox", name="Automatically sync both ways")
                    with page.expect_response(
                        lambda response: (
                            response.url.endswith(endpoint) and response.request.method == "PUT"
                        )
                    ) as configured:
                        auto.check()
                    assert configured.value.ok, configured.value.text()
                    expect(auto).to_be_checked()
                    dialog.get_by_role("button", name="Sync now").click()
                    expect(dialog).to_contain_text("Uploaded to GitHub.")
                    _ = git(editor, "pull", "--ff-only")
                    service = ArtifactStore(app.store)
                    latest = service.get_artifact(app.artifact_id)
                    _ = service.revise_artifact(
                        app.artifact_id,
                        [ArtifactFileInput(path="source.md", text="Fictional Studio edit")],
                        latest.content_hash,
                        "fictional-local-edit",
                    )
                    (editor / "demo/site/source.md").write_text("Fictional GitHub edit")
                    _ = git(editor, "add", "--all")
                    _ = git(editor, "commit", "-m", "test: update fictional source")
                    _ = git(editor, "push")
                    dialog.get_by_role("button", name="Sync now").click()
                    expect(dialog).to_contain_text("Needs a decision")
                    dialog.get_by_text("1 differing file").click()
                    expect(dialog.get_by_text("source.md", exact=True)).to_be_visible()
                    dialog.get_by_role("button", name="Keep GitHub").click()
                    dialog.get_by_role("button", name="Replace Studio source").click()
                    expect(dialog).to_contain_text("Updated in Studio from GitHub.")
                    assert (
                        service.snapshot_files(app.artifact_id)["source.md"]
                        == b"Fictional GitHub edit"
                    )
                    assert (
                        service.snapshot_files(app.artifact_id, 2)["source.md"]
                        == b"Fictional Studio edit"
                    )
                    page.keyboard.press("Escape")
                    expect(dialog).to_be_hidden()
                    expect(options).to_be_focused()

                with routed_multiplayer_context(browser, app, "alice") as context:
                    page = context.new_page()
                    page.goto(URL)
                    expect(page.get_by_role("heading", name="All artifacts")).to_be_visible()
                    assert page.get_by_text("GitHub sync…", exact=True).count() == 0
            finally:
                browser.close()


def test_sync_refuses_unbounded_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An ordinary directory cannot enable sync or download remote objects."""
    _, sharing, project, _, binding = setup(tmp_path)
    scratch = tmp_path / "ordinary-directory"
    scratch.mkdir(mode=0o700)
    sync = ProjectGitHubSync(sharing, [binding], scratch=scratch)
    scope = Scope("default", "project", project)
    owner = sharing.human(Principal("owner", "owner@example.test"))

    def unexpected(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Remote Git ran without a bounded staging mount")

    monkeypatch.setattr(project_sync, "GitTree", unexpected)
    with pytest.raises(ConflictError, match="size-limited staging mount"):
        _ = sync.configure(owner, scope, True)
    with pytest.raises(ConflictError, match="size-limited staging mount"):
        _ = sync.run(owner, scope)
    assert not sync.status(owner, scope).enabled
    assert not list(scratch.iterdir())


def test_staging_requires_kernel_byte_and_inode_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accept a dedicated mount only when both kernel limits are finite and below the maximum."""
    scratch = tmp_path / "mount"
    scratch.mkdir(mode=0o700)
    mount_info = tmp_path / "mountinfo"
    monkeypatch.setattr(project_sync, "MOUNT_INFO", mount_info)
    capacity = SimpleNamespace(f_frsize=4096, f_blocks=512, f_files=128)

    def statvfs(_path: Path) -> SimpleNamespace:
        """Supply the kernel capacity fields read by the staging guard."""
        return capacity

    monkeypatch.setattr(os, "statvfs", statvfs)
    verify = project_sync._verify_scratch  # pyright: ignore[reportPrivateUsage]
    valid = f"24 19 0:37 / {scratch} rw,nosuid,nodev,noexec - tmpfs tmpfs rw,size=2048k\n"
    _ = mount_info.write_text(valid)
    verify(scratch)

    for invalid in (
        valid.replace(" - tmpfs tmpfs", " - overlay overlay"),
        valid.replace(" / ", " /unrelated/child "),
        valid.replace(",noexec", ""),
    ):
        _ = mount_info.write_text(invalid)
        with pytest.raises(ConflictError, match="size-limited staging mount"):
            verify(scratch)
    _ = mount_info.write_text(valid)
    capacity.f_blocks = project_sync.SYNC_SCRATCH_BYTES // capacity.f_frsize + 1
    with pytest.raises(ConflictError, match="size-limited staging mount"):
        verify(scratch)
    capacity.f_blocks = 512
    capacity.f_files = 0
    with pytest.raises(ConflictError, match="size-limited staging mount"):
        verify(scratch)


def test_staging_slot_is_shared_and_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two instances sharing a staging mount cannot stage simultaneous projects."""
    _, sharing, _, _, binding = setup(tmp_path)
    scratch = tmp_path / "quota-fixture"
    scratch.mkdir()
    monkeypatch.setattr(project_sync, "_verify_scratch", assume_test_quota)
    first = ProjectGitHubSync(sharing, [binding], scratch=scratch)
    second = ProjectGitHubSync(sharing, [], scratch=scratch)
    leftover = scratch / "studio-github-interrupted"
    leftover.mkdir()
    first_slot = first._staging_slot  # pyright: ignore[reportPrivateUsage]
    second_slot = second._staging_slot  # pyright: ignore[reportPrivateUsage]
    with first_slot() as folder:
        assert not leftover.exists()
        (folder / "staged-object").write_bytes(b"fictional content")
        with (
            pytest.raises(ConflictError, match="Another project is syncing"),
            second_slot(),
        ):
            pytest.fail("A second sync acquired the same staging slot")
    assert not folder.exists()
    with second_slot() as next_folder:
        assert next_folder.is_dir()
