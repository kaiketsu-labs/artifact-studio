"""Saved reference pills preview frozen attachments without reopening mutable sources."""

import os
from pathlib import Path
from typing import Literal

import pytest
from playwright.sync_api import Browser, Page, Route, expect

from artifact_studio.artifact_models import ArtifactFileInput
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.chat_references import ChatReferences
from artifact_studio.favorites import Favorites, StarInput
from artifact_studio.models import SlidePatch
from artifact_studio.storage import digest
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


@pytest.mark.parametrize("composer", ["presentation", "artifact"])
def test_artifact_pill_hover_and_focus_keep_frozen_source_and_reuse_preview(
    page: Page, prepared: RunningApp, composer: str
) -> None:
    registry = ArtifactStore(prepared.store)
    source = next(item for item in registry.list_artifacts() if item.title == "Brand direction")
    target = next(item for item in registry.list_artifacts() if item.title == "Launch brief")
    presentation = composer == "presentation"
    page.goto(
        f"{prepared.url}/?deck={DECK_ID}#workflow"
        if presentation
        else f"{prepared.url}/?artifact={target.id}"
    )
    text = page.locator("#feedback-text" if presentation else "#hub-feedback-text")
    text.fill("Compare @Brand direction")
    expect(
        page.get_by_role("listbox", name="Workspace references").get_by_role("option")
    ).to_have_count(1)
    text.press("Tab")
    pill = page.locator("form .is-path-reference")
    expect(pill).to_have_count(1)
    _ = registry.revise_artifact(
        source.id,
        [ArtifactFileInput(path="index.html", text="<h1>NEW SOURCE AFTER ATTACH</h1>")],
        source.content_hash,
        "change-after-attaching-reference",
    )
    preview = page.locator("#chat-saved-reference-preview")
    pill.hover()
    expect(preview).to_be_visible()
    frame = preview.frame_locator("iframe")
    expect(frame.locator("h1")).to_have_text("A little more human.")
    expect(frame.locator("body")).not_to_contain_text("NEW SOURCE")
    # A keyboard focus while the pointer remains on the open pill must not
    # reload its already-rendered browsing context.
    original_frame = preview.locator("iframe").element_handle().content_frame()
    assert original_frame is not None
    pill.focus()
    page.evaluate("() => new Promise(resolve => setTimeout(resolve, 0))")
    assert not original_frame.is_detached()
    expect(preview).to_contain_text("Saved artifact")
    marker = preview.locator("iframe").evaluate("node => node.dataset.reused = 'yes'")
    assert marker == "yes"
    if presentation:
        text.hover()
    else:
        # Opaque frame crossings can omit boundary events in both documents.
        # Exercise repeated returns during closing and after a complete fold.
        # Locator.hover waits for the fold and scrolls its moving target into view;
        # that scroll dismisses previews. Move directly between visible points.
        frame_box = page.locator("#hub-html-preview").bounding_box()
        reference_box = pill.locator(".reference-symbol").bounding_box()
        assert frame_box is not None and reference_box is not None
        for pause in (0, 350, 0, 350):
            page.mouse.move(
                frame_box["x"] + frame_box["width"] / 2,
                frame_box["y"] + frame_box["height"] / 2,
            )
            expect(pill).to_have_attribute("aria-expanded", "false")
            page.wait_for_timeout(pause)
            page.mouse.move(
                reference_box["x"] + reference_box["width"] / 2,
                reference_box["y"] + reference_box["height"] / 2,
            )
            expect(pill).to_have_attribute("aria-expanded", "true")
        text.hover()
    expect(preview).to_be_hidden()
    pill.hover()
    expect(preview).to_be_visible()
    expect(preview.locator("iframe")).to_have_attribute("data-reused", "yes")
    text.hover()
    pill.focus()
    expect(preview).to_be_visible()
    page.keyboard.press("Escape")
    expect(preview).to_be_hidden()
    expect(text).to_be_visible()
    expect(pill).to_be_focused()
    pill.get_by_role("button").click()
    expect(preview).to_be_hidden()
    expect(preview.locator("iframe")).to_have_count(0)
    text.fill("Compare @Brand direction")
    expect(
        page.get_by_role("listbox", name="Workspace references").get_by_role("option")
    ).to_have_count(1)
    text.press("Enter")
    text.press_sequentially("with this draft.")
    text.press("Enter")
    sent = page.locator(
        "#feedback-list .is-path-reference" if presentation else "#hub-thread .is-path-reference"
    )
    expect(sent).to_have_count(1)
    downloads: list[str] = []
    page.on(
        "request",
        lambda request: (
            downloads.append(request.url)
            if "/feedback/" in request.url and "/files/" in request.url
            else None
        ),
    )
    sent.hover()
    expect(preview).to_be_visible()
    expect(preview.frame_locator("iframe").locator("h1")).to_have_text("NEW SOURCE AFTER ATTACH")
    assert len(downloads) == 1
    if presentation:
        text.hover()
        sent.hover()
    else:
        # Keep the pointer on the visible reference bar while it folds. Hovering
        # the whole moving pill can scroll it and dismiss the preview.
        text_box = text.bounding_box()
        sent_box = sent.locator(".reference-symbol").bounding_box()
        assert text_box is not None and sent_box is not None
        page.mouse.move(
            text_box["x"] + text_box["width"] / 2, text_box["y"] + text_box["height"] / 2
        )
        expect(sent).to_have_attribute("aria-expanded", "false")
        page.mouse.move(
            sent_box["x"] + sent_box["width"] / 2, sent_box["y"] + sent_box["height"] / 2
        )
    expect(preview).to_be_visible()
    assert len(downloads) == 1
    page.reload()
    expect(page.locator("#chat-saved-reference-preview")).to_have_count(0)


@pytest.mark.parametrize("favorite", [False, True])
def test_slide_pill_uses_saved_html_and_project_pill_has_saved_summary(
    page: Page, prepared: RunningApp, favorite: bool
) -> None:
    page.goto(f"{prepared.url}/?deck={DECK_ID}#workflow")
    text = page.locator("#feedback-text")
    if favorite:
        page.get_by_role("button", name="Reference favorite slides", exact=True).click()
        picker = page.get_by_role("dialog", name="Favorite slides", exact=True)
        picker.locator("[data-favorite-pick]").first.click()
        picker.get_by_role("button", name="Add references", exact=True).click()
    else:
        text.fill("@three-step")
        expect(page.get_by_role("option")).to_have_count(1)
        text.press("Tab")
    pill = page.locator("form .is-path-reference")
    pill.hover()
    preview = page.locator("#chat-saved-reference-preview")
    expect(preview).to_be_visible()
    expect(preview.frame_locator("iframe").locator(".slide")).to_be_visible()
    expect(preview.frame_locator("iframe").locator("body")).to_contain_text("Brief")
    assert (
        preview.frame_locator("iframe")
        .locator(".slide")
        .evaluate(
            "el=>{const box=el.getBoundingClientRect();"
            "return box.right<=innerWidth&&box.bottom<=innerHeight;}"
        )
    )
    expect(
        pill.locator(".chat-reference-bar iframe,.chat-reference-bar img,.chat-reference-bar small")
    ).to_have_count(0)
    text.hover()
    expect(preview).to_be_hidden()
    text.fill("@Fieldnotes")
    options = page.get_by_role("listbox", name="Workspace references")
    options.get_by_role("option", name="/Fieldnotes", exact=True).click()
    page.locator("form .is-path-reference").last.hover()
    expect(preview).to_contain_text("Saved project brief")
    expect(preview).to_contain_text("Brand direction")
    expect(preview.locator("iframe")).to_have_count(0)


@pytest.mark.parametrize("kind", ["presentation", "slide", "favorite"])
def test_saved_slide_canvas_fits_and_survives_source_change(
    page: Page, prepared: RunningApp, kind: str
) -> None:
    original = prepared.store.locate(prepared.store.read(DECK_ID), "cover")
    _ = prepared.store.revise(
        DECK_ID,
        "cover",
        digest(original.model_dump_json()),
        "saved-edge-marker",
        SlidePatch(
            html='<article class="slide"><h1>Saved first slide</h1>'
            '<span id="saved-edge" style="position:absolute;right:0;bottom:0">EDGE</span>'
            "</article>"
        ),
    )
    _ = Favorites(prepared.store).set(
        StarInput(kind="slide", artifact_id=DECK_ID, slide_id="cover", starred=True)
    )
    page.goto(f"{prepared.url}/?deck={DECK_ID}#workflow")
    text = page.locator("#feedback-text")
    if kind == "favorite":
        page.get_by_role("button", name="Reference favorite slides", exact=True).click()
        picker = page.get_by_role("dialog", name="Favorite slides", exact=True)
        picker.locator(f'[data-favorite-pick="{DECK_ID}/cover"]').click()
        picker.get_by_role("button", name="Add references", exact=True).click()
    else:
        entry = next(
            entry
            for entry in ChatReferences(prepared.store).catalog()
            if entry.kind == kind
            and entry.artifact_id == DECK_ID
            and (kind == "presentation" or entry.slide_id == "cover")
        )
        text.fill("@" + entry.title)
        page.get_by_role("option", name=entry.path, exact=True).click()
    expect(page.locator("form .is-path-reference")).to_have_count(1)
    selected = prepared.store.locate(prepared.store.read(DECK_ID), "cover")
    _ = prepared.store.revise(
        DECK_ID,
        "cover",
        digest(selected.model_dump_json()),
        "changed-edge-marker",
        SlidePatch(html='<article class="slide">NEW SOURCE AFTER ATTACH</article>'),
    )
    page.locator("form .is-path-reference").hover()
    preview = page.locator("#chat-saved-reference-preview")
    expect(preview).to_be_visible()
    frame = preview.frame_locator("iframe")
    expect(frame.locator("h1")).to_have_text("Saved first slide")
    expect(frame.locator("#saved-edge")).to_be_visible()
    assert frame.locator("#saved-edge").evaluate(
        "el=>{const b=el.getBoundingClientRect();"
        "return b.right===1600&&b.bottom===900&&innerWidth===1600&&innerHeight===900;}"
    )
    expect(frame.locator("body")).not_to_contain_text("NEW SOURCE AFTER ATTACH")


def test_delayed_artifact_pointer_event_does_not_close_returned_pill(
    page: Page, prepared: RunningApp
) -> None:
    """A late message from the iframe cannot undo a newer pointer return."""
    target = next(
        item
        for item in ArtifactStore(prepared.store).list_artifacts()
        if item.title == "Launch brief"
    )
    page.goto(f"{prepared.url}/?artifact={target.id}")
    text = page.locator("#hub-feedback-text")
    text.fill("@Brand direction")
    expect(
        page.get_by_role("listbox", name="Workspace references").get_by_role("option")
    ).to_have_count(1)
    text.press("Tab")
    pill = page.locator("form .is-path-reference")
    page.evaluate(
        "document.addEventListener('studio:reference-preview', "
        "e => {window.lastEpoch=e.detail.request_id})"
    )
    pill.hover()
    expect(pill).to_have_attribute("aria-expanded", "true")
    old_epoch = page.evaluate("window.lastEpoch")
    target_box = page.locator("#hub-html-preview").bounding_box()
    bar_box = pill.locator(".chat-reference-bar").bounding_box()
    assert target_box and bar_box
    page.evaluate("""document.addEventListener('studio:preview-pointer-enter', e => {
        if (window.delayedEpoch === undefined) {
            window.delayedEpoch = e.detail; e.stopImmediatePropagation();
        }
    }, true)""")
    page.mouse.move(
        target_box["x"] + target_box["width"] / 2, target_box["y"] + target_box["height"] / 2
    )
    page.wait_for_function("window.delayedEpoch !== undefined")
    page.mouse.move(bar_box["x"] + bar_box["width"] / 2, bar_box["y"] + bar_box["height"] / 2)
    assert page.evaluate("window.lastEpoch") > old_epoch
    page.evaluate(
        "document.dispatchEvent(new CustomEvent('studio:preview-pointer-enter', "
        "{detail:window.delayedEpoch}))"
    )
    expect(pill).to_have_attribute("aria-expanded", "true")
    expect(
        page.locator("#chat-saved-reference-preview").frame_locator("iframe").locator("h1")
    ).to_have_text("A little more human.")


def test_open_saved_reference_survives_live_slide_refresh(page: Page, prepared: RunningApp) -> None:
    """An incoming slide revision must not close an unchanged attachment preview."""
    page.goto(f"{prepared.url}/?deck={DECK_ID}#workflow")
    text = page.locator("#feedback-text")
    text.fill("@three-step")
    expect(page.get_by_role("option")).to_have_count(1)
    text.press("Tab")
    pill = page.locator("form .is-path-reference")
    pill.hover()
    preview = page.locator("#chat-saved-reference-preview")
    expect(preview).to_be_visible()
    frame = preview.locator("iframe").element_handle().content_frame()
    assert frame is not None
    expect(preview.frame_locator("iframe").locator(".slide")).to_be_visible()
    current = prepared.store.locate(prepared.store.read(DECK_ID), "workflow")
    _ = prepared.store.revise(
        DECK_ID,
        "workflow",
        digest(current.model_dump_json()),
        "incoming-preview-refresh",
        SlidePatch(html='<article class="slide"><h1>Incoming revision</h1></article>'),
    )
    expect(page.frame_locator("#preview").locator("h1")).to_have_text("Incoming revision")
    assert not frame.is_detached()
    expect(preview).to_be_visible()
    expect(preview.frame_locator("iframe").locator(".slide")).to_be_visible()


def test_static_artifact_reconstruction_preserves_root_styles_and_blocks_external_requests(
    page: Page, prepared: RunningApp, tmp_path: Path
) -> None:
    registry = ArtifactStore(prepared.store)
    project = registry.create_project("Fictional static security")
    source = tmp_path / "static-source"
    source.mkdir()
    _ = (source / "index.html").write_text(
        '<html class="saved-root" style="--saved-color:rgb(12, 34, 56)"><head>'
        '<link rel="stylesheet" href="style.css"></head><body data-saved="yes">'
        '<h1>Saved static source</h1><img src="https://preview.invalid/leak.png">'
        '<script>fetch("https://preview.invalid/script")</script></body></html>'
    )
    _ = (source / "style.css").write_text(
        'h1{color:var(--saved-color)}/* </style><meta http-equiv="refresh" '
        'content="0;url=https://preview.invalid/css-refresh"> */'
    )
    _ = registry.register_artifact(project.id, "Static preview safety", source, "index.html")
    external: list[str] = []
    page.on(
        "request",
        lambda request: external.append(request.url) if "preview.invalid" in request.url else None,
    )
    page.goto(f"{prepared.url}/?deck={DECK_ID}#workflow")
    text = page.locator("#feedback-text")
    text.fill("@Static preview safety")
    expect(page.get_by_role("option")).to_have_count(1)
    text.press("Tab")
    page.locator("form .is-path-reference").hover()
    preview = page.locator("#chat-saved-reference-preview")
    expect(preview).to_be_visible()
    frame = preview.frame_locator("iframe")
    expect(frame.locator("h1")).to_have_css("color", "rgb(12, 34, 56)")
    expect(frame.locator("html")).to_have_class("saved-root")
    expect(frame.locator("body")).to_have_attribute("data-saved", "yes")
    expect(frame.locator("meta[http-equiv=refresh],script")).to_have_count(0)
    page.wait_for_timeout(300)
    assert not external


def test_saved_download_failure_retries_and_late_response_cannot_reopen_preview(
    browser: Browser, prepared: RunningApp
) -> None:
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, color_scheme="dark")
    page = context.new_page()
    try:
        page.goto(f"{prepared.url}/?deck={DECK_ID}#workflow")
        text = page.locator("#feedback-text")
        text.fill("@Brand direction")
        expect(page.get_by_role("option")).to_have_count(1)
        text.press("Tab")
        expect(page.locator("form .is-path-reference")).to_have_count(1)
        text.press("Enter")
        sent = page.locator("#feedback-list .is-path-reference")
        expect(sent).to_have_count(1)
        url = sent.get_attribute("href")
        assert url is not None
        contents = Path(prepared.store.comments(DECK_ID)[-1].files[0].path).read_bytes()
        attempts = 0
        pending: list[Route] = []
        hold = False

        def attachment(route: Route) -> None:
            nonlocal attempts
            attempts += 1
            if hold:
                pending.append(route)
            elif attempts == 1:
                route.fulfill(status=503, body="Temporary fictional outage")
            else:
                route.fulfill(status=200, content_type="application/json", body=contents)

        page.route("**" + url, attachment)
        sent.hover()
        preview = page.locator("#chat-saved-reference-preview")
        expect(preview).to_contain_text("Saved reference preview unavailable")
        text.hover()
        sent.hover()
        expect(preview.frame_locator("iframe").locator("h1")).to_have_text("A little more human.")
        assert attempts == 2
        text.hover()
        sent.hover()
        expect(preview).to_be_visible()
        assert attempts == 2
        sent.focus()
        page.keyboard.press("Escape")
        expect(preview).to_be_hidden()
        expect(text).to_be_visible()
        expect(sent).to_be_focused()
        hold = True
        page.reload()
        sent.hover()
        page.wait_for_timeout(250)
        assert len(pending) == 1
        text.hover()
        pending.pop().fulfill(status=200, content_type="application/json", body=contents)
        page.wait_for_timeout(200)
        expect(page.locator("#chat-saved-reference-preview")).to_be_hidden()
        expect(page.locator("#chat-saved-reference-preview iframe")).to_have_count(0)
    finally:
        context.close()


def test_text_svg_reference_has_saved_image_thumbnail(
    page: Page, prepared: RunningApp, tmp_path: Path
) -> None:
    registry = ArtifactStore(prepared.store)
    project = registry.create_project("Fictional artwork")
    source = tmp_path / "vector-source"
    source.mkdir()
    _ = (source / "diagram.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
        '<rect width="200" height="100" fill="#315fce"/></svg>'
    )
    _ = registry.register_artifact(project.id, "Saved vector", source, "diagram.svg")
    page.goto(f"{prepared.url}/?deck={DECK_ID}#workflow")
    text = page.locator("#feedback-text")
    text.fill("@Saved vector")
    expect(page.get_by_role("option")).to_have_count(1)
    text.press("Tab")
    page.locator("form .is-path-reference").hover()
    preview = page.locator("#chat-saved-reference-preview")
    expect(preview).to_contain_text("Saved image")
    image = preview.frame_locator("iframe").locator("img")
    expect(image).to_be_visible()
    expect(image).to_have_js_property("naturalWidth", 200)


def test_saved_json_preview_handles_null_arrays_and_malformed_file_metadata(
    page: Page, prepared: RunningApp
) -> None:
    page.goto(f"{prepared.url}/?deck={DECK_ID}#workflow")
    text = page.locator("#feedback-text")
    text.fill("@Brand direction")
    expect(page.get_by_role("option")).to_have_count(1)
    text.press("Tab")
    expect(page.locator("form .is-path-reference")).to_have_count(1)
    text.press("Enter")
    sent = page.locator("#feedback-list .is-path-reference")
    expect(sent).to_have_count(1)
    url = sent.get_attribute("href")
    assert url is not None
    body = "null"
    page.route(
        "**" + url,
        lambda route: route.fulfill(status=200, content_type="application/json", body=body),
    )
    for document in [
        "null",
        "[]",
        '{"format":"artifact-studio-reference/v1","files":"invalid","context":123}',
    ]:
        body = document
        page.reload()
        sent.hover()
        preview = page.locator("#chat-saved-reference-preview")
        expect(preview).to_contain_text("Saved source available to download.")
        expect(preview.locator("iframe")).to_have_count(0)


@pytest.mark.parametrize("reduced_motion", ["no-preference", "reduce"])
def test_unfold_keeps_composer_fixed_and_pointer_inside_saved_slide(
    browser: Browser, prepared: RunningApp, reduced_motion: Literal["no-preference", "reduce"]
) -> None:
    context = browser.new_context(
        viewport={"width": 1440, "height": 1000}, reduced_motion=reduced_motion
    )
    page = context.new_page()
    try:
        page.goto(f"{prepared.url}/?deck={DECK_ID}#workflow")
        text = page.locator("#feedback-text")
        text.fill("Compare @three-step")
        expect(page.get_by_role("option")).to_have_count(1)
        text.press("Tab")
        pill = page.locator("form .is-path-reference")
        title = pill.locator(".reference-symbol")
        title_before = title.bounding_box()
        text_before = text.bounding_box()
        send_before = page.locator("#send-feedback").bounding_box()
        pill.hover()
        expect(pill).to_have_attribute("aria-expanded", "true")
        page.wait_for_timeout(350)
        assert text.bounding_box() == text_before
        assert page.locator("#send-feedback").bounding_box() == send_before
        assert title.bounding_box() == title_before
        opened = pill.bounding_box()
        assert opened is not None and opened["height"] > 100
        page.mouse.move(opened["x"] + 30, opened["y"] + 30)
        page.wait_for_timeout(150)
        expect(pill).to_have_attribute("aria-expanded", "true")
        text.hover()
        expect(pill).to_have_attribute("aria-expanded", "false")
        # Reverse a closing transition using the title's original position.
        assert title_before is not None
        page.mouse.move(title_before["x"] + 3, title_before["y"] + 3)
        expect(pill).to_have_attribute("aria-expanded", "true")
        page.wait_for_timeout(350)
        assert title.bounding_box() == title_before
        pill.focus()
        page.keyboard.press("Escape")
        expect(pill).to_have_attribute("aria-expanded", "false")
        expect(pill).to_be_focused()
        page.mouse.move(title_before["x"] + 5, title_before["y"] + 5)
        page.wait_for_timeout(150)
        expect(pill).to_have_attribute("aria-expanded", "false")
        page.wait_for_timeout(350)
        assert not pill.evaluate("node => node.matches(':popover-open')")
        expect(pill.locator("iframe")).to_have_count(0)
        expect(text).to_have_value("Compare ")
    finally:
        context.close()


@pytest.mark.parametrize("width", [320, 390])
def test_touch_pill_toggles_without_shifting_and_sent_reference_still_downloads(
    browser: Browser, prepared: RunningApp, width: int
) -> None:
    context = browser.new_context(
        viewport={"width": width, "height": 844}, has_touch=True, is_mobile=True
    )
    page = context.new_page()
    try:
        target = next(
            item
            for item in ArtifactStore(prepared.store).list_artifacts()
            if item.title == "Launch brief"
        )
        page.goto(f"{prepared.url}/?artifact={target.id}")
        text = page.locator("#hub-feedback-text")
        text.fill("@three-step")
        page.get_by_role("listbox", name="Workspace references").get_by_role("option").tap()
        pill = page.locator("form .is-path-reference")
        pill.scroll_into_view_if_needed()
        text_before = text.bounding_box()
        pill.tap()
        expect(pill).to_have_attribute("aria-expanded", "true")
        page.wait_for_timeout(350)
        assert text.bounding_box() == text_before
        opened = pill.bounding_box()
        assert opened is not None
        assert opened["x"] >= 0 and opened["x"] + opened["width"] <= width
        assert opened["y"] >= 0 and opened["y"] + opened["height"] <= 844
        pill.locator(".chat-reference-bar").tap()
        expect(pill).to_have_attribute("aria-expanded", "false")
        text.fill("Use this saved slide.")
        page.locator("#hub-send").tap()
        sent = page.locator("#hub-thread .is-path-reference")
        expect(sent).to_have_count(1)
        sent.tap()
        expect(sent).to_have_attribute("aria-expanded", "true")
        with page.expect_download() as saved:
            sent.locator(".chat-reference-bar").tap()
        assert "reference" in saved.value.suggested_filename
    finally:
        context.close()


@pytest.mark.parametrize("switch_reference", [False, True])
def test_new_preview_ignores_a_delayed_pointer_notification_from_previous_opening(
    page: Page, prepared: RunningApp, switch_reference: bool
) -> None:
    target = next(
        item
        for item in ArtifactStore(prepared.store).list_artifacts()
        if item.title == "Launch brief"
    )
    page.goto(f"{prepared.url}/?artifact={target.id}")
    authored = page.frame_locator("#hub-html-preview").locator("body")
    _ = authored.evaluate(
        "() => { window.observedReferenceRequest = null; "
        "addEventListener('message', event => {"
        "if(event.source === parent && event.data?.type === 'artifact:reference-preview' "
        "&& event.data.active) window.observedReferenceRequest = event.data.request_id;"
        "}); }"
    )
    text = page.locator("#hub-feedback-text")
    text.fill("@Brand direction")
    expect(page.get_by_role("listbox").get_by_role("option")).to_have_count(1)
    text.press("Tab")
    if switch_reference:
        text.fill("@Research notes")
        expect(page.get_by_role("listbox").get_by_role("option")).to_have_count(1)
        text.press("Tab")
    pill = page.locator("form .is-path-reference").first
    pill.hover()
    expect(pill).to_have_attribute("aria-expanded", "true")
    frame_node = page.locator("#hub-html-preview").element_handle()
    assert frame_node is not None
    frame_window = frame_node.content_frame()
    assert frame_window is not None
    _ = frame_window.wait_for_function("Number.isSafeInteger(window.observedReferenceRequest)")
    previous_request = authored.evaluate("() => window.observedReferenceRequest")
    assert isinstance(previous_request, int)
    if switch_reference:
        pill = page.locator("form .is-path-reference").last
    else:
        page.locator("#hub-html-preview").hover()
        expect(pill).to_have_attribute("aria-expanded", "false")
    pill.locator(".chat-reference-bar").hover()
    expect(pill).to_have_attribute("aria-expanded", "true")
    # Deliver a delayed notification from the real, source-validated frame after
    # the user has reopened the pill. It belongs to the earlier opening.
    _ = page.evaluate(
        "() => {window.deliveredReferenceRequest = null; addEventListener('message', event => {"
        "if(event.source === document.getElementById('hub-html-preview').contentWindow "
        "&& event.data?.type === 'artifact:pointer-enter') "
        "window.deliveredReferenceRequest = event.data.request_id;});}"
    )
    _ = authored.evaluate(
        "(_element, {origin, request}) => "
        "parent.postMessage({type:'artifact:pointer-enter', request_id:request}, origin)",
        {"origin": prepared.url, "request": previous_request},
    )
    _ = page.wait_for_function(
        "request => window.deliveredReferenceRequest === request", arg=previous_request
    )
    expect(pill).to_have_attribute("aria-expanded", "true")
    text.hover()
    expect(pill).to_have_attribute("aria-expanded", "false")
