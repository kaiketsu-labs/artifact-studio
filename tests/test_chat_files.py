"""Private chat attachments, explicit edit intent and portable project behavior."""

import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from studio import app as service
from studio.libraries import inventory
from studio.models import EditIntent, ElementTarget, FileInput
from studio.projects import ClientState, open_project, save_project
from studio.storage import ConflictError, Store
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """Build a fictional local presentation with separate private review storage."""
    make_design(tmp_path / "collection" / TEMPLATE_ID)
    return Store(tmp_path / "collection", tmp_path / "private" / "review.sqlite3")


def reference(data: bytes = b"private reference", name: str = "slide reference.pdf") -> FileInput:
    """Encode a small reference through the real shared file input contract."""
    return FileInput(name=name, data_base64=base64.b64encode(data).decode())


def test_file_only_feedback_is_durable_private_and_idempotent(store: Store) -> None:
    """Deliver original names, sizes, hashes and paths without putting files in a deck."""
    slide = store.view(TEMPLATE_ID).slides[0]
    upload = reference()
    event = store.add_feedback(
        TEMPLATE_ID, slide.id, "", slide.content_hash, "file-only-feedback", files=[upload]
    )
    assert (
        store.add_feedback(
            TEMPLATE_ID, slide.id, "", slide.content_hash, "file-only-feedback", files=[upload]
        )
        == event
    )
    restarted = Store(store.root, store.database)
    messages = restarted.feedback_feed(TEMPLATE_ID).events
    assert len(messages) == 1 and messages[0].message == ""
    file = messages[0].files[0]
    assert file.name == upload.name and file.size == len(b"private reference")
    assert file.sha256 == hashlib.sha256(b"private reference").hexdigest()
    assert Path(file.path).read_bytes() == b"private reference"
    assert not Path(file.path).is_relative_to(store.root)
    assert file.download_url == f"/api/decks/{TEMPLATE_ID}/feedback/{event}/files/0"
    with pytest.raises(ConflictError):
        _ = store.add_feedback(
            TEMPLATE_ID,
            slide.id,
            "",
            slide.content_hash,
            "file-only-feedback",
            files=[reference(b"changed")],
        )
    assert len(list((store.database.parent / "feedback-files").rglob("*.attachment"))) == 1
    assert not any("feedback-files" in file.path for file in inventory(store.root))


def test_batch_withholds_files_and_edit_intent_until_explicit_release(store: Store) -> None:
    """Keep structured edit proposals and attachments private with their original target."""
    slide = store.view(TEMPLATE_ID).slides[0]
    target = ElementTarget(selector="h1", label="Heading", x=0.1, y=0.2, width=0.3, height=0.1)
    intent = EditIntent(action="transform", values={"x": 0.25, "rotation_degrees": 15.0})
    _ = store.set_mode(TEMPLATE_ID, "batch")
    _ = store.add_feedback(
        TEMPLATE_ID,
        slide.id,
        "Move and rotate this heading",
        slide.content_hash,
        "targeted-files",
        target=target,
        edit_intent=intent,
        files=[reference()],
    )
    assert store.feedback_feed(TEMPLATE_ID).events == []
    assert store.view(TEMPLATE_ID, include_pending=False).slides[0].feedback == []
    pending = store.view(TEMPLATE_ID).slides[0].feedback[0]
    assert pending.edit_intent == intent and pending.target == target and len(pending.files) == 1
    _ = store.release_batch(TEMPLATE_ID)
    released = store.feedback_feed(TEMPLATE_ID).events[0]
    assert released.edit_intent == intent and released.content_hash == slide.content_hash
    assert released.files == pending.files and released.released


def test_chat_http_accepts_multiple_files_and_downloads_inertly(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Support file-only slide messages and mixed general messages through the browser API."""
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    slide = store.view(TEMPLATE_ID).slides[0]
    response = client.post(
        f"/api/decks/{TEMPLATE_ID}/slides/{slide.id}/feedback",
        json={
            "content_hash": slide.content_hash,
            "request_id": "http-file-feedback",
            "files": [
                reference(name="demo.html").model_dump(),
                reference(name="chart.csv").model_dump(),
            ],
        },
    )
    assert response.status_code == 200
    file = store.feedback_feed(TEMPLATE_ID).events[0].files[0]
    download = client.get(file.download_url)
    assert download.content == b"private reference"
    assert download.headers["content-type"] == "application/octet-stream"
    assert "demo.html" in download.headers["content-disposition"]
    assert download.headers["x-content-type-options"] == "nosniff"
    assert download.headers["cache-control"] == "no-store"
    assert client.get(file.download_url.rsplit("/", 1)[0] + "/99").status_code == 404
    general = client.post(
        f"/api/decks/{TEMPLATE_ID}/feedback",
        json={
            "message": "Use this reference",
            "request_id": "general-file-feedback",
            "context_slide_id": slide.id,
            "files": [reference().model_dump()],
        },
    )
    assert general.status_code == 200
    assert store.view(TEMPLATE_ID).feedback[0].files[0].name == "slide reference.pdf"
    empty = client.post(
        f"/api/decks/{TEMPLATE_ID}/slides/{slide.id}/feedback",
        json={
            "message": " ",
            "content_hash": slide.content_hash,
            "request_id": "empty-feedback",
        },
    )
    assert empty.status_code == 400


def test_feedback_file_limits_and_invalid_data_are_rejected_before_writes(store: Store) -> None:
    """Reject malformed or oversized selections without saving partial messages or files."""
    slide = store.view(TEMPLATE_ID).slides[0]
    for files in (
        [reference()] * 6,
        [FileInput(name="bad.pdf", data_base64="%%%")],
        [reference(b"a" * 25_000_001)],
        [reference(b"a" * 17_000_000)] * 3,
    ):
        with pytest.raises(ValueError):
            _ = store.add_feedback(
                TEMPLATE_ID, slide.id, "", slide.content_hash, "invalid-files", files=files
            )
    assert store.comments(TEMPLATE_ID) == []
    assert not (store.database.parent / "feedback-files").exists()


def test_portable_project_rehomes_chat_files_and_preserves_private_intents(
    store: Store, tmp_path: Path
) -> None:
    """Package private files explicitly and restore them outside any synced presentation tree."""
    slide = store.view(TEMPLATE_ID).slides[0]
    _ = store.set_mode(TEMPLATE_ID, "batch")
    intent = EditIntent(action="delete")
    target = ElementTarget(selector="h1", label="Heading", x=0, y=0, width=1, height=1)
    _ = store.add_feedback(
        TEMPLATE_ID,
        slide.id,
        "Remove this",
        slide.content_hash,
        "portable-files",
        files=[reference()],
        target=target,
        edit_intent=intent,
    )
    data = save_project(store, TEMPLATE_ID, ClientState())
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert any(name.startswith("files/review-attachments/") for name in archive.namelist())
        manifest = archive.read("project.json").decode()
        assert str(store.database.parent) not in manifest
    destination = Store(tmp_path / "restored", tmp_path / "restored-state/review.sqlite3")
    deck, _ = open_project(destination, data)
    message = destination.view(deck.id).slides[0].feedback[0]
    assert message.edit_intent == intent and not message.released
    assert Path(message.files[0].path).read_bytes() == b"private reference"
    assert not Path(message.files[0].path).is_relative_to(destination.root)
    assert not (destination.folder(deck.id) / "review-attachments").exists()
    assert destination.feedback_feed(deck.id).events == []
    _ = destination.release_batch(deck.id)
    assert destination.feedback_feed(deck.id).events[0].files == message.files
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, "w") as corrupt:
        for name in source.namelist():
            content = source.read(name)
            if name == "project.json":
                manifest_json = cast(dict[str, object], json.loads(content))
                feedback = cast(list[dict[str, object]], manifest_json["feedback"])
                attachments = cast(list[dict[str, object]], feedback[0]["files"])
                attachments[0]["path"] = "/etc/passwd"
                content = json.dumps(manifest_json).encode()
            corrupt.writestr(name, content)
    with pytest.raises(ValueError, match="outside its message"):
        _ = open_project(destination, output.getvalue())


def test_sync_inventory_excludes_review_storage_even_inside_collection(tmp_path: Path) -> None:
    """Prevent optional repository snapshots from sweeping private chat references."""
    folder = tmp_path / "feedback-files" / "deck"
    folder.mkdir(parents=True)
    _ = (folder / "confidential.pdf").write_bytes(b"private")
    assert inventory(tmp_path) == []
