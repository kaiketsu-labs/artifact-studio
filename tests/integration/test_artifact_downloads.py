"""Browser-managed downloads from isolated authored artifact previews."""

import os
from hashlib import sha256
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from artifact_studio.artifacts import ArtifactStore
from tests.integration.support import RunningApp

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_preview_downloads_preserve_contents_and_sources(
    live_app: RunningApp, page: Page, tmp_path: Path
) -> None:
    """Save generated HTML, JSON and CSV and an owned file without altering the bundle."""
    bundle = tmp_path / "fictional-bundle"
    bundle.mkdir()
    source = """<!doctype html><html><body>
<button onclick="save('report.html', '<h1>Fictional</h1>', 'text/html')">HTML</button>
<button onclick="save('report.json', '{&quot;value&quot;:42}', 'application/json')">JSON</button>
<button onclick="save('report.csv', 'name,value\\nexample,42\\n', 'text/csv')">CSV</button>
<a href="data.csv" download="local.csv">Local CSV</a>
<script>
function save(name, content, type) {
  const anchor = document.createElement('a');
  anchor.href = URL.createObjectURL(new Blob([content], {type}));
  anchor.download = name;
  anchor.click();
}
</script></body></html>"""
    (bundle / "index.html").write_text(source)
    (bundle / "data.csv").write_text("name,value\nlocal,7\n")
    original = {path.name: sha256(path.read_bytes()).hexdigest() for path in bundle.iterdir()}
    store = ArtifactStore(live_app.store)
    project = store.create_project("Fictional downloads", "")
    artifact = store.register_artifact(project.id, "Download fixture", bundle, "index.html")
    _ = page.goto(f"{live_app.url}/?artifact={artifact.id}")
    frame = page.frame_locator("#hub-html-preview")
    expect(frame.get_by_text("Local CSV")).to_be_visible()
    for label, filename, content in (
        ("HTML", "report.html", b"<h1>Fictional</h1>"),
        ("JSON", "report.json", b'{"value":42}'),
        ("CSV", "report.csv", b"name,value\nexample,42\n"),
        ("Local CSV", "data.csv", b"name,value\nlocal,7\n"),
    ):
        with page.expect_download() as event:
            frame.get_by_text(label, exact=True).click()
        download = event.value
        assert download.suggested_filename == filename
        path = download.path()
        assert path is not None and path.read_bytes() == content
    after = {path.name: sha256(path.read_bytes()).hexdigest() for path in bundle.iterdir()}
    assert original == after
    assert store.get_artifact(artifact.id).content_hash == artifact.content_hash
