"""Real browser handoff through a fictional edge login; no provider/account calls."""

import json
import os
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import Page, Route, expect, sync_playwright
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import Receive, Send
from starlette.types import Scope as ASGIScope

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.authentication import AuthenticationError, Principal
from artifact_studio.invitation_provisioning import InvitationProvisioning
from artifact_studio.multiplayer import create_app
from artifact_studio.multiplayer_access import Scope
from artifact_studio.shared_editor import create_shared_editor
from artifact_studio.unified import UnifiedStudio
from tests.test_invitation_provisioning import Fixture
from tests.test_invitation_provisioning import setup as setup

KEY = "artifact-studio-invitation-handoff-v1"
ORIGIN = "https://studio.example.test"
LOGIN = "https://login.example.test"


class FixtureAuth:
    async def authenticate(self, headers: Headers) -> Principal:
        identity = headers.get("x-fixture-auth", "")
        if identity not in {"owner", "alice", "bob"}:
            raise AuthenticationError()
        return Principal(identity, identity + "@example.test")


def application(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch, *, enabled: bool = True
) -> FastAPI:
    sharing, _, _, _, _, config, adapter = setup
    monkeypatch.setenv("ARTIFACT_STUDIO_INVITATION_LANDING", "1" if enabled else "0")
    admission = InvitationProvisioning(sharing, config, adapter)
    return create_app(config, FixtureAuth(), sharing, invitation_provisioning=admission)


@dataclass
class Journey:
    page: Page
    requests: list[str]
    tokens: list[str]


@contextmanager
def browser_journey(
    app: FastAPI, *, identity: str = "alice", width: int = 1100, dark: bool = False
) -> Generator[Journey]:
    with TestClient(app, base_url=ORIGIN) as client, sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
        )
        context = browser.new_context(
            viewport={"width": width, "height": 780}, color_scheme="dark" if dark else "light"
        )
        logged_in = False
        requests: list[str] = []
        tokens: list[str] = []

        def route(request: Route) -> None:
            nonlocal logged_in
            url = urlsplit(request.request.url)
            requests.append(request.request.url)
            if url.netloc == "login.example.test":
                if url.path == "/complete":
                    logged_in = True
                    request.fulfill(status=302, headers={"Location": ORIGIN + "/invitation"})
                else:
                    request.fulfill(
                        content_type="text/html",
                        body=(
                            "<!doctype html><title>Fictional identity provider</title>"
                            '<h1>Fictional sign-in</h1><a href="/complete">Sign in</a>'
                        ),
                    )
                return
            assert url.netloc in {"studio.example.test", "preview.example.test"}
            # Mimic the separately configured edge: only the exact public stub bypasses login.
            if not logged_in and url.path == "/invitation":
                request.fulfill(status=302, headers={"Location": LOGIN + "/sign-in"})
                return
            headers = {"X-Fixture-Auth": identity} if logged_in else {}
            browser_headers = request.request.all_headers()
            for name in ("sec-fetch-dest", "sec-fetch-site", "sec-fetch-mode"):
                if value := browser_headers.get(name):
                    headers[name] = value
            # Chromium's route interception can precede generated fetch metadata.
            # Mirror the browser request type when bridging into the ASGI client.
            destination = {
                "document": "iframe" if request.request.frame.parent_frame else "document",
                "stylesheet": "style",
                "script": "script",
                "image": "image",
                "font": "font",
            }.get(request.request.resource_type, "empty")
            headers.setdefault("sec-fetch-dest", destination)
            if origin := request.request.headers.get("origin"):
                headers["Origin"] = origin
            if request.request.method == "POST":
                headers["Content-Type"] = "application/json"
                if url.path == "/api/invitations/accept":
                    tokens.append(json.loads(request.request.post_data or "{}")["token"])
            response = client.request(
                request.request.method,
                request.request.url,
                headers=headers,
                content=request.request.post_data_buffer,
            )
            request.fulfill(
                status=response.status_code,
                body=response.content,
                headers={
                    key: value
                    for key, value in response.headers.items()
                    if key not in {"content-length", "content-encoding"}
                },
            )

        context.route("**/*", route)
        page = context.new_page()
        try:
            yield Journey(page, requests, tokens)
        finally:
            context.close()
            browser.close()


def test_exact_public_stub_and_all_other_auth_boundaries(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = application(setup, monkeypatch)
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get("/_invitation")
        assert response.status_code == 200
        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert "'sha256-" in response.headers["content-security-policy"]
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["cache-control"] == "no-store"
        assert "alice@example.test" not in response.text
        assert client.head("/_invitation").content == b""
        for path in ("/_invitation/", "/_invitation/extra", "/invitation", "/api/together/session"):
            assert client.get(path).status_code == 401
        for path in ("/_invitation?invite=fictional", "/%5Finvitation"):
            assert client.get(path).status_code == 404
        assert client.post("/_invitation", json={"token": "fictional"}).status_code == 404
        assert client.get("/_invitation", headers={"Host": "evil.example.test"}).status_code == 400
        assert (
            client.get("/_invitation", headers={"Host": "preview.example.test"}).status_code == 404
        )
        assert (
            client.get(
                "/invitation?next=https://evil.example.test",
                headers={
                    "X-Fixture-Auth": "owner",
                },
            ).status_code
            == 404
        )
        assert client.post("/api/invitations/accept", json={"token": "a" * 43}).status_code == 403
        assert (
            client.post(
                "/api/invitations/accept", headers={"Origin": ORIGIN}, json={"token": "a" * 43}
            ).status_code
            == 401
        )
    disabled = application(setup, monkeypatch, enabled=False)
    with TestClient(disabled, base_url=ORIGIN) as client:
        assert client.get("/_invitation").status_code == 401
        assert client.get("/invitation", headers={"X-Fixture-Auth": "owner"}).status_code == 404


@pytest.mark.parametrize("width,dark,kind", [(1100, False, "artifact"), (320, True, "project")])
def test_fragment_survives_fictional_login_and_opens_accepted_destination(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch, width: int, dark: bool, kind: str
) -> None:
    sharing, _, alice, project, artifact, _, _ = setup
    target = project if kind == "project" else artifact
    app = application(setup, monkeypatch)
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post(
            "/api/workspaces/default/invitations",
            headers={
                "X-Fixture-Auth": "owner",
                "Origin": ORIGIN,
            },
            json={"kind": target.kind, "resource": target.resource, "email": "alice@example.test"},
        )
        link = response.json()["url"]
    assert link.startswith(ORIGIN + "/_invitation#invite=")
    token = link.split("#invite=")[1]
    app = application(setup, monkeypatch)
    with browser_journey(app, width=width, dark=dark) as journey:
        page = journey.page
        page.goto(link)
        expect(page).to_have_url(ORIGIN + "/_invitation")
        expect(page.get_by_role("button", name="Continue to sign in")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        assert json.loads(page.evaluate("key=>sessionStorage.getItem(key)", KEY))["token"] == token
        handoff = page.evaluate("key=>sessionStorage.getItem(key)", KEY)
        page.reload()
        expect(page.get_by_role("button", name="Continue to sign in")).to_be_visible()
        assert page.evaluate("key=>sessionStorage.getItem(key)", KEY) == handoff
        page.keyboard.press("Tab")
        expect(page.get_by_role("button", name="Continue to sign in")).to_be_focused()
        page.keyboard.press("Enter")
        expect(page).to_have_url(LOGIN + "/sign-in")
        page.get_by_role("link", name="Sign in", exact=True).click()
        expect(page).to_have_url(ORIGIN + "/invitation")
        expect(page.locator("#account")).to_have_text("Signed in as alice@example.test")
        page.get_by_role("button", name="Join", exact=True).click()
        expect(page).to_have_url(ORIGIN + "/?" + target.kind + "=" + target.resource)
        assert page.evaluate("key=>sessionStorage.getItem(key)", KEY) is None
        assert all(token not in url for url in journey.requests)
        assert journey.tokens == [token]
        expect(
            page.frame_locator("#preview").get_by_text("Fictional source", exact=True)
        ).to_be_visible()
    assert sharing.level(alice, target) == 3
    if kind == "artifact":
        assert sharing.level(alice, project) == 0


def test_unified_routes_only_exact_public_landing_to_collaboration(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    sharing, owner, alice, _, _, config, _ = setup
    collaboration = application(setup, monkeypatch)
    workspace = sharing.create_workspace(owner, "Fictional invited workspace")
    from artifact_studio.multiplayer_access import Scope

    invitation = sharing.invite(owner, Scope(workspace), "alice@example.test", "view")

    async def editor(scope: ASGIScope, receive: Receive, send: Send) -> None:
        await JSONResponse({"detail": "Fixture editor requires auth"}, status_code=401)(
            scope, receive, send
        )

    unified = UnifiedStudio(
        config, editor, collaboration, create_shared_editor(config, FixtureAuth(), sharing), sharing
    )
    with TestClient(unified, base_url=ORIGIN) as client:
        assert client.get("/_invitation").status_code == 200
        assert client.get(f"/workspaces/{workspace}/_invitation").status_code == 401
        assert client.get("/invitation").status_code == 401
        response = client.post(
            "/api/invitations/accept",
            headers={
                "X-Fixture-Auth": "alice",
                "Origin": ORIGIN,
            },
            json={"token": invitation["token"]},
        )
        assert response.status_code == 200
        assert response.json()["destination"] == f"/workspaces/{workspace}/"
    assert sharing.level(alice, Scope(workspace)) == 1


@pytest.mark.parametrize("failure", ["recipient", "expired", "revoked", "replay"])
def test_invalid_acceptance_clears_handoff_without_granting_access(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    sharing, owner, alice, project, artifact, _, _ = setup
    app = application(setup, monkeypatch)
    invitation = sharing.invite(owner, artifact, "alice@example.test", "view")
    if failure == "recipient":
        # Bob can sign in for his own invitation but cannot claim Alice's link.
        sharing.invite(owner, project, "bob@example.test", "view")
    with browser_journey(app, identity="bob" if failure == "recipient" else "alice") as journey:
        page = journey.page
        page.goto(ORIGIN + "/_invitation#invite=" + invitation["token"])
        page.get_by_role("button", name="Continue to sign in").click()
        page.get_by_role("link", name="Sign in", exact=True).click()
        expect(page.get_by_role("button", name="Join", exact=True)).to_be_visible()
        if failure == "expired":
            with sharing.connect() as db:
                db.execute(
                    "UPDATE invitations SET expires=? WHERE id=?",
                    (time.time() - 1, invitation["id"]),
                )
        elif failure == "revoked":
            sharing.revoke(owner, artifact, invitation["id"])
        elif failure == "replay":
            sharing.accept(alice, invitation["token"])
        page.get_by_role("button", name="Join", exact=True).click()
        expect(page.locator("#status")).to_contain_text("unavailable for this account")
        expect(page.get_by_role("button", name="Join", exact=True)).to_be_hidden()
        assert page.evaluate("key=>sessionStorage.getItem(key)", KEY) is None
        assert page.url == ORIGIN + "/invitation"
    assert sharing.level(alice, artifact) == (1 if failure == "replay" else 0)


def test_uninvited_identity_cannot_open_protected_continuation(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    sharing, owner, alice, _, artifact, _, _ = setup
    app = application(setup, monkeypatch)
    invitation = sharing.invite(owner, artifact, "alice@example.test", "view")
    with browser_journey(app, identity="bob") as journey:
        page = journey.page
        page.goto(ORIGIN + "/_invitation#invite=" + invitation["token"])
        page.get_by_role("button", name="Continue to sign in").click()
        page.get_by_role("link", name="Sign in", exact=True).click()
        expect(page.locator("#status")).to_contain_text("unavailable for this account")
        expect(page.get_by_role("button", name="Join", exact=True)).to_be_hidden()
        assert page.evaluate("key=>sessionStorage.getItem(key)", KEY) is None
        assert journey.tokens == []
    assert sharing.level(alice, artifact) == 0


def test_blocked_browser_storage_does_not_forward_token(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    sharing, owner, _, project, _, _, _ = setup
    app = application(setup, monkeypatch)
    invitation = sharing.invite(owner, project, "alice@example.test", "view")
    with browser_journey(app) as journey:
        page = journey.page
        page.add_init_script(
            "Object.defineProperty(window,'sessionStorage',{get(){throw Error('blocked');}})"
        )
        page.goto(ORIGIN + "/_invitation#invite=" + invitation["token"])
        expect(page.locator("#status")).to_contain_text("Could not continue securely")
        expect(page.get_by_role("button", name="Continue to sign in")).to_be_hidden()
        assert page.url == ORIGIN + "/_invitation"
        assert journey.tokens == []


def test_landing_flag_requires_an_enabled_admission_service(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    sharing, _, _, _, _, config, _ = setup
    monkeypatch.setenv("ARTIFACT_STUDIO_INVITATION_LANDING", "1")
    monkeypatch.delenv("ARTIFACT_STUDIO_EXPERIMENTAL_INVITATIONS", raising=False)
    with pytest.raises(ValueError, match="requires the configured"):
        create_app(config, FixtureAuth(), sharing)


def test_missing_or_old_handoff_never_sends_an_acceptance(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    sharing, owner, _, project, _, _, _ = setup
    app = application(setup, monkeypatch)
    invitation = sharing.invite(owner, project, "alice@example.test", "view")
    with browser_journey(app) as journey:
        page = journey.page
        page.goto(ORIGIN + "/_invitation#invite=" + invitation["token"])
        page.evaluate(
            "key=>{const value=JSON.parse(sessionStorage.getItem(key));value.created=0;"
            "sessionStorage.setItem(key,JSON.stringify(value));}",
            KEY,
        )
        page.get_by_role("button", name="Continue to sign in").click()
        expect(page.locator("#status")).to_contain_text("expired")
        assert journey.tokens == []
        page.goto(ORIGIN + "/_invitation#invite=bad&redirect=https://evil.example.test")
        expect(page.locator("#status")).to_contain_text("incomplete")
        assert page.evaluate("key=>sessionStorage.getItem(key)", KEY) is None


@pytest.mark.parametrize("created", [0, 9999999999999])
def test_public_reload_does_not_revive_invalid_handoff(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch, created: int
) -> None:
    sharing, owner, _, project, _, _, _ = setup
    app = application(setup, monkeypatch)
    invitation = sharing.invite(owner, project, "alice@example.test", "view")
    with browser_journey(app) as journey:
        page = journey.page
        page.goto(ORIGIN + "/_invitation#invite=" + invitation["token"])
        page.evaluate(
            "([key,created])=>{const value=JSON.parse(sessionStorage.getItem(key));"
            "value.created=created;sessionStorage.setItem(key,JSON.stringify(value));}",
            [KEY, created],
        )
        page.reload()
        expect(page.locator("#status")).to_contain_text("original invitation link")
        expect(page.get_by_role("button", name="Continue to sign in")).to_be_hidden()
        assert page.evaluate("key=>sessionStorage.getItem(key)", KEY) is None
        assert journey.tokens == []


@pytest.mark.parametrize("empty", [False, True])
def test_project_invitation_opens_that_project_with_existing_other_access(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, empty: bool
) -> None:
    sharing, owner, alice, _, original, _, _ = setup
    service = ArtifactStore(sharing.store("default"))
    project = service.create_project("Another fictional project")
    source = tmp_path / "another.md"
    source.write_text("Another fictional source")
    another = service.register_artifact(
        project.id, "Another fictional artifact", source, source.name
    )
    first, target = sorted(
        [service.get_artifact(original.resource), another], key=lambda item: item.id
    )
    app = application(setup, monkeypatch)
    prior = sharing.invite(
        owner, Scope("default", "project", first.project_id), "alice@example.test", "view"
    )
    sharing.accept(alice, prior["token"])
    target_project = (
        service.create_project("Empty fictional project").id if empty else target.project_id
    )
    invitation = sharing.invite(
        owner, Scope("default", "project", target_project), "alice@example.test", "view"
    )
    with browser_journey(app) as journey:
        page = journey.page
        page.goto(ORIGIN + "/_invitation#invite=" + invitation["token"])
        page.get_by_role("button", name="Continue to sign in").click()
        page.get_by_role("link", name="Sign in", exact=True).click()
        page.get_by_role("button", name="Join", exact=True).click()
        if empty:
            expect(page).to_have_url(ORIGIN + "/?workspace=default&project=" + target_project)
            expect(page.locator("#editor")).to_be_hidden()
            page.reload()
            expect(page.locator("#catalog")).to_contain_text("Empty fictional project")
            expect(page.locator("#editor")).to_be_hidden()
            expect(page).to_have_url(ORIGIN + "/?workspace=default&project=" + target_project)
        else:
            expect(page.locator("#artifact-title")).to_have_text(target.title)
