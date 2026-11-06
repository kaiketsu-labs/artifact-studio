"""Keep streaming integrity checks compatible with already recorded artifact identities."""

import base64
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from artifact_studio.artifact_models import ArtifactFileInput, ArtifactView
from artifact_studio.artifacts import ArtifactStore, file_inventory, read_tree, tree_inventory
from artifact_studio.storage import ConflictError, Store
from tests.test_artifact_api import client as client  # noqa: F401


@pytest.mark.parametrize(
    "paths",
    [
        ["assets.js", "assets/icon.svg", "index.html"],
        ["data.csv", "data/values.csv", "data-backup.csv", "index.html"],
        ["src/app.css", "src/app/main.js", "src/app-test.js", "index.html"],
        ["Café.json", "Café/notes.md", "z.html", "index.html"],
    ],
)
def test_streaming_inventory_uses_recorded_posix_filename_order(
    tmp_path: Path, paths: list[str]
) -> None:
    """Path-component ordering must not change the canonical manifest's hashed list."""
    for name in paths:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_text("Fictional source for " + name)
    expected = file_inventory(read_tree(tmp_path))
    actual = tree_inventory(tmp_path)
    assert [item.path for item in actual] == sorted(paths)
    assert actual == expected


def test_prefix_bundle_keeps_existing_revision_approval_and_snapshot_bytes(tmp_path: Path) -> None:
    """Loading, revising and reading history must not reject or rewrite an intact saved bundle."""
    source = tmp_path / "source"
    (source / "assets").mkdir(parents=True)
    _ = (source / "index.html").write_text(
        '<h1>Fictional page</h1><script src="assets.js"></script>'
    )
    _ = (source / "assets.js").write_text("window.fictional = true")
    _ = (source / "assets" / "icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    artifacts = ArtifactStore(Store(tmp_path / "collection", tmp_path / "state" / "review.sqlite3"))
    project = artifacts.create_project("Fictional project")
    created = artifacts.register_artifact(project.id, "Prefix bundle", source, "index.html")
    snapshot = Path(created.source_path).parent / "revisions" / "1" / "files"
    before = {
        path.relative_to(snapshot).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in snapshot.rglob("*")
        if path.is_file()
    }
    _ = artifacts.approve(created.id, created.content_hash, True)
    for _ in range(3):
        loaded = artifacts.list_artifacts()[0]
        assert loaded.revision == 1 and loaded.content_hash == created.content_hash
        assert loaded.approved
        assert artifacts.get_artifact_revision(created.id, 1).content_hash == created.content_hash
    revised = artifacts.revise_artifact(
        created.id,
        [ArtifactFileInput(path="assets.js", text="window.fictional = 2")],
        created.content_hash,
        "canonical-order-revision",
    )
    assert revised.revision == 2 and not revised.approved
    assert (
        artifacts.read_artifact_files(created.id, ["assets.js"], 1)[0].text
        == "window.fictional = true"
    )
    after = {
        path.relative_to(snapshot).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in snapshot.rglob("*")
        if path.is_file()
    }
    assert after == before
    # Actual changes to retained bytes must still be rejected.
    retained = Path(revised.source_path).parent / "revisions" / "2" / "files" / "assets.js"
    _ = retained.write_text("Changed outside the hub")
    with pytest.raises(ConflictError, match="immutable artifact snapshot"):
        _ = artifacts.get_artifact(created.id)


def test_prefix_bundle_does_not_break_the_library_http_response(client: TestClient) -> None:
    """The actual hub must list an untouched prefix bundle alongside healthy work."""
    project = client.post("/api/hub/projects", json={"title": "Fictional snapshot project"}).json()
    artifacts: list[ArtifactView] = []
    for title, files in (
        (
            "Prefix bundle",
            {
                "index.html": "<h1>Prefix bundle</h1>",
                "assets.js": "window.example=1",
                "assets/icon.svg": "<svg/>",
            },
        ),
        ("Healthy page", {"index.html": "<h1>Healthy page</h1>"}),
    ):
        response = client.post(
            "/api/artifacts/import",
            json={
                "project_id": project["id"],
                "title": title,
                "entrypoint": "index.html",
                "files": [
                    {"name": name, "data_base64": base64.b64encode(text.encode()).decode()}
                    for name, text in files.items()
                ],
            },
        )
        assert response.status_code == 200, response.text
        artifacts.append(ArtifactView.model_validate_json(response.text))
    for _ in range(2):
        response = client.get("/api/hub")
        assert response.status_code == 200, response.text
        assert {item["id"] for item in response.json()["artifacts"]} == {
            item.id for item in artifacts
        }
    for item in artifacts:
        response = client.get(f"/api/artifacts/{item.id}")
        assert response.status_code == 200, response.text
        assert response.json()["revision"] == 1
        assert response.json()["content_hash"] == item.content_hash
    response = client.get(f"/api/artifacts/{artifacts[0].id}/revisions/1/files/assets.js")
    assert response.status_code == 200 and response.text == "window.example=1"
