"""Optional background workflows coexist in one app without live provider calls."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from artifact_studio import project_sync
from artifact_studio.authentication import Principal
from artifact_studio.deployment import DeploymentConfig
from artifact_studio.invitation_provisioning import InvitationProvisioning
from artifact_studio.multiplayer import create_app
from artifact_studio.multiplayer_access import Scope, Sharing
from artifact_studio.project_sync import GitHubProject, ProjectGitHubSync
from artifact_studio.storage import ConflictError
from tests.test_invitation_provisioning import FictionalAdmission
from tests.test_project_github_sync import URL, FakeAuth, git
from tests.test_project_github_sync import sync_fixture as sync_fixture


def test_sources_and_guest_admission_reconcile_in_the_same_app(
    sync_fixture: tuple[Path, Sharing, str, str, GitHubProject, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Starting one service must not silently drop either opt-in background worker."""
    editor, sharing, project, _, binding, scratch = sync_fixture
    owner = sharing.human(Principal("owner", "owner@example.test"))
    scope = Scope("default", "project", project)
    sync = ProjectGitHubSync(sharing, [binding], scratch=scratch)
    config = DeploymentConfig(
        "hosted",
        URL,
        "https://preview.example.test",
        "cloudflare",
        {"team_domain": "fictional.cloudflareaccess.com", "audience": "staging"},
        ("owner@example.test",),
        (),
    )
    adapter = FictionalAdmission()
    admission = InvitationProvisioning(sharing, config, adapter)
    _ = sharing.invite(owner, scope, "guest@example.test", "view")
    _ = sync.configure(owner, scope, True)
    monkeypatch.setattr(project_sync, "PROJECT_SYNC_INTERVAL", 1)
    app = create_app(
        config, FakeAuth(), sharing, project_sync=sync, invitation_provisioning=admission
    )
    with TestClient(app, base_url=URL):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            reports = sync.status(owner, scope).reports
            admitted = admission.status(owner, "default")
            if reports and admitted and admitted[0]["state"] == "active":
                break
            time.sleep(0.05)
        else:
            raise AssertionError("Source sync and guest admission did not both complete.")
        assert reports[0].action == "upload"
        assert adapter.members == {"guest@example.test"}
        _ = git(editor, "fetch", "origin")
        assert git(editor, "show", "origin/main:demo/site/index.html").strip() == (
            "<h1>Studio original</h1>"
        )
        # Disable polling before the disposable Git transport is removed by fixture cleanup.
        while time.monotonic() < deadline:
            try:
                _ = sync.configure(owner, scope, False)
                break
            except ConflictError:
                time.sleep(0.05)
        else:
            raise AssertionError("Source sync did not release its publication lock.")
