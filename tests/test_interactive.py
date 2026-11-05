"""Custom HTML remains unrestricted inside isolated, audience-only documents."""

import os
from pathlib import Path

import pytest

from artifact_studio.models import SlidePatch
from artifact_studio.projects import audience_html
from artifact_studio.storage import Store, digest
from artifact_studio.visuals import CaptureOptions, capture, snapshot
from tests.design_factory import TEMPLATE_ID, make_design

CUSTOM = """<style>
body{background:rgb(25,140,70);color:white;font:60px system-ui}button{font:inherit}
</style><h1 id="result">Interactive</h1>
<button onclick="document.getElementById('result').textContent='Clicked'">Try it</button>
<script>window.artifactStudioCapture=({elapsed_ms,final})=>{
document.getElementById('result').textContent=final?'Final state':String(elapsed_ms);
};</script>"""


def test_custom_content_roundtrip_versions_and_export(tmp_path: Path) -> None:
    """Anchor custom code to revisions and preserve it across portable audience exports."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "review.sqlite3")
    before = store.read(TEMPLATE_ID).slides[0]
    revised = store.revise(
        TEMPLATE_ID,
        before.id,
        digest(before.model_dump_json()),
        "interactive-change",
        SlidePatch(interactive_html=CUSTOM),
    )
    assert revised.revision == before.revision + 1
    assert store.content_hash(TEMPLATE_ID, before) != store.content_hash(TEMPLATE_ID, revised)
    assert store.read(TEMPLATE_ID).slides[0].interactive_html == CUSTOM
    exported = audience_html(store, TEMPLATE_ID)
    assert "artifactStudioCapture" in exported and "interactive_html" in exported
    assert "allow-scripts" in exported and "allow-same-origin" not in exported
    printed = audience_html(store, TEMPLATE_ID, printing=True)
    assert "interactive-data" in printed and "artifactStudioCapture" in printed
    prepared = snapshot(store, TEMPLATE_ID, revised, 1, CaptureOptions(elapsed_ms=500))
    assert "interactive-data" in prepared.document and "artifactStudioCapture" in prepared.document
    with pytest.raises(ValueError, match="stage 0"):
        snapshot(store, TEMPLATE_ID, revised, 1, CaptureOptions(stage=7))
    cleared = store.revise(
        TEMPLATE_ID,
        revised.id,
        digest(revised.model_dump_json()),
        "interactive-clear",
        SlidePatch(interactive_html=""),
    )
    assert not cleared.interactive_html


@pytest.mark.skipif(os.environ.get("ARTIFACT_STUDIO_RENDER_TESTS") != "1", reason="Opt-in Chromium")
def test_custom_capture_is_sandboxed_and_renders(tmp_path: Path) -> None:
    """Render actual custom scripts while blocking parent access and all outgoing requests."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "review.sqlite3")
    slide = store.read(TEMPLATE_ID).slides[0]
    content = (
        CUSTOM
        + """<script>
let parentBlocked=false;try{parent.document.body.innerHTML='ESCAPED'}catch{parentBlocked=true}
window.artifactStudioReady=Promise.resolve().then(()=>{if(!parentBlocked)throw Error('escaped');});
fetch('https://test.invalid/api/decks').catch(()=>{});
</script>"""
    )
    store.revise(
        TEMPLATE_ID,
        slide.id,
        digest(slide.model_dump_json()),
        "interactive-capture",
        SlidePatch(interactive_html=content, notes="PRIVATE NOTES"),
    )
    result = capture(
        store,
        TEMPLATE_ID,
        tmp_path / "captures",
        slide_id=slide.id,
        options=CaptureOptions(stage=0, elapsed_ms=500),
    )
    assert result.frames[0].diagnostics is not None
    from PIL import Image

    with Image.open(result.frames[0].path) as rendered:
        assert rendered.convert("RGB").getpixel((1500, 850)) == (25, 140, 70)


def test_prepare_custom_assets_and_preserve_static_fallback(tmp_path: Path) -> None:
    """Embed brand resources once and keep independent static edits after interactive updates."""
    from artifact_studio.interactive import prepare_document

    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "review.sqlite3")
    folder = store.folder(TEMPLATE_ID)
    (folder / "assets/control.js").write_text("window.controlLoaded=true;")
    (folder / "assets/control.css").write_text("body{background:url('custom.svg')}")
    document = prepare_document(
        store,
        TEMPLATE_ID,
        '<link rel="stylesheet" href="assets/control.css">'
        '<script src="assets/control.js"></script><img src="assets/custom.svg">',
    )
    assert "window.controlLoaded=true" in document
    assert "data:image/svg+xml;base64," in document
    assert 'src="assets/' not in document and 'href="assets/' not in document
    with pytest.raises(ValueError, match="local assets"):
        prepare_document(store, TEMPLATE_ID, '<img src="https://test.invalid/image.png">')
    before = store.read(TEMPLATE_ID).slides[0]
    legacy_hash = digest(
        before.model_dump_json(include={"title", "html", "sources"})
        + store.visual_hash(TEMPLATE_ID)
    )
    assert store.content_hash(TEMPLATE_ID, before) == legacy_hash
    revised = store.revise(
        TEMPLATE_ID,
        before.id,
        digest(before.model_dump_json()),
        "custom-assets",
        SlidePatch(interactive_html=document),
    )
    fallback = '<article class="slide"><h1>Updated fallback</h1></article>'
    revised = store.revise(
        TEMPLATE_ID,
        revised.id,
        digest(revised.model_dump_json()),
        "static-fallback",
        SlidePatch(html=fallback),
    )
    assert revised.interactive_html == document and revised.html == fallback


@pytest.mark.skipif(os.environ.get("ARTIFACT_STUDIO_RENDER_TESTS") != "1", reason="Opt-in Chromium")
def test_custom_interaction_network_boundaries_and_print_final(tmp_path: Path) -> None:
    """Exercise actual clicks, time hooks, final PDF state and blocked network/navigation."""
    from playwright.sync_api import Route, sync_playwright

    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "review.sqlite3")
    before = store.read(TEMPLATE_ID).slides[0]
    content = (
        CUSTOM
        + """<button id="navigate" onclick="attemptNavigation()">Navigate</button><script>
fetch('https://test.invalid/api/private').catch(()=>{});
const image=new Image();image.src='https://test.invalid/pixel';
window.attemptNavigation=()=>{location.href='https://test.invalid/navigate'};
</script>"""
    )
    revised = store.revise(
        TEMPLATE_ID,
        before.id,
        digest(before.model_dump_json()),
        "custom-boundaries",
        SlidePatch(interactive_html=content),
    )
    prepared = snapshot(store, TEMPLATE_ID, revised, 1, CaptureOptions(stage=0, elapsed_ms=375))
    with sync_playwright() as renderer:
        browser = renderer.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            context = browser.new_context(viewport={"width": 1600, "height": 900})
            page = context.new_page()
            page.set_default_timeout(5000)
            requests: list[str] = []

            def outgoing(route: Route) -> None:
                requests.append(route.request.url)
                route.abort()

            page.route("**/*", outgoing)
            page.set_content(prepared.document, wait_until="load")
            page.evaluate("() => window.ArtifactStudioCaptureReady")
            child = page.frames[-1]
            assert child.locator("#result").inner_text() == "375"
            child.get_by_role("button", name="Try it").click()
            assert child.locator("#result").inner_text() == "Clicked"
            assert (
                child.evaluate(
                    "() => {try{return parent.document.body.innerHTML}catch{return 'blocked'}}"
                )
                == "blocked"
            )
            child.locator("#navigate").click()
            page.wait_for_timeout(200)
            assert child.url in {"about:srcdoc", "chrome-error://chromewebdata/"}
            assert not [url for url in requests if url.startswith(("http:", "https:"))]
            page.goto("about:blank")
            page.set_content(audience_html(store, TEMPLATE_ID, printing=True))
            page.wait_for_function("() => document.querySelector('iframe') !== null")
            custom = page.frames[-1]
            custom.locator("#result").wait_for()
            assert custom.locator("#result").inner_text() == "Final state"
            assert len(page.pdf()) > 1000
            assert page.evaluate("""async()=>{
const host=document.createElement('div');document.body.append(host);
const mounted=window.ArtifactStudioInteractive.mount(host,
  '<script>window.artifactStudioReady=new Promise(()=>{})<'+'/script>');
const settled=mounted.ready.then(()=>false,()=>true);mounted.dispose();return await settled;
}""")
        finally:
            browser.close()


@pytest.mark.skipif(os.environ.get("ARTIFACT_STUDIO_RENDER_TESTS") != "1", reason="Opt-in Chromium")
def test_custom_slideshow_keys_state_mirroring_and_disposal(tmp_path: Path) -> None:
    """Mirror declared public state and retain slideshow control from a focused custom frame."""
    from playwright.sync_api import Route, sync_playwright

    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "review.sqlite3")
    before = store.read(TEMPLATE_ID).slides[0]
    content = """<style>body{font:60px system-ui;background:#194b99;color:white}</style>
<h1 id="state">0</h1><button id="increment">Add</button><input aria-label="Text">
<script>
let value=0;window.artifactStudioSetState=state=>{
value=state.value;document.getElementById('state').textContent=value;};
document.getElementById('increment').onclick=()=>{value++;
document.getElementById('state').textContent=value;window.artifactStudio.emitState({value});};
</script>"""
    store.revise(
        TEMPLATE_ID,
        before.id,
        digest(before.model_dump_json()),
        "custom-state",
        SlidePatch(interactive_html=content),
    )
    html = audience_html(store, TEMPLATE_ID)
    with sync_playwright() as renderer:
        browser = renderer.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            context = browser.new_context(viewport={"width": 1600, "height": 900})
            page = context.new_page()
            page.set_default_timeout(5000)

            def document(route: Route) -> None:
                route.fulfill(status=200, body=html, content_type="text/html")

            page.route("https://fiction.invalid/**", document)
            page.goto("https://fiction.invalid/export?channel=fictional-state-check")
            current = page.frame_locator("#stage > .runtime-slide-canvas > iframe").frame_locator(
                "iframe"
            )
            private = page.frame_locator(
                "#presenter-current > .runtime-slide-canvas > iframe"
            ).frame_locator("iframe")
            current.locator("#increment").click()
            private.locator("#state").get_by_text("1", exact=True).wait_for(state="attached")
            audience = page.context.new_page()
            audience.route("https://fiction.invalid/**", document)
            audience.goto("https://fiction.invalid/export?channel=fictional-state-check&audience=1")
            audience_view = audience.frame_locator(
                "#stage > .runtime-slide-canvas > iframe"
            ).frame_locator("iframe")
            audience_view.locator("#state").get_by_text("1", exact=True).wait_for()
            current.get_by_label("Text").fill("A")
            current.get_by_label("Text").press("x")
            assert page.locator("#single-counter").inner_text() == "1 / 2"
            # Navigation disposes the frame, so dispatch keys outside its element action.
            current.locator("#increment").focus()
            page.keyboard.press("ArrowRight")
            page.wait_for_function(
                "() => document.getElementById('single-counter').textContent==='2 / 2'"
            )
            assert page.locator("#stage > .runtime-slide-canvas > iframe").count() == 0
            page.keyboard.press("ArrowLeft")
            current.locator("#state").get_by_text("1", exact=True).wait_for()
            current.locator("#increment").focus()
            page.keyboard.press("Escape")
            page.wait_for_function("() => !document.body.classList.contains('single-screen')")
        finally:
            browser.close()
