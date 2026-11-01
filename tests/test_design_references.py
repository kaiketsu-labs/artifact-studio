"""Design-scoped original sources, selected-deck context and export boundaries."""

import base64
import hashlib
import io
import shutil
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError

from studio import app as service
from studio.design_references import DesignReferences, ReferenceInput, validate_powerpoint
from studio.libraries import Libraries
from studio.models import FileInput
from studio.projects import ClientState, audience_html, save_project
from studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design, powerpoint_source


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    """Keep both design packs and private review state in a disposable workspace."""
    make_design(tmp_path / "presentations" / TEMPLATE_ID)
    result = Store(tmp_path / "presentations", tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", result)
    return result


def attachment(name: str, data: bytes) -> dict[str, str]:
    """Serialize the same upload contract used by the browser."""
    return {"name": name, "data_base64": base64.b64encode(data).decode()}


def test_design_upload_preserves_original_and_reaches_context(store: Store) -> None:
    """Save a source once, preserve rendered approval and expose inert source access."""
    client = TestClient(service.app)
    before = store.view(TEMPLATE_ID).slides[0]
    store.approve(TEMPLATE_ID, before.id, before.content_hash, True)
    content = powerpoint_source("Corporate master slides")
    body = {"files": [attachment("Corporate master slides.pptx", content)]}
    endpoint = "/api/designs/test-brand/references"
    response = client.post(endpoint, json=body)
    assert response.status_code == 200
    files = response.json()
    original = next(item for item in files if item["name"] == "Corporate master slides.pptx")
    assert original["size"] == len(content)
    assert original["sha256"] == hashlib.sha256(content).hexdigest()
    assert Path(original["local_path"]).read_bytes() == content
    assert original["path"].startswith("reference/")
    downloaded = client.get(original["url"])
    assert downloaded.content == content
    assert downloaded.headers["content-type"] == "application/octet-stream"
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert client.get(endpoint).json() == files
    assert client.post(endpoint, json=body).json() == files
    assert len(store.design_inbox().messages) == 1
    notification = store.design_inbox().messages[0]
    assert (
        "test-brand" in notification.message
        and "Corporate master slides.pptx" in notification.message
    )
    assert "does not request" in notification.message
    assert notification.files == []
    assert store.view(TEMPLATE_ID).slides[0].content_hash == before.content_hash
    assert store.view(TEMPLATE_ID).slides[0].approved
    context = service.get_working_context(deck_id=TEMPLATE_ID)
    assert context["design_references"] == files
    assert "not insert binary" in str(context["reference_usage"])
    assert context["design_assets"] == []
    assert any(item["path"] == original["path"] for item in service.get_design_assets("test-brand"))


@pytest.mark.parametrize("detail", ["compact", "full"])
def test_deck_context_uses_selected_brand_not_default(store: Store, detail: str) -> None:
    """Read the selected deck's brand and source pointers in both startup modes."""
    from typing import Literal, cast

    blue = store.root / "2026-11-01-blue-design"
    make_design(blue, "blue")
    _ = (blue / "PREFERENCES.md").write_text("BLUE_SELECTED_PREFERENCES")
    _ = (blue / "Master.pptx").unlink()
    _ = store.set_default_design("test-brand")
    deck = store.create("2026-11-01-blue-story", "Blue story", "blue")
    context = service.get_working_context(
        deck_id=deck.id, detail=cast(Literal["compact", "full"], detail)
    )
    assert context["selected_design_id"] == "blue"
    assert context["default_design_id"] == "test-brand"
    assert context["preferences"] == "BLUE_SELECTED_PREFERENCES"
    assert context["design_status"] == "available"
    assert context["design_references"] == []
    capabilities = context["authoring_capabilities"]
    assert isinstance(capabilities, dict)
    assert capabilities["powerpoint_export"] is False
    assert capabilities["powerpoint_reference_input"] is True
    assert capabilities["free_composition"] is True
    with pytest.raises(ToolError, match="does not match"):
        _ = service.get_working_context(deck_id=deck.id, design_id="test-brand")
    blue_source = powerpoint_source("Blue source")
    _ = DesignReferences(store).attach(
        "blue", ReferenceInput(files=[FileInput(**attachment("Blue master.potx", blue_source))])
    )
    assert len(DesignReferences(store).list("test-brand")) == 1
    assert len(DesignReferences(store).list("blue")) == 1


def test_missing_portable_pack_does_not_substitute_default(store: Store) -> None:
    """An imported deck can continue from its own files when its pack is absent."""
    blue = store.root / "2026-11-01-blue-design"
    make_design(blue, "blue")
    _ = (blue / "PREFERENCES.md").write_text("Deck-local blue preferences")
    deck = store.create("2026-11-01-imported-story", "Imported story", "blue")
    shutil.rmtree(blue)
    context = service.get_working_context(deck_id=deck.id)
    assert context["selected_design_id"] == "blue"
    assert context["design_status"] == "missing"
    assert context["preferences"] == "Deck-local blue preferences"
    assert context["design_references"] == []
    assert "do not substitute" in str(context["design"])
    assert context["asset_inventory_arguments"] == {"deck_id": deck.id}
    guide = service.get_authoring_guide("design_guide", deck_id=deck.id)
    assert guide["markdown"] == (store.folder(deck.id) / "AGENT-GUIDE.md").read_text()
    context_guide = service.get_authoring_guide("design_context", deck_id=deck.id)
    assert context_guide["markdown"] == (store.folder(deck.id) / "README.md").read_text()
    assert "Imported story" in context_guide["markdown"]
    full = service.get_working_context(deck_id=deck.id, detail="full")
    assert full["design_guide"] == guide["markdown"]
    files = service.get_design_assets(deck_id=deck.id)
    asset = next(item for item in files if item["path"] == "assets/blue.svg")
    assert isinstance(asset["url"], str)
    downloaded = TestClient(service.app).get(asset["url"])
    assert downloaded.content == (store.folder(deck.id) / "assets/blue.svg").read_bytes()
    assert downloaded.headers["content-type"] == "application/octet-stream"
    with pytest.raises(ToolError, match="Unknown design"):
        _ = service.get_working_context(design_id="blue")


def test_sources_stay_in_pack_and_only_workspace_snapshots_include_them(store: Store) -> None:
    """A reusable source is not copied to new decks or audience/project exports."""
    marker = "PRIVATE_MASTER_CONTENT_89ba71"
    source = powerpoint_source(marker)
    references = DesignReferences(store).attach(
        "test-brand", ReferenceInput(files=[FileInput(**attachment("Master.potx", source))])
    )
    deck = store.create("2026-11-01-context-story", "Context story", "test-brand")
    assert not list(store.folder(deck.id).rglob("*.pptx"))
    assert not list(store.folder(deck.id).rglob("*.potx"))
    library = Libraries(store)
    assert any(item.path.endswith(".potx") for item in library.snapshot_files())
    assert not any(
        item.path.endswith((".pptx", ".potx")) for item in library.snapshot_files(deck.id)
    )
    assert marker not in audience_html(store, deck.id)
    project = save_project(store, deck.id, ClientState())
    with ZipFile(io.BytesIO(project)) as archive:
        assert not any(name.endswith((".pptx", ".potx")) for name in archive.namelist())
    assert Path(references[-1].local_path).is_file()


def test_filename_collision_and_unicode_are_safe_and_non_destructive(store: Store) -> None:
    """Keep distinct source revisions and constrain paths, including long Unicode names."""
    client = TestClient(service.app)
    endpoint = "/api/designs/test-brand/references"
    sources = [powerpoint_source("First"), powerpoint_source("Second")]
    for data in sources:
        response = client.post(endpoint, json={"files": [attachment("../Same.pptx", data)]})
        assert response.status_code == 200
    files = [item for item in client.get(endpoint).json() if item["name"] == "Same.pptx"]
    assert len(files) == 2
    assert {client.get(item["url"]).content for item in files} == set(sources)
    response = client.post(endpoint, json={"files": [attachment("ü" * 240 + ".pptx", sources[0])]})
    assert response.status_code == 200
    assert all(
        Path(item["local_path"]).resolve().is_relative_to(store.root) for item in response.json()
    )


@pytest.mark.parametrize(
    "file",
    [
        attachment("Renamed.pptx", b"not a zip"),
        attachment("Empty.pptx", b""),
        attachment("Old.ppt", b"legacy format"),
        attachment("Macro.pptm", powerpoint_source()),
        {"name": "Bad.pptx", "data_base64": "*not base64*"},
    ],
)
def test_reject_invalid_upload_before_writing(store: Store, file: dict[str, str]) -> None:
    """Reject wrong or unreadable types, including a bad member late in a batch."""
    client = TestClient(service.app)
    response = client.post(
        "/api/designs/test-brand/references",
        json={"files": [attachment("Good.pptx", powerpoint_source()), file]},
    )
    assert response.status_code == 400
    assert not (store.folder(TEMPLATE_ID) / "reference").exists()
    assert store.design_inbox().messages == []


@pytest.mark.parametrize("extra", ["../escape.xml", "ppt/vbaProject.bin", "\\outside.xml"])
def test_reject_unsafe_container(extra: str) -> None:
    """Never extract ZIP entries or accept traversal and renamed macro containers."""
    output = io.BytesIO(powerpoint_source())
    with ZipFile(output, "a") as archive:
        archive.writestr(extra, "untrusted source")
    with pytest.raises(ValueError, match="macro-free"):
        validate_powerpoint("Master.pptx", output.getvalue())


def test_reject_incomplete_or_disconnected_powerpoint() -> None:
    """A renamed ZIP or package with broken slide pointers is not a usable source."""
    with ZipFile(io.BytesIO(powerpoint_source())) as original:
        for missing_root in (True, False):
            output = io.BytesIO()
            with ZipFile(output, "w") as archive:
                for name in original.namelist():
                    if missing_root and name == "_rels/.rels":
                        continue
                    data = original.read(name)
                    if not missing_root and name == "ppt/presentation.xml":
                        data = (
                            b'<p:presentation xmlns:p="http://schemas.openxmlformats.org/'
                            b'presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
                            b'officeDocument/2006/relationships"><p:sldIdLst>'
                            b'<p:sldId id="256" r:id="missingSlide"/>'
                            b"</p:sldIdLst></p:presentation>"
                        )
                    archive.writestr(name, data)
            with pytest.raises(ValueError, match="could not be read|missing slide"):
                validate_powerpoint("Master.pptx", output.getvalue())


def test_symlink_and_cross_origin_uploads_are_blocked(store: Store, tmp_path: Path) -> None:
    """Keep reference writes inside the selected pack and under the origin guard."""
    client = TestClient(service.app)
    destination = tmp_path / "outside"
    destination.mkdir()
    (store.folder(TEMPLATE_ID) / "reference").symlink_to(destination, target_is_directory=True)
    body = {"files": [attachment("Master.pptx", powerpoint_source())]}
    endpoint = "/api/designs/test-brand/references"
    assert client.post(endpoint, json=body).status_code == 400
    assert (
        client.post(
            endpoint, json=body, headers={"Origin": "https://elsewhere.example"}
        ).status_code
        == 403
    )
    assert list(destination.iterdir()) == []
    assert client.post("/api/designs/missing/references", json=body).status_code == 400
