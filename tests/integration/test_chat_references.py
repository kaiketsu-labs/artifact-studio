"""Type @ paths through the real composer, including cursor previews and delivery."""

import json
import os
from collections import Counter
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, Route, expect

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.hub_previews import cover_path, cover_token, cover_url
from scripts.preview_favorites import prepare_covers, seed_favorites
from tests.integration.support import DECK_ID, RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


@pytest.fixture
def prepared(live_app: RunningApp, browser: Browser, tmp_path: Path) -> RunningApp:
    seed_favorites(live_app, tmp_path)
    prepare_covers(live_app, browser)
    return live_app


def test_keyboard_paths_and_reference_message_delivery(page: Page, prepared: RunningApp) -> None:
    page.goto(f"{prepared.url}/?deck={DECK_ID}#workflow")
    text = page.locator("#feedback-text")
    text.fill("Use @Fieldnotes")
    options = page.get_by_role("listbox", name="Workspace references")
    expect(options).to_be_visible()
    # Keyboard browse a project without attaching it; Enter chooses the resulting artifact.
    row = options.get_by_role("option", name="/Fieldnotes", exact=True)
    row.hover()
    text.press("ArrowRight")
    expect(text).to_have_value("Use @/Fieldnotes/")
    text.press("Tab")
    expect(page.locator(".is-path-reference")).to_have_count(1)
    expect(text).to_be_focused()
    assert not prepared.store.comments(DECK_ID)
    text.press_sequentially("again @Brand direction")
    expect(options.get_by_role("option")).to_have_count(1)
    text.press("Enter")
    expect(page.locator(".is-path-reference")).to_have_count(1)
    text.press("End")
    text.press_sequentially("and @three-step")
    expect(options.get_by_role("option")).to_have_count(1)
    text.press("Enter")
    expect(page.locator(".is-path-reference")).to_have_count(2)
    text.press_sequentially("for layout")
    text.press("Enter")
    expect(page.locator("form .is-path-reference")).to_have_count(0)
    event = prepared.store.comments(DECK_ID)[-1]
    assert len(event.files) == 2
    assert "for layout" in event.message
    assert "reference" in event.files[0].name and "artifact-" not in event.files[0].name
    assert "Clear three-step process." in Path(event.files[1].path).read_text()
    page.reload()
    expect(page.locator("#feedback-list .is-path-reference")).to_have_count(2)
    assert '@"/' not in event.message


def test_hover_preview_tracks_cursor_and_clamps_to_viewport(
    page: Page, prepared: RunningApp
) -> None:
    page.goto(f"{prepared.url}/?deck={DECK_ID}#workflow")
    text = page.locator("#feedback-text")
    text.fill("@three-step")
    row = page.get_by_role("option").first
    row.hover()
    preview = page.locator(".chat-reference-preview")
    expect(preview).to_be_visible()
    expect(preview.frame_locator("iframe").locator(".slide")).to_be_visible()
    box = preview.bounding_box()
    assert box is not None and box["x"] >= 0 and box["y"] >= 0
    assert box["x"] + box["width"] <= page.viewport_size["width"]  # type: ignore[index]
    assert preview.evaluate("el=>getComputedStyle(el).pointerEvents") == "none"
    bounds = row.bounding_box()
    assert bounds is not None
    page.mouse.move(bounds["x"] + 20, bounds["y"] + 10)
    page.wait_for_timeout(200)
    moved = preview.bounding_box()
    assert moved is not None and moved != box
    text.press("Escape")
    expect(preview).to_be_hidden()
    expect(page.get_by_role("listbox", name="Workspace references")).to_be_hidden()
    text.fill("Contact editor@example.test")
    expect(page.get_by_role("listbox", name="Workspace references")).to_be_hidden()


def test_artifact_hover_waits_for_pending_cover_and_reuses_decoded_image(
    page: Page, prepared: RunningApp
) -> None:
    """The actual artifact cover replaces a pending response and survives another hover."""
    item = next(
        item
        for item in ArtifactStore(prepared.store).list_artifacts()
        if item.title == "Brand direction"
    )
    token = cover_token(prepared.store, item)
    url = prepared.url + cover_url(item.id, token)
    png = cover_path(prepared.store, item.id, token).read_bytes()
    attempts = 0

    def image(route: Route) -> None:
        nonlocal attempts
        attempts += 1
        assert route.request.url == url
        if attempts == 1:
            route.fulfill(
                status=202,
                content_type="application/json",
                body=json.dumps({"code": "cover_pending", "detail": "Updating preview."}),
                headers={"Retry-After": "1"},
            )
        else:
            route.fulfill(status=200, content_type="image/png", body=png)

    page.route("**/api/hub/previews/**", image)
    page.goto(f"{prepared.url}/?deck={DECK_ID}#workflow")
    text = page.locator("#feedback-text")
    text.fill("@Brand direction")
    assert attempts == 0
    row = page.get_by_role("option", name="/Fieldnotes/Brand direction", exact=True)
    with page.expect_response(url) as response:
        row.hover()
    assert response.value.status == 202
    preview = page.locator(".chat-reference-preview")
    expect(preview).to_be_visible()
    expect(preview).to_contain_text(item.title)
    cover = preview.locator("img")
    expect(cover).to_be_visible()
    assert cover.evaluate("image => image.complete && image.naturalWidth > 0")
    assert attempts == 2
    original_url = cover.get_attribute("src")
    assert original_url is not None and original_url.startswith("blob:")
    text.hover()
    expect(preview).to_be_hidden()
    expect(preview.locator("img")).to_have_count(0)
    row.hover()
    expect(cover).to_be_visible()
    expect(cover).to_have_attribute("src", original_url)
    assert attempts == 2


@pytest.mark.parametrize("dismiss", ["pointerleave", "Escape"])
def test_artifact_hover_dismissal_stops_pending_retries(
    page: Page, prepared: RunningApp, dismiss: str
) -> None:
    """Leaving the suggestion or closing it removes its image and pending retry demand."""
    attempts = 0

    def pending(route: Route) -> None:
        nonlocal attempts
        attempts += 1
        route.fulfill(
            status=202,
            content_type="application/json",
            body=json.dumps({"code": "cover_pending", "detail": "Updating preview."}),
            headers={"Retry-After": "1"},
        )

    page.route("**/api/hub/previews/**", pending)
    page.goto(f"{prepared.url}/?deck={DECK_ID}#workflow")
    text = page.locator("#feedback-text")
    text.fill("@Brand direction")
    with page.expect_response("**/api/hub/previews/**") as response:
        page.get_by_role("option", name="/Fieldnotes/Brand direction", exact=True).hover()
    assert response.value.status == 202
    preview = page.locator(".chat-reference-preview")
    expect(preview).to_be_visible()
    expect(preview.locator("img")).to_be_hidden()
    if dismiss == "pointerleave":
        text.hover()
    else:
        text.press("Escape")
    expect(preview).to_be_hidden()
    expect(preview.locator("img")).to_have_count(0)
    page.wait_for_timeout(1500)
    assert attempts == 1


def test_artifact_hover_keeps_saved_cover_and_loads_cached_cover_during_renderer_failure(
    browser: Browser, prepared: RunningApp
) -> None:
    """A renderer failure keeps the caption and saved image without blocking cached work."""
    items = ArtifactStore(prepared.store).list_artifacts()
    item = next(item for item in items if item.title == "Brand direction")
    ready = next(item for item in items if item.title == "Launch brief")
    token, ready_token = cover_token(prepared.store, item), cover_token(prepared.store, ready)
    current = prepared.url + cover_url(item.id, token)
    fallback = cover_url(item.id, "1" * 64)
    cached = prepared.url + cover_url(ready.id, ready_token)
    images = {
        prepared.url + fallback: cover_path(prepared.store, item.id, token).read_bytes(),
        cached: cover_path(prepared.store, ready.id, ready_token).read_bytes(),
    }
    requests: Counter[str] = Counter()
    errors: list[str] = []

    def catalog(route: Route) -> None:
        response = route.fetch()
        data = response.json()
        for entry in data:
            if entry["artifact_id"] == item.id:
                entry["preview_fallback_url"] = fallback
                entry["preview_cached"] = False
        route.fulfill(response=response, body=json.dumps(data), content_type="application/json")

    def image(route: Route) -> None:
        url = route.request.url
        requests[url] += 1
        if url == current:
            route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps(
                    {"code": "renderer_unavailable", "detail": "Preview temporarily unavailable."}
                ),
                headers={"Retry-After": "30"},
            )
        else:
            route.fulfill(status=200, content_type="image/png", body=images[url])

    context = browser.new_context(viewport={"width": 1600, "height": 1000}, color_scheme="dark")
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.clock.install()
    page.route("**/api/chat-references", catalog)
    page.route("**/api/hub/previews/**", image)
    try:
        page.goto(f"{prepared.url}/?deck={DECK_ID}#workflow")
        text = page.locator("#feedback-text")
        text.fill("@Brand direction")
        with page.expect_response(current) as response:
            page.get_by_role("option", name="/Fieldnotes/Brand direction", exact=True).hover()
        assert response.value.status == 503
        preview = page.locator(".chat-reference-preview")
        expect(preview).to_contain_text(item.title)
        expect(preview.locator("img")).to_be_visible()
        saved_url = preview.locator("img").get_attribute("src")
        assert saved_url is not None
        text.fill("@Launch brief")
        page.get_by_role("option", name="/Fieldnotes/Launch brief", exact=True).hover()
        expect(preview.locator("img")).to_be_visible()
        assert requests[cached] == 1
        text.fill("@Brand direction")
        page.get_by_role("option", name="/Fieldnotes/Brand direction", exact=True).hover()
        expect(preview.locator("img")).to_have_attribute("src", saved_url)
        page.clock.fast_forward(31_000)
        page.wait_for_timeout(100)
        assert requests[current] == 2
        assert requests[prepared.url + fallback] == 1
        expect(preview.locator("img")).to_have_attribute("src", saved_url)
        assert not errors, errors
    finally:
        context.close()


def test_touch_native_artifact_reference_keeps_focus_and_saved_context(
    browser: Browser, prepared: RunningApp
) -> None:
    item = next(
        item
        for item in ArtifactStore(prepared.store).list_artifacts()
        if item.title == "Launch brief"
    )
    context = browser.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, color_scheme="dark"
    )
    page = context.new_page()
    try:
        page.goto(f"{prepared.url}/?artifact={item.id}")
        text = page.locator("#hub-feedback-text")
        text.fill("Compare @Research notes")
        options = page.get_by_role("listbox", name="Workspace references")
        options.get_by_role("option").first.tap()
        expect(page.locator(".is-path-reference")).to_have_count(1)
        expect(text).to_be_focused()
        text.press_sequentially("with @Fieldnotes")
        expect(page.get_by_role("listbox", name="Workspace references")).to_be_visible()
        options.get_by_role("option", name="/Fieldnotes", exact=True).tap()
        expect(page.locator(".is-path-reference")).to_have_count(2)
        expect(text).to_be_focused()
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        page.locator("#hub-send").tap()
        expect(page.locator("form .is-path-reference")).to_have_count(0)
        event = ArtifactStore(prepared.store).feedback(item.id, include_pending=True)[-1]
        assert len(event.files) == 2
        assert json.loads(Path(event.files[1].path).read_text())["scope"].startswith(
            "Project brief"
        )
        page.reload()
        expect(page.locator("#hub-thread")).to_contain_text("Research notes")
    finally:
        context.close()


def test_path_browsing_handles_slashes_quotes_and_at_signs(
    page: Page, prepared: RunningApp, tmp_path: Path
) -> None:
    artifacts = ArtifactStore(prepared.store)
    project = artifacts.create_project('Research / @home "2026"')
    source = tmp_path / "reference-source"
    source.mkdir()
    _ = (source / "index.html").write_text("<h1>Odd path names work</h1>")
    _ = artifacts.register_artifact(project.id, "Exact source", source, "index.html")
    page.goto(f"{prepared.url}/?deck={DECK_ID}#workflow")
    text = page.locator("#feedback-text")
    text.fill("Use @Research")
    options = page.get_by_role("listbox", name="Workspace references")
    options.get_by_role("option", name="/" + project.title, exact=True).hover()
    text.press("ArrowRight")
    expect(options.get_by_role("option")).to_have_count(1)
    text.press("Enter")
    expect(page.locator(".is-path-reference")).to_contain_text("Exact source")
    expect(text).to_have_value("Use ")


@pytest.mark.parametrize("composer", ["presentation", "artifact"])
def test_tab_attaches_highlighted_title_pill_and_keeps_prose(
    page: Page, prepared: RunningApp, composer: str
) -> None:
    """Both chats attach the highlighted item, remove pills and deliver frozen references."""
    item = next(
        item
        for item in ArtifactStore(prepared.store).list_artifacts()
        if item.title == "Launch brief"
    )
    presentation = composer == "presentation"
    page.goto(
        f"{prepared.url}/?deck={DECK_ID}#workflow"
        if presentation
        else f"{prepared.url}/?artifact={item.id}"
    )
    text = page.locator("#feedback-text" if presentation else "#hub-feedback-text")
    text.fill("Compare @Fieldnotes")
    options = page.get_by_role("listbox", name="Workspace references")
    expect(options.get_by_role("option")).not_to_have_count(0)
    text.press("ArrowDown")
    selected = options.locator('[aria-selected="true"]')
    title = selected.locator("strong").inner_text()
    text.press("Tab")
    pill = page.locator("form .is-path-reference")
    expect(pill).to_have_count(1)
    expect(pill.locator(".chat-file-name")).to_have_text(title)
    expect(pill.locator("small,img,iframe")).to_have_count(0)
    expect(text).to_have_value("Compare ")
    expect(text).to_be_focused()
    pill.get_by_role("button").click()
    expect(pill).to_have_count(0)
    expect(text).to_have_value("Compare ")
    text.fill("Compare @Brand direction")
    expect(options.get_by_role("option")).to_have_count(1)
    text.press("Enter")
    expect(pill).to_have_count(1)
    text.press_sequentially("with the current draft.")
    text.press("Enter")
    expect(pill).to_have_count(0)
    event = (
        prepared.store.comments(DECK_ID)[-1]
        if presentation
        else ArtifactStore(prepared.store).feedback(item.id, include_pending=True)[-1]
    )
    assert event.message == "Compare with the current draft."
    assert len(event.files) == 1
    frozen = json.loads(Path(event.files[0].path).read_text())
    assert frozen["title"] == "Brand direction"
    page.reload()
    sent = page.locator("#feedback-list" if presentation else "#hub-thread")
    expect(sent.locator(".is-path-reference")).to_have_text("@Brand direction")
    expect(sent).not_to_contain_text('@"/')
    expect(sent).not_to_contain_text(" - artifact reference.json")


@pytest.mark.parametrize("width", [320, 390])
def test_narrow_artifact_sidebar_and_chat_controls_are_independently_clickable(
    page: Page, prepared: RunningApp, width: int,
) -> None:
    """Wrapped headers and full-width sidebars must not put both toggles in one corner."""
    page.set_viewport_size({"width": width, "height": 844})
    item = next(
        item for item in ArtifactStore(prepared.store).list_artifacts()
        if item.title == "Launch brief"
    )
    page.goto(f"{prepared.url}/?artifact={item.id}")
    sidebar = page.get_by_role("button", name="Close sidebar", exact=True)
    chat = page.get_by_role("button", name="Close chat", exact=True)
    expect(sidebar).to_be_visible()
    expect(chat).to_be_visible()
    sidebar.click()
    page.get_by_role("button", name="Open sidebar", exact=True).click()
    chat.click()
    page.get_by_role("button", name="Open chat", exact=True).click()
    expect(sidebar).to_be_visible()
    expect(chat).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
