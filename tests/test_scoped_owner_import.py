"""Exercise the existing import dialog and scoped owner upload boundary."""

import asyncio
import base64
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI, Request
from playwright.sync_api import Route, expect, sync_playwright

from artifact_studio.deployment import DeploymentConfig
from artifact_studio.multiplayer_access import Sharing
from artifact_studio.multiplayer_http import MultiplayerAuth
from artifact_studio.storage import Store
from tests.integration.multiplayer_fixture import (
    WORKSPACE,
    routed_multiplayer_context,
    running_multiplayer_app,
)
from tests.test_multiplayer_edge_review import FixtureProvider
from tests.test_multiplayer_server import accept, human


def test_owner_import_has_separate_admission_and_checks_identity_before_reading(
    tmp_path: Path,
) -> None:
    """A pending import cannot consume editor capacity or let a guest stage a body."""

    async def journey() -> None:
        store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
        sharing = Sharing(tmp_path / "sharing", store, "owner@example.test")
        config = DeploymentConfig("hosted", WORKSPACE, "https://preview.example.test")
        app = FastAPI()
        body_started, release = asyncio.Event(), asyncio.Event()
        read_rejected = False

        @app.post("/api/artifacts/import")
        async def import_artifact(request: Request) -> dict[str, bool]:
            return {"ok": await request.body() == b"fictional"}

        @app.get("/web/session.js")
        @app.get("/api/hub")
        async def available() -> dict[str, bool]:
            return {"ok": True}

        async def held_body() -> AsyncIterator[bytes]:
            body_started.set()
            await release.wait()
            yield b"fictional"

        async def rejected_body() -> AsyncIterator[bytes]:
            nonlocal read_rejected
            read_rejected = True
            yield b"fictional"

        boundary = MultiplayerAuth(app, config, FixtureProvider(), sharing, shared_shell=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=boundary), base_url=WORKSPACE
        ) as client:
            route = "/api/artifacts/import"
            headers = {"origin": WORKSPACE}
            waiting = asyncio.create_task(client.post(route, headers=headers, content=held_body()))
            try:
                await asyncio.wait_for(body_started.wait(), timeout=5)
                rejected = await client.post(
                    route,
                    headers={**headers, "x-fixture-auth": "bob"},
                    content=rejected_body(),
                )
                assert rejected.status_code == 404 and not read_rejected
                busy = await client.post(route, headers=headers, content=rejected_body())
                assert busy.status_code == 429 and not read_rejected
                for path in ("/web/session.js", "/api/hub"):
                    assert (await client.get(path)).is_success
            finally:
                release.set()
                assert (await waiting).json() == {"ok": True}

    asyncio.run(journey())


def test_personal_import_writes_only_to_the_owned_workspace(tmp_path: Path) -> None:
    """An owner can upload more than the general request limit without widening grants."""
    with (
        running_multiplayer_app(
            tmp_path / "app",
            tmp_path / "server.log",
            unified=True,
            personal_workspace_emails=("alice@example.test",),
        ) as app,
        human(app, "owner") as primary,
        human(app, "alice") as personal,
        human(app, "bob") as guest,
    ):
        home = personal.get("/").headers["location"]
        project = personal.post(home + "api/hub/projects", json={"title": "Fictional workspace"})
        project.raise_for_status()
        project_id = cast(str, project.json()["id"])
        workspace = home.split("/")[2]
        invite = personal.post(
            f"/api/workspaces/{workspace}/invitations",
            json={
                "kind": "project",
                "resource": project_id,
                "email": "bob@example.test",
                "role": "edit",
            },
        )
        invite.raise_for_status()
        accept(guest, cast(str, invite.json()["url"]).split("#invite=")[1])

        data = b"fictional" * 200_000
        encoded = base64.b64encode(data).decode()
        assert len(encoded) > 2 * 1024 * 1024
        payload = {
            "project_id": project_id,
            "title": "Fictional bundle",
            "files": [{"name": "fixture.dat", "data_base64": encoded}],
        }
        route = home + "api/artifacts/import"
        assert primary.post(route, json=payload).status_code == 404
        assert guest.post(route, json=payload).status_code == 404
        assert (
            personal.post(route, json={**payload, "project_id": app.project_id}).status_code == 404
        )
        invalid = personal.post(
            route, json={**payload, "files": [{"name": 9, "data_base64": encoded}]}
        )
        assert invalid.status_code == 422 and len(invalid.content) < 300
        imported = personal.post(route, json=payload)
        imported.raise_for_status()
        assert set(imported.json()) == {"id"}
        artifact = f"api/artifacts/{imported.json()['id']}"
        assert personal.get(home + artifact + "/files/fixture.dat").content == data
        assert guest.get(home + artifact).is_success
        assert primary.get(home + artifact).status_code == 404
        assert primary.get("/" + artifact).status_code == 404


@pytest.mark.browser
@pytest.mark.skipif(
    os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
)
def test_personal_owner_uses_the_existing_import_dialog(tmp_path: Path) -> None:
    """Create the first project, import a folder, and run its scoped HTML preview."""
    bundle = tmp_path / "sample"
    bundle.mkdir()
    _ = (bundle / "index.html").write_text(
        '<!doctype html><h1 id="title">Fictional handout</h1><script src="script.js"></script>'
    )
    _ = (bundle / "script.js").write_text(
        "document.querySelector('#title').textContent = 'Fictional bundle loaded'"
    )
    with (
        running_multiplayer_app(
            tmp_path / "app",
            tmp_path / "server.log",
            unified=True,
            personal_workspace_emails=("alice@example.test",),
        ) as app,
        human(app, "alice") as personal,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, "alice") as context:
                page = context.new_page()
                home = WORKSPACE + personal.get("/").headers["location"]

                def forward_preview(route: Route) -> None:
                    """Keep the fixture redirect on loopback without changing its preview policy."""
                    parts = urlsplit(route.request.url)
                    headers = {
                        **route.request.headers,
                        "host": parts.netloc,
                        "x-fixture-auth": "alice",
                        "sec-fetch-dest": "iframe",
                    }
                    redirect = route.fetch(
                        url=f"{app.url}{parts.path}?{parts.query}",
                        headers=headers,
                        max_redirects=0,
                    )
                    location = redirect.headers["location"]
                    workspace = urlsplit(home).path.split("/")[2]
                    assert redirect.status == 307
                    assert location.startswith(f"/api/workspaces/{workspace}/artifacts/")
                    result = route.fetch(url=app.url + location, headers=headers, max_redirects=0)
                    assert result.status == 200
                    route.fulfill(response=result)

                page.route(
                    re.compile(r"/workspaces/[0-9a-f]{24}/api/artifacts/[^/]+/preview\?"),
                    forward_preview,
                )
                page.goto(home)
                expect(page).to_have_url(re.compile(r"/workspaces/[0-9a-f]{24}/$"))
                page.get_by_role("button", name="Import", exact=True).click()
                page.get_by_role("textbox", name="Project name").fill("Fictional project")
                page.get_by_role("button", name="Create project", exact=True).click()
                dialog = page.get_by_role("dialog", name="Import artifact")
                dialog.get_by_role("textbox", name="Title").fill("Fictional web bundle")
                with page.expect_file_chooser() as selection:
                    dialog.get_by_role("button", name="Choose folder").click()
                selection.value.set_files(str(bundle))
                dialog.get_by_role("button", name="Import artifact", exact=True).click()
                expect(page).to_have_url(re.compile(r"/workspaces/[0-9a-f]{24}/\?artifact="))
                expect(
                    page.frame_locator("#hub-html-preview").get_by_role(
                        "heading", name="Fictional bundle loaded"
                    )
                ).to_be_visible()
        finally:
            browser.close()
