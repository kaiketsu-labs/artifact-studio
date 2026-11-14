"""PowerPoint-style commands stay provisional, scoped and safe beside native typing."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.models import ElementTarget
from tests.integration.support import DECK_ID, PRIVATE_NOTE, RunningApp
from tests.integration.test_browser import open_editor
from tests.integration.voice_support import install_voice, voice_end, voice_result

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_slide_delete_move_undo_redo_stay_local(page: Page, live_app: RunningApp) -> None:
    open_editor(page, live_app)
    heading = page.frame_locator("#preview").get_by_role("heading", level=1)
    heading.click()
    original = heading.bounding_box()
    assert original is not None
    heading.press("ArrowRight")
    page.wait_for_function(
        "document.querySelector('#preview').contentDocument.querySelector('h1').style.transform.includes('translate')"
    )
    moved = heading.bounding_box()
    assert moved is not None and moved["x"] > original["x"]
    page.keyboard.press("ControlOrMeta+z")
    expect(heading).not_to_have_attribute("style", "transform")
    restored = heading.bounding_box()
    assert restored is not None and restored["x"] == pytest.approx(original["x"])
    page.keyboard.press("ControlOrMeta+Shift+z")
    page.keyboard.press("Delete")
    expect(heading).to_be_hidden()
    page.keyboard.press("ControlOrMeta+z")
    expect(heading).to_be_visible()
    undone_delete = heading.bounding_box()
    assert undone_delete is not None and undone_delete["x"] == pytest.approx(moved["x"])
    page.keyboard.press("ControlOrMeta+y")
    expect(heading).to_be_hidden()
    page.keyboard.press("Escape")
    expect(heading).to_be_visible()
    assert live_app.store.comments(DECK_ID) == []
    assert live_app.store.read(DECK_ID).slides[0].notes == PRIVATE_NOTE


def test_slide_menu_and_native_text_undo(page: Page, live_app: RunningApp, artifacts: Path) -> None:
    open_editor(page, live_app)
    heading = page.frame_locator("#preview").get_by_role("heading", level=1)
    heading.click(button="right")
    menu = page.get_by_role("menu", name="Object actions")
    expect(menu).to_be_visible()
    _ = page.screenshot(path=str(artifacts / "slide-context-menu.png"))
    page.get_by_role("menuitem", name="Edit text", exact=True).click()
    text = page.get_by_role("textbox", name="Text", exact=True)
    text.fill("Review together ")
    text.press("End")
    text.press_sequentially("typed")
    text.press("ControlOrMeta+z")
    expect(text).to_have_value("Review together ")
    text.fill("Review together")
    text.fill("A new heading")
    heading.click(button="right")
    page.get_by_role("menuitem", name="Undo", exact=True).click()
    expect(heading).to_have_text("Review together")
    page.keyboard.press("ControlOrMeta+y")
    expect(heading).to_have_text("A new heading")
    page.keyboard.press("Escape")
    assert live_app.store.comments(DECK_ID) == []
    assert len(live_app.store.read(DECK_ID).slides) == 2


def test_slide_delete_menu_send_preserves_original_target(page: Page, live_app: RunningApp) -> None:
    open_editor(page, live_app)
    heading = page.frame_locator("#preview").get_by_role("heading", level=1)
    heading.click()
    heading.press("Delete")
    expect(heading).to_be_hidden()
    page.locator("#element-properties").click(button="right", position={"x": 3, "y": 3})
    page.get_by_role("menuitem", name="Send edit", exact=True).click()
    expect(page.locator("#feedback-list")).to_contain_text("Delete this element.")
    comments = live_app.store.comments(DECK_ID)
    assert len(comments) == 1 and comments[0].edit_intent is not None
    assert comments[0].edit_intent.action == "delete"
    target = comments[0].target
    assert isinstance(target, ElementTarget) and "visibility" not in target.html
    assert comments[0].released
    assert [event.event_id for event in live_app.store.feedback_feed(DECK_ID).events] == [
        comments[0].event_id
    ]


@pytest.mark.parametrize("delay", ["before", "after"])
def test_slide_delete_waits_for_normal_delivery_mode(
    page: Page, live_app: RunningApp, delay: str
) -> None:
    """A delayed legacy mode change must finish before a delete edit is delivered."""
    _ = live_app.store.set_mode(DECK_ID, "batch")
    page.add_init_script(
        """(() => {
          const original = window.fetch;
          const held = new Promise(resolve => { window.releaseFixtureMode = resolve; });
          window.fetch = async (...args) => {
            if (!String(args[0]).includes('/feedback-mode')) return original(...args);
            if ('DELAY_POINT' === 'before') await held;
            const response = await original(...args);
            if ('DELAY_POINT' === 'after') await held;
            return response;
          };
        })()""".replace("DELAY_POINT", delay)
    )
    open_editor(page, live_app)
    feedback_requests: list[str] = []
    page.on(
        "request",
        lambda request: (
            feedback_requests.append(request.url)
            if request.method == "POST" and request.url.endswith("/feedback")
            else None
        ),
    )
    heading = page.frame_locator("#preview").get_by_role("heading", level=1)
    heading.click()
    heading.press("Delete")
    expect(heading).to_be_hidden()
    page.locator("#element-properties").click(button="right", position={"x": 3, "y": 3})
    page.get_by_role("menuitem", name="Send edit", exact=True).click()
    assert feedback_requests == []
    assert live_app.store.comments(DECK_ID) == []
    page.evaluate("window.releaseFixtureMode()")
    expect(page.locator("#feedback-list")).to_contain_text("Delete this element.")
    comments = live_app.store.comments(DECK_ID)
    assert len(comments) == 1 and comments[0].edit_intent is not None
    assert comments[0].edit_intent.action == "delete"
    target = comments[0].target
    assert isinstance(target, ElementTarget) and "visibility" not in target.html
    assert comments[0].released
    assert [event.event_id for event in live_app.store.feedback_feed(DECK_ID).events] == [
        comments[0].event_id
    ]


def open_artifact(
    page: Page, live_app: RunningApp, tmp_path: Path
) -> tuple[ArtifactStore, str, Path]:
    store = ArtifactStore(live_app.store)
    project = store.create_project("Keyboard fixture", "")
    folder = tmp_path / "page"
    folder.mkdir()
    source = folder / "index.html"
    _ = source.write_text(
        '<body style="margin:0;min-height:600px">'
        '<h1 id="title" style="position:absolute;left:30px;top:30px;width:180px">'
        'Original heading</h1><p id="body" style="position:absolute;top:200px">'
        "Original body</p></body>"
    )
    item = store.register_artifact(project.id, "Keyboard page", folder, "index.html")
    _ = live_app.post(f"/api/artifacts/{item.id}/mode", {"mode": "batch"})
    page.goto(f"{live_app.url}/?artifact={item.id}", wait_until="networkidle")
    page.get_by_role("button", name="Select", exact=True).click()
    return store, item.id, source


def test_artifact_delete_undo_redo_and_explicit_send(
    page: Page, live_app: RunningApp, tmp_path: Path
) -> None:
    store, artifact_id, source = open_artifact(page, live_app, tmp_path)
    original = source.read_text()
    heading = page.frame_locator("#hub-html-preview").locator("#title")
    heading.click(button="right")
    page.get_by_role("menuitem", name="Delete element", exact=True).click()
    expect(heading).to_be_hidden()
    page.locator("#hub-html-preview").focus()
    page.keyboard.press("ControlOrMeta+z")
    expect(heading).to_be_visible()
    page.keyboard.press("ControlOrMeta+Shift+z")
    expect(heading).to_be_hidden()
    assert store.feedback(artifact_id, include_pending=True) == []
    page.locator("#hub-element-properties").click(button="right", position={"x": 3, "y": 3})
    page.get_by_role("menuitem", name="Send edit", exact=True).click()
    expect(page.locator("#hub-thread")).to_contain_text("Delete this element.")
    feedback = store.feedback(artifact_id, include_pending=True)
    assert len(feedback) == 1 and feedback[0].edit_intent is not None
    assert feedback[0].edit_intent.action == "delete" and feedback[0].released
    assert source.read_text() == original
    assert [event.event_id for event in store.feedback_feed(artifact_id).events] == [
        feedback[0].event_id
    ]


def test_artifact_nudge_history_and_forged_send_are_local(
    page: Page, live_app: RunningApp, tmp_path: Path
) -> None:
    store, artifact_id, source = open_artifact(page, live_app, tmp_path)
    original = source.read_text()
    frame = page.frame_locator("#hub-html-preview")
    heading = frame.locator("#title")
    heading.click()
    expect(page.locator("#hub-element-properties")).to_be_visible()
    before = heading.bounding_box()
    assert before is not None
    page.keyboard.press("Shift+ArrowDown")
    expect(heading).to_have_css("transform", "matrix(1, 0, 0, 1, 0, 10)")
    page.keyboard.press("ControlOrMeta+z")
    expect(heading).to_have_css("transform", "none")
    page.keyboard.press("ControlOrMeta+y")
    expect(heading).to_have_css("transform", "matrix(1, 0, 0, 1, 0, 10)")
    page.keyboard.press("ControlOrMeta+z")
    expect(heading).to_have_css("transform", "none")
    page.keyboard.press("ArrowDown")
    expect(heading).to_have_css("transform", "matrix(1, 0, 0, 1, 0, 1)")
    page.keyboard.press("Escape")
    page.locator("#hub-viewer").evaluate(
        "el => {for(let i=0;i<3;i++)el.dispatchEvent(new KeyboardEvent('keydown',"
        "{key:'ArrowDown',shiftKey:true,bubbles:true}))}"
    )
    expect(heading).to_have_css("transform", "matrix(1, 0, 0, 1, 0, 30)")
    heading.evaluate(
        "el => parent.postMessage({type:'artifact:element-key',selector:'#title',"
        "key:'Enter',ctrlKey:true},'*')"
    )
    heading.evaluate(
        "el => parent.postMessage({type:'artifact:contextmenu',selector:'#title',"
        "x:Infinity,y:0,viewport:{width:1000,height:600}},'*')"
    )
    expect(page.get_by_role("menu", name="Element actions")).not_to_be_visible()
    page.keyboard.press("Escape")
    expect(heading).to_have_css("transform", "none")
    assert store.feedback(artifact_id, include_pending=True) == []
    assert source.read_text() == original


@pytest.mark.parametrize("surface", ["slide", "artifact"])
def test_enter_on_toolbar_buttons_stays_native(
    page: Page, live_app: RunningApp, tmp_path: Path, surface: str
) -> None:
    if surface == "slide":
        open_editor(page, live_app)
        heading = page.frame_locator("#preview").get_by_role("heading", level=1)
        panel = page.locator("#element-properties")
        original = "Review together"
    else:
        _ = open_artifact(page, live_app, tmp_path)
        heading = page.frame_locator("#hub-html-preview").locator("#title")
        panel = page.locator("#hub-element-properties")
        original = "Original heading"
    heading.click()
    expect(panel).to_be_visible()
    page.keyboard.press("F2")
    text = panel.get_by_role("textbox", name="Text", exact=True)
    expect(text).to_be_visible()
    text.fill("A provisional title")
    button = panel.get_by_role("button", name="Cancel edit", exact=True)
    button.focus()
    button.press("Enter")
    expect(heading).to_have_text(original)


def test_artifact_group_context_menu_preserves_selection(
    page: Page, live_app: RunningApp, tmp_path: Path
) -> None:
    store, artifact_id, _ = open_artifact(page, live_app, tmp_path)
    frame = page.frame_locator("#hub-html-preview")
    frame.locator("#title").click()
    frame.locator("#body").click(modifiers=["Shift"])
    expect(page.locator("#hub-targets")).to_contain_text("2 selected")
    frame.locator("#title").click(button="right")
    expect(page.get_by_role("menu", name="Element actions")).to_be_visible()
    expect(page.locator("#hub-targets")).to_contain_text("2 selected")
    page.keyboard.press("Escape")
    page.locator("#hub-html-preview").focus()
    page.keyboard.press("Shift+F10")
    expect(page.get_by_role("menu", name="Element actions")).to_be_visible()
    page.get_by_role("menuitem", name="Clear selection", exact=True).click()
    expect(page.locator("#hub-targets")).not_to_contain_text("2 selected")
    assert store.feedback(artifact_id, include_pending=True) == []


@pytest.mark.parametrize("surface", ["slide", "artifact"])
def test_delete_does_not_discard_an_active_recording(
    page: Page, live_app: RunningApp, tmp_path: Path, surface: str
) -> None:
    install_voice(page)
    if surface == "slide":
        open_editor(page, live_app)
        heading = page.frame_locator("#preview").get_by_role("heading", level=1)
        frame_id, input_id, stop_id = "preview", "feedback-text", "stop-feedback-voice"
    else:
        _ = open_artifact(page, live_app, tmp_path)
        heading = page.frame_locator("#hub-html-preview").locator("#title")
        frame_id, input_id, stop_id = "hub-html-preview", "hub-feedback-text", "hub-stop-voice"
    heading.click()
    mic = page.locator(f".voice-input-button[data-voice-for='{input_id}']")
    mic.click()
    page.locator(f"#{frame_id}").focus()
    page.keyboard.press("Delete")
    expect(heading).to_be_visible()
    expect(mic).to_have_attribute("aria-pressed", "true")
    voice_result(page, "The recording still belongs to this element.")
    page.locator(f"#{stop_id}").click()
    voice_end(page)
    expect(mic).to_have_attribute("aria-pressed", "false")


def test_slide_geometry_undo_preserves_live_child_nodes(page: Page, live_app: RunningApp) -> None:
    before = live_app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    source = str(before["html"]).replace(
        "Review together", '<span id="live-child">Review together</span>'
    )
    _ = live_app.call(
        "revise_slide",
        deck_id=DECK_ID,
        slide_id="cover",
        expected_hash=before["expected_hash"],
        request_id="child-identity-fixture",
        patch={"html": source},
    )
    open_editor(page, live_app)
    heading = page.frame_locator("#preview").get_by_role("heading", level=1)
    page.evaluate(
        "() => {const el=document.querySelector('#preview').contentDocument.querySelector('h1');"
        "window.originalChild=el.firstElementChild;window.childClicks=0;"
        "el.firstElementChild.addEventListener('click',()=>window.childClicks++);}"
    )
    bounds = heading.bounding_box()
    assert bounds is not None
    heading.click(position={"x": bounds["width"] - 3, "y": 5})
    page.evaluate("window.childClicks=0")
    heading.press("ArrowRight")
    expect(heading).not_to_have_css("transform", "none")
    page.keyboard.press("ControlOrMeta+z")
    page.keyboard.press("ControlOrMeta+y")
    assert (
        page.evaluate(
            "document.querySelector('#preview').contentDocument.querySelector('h1').firstElementChild"
            "===window.originalChild"
        )
        is True
    )
    heading.locator("#live-child").evaluate(
        "el => el.dispatchEvent(new MouseEvent('click',{bubbles:true}))"
    )
    assert page.evaluate("window.childClicks") == 1


def test_forged_frame_clear_cannot_discard_feedback_targets(
    page: Page, live_app: RunningApp, tmp_path: Path
) -> None:
    store, artifact_id, _ = open_artifact(page, live_app, tmp_path)
    frame = page.frame_locator("#hub-html-preview")
    frame.locator("#title").click()
    page.locator("#hub-feedback-text").fill("Keep this feedback context")
    panel = page.locator("#hub-element-properties")
    panel.get_by_role("button", name="Edit text", exact=True).click()
    panel.get_by_role("textbox", name="Text", exact=True).fill("Keep this unsent edit")
    frame.locator("#title").evaluate(
        "el => {parent.postMessage({type:'artifact:clear-selection'},'*');"
        "parent.postMessage({type:'artifact:element-key',selector:'#title',key:'Escape'},'*');"
        "parent.postMessage({type:'artifact:element-key',selector:'#title',key:'z',ctrlKey:true},'*');}"
    )
    expect(page.locator("#hub-targets")).to_contain_text("Original heading")
    expect(panel.get_by_role("textbox", name="Text", exact=True)).to_have_value(
        "Keep this unsent edit"
    )
    expect(page.locator("#hub-feedback-text")).to_have_value("Keep this feedback context")
    assert store.feedback(artifact_id, include_pending=True) == []


def test_mac_command_enter_sends_the_selected_edit_instead_of_presenting(
    page: Page, live_app: RunningApp
) -> None:
    page.add_init_script("Object.defineProperty(navigator,'platform',{value:'MacIntel'});")
    open_editor(page, live_app)
    heading = page.frame_locator("#preview").get_by_role("heading", level=1)
    heading.click()
    heading.press("ArrowRight")
    heading.press("Meta+Enter")
    expect(page.locator("#feedback-list")).to_contain_text("Move to")
    comments = live_app.store.comments(DECK_ID)
    assert len(comments) == 1 and comments[0].edit_intent is not None
    assert comments[0].edit_intent.action == "move"
    assert page.url.endswith("#cover")


def test_artifact_movement_request_has_exact_canvas_context(
    page: Page, live_app: RunningApp, tmp_path: Path
) -> None:
    store, artifact_id, source = open_artifact(page, live_app, tmp_path)
    original = source.read_text()
    original_hash = store.get_artifact(artifact_id).content_hash
    heading = page.frame_locator("#hub-html-preview").locator("#title")
    heading.click()
    expect(page.locator("#hub-element-properties")).to_be_visible()
    page.keyboard.press("Shift+ArrowDown")
    expect(heading).to_have_css("transform", "matrix(1, 0, 0, 1, 0, 10)")
    position = heading.evaluate(
        "el => ({x:el.getBoundingClientRect().left,y:el.getBoundingClientRect().top})"
    )
    page.keyboard.press("ControlOrMeta+Enter")
    expect(page.locator("#hub-thread")).to_contain_text("Move this element.")
    feedback = store.feedback(artifact_id, include_pending=True)
    assert len(feedback) == 1 and feedback[0].edit_intent is not None
    values = feedback[0].edit_intent.values
    assert values["canvas_reference"] == "artifact_document"
    if "x" in values:
        assert float(values["x"]) * float(values["canvas_width"]) == pytest.approx(
            position["x"], abs=0.02
        )
    else:
        assert position["x"] == pytest.approx(30)
    assert float(values["y"]) * float(values["canvas_height"]) == pytest.approx(
        position["y"], abs=0.02
    )
    assert feedback[0].released and feedback[0].content_hash == original_hash
    feed = store.feedback_feed(artifact_id)
    assert [event.event_id for event in feed.events] == [feedback[0].event_id]
    assert feed.events[0].edit_intent is not None and feed.events[0].edit_intent.action == "move"
    assert source.read_text() == original
