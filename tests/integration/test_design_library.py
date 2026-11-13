"""Exercise the dedicated Designs overview, file context and recoverable removal."""

import base64
import io
import os
from pathlib import Path
from zipfile import ZipFile

import pytest
from PIL import Image
from playwright.sync_api import Browser, Page, expect

from artifact_studio.design_library import DesignCreate, DesignLibrary
from artifact_studio.design_references import ReferenceInput
from artifact_studio.models import FileInput
from tests.design_factory import make_design, powerpoint_source
from tests.integration.support import DECK_ID, RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_design_transfer_controls_export_and_import_a_reference_collection(
    browser: Browser, live_app: RunningApp, tmp_path: Path
) -> None:
    """A keyboard user can export a file and import a new design through the native library."""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    try:
        _design_transfer_journey(context.new_page(), live_app, tmp_path)
    finally:
        context.close()


def _design_transfer_journey(page: Page, live_app: RunningApp, tmp_path: Path) -> None:
    """Verify a rejected duplicate and successful retry through the same dialog."""
    library = DesignLibrary(live_app.store)
    item = library.create(DesignCreate(name="Studio Blue"))
    page.goto(live_app.url + "/?view=designs")
    export = page.get_by_role("link", name="Export Studio Blue design", exact=True)
    export.focus()
    with page.expect_download() as transfer:
        export.press("Enter")
    download = tmp_path / transfer.value.suggested_filename
    transfer.value.save_as(download)
    new = tmp_path / "transferred.design.zip"
    with ZipFile(new, "w") as archive:
        archive.writestr("design-context.json", '{"id":"transferred","name":"Transferred"}')
        archive.writestr("reference/palette.csv", "color\nblue")
    with ZipFile(download) as original:
        assert "Studio Blue" in original.read("design-context.json").decode()
    upload = page.get_by_role("button", name="Import design", exact=True)
    upload.focus()
    upload.press("Enter")
    dialog = page.get_by_role("dialog", name="Import design")
    dialog.get_by_label("Design ZIP").set_input_files(download)
    dialog.get_by_role("button", name="Import design", exact=True).click()
    expect(dialog.get_by_role("alert")).to_have_text("This design is already in this workspace.")
    dialog.get_by_label("Design ZIP").set_input_files(new)
    dialog.get_by_role("button", name="Import design", exact=True).click()
    expect(page.get_by_role("heading", name="Transferred", exact=True)).to_be_visible()
    assert library.get(item.id).name == "Studio Blue"


def test_default_design_failure_keeps_keyboard_focus_and_can_retry(
    browser: Browser, live_app: RunningApp
) -> None:
    """A failed save preserves the current choice and a focused keyboard retry."""
    make_design(live_app.store.root / "2026-11-06-blue-design", "blue-brand")
    context = browser.new_context()
    try:
        page = context.new_page()
        page.route(
            "**/api/design-library/blue-brand/default",
            lambda route: route.fulfill(status=503, json={"detail": "Please retry"}),
            times=1,
        )
        page.goto(live_app.url + "/?view=designs")
        choose = page.get_by_role("button", name="Set blue-brand as default", exact=True)
        choose.focus()
        choose.press("Enter")
        expect(page.locator("#hub-toast")).to_have_text("Please retry")
        expect(choose).to_be_focused()
        expect(choose).to_be_enabled()
        assert live_app.store.default_design_id() == "test-brand"
        choose.press("Enter")
        selected = page.get_by_role("button", name="blue-brand is the default design", exact=True)
        expect(selected).to_be_focused()
        assert live_app.store.default_design_id() == "blue-brand"
    finally:
        context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_default_design_can_be_changed_from_cards_and_detail(
    page: Page, live_app: RunningApp, artifacts: Path, width: int
) -> None:
    """Choose a default with mouse or keyboard and keep feedback visible across reloads."""
    make_design(live_app.store.root / "2026-11-06-blue-design", "blue-brand")
    page.set_viewport_size({"width": width, "height": 1000 if width > 700 else 844})
    page.goto(live_app.url + "/?view=designs")
    choose = page.get_by_role("button", name="Set blue-brand as default", exact=True)
    choose.click()
    selected = page.get_by_role("button", name="blue-brand is the default design", exact=True)
    expect(selected).to_have_attribute("aria-pressed", "true")
    expect(selected).to_be_focused()
    assert live_app.store.default_design_id() == "blue-brand"
    page.reload()
    expect(selected).to_be_visible()
    page.get_by_role("button", name="Open test-brand design", exact=True).click()
    choose = page.get_by_role("button", name="Set test-brand as default", exact=True)
    choose.focus()
    choose.press("Enter")
    expect(
        page.get_by_role("button", name="test-brand is the default design", exact=True)
    ).to_be_focused()
    assert live_app.store.default_design_id() == "test-brand"
    page.locator("#appearance-button").click()
    page.locator('[data-appearance="dark"]').click()
    _ = page.screenshot(path=str(artifacts / "default-design-dark.png"))
    assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth")


@pytest.mark.parametrize("width", [1440, 390])
def test_text_source_editor_saves_cancels_searches_and_undoes(
    page: Page, live_app: RunningApp, artifacts: Path, width: int
) -> None:
    """Edit actual source through the keyboard and compact, themed code-editor controls."""
    library = DesignLibrary(live_app.store)
    markdown = "# Shared language\n\n- Clear headings\n- Short sentences\n"
    item = library.create(
        DesignCreate(
            name="Editable reference",
            files=[
                FileInput(name="guide.md", data_base64=base64.b64encode(markdown.encode()).decode())
            ],
        )
    )
    page.set_viewport_size({"width": width, "height": 1000 if width > 700 else 844})
    page.goto(live_app.url + f"/?view=designs&design={item.id}")
    page.get_by_role("button", name="Open guide.md", exact=True).click()
    dialog = page.get_by_role("dialog", name="guide.md", exact=True)
    dialog.get_by_role("button", name="Edit", exact=True).click()
    editor = dialog.get_by_role("textbox", name="Edit guide.md", exact=True)
    expect(editor).to_be_visible()
    expect(dialog.locator(".cm-lineNumbers")).to_be_visible()
    expect(dialog.locator("[data-code-language]")).to_have_text("Markdown")
    save = dialog.get_by_role("button", name="Save", exact=True)
    expect(save).to_be_disabled()
    page.keyboard.press("ControlOrMeta+End")
    page.keyboard.insert_text("\n## Next\n\nMake room for the message.\n")
    expect(save).to_be_enabled()
    page.keyboard.press("ControlOrMeta+z")
    expect(save).to_be_disabled()
    page.keyboard.press("ControlOrMeta+Shift+z")
    expect(save).to_be_enabled()
    dialog.get_by_role("button", name="Find in file", exact=True).click()
    search = dialog.locator('.cm-search input[name="search"]')
    expect(search).to_be_visible()
    search.press_sequentially("message")
    expect(dialog.locator(".cm-searchMatch")).to_have_count(1)
    page.keyboard.press("Escape")
    expect(dialog.locator(".cm-search")).to_have_count(0)
    expect(dialog).to_be_visible()
    _ = page.screenshot(path=str(artifacts / "source-editor-light.png"))
    editor.click()
    page.keyboard.press("ControlOrMeta+s")
    expect(dialog.get_by_role("heading", name="Next", exact=True)).to_be_visible()
    assert "Make room for the message." in Path(item.files[0].local_path).read_text()
    saved = library.get(item.id)
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    page.locator("#appearance-button").click()
    page.locator('[data-appearance="dark"]').click()
    page.get_by_role("button", name="Open guide.md", exact=True).click()
    dialog.get_by_role("button", name="Edit", exact=True).click()
    expect(editor).to_be_visible()
    _ = page.screenshot(path=str(artifacts / "source-editor-dark.png"))
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.insert_text("An unsaved edit")
    page.once("dialog", lambda prompt: prompt.dismiss())
    page.keyboard.press("Escape")
    expect(editor).to_have_text("An unsaved edit")
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    expect(dialog.get_by_role("heading", name="Next", exact=True)).to_be_visible()
    assert library.get(item.id).version == saved.version
    assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
    page.keyboard.press("Escape")


def test_text_source_conflict_keeps_the_draft_and_can_open_latest(
    browser: Browser, live_app: RunningApp
) -> None:
    """Preserve both writers' work and require an explicit discard before reloading."""
    library = DesignLibrary(live_app.store)
    item = library.create(
        DesignCreate(
            name="Concurrent sources",
            files=[
                FileInput(
                    name="theme.css",
                    data_base64=base64.b64encode(b".slide { color: blue; }\n").decode(),
                )
            ],
        )
    )
    context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
    page = context.new_page()
    errors: list[str] = []
    statuses: list[int] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "response",
        lambda response: statuses.append(response.status) if response.status >= 400 else None,
    )
    try:
        page.goto(live_app.url + f"/?view=designs&design={item.id}")
        page.get_by_role("button", name="Open theme.css", exact=True).click()
        dialog = page.get_by_role("dialog", name="theme.css", exact=True)
        dialog.get_by_role("button", name="Edit", exact=True).click()
        editor = dialog.get_by_role("textbox", name="Edit theme.css", exact=True)
        expect(editor).to_be_visible()
        assert dialog.locator(".cm-line span").count() > 0
        page.keyboard.press("ControlOrMeta+a")
        page.keyboard.insert_text(".slide { color: red; }")
        source = Path(item.files[0].local_path)
        _ = source.write_text(".slide { color: green; }\n")
        dialog.get_by_role("button", name="Save", exact=True).click()
        expect(dialog.get_by_role("alert")).to_contain_text("changed elsewhere")
        expect(editor).to_have_text(".slide { color: red; }")
        assert source.read_text() == ".slide { color: green; }\n"
        dialog.get_by_role("button", name="Copy edits", exact=True).click()
        assert page.evaluate("navigator.clipboard.readText()") == ".slide { color: red; }"
        page.once("dialog", lambda prompt: prompt.accept())
        dialog.get_by_role("button", name="Open latest", exact=True).click()
        expect(dialog.locator("pre code")).to_have_text(".slide { color: green; }\n")
        assert not errors and statuses == [409]
    finally:
        context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_text_context_files_open_in_place(
    page: Page, live_app: RunningApp, artifacts: Path, tmp_path: Path, width: int
) -> None:
    """Preview text and formatted Markdown without executing source or losing downloads."""
    markdown = (
        "# A clear visual language\n\nUse **short headlines** and useful diagrams.\n\n"
        "- One idea at a time\n- Keep room around the message\n\n"
        '<img src="/must-not-load" onerror="window.previewExecuted=true">\n'
    )
    files = {
        "AGENT-GUIDE.md": markdown.encode(),
        "license.txt": "Fictional font license\nGrüße aus München.".encode("utf-16"),
        "theme.css": b".slide { color: #315bea; }",
        "presentation.js": b"window.previewExecuted = true;",
        "index.html": b"<script>window.previewExecuted=true</script><h1>Source only</h1>",
        "tokens.json": b'{"accent":"#315bea"}',
        "palette.csv": b"name,color\naccent,#315bea\n",
        "settings.yaml": b"spacing: generous\n",
        "NOTICE": b"Extensionless references also open here.",
    }
    library = DesignLibrary(live_app.store)
    uploads = [
        FileInput(name=name, data_base64=base64.b64encode(data).decode())
        for name, data in files.items()
    ]
    item = library.create(DesignCreate(name="Text reference design", files=uploads[:5]))
    item = library.attach(item.id, ReferenceInput(files=uploads[5:]))
    page.set_viewport_size({"width": width, "height": 1000 if width > 700 else 844})
    page.goto(live_app.url + f"/?view=designs&design={item.id}")
    page.get_by_role("button", name="Open AGENT-GUIDE.md", exact=True).click()
    dialog = page.get_by_role("dialog", name="AGENT-GUIDE.md", exact=True)
    expect(dialog.get_by_role("heading", name="A clear visual language")).to_be_visible()
    expect(dialog.locator("strong")).to_have_text("short headlines")
    expect(dialog.locator("img")).to_have_count(0)
    dialog.get_by_role("button", name="Source", exact=True).click()
    expect(dialog.locator("pre code")).to_have_text(markdown)
    dialog.get_by_role("button", name="Preview", exact=True).click()
    expect(dialog.get_by_role("heading", name="A clear visual language")).to_be_visible()
    assert page.evaluate("window.previewExecuted") is None
    with page.expect_download() as download:
        dialog.get_by_role("link", name="Download original").click()
    original = tmp_path / "guide.md"
    download.value.save_as(original)
    assert original.read_bytes() == markdown.encode()
    _ = page.screenshot(path=str(artifacts / "markdown-preview-light.png"))
    page.keyboard.press("Escape")
    expect(page.get_by_role("button", name="Open AGENT-GUIDE.md", exact=True)).to_be_focused()
    page.locator("#appearance-button").click()
    page.locator('[data-appearance="dark"]').click()
    page.get_by_role("button", name="Preview AGENT-GUIDE.md", exact=True).focus()
    page.keyboard.press("Enter")
    expect(dialog.get_by_role("heading", name="A clear visual language")).to_be_visible()
    _ = page.screenshot(path=str(artifacts / "markdown-preview-dark.png"))
    page.keyboard.press("Escape")
    for name, data in files.items():
        if name.endswith(".md"):
            continue
        page.get_by_role("button", name=f"Open {name}", exact=True).click()
        file_dialog = page.get_by_role("dialog", name=name, exact=True)
        expected = data.decode("utf-16") if name == "license.txt" else data.decode()
        expect(file_dialog.locator("pre code")).to_have_text(expected)
        assert page.evaluate("window.previewExecuted") is None
        assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        bounds = file_dialog.bounding_box()
        assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
        if name == "theme.css":
            _ = page.screenshot(path=str(artifacts / "source-preview-dark.png"))
        page.keyboard.press("Escape")
    assert library.get(item.id).version == item.version


def test_text_preview_limits_and_binary_fallback(page: Page, live_app: RunningApp) -> None:
    """Keep large reads bounded and offer original downloads when text decoding fails."""
    library = DesignLibrary(live_app.store)
    item = library.create(
        DesignCreate(
            name="Preview bounds",
            files=[
                FileInput(
                    name="large.txt", data_base64=base64.b64encode(b"A" * 1_100_000).decode()
                ),
                FileInput(
                    name="unknown.bin", data_base64=base64.b64encode(b"\x00\xffbinary").decode()
                ),
            ],
        )
    )
    page.goto(live_app.url + f"/?view=designs&design={item.id}")
    page.get_by_role("button", name="Open large.txt", exact=True).click()
    dialog = page.get_by_role("dialog", name="large.txt", exact=True)
    expect(dialog.locator(".design-text-limit")).to_be_visible()
    expect(dialog.get_by_role("button", name="Edit", exact=True)).to_be_disabled()
    assert len(dialog.locator("pre code").inner_text()) == 1024 * 1024
    page.keyboard.press("Escape")
    page.get_by_role("button", name="Open unknown.bin", exact=True).click()
    fallback = page.get_by_role("dialog", name="unknown.bin", exact=True)
    expect(fallback.get_by_role("alert")).to_contain_text("not readable text")
    expect(fallback.get_by_role("button", name="Edit", exact=True)).to_be_disabled()
    expect(fallback.get_by_role("link", name="Download original")).to_be_visible()
    page.keyboard.press("Escape")


@pytest.mark.parametrize("width", [1440, 390])
def test_design_image_and_powerpoint_thumbnails(
    page: Page, live_app: RunningApp, artifacts: Path, width: int
) -> None:
    """Show real previews, retain file icons on missing covers and expand without downloading."""
    raster = io.BytesIO()
    Image.new("RGB", (960, 540), (35, 80, 130)).save(raster, format="PNG")
    picture = raster.getvalue()
    files = {
        "moodboard.png": picture,
        "brand-master.pptx": powerpoint_source(thumbnail=picture),
        "without-cover.pptx": powerpoint_source(),
        "guidelines.pdf": b"Fictional reference",
        "tokens.json": b'{"accent":"#426bec"}',
    }
    item = DesignLibrary(live_app.store).create(
        DesignCreate(
            name="Visual references",
            files=[
                FileInput(name=name, data_base64=base64.b64encode(data).decode())
                for name, data in files.items()
            ],
        )
    )
    page.set_viewport_size({"width": width, "height": 1000 if width > 700 else 844})
    _ = page.goto(live_app.url + f"/?view=designs&design={item.id}")
    for name in ("moodboard.png", "brand-master.pptx"):
        preview = page.get_by_role("button", name=f"Preview {name}", exact=True)
        expect(preview.locator("img")).to_have_js_property("naturalWidth", 640)
        preview.focus()
        page.keyboard.press("Enter")
        dialog = page.get_by_role("dialog", name=name, exact=True)
        expect(dialog.locator("img")).to_have_js_property("naturalWidth", 640)
        original = next(file for file in item.files if file.name == name)
        expect(dialog.get_by_role("link", name="Download original")).to_have_attribute(
            "href", original.url
        )
        page.keyboard.press("Escape")
        expect(dialog).not_to_be_visible()
    missing = page.get_by_role("button", name="without-cover.pptx", exact=True)
    expect(missing).to_be_disabled()
    expect(missing.locator("img")).to_have_count(0)
    expect(page.locator(".design-file-badge.is-pdf .ui-icon")).to_be_visible()
    expect(page.locator(".design-file-badge.is-data .ui-icon")).to_be_visible()
    assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
    _ = page.screenshot(path=str(artifacts / "design-file-previews.png"), full_page=True)
    page.locator("#appearance-button").click()
    page.locator('[data-appearance="dark"]').click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    _ = page.screenshot(path=str(artifacts / "design-file-previews-dark.png"), full_page=True)


@pytest.mark.parametrize("width", [1440, 390])
def test_design_text_file_tiles(
    page: Page, live_app: RunningApp, artifacts: Path, width: int
) -> None:
    """Keep distinct file tiles full width and open text by mouse and keyboard."""
    files = {
        "tokens.json": b'{"fictional":"#426bec"}',
        "theme.css": b".fictional { color: #426bec; }",
        "player.js": b"const fictional = true;",
        "example.html": b"<h1>Fictional example</h1>",
        "notes.md": b"# Fictional notes",
    }
    item = DesignLibrary(live_app.store).create(
        DesignCreate(
            name="Fictional design files",
            files=[
                FileInput(name=name, data_base64=base64.b64encode(data).decode())
                for name, data in files.items()
            ],
        )
    )
    page.set_viewport_size({"width": width, "height": 1000 if width > 700 else 844})
    _ = page.goto(live_app.url + f"/?view=designs&design={item.id}")
    for name, kind in (
        ("tokens.json", "data"),
        ("theme.css", "stylesheet"),
        ("player.js", "script"),
        ("example.html", "markup"),
        ("notes.md", "document"),
    ):
        visual = page.get_by_role("button", name=f"Preview {name}", exact=True)
        badge = visual.locator(f".design-file-badge.is-{kind}")
        expect(badge.locator(".ui-icon")).to_be_visible()
        expect(visual).to_have_attribute("title", f"Preview {name}")
        bounds = visual.bounding_box()
        badge_bounds = badge.bounding_box()
        assert bounds is not None and badge_bounds is not None
        assert badge_bounds["width"] >= bounds["width"] - 2
        visual.click()
        dialog = page.get_by_role("dialog", name=name, exact=True)
        expect(dialog.locator(".design-text-content")).to_contain_text(
            "fictional", ignore_case=True
        )
        page.keyboard.press("Escape")
        visual.focus()
        page.keyboard.press("Enter")
        expect(dialog).to_be_visible()
        page.keyboard.press("Escape")
    assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
    _ = page.screenshot(path=str(artifacts / "design-text-file-tiles.png"), full_page=True)
    page.locator("#appearance-button").click()
    page.locator('[data-appearance="dark"]').click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    _ = page.screenshot(path=str(artifacts / "design-text-file-tiles-dark.png"), full_page=True)


@pytest.mark.parametrize("width", [1440, 390])
def test_design_overview_add_context_files_delete_and_restore(
    page: Page, live_app: RunningApp, artifacts: Path, width: int
) -> None:
    page.set_viewport_size({"width": width, "height": 1000 if width > 700 else 844})
    _ = page.goto(live_app.url + "/?view=designs")
    expect(page.frame_locator(".design-card-art iframe").locator(".slide")).to_be_visible()
    page.get_by_role("button", name="Open test-brand design", exact=True).click()
    expect(page.get_by_role("heading", name="Context files")).to_be_visible()
    expect(page.locator(".design-file-group")).to_contain_text(
        ["Master.pptx", "README.md", "index.html"]
    )
    expect(page.get_by_role("link", name="Open example slides")).to_be_visible()
    assert "deck=" not in page.url
    page.get_by_role("navigation", name="Breadcrumb").get_by_role(
        "button", name="Designs", exact=True
    ).click()
    page.get_by_role("button", name="Add design", exact=True).click()
    form = page.get_by_role("dialog", name="Add design", exact=True)
    expect(form.get_by_role("button", name="Add design", exact=True)).to_be_disabled()
    expect(form.get_by_role("textbox", name="Description", exact=True)).not_to_be_visible()
    page.get_by_role("textbox", name="Name", exact=True).fill("Editorial system")
    form.locator("summary").click()
    page.get_by_role("textbox", name="Description", exact=False).fill(
        "Quiet typography, clear hierarchy and generous space."
    )
    page.locator("#design-library-input").set_input_files(
        [
            {
                "name": "brand-guide.pdf",
                "mimeType": "application/pdf",
                "buffer": b"Fictional brand guide",
            },
            {
                "name": "palette.csv",
                "mimeType": "text/csv",
                "buffer": b"ink,#142a43\naccent,#426bec",
            },
            {"name": "unused.txt", "mimeType": "text/plain", "buffer": b"Remove before saving"},
            {
                "name": "preview.svg",
                "mimeType": "image/svg+xml",
                "buffer": (
                    b'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200">'
                    b'<rect width="300" height="200" fill="#426bec"/></svg>'
                ),
            },
        ]
    )
    page.get_by_role("button", name="Remove unused.txt from selection").click()
    form.get_by_role("button", name="Add design", exact=True).click()
    expect(page.get_by_role("heading", name="Editorial system", exact=True)).to_be_visible()
    expect(page.locator(".design-file-group")).to_contain_text("brand-guide.pdf")
    expect(page.locator(".design-file-group")).not_to_contain_text("unused.txt")
    expect(page.get_by_role("link", name="Open example slides")).to_have_count(0)
    _ = page.screenshot(path=str(artifacts / "design-context.png"), full_page=True)
    _ = page.reload()
    expect(page.get_by_role("heading", name="Editorial system", exact=True)).to_be_visible()
    page.get_by_role("button", name="Add files", exact=True).click()
    expect(page.get_by_role("dialog").get_by_role("button", name="Add files")).to_be_disabled()
    dropped = page.evaluate_handle(
        """() => {
          const files = new DataTransfer();
          files.items.add(new File(['Fictional design source'], 'layout.fig'));
          return files;
        }"""
    )
    _ = page.locator("[data-design-drop]").evaluate(
        "(zone, files) => zone.dispatchEvent(new DragEvent('drop', "
        "{bubbles:true, dataTransfer:files}))",
        dropped,
    )
    dropped.dispose()
    page.get_by_role("button", name="Add files", exact=True).last.click()
    expect(page.locator(".design-file-group")).to_contain_text("layout.fig")
    page.get_by_role("button", name="Remove palette.csv from design context").click()
    expect(page.locator(".design-file-group")).not_to_contain_text("palette.csv")
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=live_app.url)
    page.get_by_role("button", name="Use with agent", exact=True).click()
    handoff = page.get_by_role("dialog", name="Use with agent", exact=True)
    message = handoff.get_by_role("textbox", name="Message for your agent").input_value()
    assert "Editorial system" in message and page.url in message
    handoff.get_by_role("button", name="Copy message", exact=True).click()
    expect(handoff).not_to_be_visible()
    assert page.evaluate("navigator.clipboard.readText()") == message
    page.get_by_role("navigation", name="Breadcrumb").get_by_role(
        "button", name="Designs", exact=True
    ).click()
    expect(page.get_by_role("button", name="Open Editorial system design")).to_be_visible()
    image = page.locator("[data-open-design]").filter(has_text="Editorial system").locator("img")
    expect(image).to_be_visible()
    expect(image).to_have_js_property("complete", True)
    assert image.evaluate("image => image.naturalWidth") == 300
    _ = page.screenshot(path=str(artifacts / "design-overview.png"), full_page=True)
    page.get_by_role("button", name="Delete Editorial system design", exact=True).click()
    page.get_by_role("button", name="Delete design", exact=True).click()
    expect(page.get_by_role("button", name="Open Editorial system design")).to_have_count(0)
    page.get_by_role("button", name="Deleted designs", exact=True).click()
    page.get_by_role("button", name="Restore", exact=True).click()
    expect(page.get_by_role("button", name="Open Editorial system design")).to_be_visible()
    assert live_app.store.read(DECK_ID).title == "CI workflow"


def test_add_files_later_and_manual_agent_message(page: Page, live_app: RunningApp) -> None:
    """An empty start is explicit, and clipboard denial preserves a usable handoff."""
    _ = page.goto(live_app.url + "/?view=designs")
    page.get_by_role("button", name="Add design", exact=True).click()
    form = page.get_by_role("dialog", name="Add design", exact=True)
    expect(form.get_by_text("Reference files · optional", exact=True)).to_be_visible()
    form.get_by_role("textbox", name="Name", exact=True).fill("   ")
    expect(form.get_by_role("button", name="Add design", exact=True)).to_be_disabled()
    form.get_by_role("textbox", name="Name", exact=True).fill("Early ideas")
    form.get_by_role("button", name="Add design", exact=True).click()
    expect(page.get_by_role("heading", name="Early ideas", exact=True)).to_be_visible()
    expect(page.get_by_text("No context files yet.", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Use with agent", exact=True)).to_be_disabled()
    page.get_by_role("navigation", name="Breadcrumb").get_by_role("button", name="Designs").click()
    page.get_by_role("button", name="Add design", exact=True).click()
    form.get_by_role("textbox", name="Name", exact=True).fill("Typography direction")
    form.locator("summary").click()
    form.get_by_role("textbox", name="Description", exact=True).fill(
        "Use generous whitespace and quiet typography."
    )
    form.get_by_role("button", name="Add design", exact=True).click()
    expect(page.get_by_role("heading", name="Typography direction", exact=True)).to_be_visible()
    _ = page.evaluate(
        """() => Object.defineProperty(navigator.clipboard, 'writeText', {
          value: async () => { throw new DOMException('Clipboard denied', 'NotAllowedError'); }
        })"""
    )
    page.get_by_role("button", name="Use with agent", exact=True).click()
    handoff = page.get_by_role("dialog", name="Use with agent", exact=True)
    handoff.get_by_role("button", name="Copy message", exact=True).click()
    expect(handoff.get_by_role("status")).to_contain_text("Select and copy the message above")
    field = handoff.get_by_role("textbox", name="Message for your agent")
    expect(field).to_be_focused()
    assert field.evaluate("field => field.selectionEnd - field.selectionStart") == len(
        field.input_value()
    )
