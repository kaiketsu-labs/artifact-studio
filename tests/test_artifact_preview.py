"""Immutable preview dependencies, preserved programs and isolated request boundaries."""

import os
import subprocess
from html import escape
from pathlib import Path
from urllib.parse import urljoin

import pytest
from PIL import Image

from artifact_studio import artifacts as artifact_storage
from artifact_studio.artifact_capture import (
    CAPTURE_ORIGIN,
    CAPTURE_URL,
    capture_artifact_image,
    capture_resource,
    capture_resources,
)
from artifact_studio.artifact_models import ArtifactFileInput, ArtifactView
from artifact_studio.artifact_preview import (
    artifact_file_headers,
    artifact_files_url,
    preview_document,
    preview_headers,
)
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.storage import ConflictError, Store

ORIGIN = "http://127.0.0.1:8793"


@pytest.fixture
def artifacts(tmp_path: Path) -> ArtifactStore:
    """Keep preview sources and their immutable snapshots in an independent workspace."""
    return ArtifactStore(Store(tmp_path / "workspace", tmp_path / "state" / "review.sqlite3"))


def register(
    artifacts: ArtifactStore, tmp_path: Path, entrypoint: str, source: str | bytes
) -> ArtifactView:
    """Register a nested entrypoint and local module dependencies without a template."""
    folder = tmp_path / "bundle"
    folder.mkdir(exist_ok=True)
    entry = folder / entrypoint
    entry.parent.mkdir(parents=True, exist_ok=True)
    _ = entry.write_bytes(source.encode() if isinstance(source, str) else source)
    (folder / "assets").mkdir(exist_ok=True)
    _ = (folder / "assets/app.js").write_text("import './nested.js'; fetch('./data.json');")
    _ = (folder / "assets/nested.js").write_text("window.loaded = true;")
    _ = (folder / "assets/data.json").write_text('{"answer":42}')
    project = artifacts.create_project("Review project", "PRIVATE PROJECT CONTEXT")
    return artifacts.register_artifact(
        project.id, "Preview title", folder, entrypoint, context="PRIVATE ARTIFACT CONTEXT"
    )


def test_html_scripts_and_nested_dependencies_keep_the_original_revision(
    artifacts: ArtifactStore, tmp_path: Path
) -> None:
    """Preserve authored code while relative imports remain inside one exact snapshot."""
    source = (
        '<!doctype html><html lang="de"><head data-note=">">'
        '<base href="https://outside.invalid/">'
        '<script type="module" src="../assets/app.js"></script>'
        "<script>const template='<head>'; window.state = {ready: true};</script>"
        '</head><body><button onclick="state.ready=false">Run</button></body></html>'
    )
    artifact = register(artifacts, tmp_path, "pages/index.html", source)
    prepared = preview_document(artifacts, artifact.id, ORIGIN)
    files = artifact_files_url(artifact.id, 1, ORIGIN)
    assert prepared.startswith('<!doctype html><html lang="de"><head data-note=">">')
    assert f'<base href="{files}pages/">' in prepared
    assert prepared.index(f'<base href="{files}') < prepared.index("https://outside.invalid/")
    assert '<script type="module" src="../assets/app.js"></script>' in prepared
    assert "const template='<head>'; window.state = {ready: true};" in prepared
    assert 'onclick="state.ready=false"' in prepared
    assert "PRIVATE" not in prepared
    module = urljoin(files + "pages/", "../assets/app.js")
    assert module == files + "assets/app.js"
    assert urljoin(module, "./nested.js") == files + "assets/nested.js"
    updated = artifacts.revise_artifact(
        artifact.id,
        [ArtifactFileInput(path="pages/index.html", text="<h1>New revision</h1>")],
        artifact.content_hash,
        "preview-revision-002",
    )
    assert updated.revision == 2
    assert preview_document(artifacts, artifact.id, ORIGIN, revision=1) == prepared
    latest = preview_document(artifacts, artifact.id, ORIGIN)
    assert "/revisions/2/files/pages/" in latest and "<h1>New revision</h1>" in latest


@pytest.mark.parametrize(
    "source",
    [
        '<!-- <head> --><html class="custom"><body><h1>Content</h1></body></html>',
        "<script>const html='<head>'; document.body.dataset.ready='yes';</script><h1>Content</h1>",
        "<h1>Content</h1>",
    ],
)
def test_head_injection_ignores_comments_and_script_literals(
    artifacts: ArtifactStore, tmp_path: Path, source: str
) -> None:
    """Install the bridge before authored scripts without rewriting their strings."""
    artifact = register(artifacts, tmp_path, "index.html", source)
    prepared = preview_document(artifacts, artifact.id, ORIGIN)
    assert prepared.count('src="' + ORIGIN + '/web/artifact-bridge.js"') == 1
    assert prepared.index("/web/artifact-bridge.js") < prepared.index("<h1>Content</h1>")
    if "<script>" in source:
        assert prepared.index("/web/artifact-bridge.js") < prepared.index("<script>")
        assert "const html='<head>'" in prepared
    if 'class="custom"' in source:
        assert '<html class="custom"><head>' in prepared


def test_policy_scopes_fetch_modules_and_images_to_the_owned_revision() -> None:
    """Allow owned resources and inline programs without granting workspace-origin access."""
    headers = preview_headers("artifact-a", 4, ORIGIN)
    policy = headers["Content-Security-Policy"]
    directives = dict(part.strip().split(" ", 1) for part in policy.split(";"))
    files = ORIGIN + "/api/artifacts/artifact-a/revisions/4/files/"
    assert directives["sandbox"] == "allow-scripts allow-downloads"
    assert "allow-same-origin" not in policy and "'self'" not in policy
    assert directives["default-src"] == "'none'"
    assert directives["connect-src"] == files
    assert directives["base-uri"] == files
    assert directives["frame-src"] == directives["worker-src"] == "'none'"
    assert directives["object-src"] == directives["form-action"] == "'none'"
    assert directives["script-src"].split() == [
        "'unsafe-inline'",
        "'unsafe-eval'",
        files,
        ORIGIN + "/web/artifact-bridge.js",
        ORIGIN + "/web/vendor/marked-18.1.2.js",
        ORIGIN + "/web/marquee.js",
        ORIGIN + "/web/image-export.js",
    ]
    assert directives["img-src"] == files + " data: blob:"
    assert headers["Referrer-Policy"] == "no-referrer"
    file_headers = artifact_file_headers("artifact-a", 4, ORIGIN)
    assert file_headers["Access-Control-Allow-Origin"] == "null"
    assert file_headers["Cross-Origin-Resource-Policy"] == "cross-origin"
    assert file_headers["X-Content-Type-Options"] == "nosniff"
    assert "Access-Control-Allow-Credentials" not in file_headers
    assert file_headers["Content-Security-Policy"] == policy


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.com",
        "http://127.0.0.1:8793/anything",
        "http://127.0.0.1:8793?x=1",
        "http://user:password@localhost",
        "http://localhost/#fragment",
        "http://localhost:0",
    ],
)
def test_preview_origin_rejects_remote_or_injected_sources(origin: str) -> None:
    """Reject malformed response origins before interpolating CSP or HTML attributes."""
    with pytest.raises(ValueError):
        _ = preview_headers("artifact-a", 1, origin)


@pytest.mark.parametrize("extension", ["md", "txt"])
def test_markdown_and_text_sources_are_escaped(
    artifacts: ArtifactStore, tmp_path: Path, extension: str
) -> None:
    """Keep script-like source out of the generated wrapper's HTML parser."""
    source = '# Review\n<script>alert("unsafe")</script>\n<img src=x onerror="bad()">'
    artifact = register(artifacts, tmp_path, "notes/review." + extension, source)
    prepared = preview_document(artifacts, artifact.id, ORIGIN)
    assert escape(source) in prepared
    assert '<script>alert("unsafe")</script>' not in prepared
    assert 'onerror="bad()"' not in prepared
    assert ("/web/vendor/marked-18.1.2.js" in prepared) == (extension == "md")


def test_image_sources_use_revision_urls_and_binary_files_fail_clearly(
    artifacts: ArtifactStore, tmp_path: Path
) -> None:
    """Wrap images without decoding binary data or exposing another artifact directory."""
    artifact = register(artifacts, tmp_path, "images/picture.png", b"\x89PNG\r\n\x1a\n")
    prepared = preview_document(artifacts, artifact.id, ORIGIN)
    assert f'/revisions/1/files/images/picture.png" alt="{artifact.title}"' in prepared
    binary = register(artifacts, tmp_path, "unknown.bin", b"\xff\x00\x81")
    with pytest.raises(ValueError, match="no HTML, image, Markdown or UTF-8"):
        _ = preview_document(artifacts, binary.id, ORIGIN)


def test_capture_allowlist_pins_bytes_and_blocks_workspace_or_network_access(
    artifacts: ArtifactStore, tmp_path: Path
) -> None:
    """Resolve original dependency bytes without opening a server or honoring other URLs."""
    artifact = register(artifacts, tmp_path, "pages/index.html", "<h1>Original</h1>")
    resources = capture_resources(artifacts, artifact)
    files = artifact_files_url(artifact.id, artifact.revision, CAPTURE_ORIGIN)
    resource = capture_resource(resources, files + "assets/app.js?version=unchanged")
    assert resource is not None and resource.body == b"import './nested.js'; fetch('./data.json');"
    assert resource.headers["Access-Control-Allow-Origin"] == "null"
    document = capture_resource(resources, CAPTURE_URL)
    assert document is not None and b"<h1>Original</h1>" in document.body
    assert b"PRIVATE" not in document.body
    for url in (
        CAPTURE_ORIGIN + "/api/projects",
        CAPTURE_ORIGIN + "/web/app.js",
        CAPTURE_ORIGIN + "/api/artifacts/another/revisions/1/files/app.js",
        files + "../app.js",
        files + "%2e%2e/app.js",
        files + "assets/missing.js",
        files.replace("/revisions/1/", "/revisions/2/") + "assets/app.js",
        "https://example.com/api",
        "file:///etc/passwd",
        "ws://127.0.0.1:8799/api",
    ):
        assert capture_resource(resources, url) is None
    assert capture_resource(resources, files + "assets/app.js", "POST") is None
    assert capture_resource(resources, files + "assets/app.js", "HEAD") == resource
    _ = artifacts.revise_artifact(
        artifact.id,
        [ArtifactFileInput(path="assets/app.js", text="window.newRevision = true;")],
        artifact.content_hash,
        "capture-revision-002",
    )
    assert capture_resource(resources, files + "assets/app.js") == resource
    assert capture_resources(artifacts, artifact)[files + "assets/app.js"] == resource


def test_capture_prepares_one_verified_snapshot_without_reopening_preview_source(
    artifacts: ArtifactStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A capture verifies once and constructs HTML from the same bytes as its resource map."""
    source = (
        "\ufeff<!doctype html><html><head></head><body>\r\n"
        '<script>window.x="<head>";</script>\r\n</body></html>'
    )
    artifact = register(artifacts, tmp_path, "pages/index.html", source)
    expected = preview_document(artifacts, artifact.id, CAPTURE_ORIGIN, artifact.revision)
    original = artifact_storage.read_tree
    calls: list[Path] = []

    def read_once(folder: Path) -> dict[str, bytes]:
        files = original(folder)
        calls.append(folder)
        # After pinning, even an external rewrite cannot substitute unverified HTML.
        _ = (folder / artifact.entrypoint).write_text("<h1>Changed after pinning</h1>")
        return files

    def no_scan(_folder: Path) -> object:
        pytest.fail("A capture must verify its pinned bytes without rescanning the bundle.")

    monkeypatch.setattr(artifact_storage, "read_tree", read_once)
    monkeypatch.setattr(artifact_storage, "tree_inventory", no_scan)
    resources = capture_resources(artifacts, artifact)
    assert len(calls) == 1
    assert resources[CAPTURE_URL].body.decode() == expected
    url = artifact_files_url(artifact.id, artifact.revision, CAPTURE_ORIGIN) + artifact.entrypoint
    assert resources[url].body == source.encode()
    with pytest.raises(ConflictError, match="modified"):
        _ = capture_resources(artifacts, artifact)


def test_bridge_preserves_interaction_and_authenticates_parent_selection() -> None:
    """Exercise real bridge handlers without a browser or changing authored DOM."""
    bridge = Path(__file__).resolve().parents[1] / "web/artifact-bridge.js"
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const listeners = new Map(), messages = [], origin = 'http://127.0.0.1:8793';
class Element {
  constructor() { this.id = 'action'; this.localName = 'button'; this.parentElement = null; }
  getAttribute() { return null; }
}
class HTMLElement extends Element {
  constructor() { super(); this.innerText = '<img src=x onerror=bad()> Run action'; }
}
const button = new HTMLElement();
const parent = {postMessage(message, targetOrigin) { messages.push({message, targetOrigin}); }};
const document = {
  currentScript: {src: origin + '/web/artifact-bridge.js', dataset: {artifactFormat: 'html'}},
  readyState: 'loading', addEventListener() {}, querySelectorAll() { return [button]; }
};
const window = {MarqueeSelection: {create: () => ({cancel() {}})},
addEventListener(name, callback) {
  if (!listeners.has(name)) listeners.set(name, []);
  listeners.get(name).push(callback);
}};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
  document, window, parent, URL, Element, HTMLElement, CSS: {escape: value => value},
  innerWidth: 1280, innerHeight: 900, requestAnimationFrame() {}
});
function fire(name, values = {}) {
  const event = {target: button, prevented: false, stopped: false,
    preventDefault() { this.prevented = true; },
    stopImmediatePropagation() { this.stopped = true; }, ...values};
  for (const listener of listeners.get(name) || []) listener(event);
  return event;
}
assert.equal(fire('click').prevented, false);
assert.equal(messages.length, 0);
fire('message', {source: {}, origin, data: {type: 'artifact:select', enabled: true}});
assert.equal(fire('click').prevented, false);
fire('message', {source: parent, origin: 'https://outside.invalid',
  data: {type: 'artifact:select', enabled: true}});
assert.equal(fire('click').prevented, false);
fire('message', {source: parent, origin, data: {type: 'artifact:select', enabled: true}});
const selected = fire('click');
assert.equal(selected.prevented, true);
assert.equal(selected.stopped, true);
assert.equal(messages[0].targetOrigin, origin);
assert.equal(messages[0].message.type, 'artifact:target');
assert.equal(messages[0].message.target.selector, '#action');
assert.equal(messages[0].message.target.quote, button.innerText);
assert.equal(messages[0].message.target.viewport.width, 1280);
assert.equal(messages[0].message.additive, false);
for (const modifier of ['metaKey', 'ctrlKey', 'shiftKey']) {
  fire('click', {[modifier]: true});
  assert.equal(messages.at(-1).message.additive, true);
}
const previous = messages.length;
assert.equal(fire('pointerdown').prevented, true);
assert.equal(messages.length, previous);
fire('message', {source: parent, origin, data: {type: 'artifact:select', enabled: false}});
assert.equal(fire('click').prevented, false);
assert.equal(messages.length, previous);
"""
    _ = subprocess.run(
        ["node", "-e", script, str(bridge)], check=True, capture_output=True, text=True
    )


@pytest.mark.skipif(os.environ.get("ARTIFACT_STUDIO_RENDER_TESTS") != "1", reason="Opt-in Chromium")
def test_isolated_capture_executes_local_modules_and_blocks_outside_requests(
    artifacts: ArtifactStore, tmp_path: Path
) -> None:
    """Render imported JavaScript and owned data while workspace and network reads fail."""
    artifact = register(
        artifacts,
        tmp_path,
        "pages/index.html",
        "<style>body{margin:0;background:rgb(200,0,0)}</style>"
        '<script type="module" src="../assets/app.js"></script>',
    )
    source = """
import './nested.js';
const data = await (await fetch(new URL('./data.json', import.meta.url))).json();
let workspaceBlocked = false, networkBlocked = false, storageBlocked = false;
try { await fetch('/api/projects'); } catch { workspaceBlocked = true; }
try { await fetch('https://example.invalid/private'); } catch { networkBlocked = true; }
try { localStorage.setItem('private', 'test'); } catch { storageBlocked = true; }
if (window.loaded && data.answer === 42 && workspaceBlocked && networkBlocked && storageBlocked) {
  document.body.style.background = 'rgb(25,140,70)';
}
"""
    revised = artifacts.revise_artifact(
        artifact.id,
        [ArtifactFileInput(path="assets/app.js", text=source)],
        artifact.content_hash,
        "capture-render-002",
    )
    result = capture_artifact_image(
        artifacts, artifact.id, tmp_path / "captures", width=640, height=480, elapsed_ms=100
    )
    assert result.content_hash == revised.content_hash and result.revision == revised.revision
    assert result.width == 640 and result.height == 480 and not result.stale
    with Image.open(result.path) as image:
        assert image.convert("RGB").getpixel((320, 240)) == (25, 140, 70)
