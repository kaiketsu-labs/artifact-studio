"""Exercise scoped card covers through the regular shared library."""

import base64
import os
import re
import shutil
import time
from io import BytesIO
from pathlib import Path
from typing import cast

import httpx
import pytest
from PIL import Image
from playwright.sync_api import Locator, Page, expect, sync_playwright

from artifact_studio.artifact_models import ArtifactFileInput
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.hub_previews import cached, cover_path, cover_token, request_current_cover
from artifact_studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design
from tests.integration.multiplayer_fixture import (
    DECK_ID,
    WORKSPACE,
    routed_multiplayer_context,
    running_multiplayer_app,
)
from tests.test_multiplayer_server import accept, grant, human
from tests.test_unified_hosting import seed_isolated_presentation


def raster(color: str) -> bytes:
    """Build a small synthetic raster fixture without external data."""
    output = BytesIO()
    Image.new("RGB", (40, 25), color).save(output, "PNG")
    return output.getvalue()


def cover_url(client: httpx.Client, identifier: str, *, prefix: str = "") -> str:
    """Read the cover advertised for one visible library card."""
    library = client.get(prefix + "/api/hub")
    library.raise_for_status()
    item = next(entry for entry in library.json()["artifacts"] if entry["id"] == identifier)
    return prefix + cast(str, item["preview_url"])


def wait_for_cover(client: httpx.Client, url: str) -> httpx.Response:
    """Wait for the background render without polling an unrestricted origin."""
    deadline = time.monotonic() + 10
    while True:
        response = client.get(url)
        if response.status_code != 202 or time.monotonic() >= deadline:
            return response
        time.sleep(0.1)


def decoded_card(page: Page, title: str) -> Locator:
    """Wait for a real image decode, with time for queued renders on CI."""
    card = page.get_by_role("button", name=re.compile(f"^Open {title},"))
    thumbnail = card.locator("img[data-preview-image]")
    expect(thumbnail).to_be_visible(timeout=45000)
    assert thumbnail.evaluate("(image) => image.complete && image.naturalWidth > 0")
    return thumbnail


def test_shared_covers_require_the_current_grant_and_revision(tmp_path: Path) -> None:
    """Deny cached old covers, private siblings and every read after revocation."""
    with (
        running_multiplayer_app(tmp_path / "app", tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as guest,
    ):
        service = ArtifactStore(app.store)
        source = tmp_path / "fixture.png"
        _ = source.write_bytes(raster("#2c6588"))
        item = service.register_artifact(app.project_id, "Fictional photo", source, source.name)
        accept(guest, grant(owner, "alice", item.id, "view"))
        original_url = cover_url(guest, item.id)
        assert guest.get(cover_url(owner, item.id)).status_code == 409
        assert guest.get(cover_url(owner, app.sibling_id)).status_code == 404
        original = wait_for_cover(guest, original_url)
        assert original.is_success, original.text
        assert original.headers["content-type"] == "image/png"
        assert original.headers["cache-control"] == "no-store"
        _ = service.revise_artifact(
            item.id,
            [
                ArtifactFileInput(
                    path=source.name, data_base64=base64.b64encode(raster("#da763c")).decode()
                )
            ],
            item.revision,
            "fictional-cover-revision",
        )
        assert owner.get(original_url).content == original.content
        assert guest.get(original_url).status_code == 409
        fresh_url = cover_url(guest, item.id)
        assert fresh_url != original_url
        current = wait_for_cover(guest, fresh_url)
        assert current.is_success and current.content != original.content
        access = "/api/workspaces/default/access"
        scope = {"kind": "artifact", "resource": item.id}
        grants = owner.get(access, params=scope).json()["grants"]
        grant_id = next(row["id"] for row in grants if row["email"] == "alice@example.test")
        assert owner.post(f"{access}/{grant_id}/revoke", json=scope).is_success
        assert guest.get(fresh_url).status_code == 404


def test_identical_deck_covers_do_not_share_a_cache(tmp_path: Path) -> None:
    """A matching ID and visual token cannot cross workspace database directories."""
    stores = [
        Store(tmp_path / name / "collection", tmp_path / name / "state/review.sqlite3")
        for name in ("first", "second")
    ]
    make_design(stores[0].root / TEMPLATE_ID)
    stores[0].create(DECK_ID, "Fictional repeated deck", "test-brand")
    _ = shutil.copytree(stores[0].root, stores[1].root, dirs_exist_ok=True)
    first, second = (ArtifactStore(store).get_artifact(DECK_ID) for store in stores)
    token = cover_token(stores[0], first, scoped=True)
    assert token == cover_token(stores[1], second, scoped=True)
    assert token != cover_token(stores[0], first)
    first_cover = cover_path(stores[0], DECK_ID, token)
    first_cover.parent.mkdir(parents=True)
    _ = first_cover.write_bytes(raster("#2c6588"))
    assert request_current_cover(stores[0], first, token).path == first_cover
    assert cached(stores[1], DECK_ID, token) is None


@pytest.mark.browser
@pytest.mark.skipif(
    os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Browser checks are opt in."
)
def test_shared_library_renders_deck_and_document_covers(tmp_path: Path) -> None:
    """A View guest can open the same decoded thumbnail cards as the owner."""
    root = tmp_path / "app"
    with (
        running_multiplayer_app(root, tmp_path / "server.log", unified=True) as app,
        human(app, "owner") as owner,
        human(app, "alice") as guest,
        human(app, "bob") as isolated_guest,
        sync_playwright() as playwright,
    ):
        for identifier in (app.deck_id, app.artifact_id):
            accept(guest, grant(owner, "alice", identifier, "view"))
        created = owner.post("/api/workspaces", json={"title": "Fictional isolated workspace"})
        created.raise_for_status()
        workspace = cast(str, created.json()["id"])
        prefix = f"/workspaces/{workspace}"
        seed_isolated_presentation(root, app, workspace)
        invitation = owner.post(
            f"/api/workspaces/{workspace}/invitations",
            json={
                "kind": "artifact",
                "resource": app.deck_id,
                "email": "bob@example.test",
                "role": "view",
            },
        )
        invitation.raise_for_status()
        accept(isolated_guest, cast(str, invitation.json()["url"]).split("#invite=")[1])
        default_cover = cover_url(guest, app.deck_id)
        isolated_cover = cover_url(isolated_guest, app.deck_id, prefix=prefix)
        assert isolated_guest.get(default_cover).status_code == 404
        assert guest.get(isolated_cover).status_code == 404
        assert isolated_guest.get(prefix + default_cover).status_code == 409
        browser = playwright.chromium.launch()
        try:
            with routed_multiplayer_context(browser, app, "alice") as context:
                page = context.new_page()
                page.goto(WORKSPACE)
                for title in ("Fictional team deck", "Shared candidate"):
                    _ = decoded_card(page, title)
                expect(
                    page.get_by_role("button", name=re.compile("^Open Private sibling"))
                ).to_have_count(0)
                page.get_by_role("button", name=re.compile("^Open Fictional team deck,")).click()
                expect(page.locator("#slide-title")).to_be_visible()
            with routed_multiplayer_context(browser, app, "bob") as context:
                page = context.new_page()
                page.goto(WORKSPACE + prefix + "/")
                expect(decoded_card(page, "Isolated presentation")).to_have_attribute(
                    "data-cover-url", isolated_cover
                )
                expect(
                    page.get_by_role("button", name=re.compile("^Open Fictional team deck,"))
                ).to_have_count(0)
                page.get_by_role("button", name=re.compile("^Open Isolated presentation,")).click()
                expect(page.locator("#slide-title")).to_be_visible()
                expect(page).to_have_url(re.compile(f"{WORKSPACE}{prefix}/[?]deck={app.deck_id}"))
        finally:
            browser.close()
