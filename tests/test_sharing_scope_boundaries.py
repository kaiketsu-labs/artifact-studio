"""Verify that overlapping shares stay scoped when a project grant is revoked."""

import json
import os
import re
from contextlib import closing
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from tests.integration.multiplayer_fixture import (
    PREVIEW,
    WORKSPACE,
    routed_multiplayer_context,
    running_multiplayer_app,
)
from tests.test_multiplayer_server import accept, grant, human
from tests.test_multiplayer_simulation import Peer


def preview_file(client: httpx.Client, artifact: str, revision: int) -> str:
    """Return the preview file path for a fixture's current source."""
    response = client.get(
        f"/api/workspaces/default/artifacts/{artifact}/preview",
        headers={"sec-fetch-dest": "iframe"},
    )
    assert response.is_success, response.text
    match = re.search(r'https://preview\.example\.test/_preview/[^/"\s]+', response.text)
    assert match, response.text
    root = urlsplit(match.group()).path
    return f"{root}/api/artifacts/{artifact}/revisions/{revision}/files/source.md"


def test_project_revoke_recalculates_existing_agent_and_preview_access(tmp_path: Path) -> None:
    """An existing agent cannot retain edit access when its owner becomes a viewer."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as editor,
        human(app, "bob") as other_editor,
        httpx.Client(base_url=app.url, headers={"host": urlsplit(PREVIEW).netloc}) as preview,
    ):
        accept(editor, grant(owner, "alice", app.artifact_id, "view"))
        accept(editor, grant(owner, "alice", app.project_id, "edit", "project"))
        accept(other_editor, grant(owner, "bob", app.project_id, "edit", "project"))
        access = "/api/workspaces/default/access"
        project = {"kind": "project", "resource": app.project_id}
        project_grants = owner.get(access, params=project)
        assert project_grants.is_success
        grant_id = next(
            row["id"]
            for row in project_grants.json()["grants"]
            if row["email"] == "alice@example.test"
        )
        credentials = editor.post(
            "/api/workspaces/default/agents", json={**project, "name": "Fictional editor agent"}
        )
        assert credentials.is_success
        peer = Peer(app, credentials.json()["token"])
        with closing(peer.client):
            initial = peer.value("list_artifacts")
            assert {item["id"] for item in cast(list[dict[str, str]], initial["artifacts"])} == {
                app.artifact_id,
                app.sibling_id,
            }
            assert peer.call("get_presentation_outline", deck_id=app.deck_id).is_error
            edit = {
                "artifact_id": app.artifact_id,
                "files_json": json.dumps([{"path": "source.md", "text": "Authorized edit"}]),
                "expected_revision": 1,
                "request_id": "authorized-project-edit",
            }
            peer.value("revise_artifact", **edit)
            artifact_preview = preview_file(editor, app.artifact_id, 2)
            sibling_preview = preview_file(editor, app.sibling_id, 1)
            assert preview.get(artifact_preview).text == "Authorized edit"
            assert preview.get(sibling_preview).is_success

            revoked = owner.post(f"{access}/{grant_id}/revoke", json=project)
            assert revoked.is_success, revoked.text

            artifacts = "/api/workspaces/default/artifacts"
            remaining = editor.get(f"{artifacts}/{app.artifact_id}/state")
            assert remaining.is_success
            assert remaining.json()["access"]["role"] == "view"
            assert editor.get(f"{artifacts}/{app.sibling_id}/state").status_code == 404
            assert other_editor.get(f"{artifacts}/{app.sibling_id}/state").is_success
            assert preview.get(artifact_preview).text == "Authorized edit"
            assert preview.get(sibling_preview).status_code == 404
            assert peer.call("list_artifacts").is_error
            assert not peer.call("get_artifact", artifact_id=app.artifact_id).is_error
            assert peer.call("get_artifact", artifact_id=app.sibling_id).is_error
            edit.update(
                files_json=json.dumps([{"path": "source.md", "text": "Forbidden edit"}]),
                expected_revision=2,
                request_id="edit-after-role-downgrade",
            )
            assert peer.call("revise_artifact", **edit).is_error
            assert editor.get(f"{artifacts}/{app.artifact_id}/state").json()["revision"] == 2


@pytest.mark.browser
@pytest.mark.skipif(
    os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
)
def test_two_editors_share_notes_in_the_regular_studio(tmp_path: Path) -> None:
    """Both identities save notes through the same controls and see the other edit."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as guest,
        sync_playwright() as playwright,
    ):
        accept(guest, grant(owner, "alice", app.deck_id))
        browser = playwright.chromium.launch()
        try:
            with (
                routed_multiplayer_context(browser, app) as owner_context,
                routed_multiplayer_context(browser, app, "alice") as guest_context,
            ):
                first, second = owner_context.new_page(), guest_context.new_page()
                for page in (first, second):
                    page.goto(WORKSPACE)
                    page.get_by_role("button", name=re.compile("Open Fictional team deck")).click()
                    expect(page.locator("#slide-title")).to_be_visible()
                second.locator('#slides button[data-id="next"]').click()
                for page, note in (
                    (first, "Owner note on cover"),
                    (second, "Collaborator note on next slide"),
                ):
                    page.locator("#notes-panel summary").click()
                    field = page.get_by_role("textbox", name="Speaker notes")
                    expect(field).to_be_editable()
                    field.fill(note)
                    field.press("Tab")

                first.locator('#slides button[data-id="next"]').click()
                second.locator('#slides button[data-id="cover"]').click()
                expect(first.get_by_role("textbox", name="Speaker notes")).to_have_value(
                    "Collaborator note on next slide", timeout=15000
                )
                expect(second.get_by_role("textbox", name="Speaker notes")).to_have_value(
                    "Owner note on cover", timeout=15000
                )
        finally:
            browser.close()
