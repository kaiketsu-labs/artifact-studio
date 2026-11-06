"""Path choices freeze explicit context without treating display names as identity."""

import base64
import json
import os
from pathlib import Path

import pytest

from artifact_studio.artifact_models import ArtifactFeedbackInput, ArtifactFileInput
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.chat_references import ChatReferences, ReferenceRequest
from artifact_studio.favorites import DescriptionInput, StarInput
from artifact_studio.hub_previews import cover_metadata, cover_path, cover_token, cover_url
from artifact_studio.models import Slide, SlidePatch
from artifact_studio.storage import ConflictError, Store, digest
from tests.design_factory import TEMPLATE_ID, make_design
from tests.test_copilot import DECK, GatewayFixture, call, data, object_value
from tests.test_copilot import gateway as gateway


@pytest.fixture
def references(tmp_path: Path) -> ChatReferences:
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    make_design(store.root / TEMPLATE_ID)
    artifacts = ArtifactStore(store)
    project = artifacts.create_project('Research / @home "2026"', "Project brief for a comparison.")
    _ = store.create(DECK, "Presentation")
    _ = artifacts.set_artifact_project(DECK, project.id)
    source = tmp_path / "source"
    source.mkdir()
    _ = (source / "index.html").write_text('<h1>Artifact source</h1><script src="app.js"></script>')
    _ = (source / "app.js").write_text("window.frozen='original'")
    _ = (source / "image.png").write_bytes(b"\x89PNG\xff")
    (source / "notes").mkdir()
    _ = (source / "notes/private.md").write_text("PRIVATE ARTIFACT NOTES")
    for _ in range(2):
        _ = artifacts.register_artifact(
            project.id, "Duplicate title", source, "index.html", context="Artifact context"
        )
    return ChatReferences(store)


def request(entry: object) -> ReferenceRequest:
    from artifact_studio.chat_references import ReferenceEntry

    assert isinstance(entry, ReferenceEntry)
    return ReferenceRequest.model_validate(
        entry.model_dump(include={"kind", "project_id", "artifact_id", "slide_id", "token"})
    )


def test_catalog_is_small_and_duplicate_paths_keep_distinct_ids(references: ChatReferences) -> None:
    entries = references.catalog()
    duplicates = [entry for entry in entries if entry.title == "Duplicate title"]
    assert len(duplicates) == 2 and duplicates[0].path == duplicates[1].path
    assert duplicates[0].artifact_id != duplicates[1].artifact_id
    assert duplicates[0].token != duplicates[1].token
    serialized = json.dumps([entry.model_dump() for entry in entries])
    assert "PRIVATE ARTIFACT NOTES" not in serialized
    assert "Artifact source" not in serialized and "Project brief for" not in serialized
    assert str(references.store.root) not in serialized


@pytest.mark.parametrize("kind", ["artifact", "presentation"])
def test_reference_covers_share_exact_cache_identity_without_changing_frozen_context(
    references: ChatReferences, kind: str
) -> None:
    entry = next(entry for entry in references.catalog() if entry.kind == kind)
    item = references.artifacts.get_artifact(entry.artifact_id)
    token = cover_token(references.store, item)
    assert entry.preview_url == cover_url(item.id, token)
    assert token != item.content_hash
    assert not entry.preview_cached and entry.preview_renderer
    before = references.prepare(request(entry))
    path = cover_path(references.store, item.id, token)
    path.parent.mkdir(exist_ok=True)
    _ = path.write_bytes(b"cached fixture cover")
    cached = next(
        value
        for value in references.catalog()
        if value.kind == kind and value.artifact_id == item.id
    )
    assert cached.preview_cached and cached.token == entry.token
    assert cached.preview_url == entry.preview_url
    after = references.prepare(request(cached))
    if kind == "presentation":
        marker = 'id="artifact-studio-workspace-reference">'
        metadata = [
            base64.b64decode(prepared.file.data_base64)
            .decode()
            .split(marker, 1)[1]
            .split("</script>", 1)[0]
            for prepared in (before, after)
        ]
        assert metadata[0] == metadata[1]
        assert '"preview_' not in metadata[0]
    else:
        assert after.file == before.file


def test_reference_covers_expose_saved_fallback_for_a_changed_artifact(
    references: ChatReferences,
) -> None:
    entry = next(entry for entry in references.catalog() if entry.kind == "artifact")
    item = references.artifacts.get_artifact(entry.artifact_id)
    token = cover_token(references.store, item)
    path = cover_path(references.store, item.id, token)
    path.parent.mkdir(exist_ok=True)
    _ = path.write_bytes(b"saved fixture cover")
    _ = (path.parent / f"last-{item.id}.json").write_text(json.dumps({"token": token}))
    changed = references.artifacts.revise_artifact(
        item.id,
        [ArtifactFileInput(path="app.js", text="window.changed=true")],
        item.revision,
        "reference-cover-revision",
    )
    current = next(
        value
        for value in references.catalog()
        if value.kind == "artifact" and value.artifact_id == item.id
    )
    assert current.model_dump(
        include={"preview_url", "preview_fallback_url", "preview_cached"}
    ) == cover_metadata(references.store, changed)
    assert current.preview_url != entry.preview_url
    assert current.preview_fallback_url == entry.preview_url
    assert not current.preview_cached


def test_project_reference_freezes_only_brief_and_index(references: ChatReferences) -> None:
    entry = next(
        entry
        for entry in references.catalog()
        if entry.kind == "project" and not entry.path.endswith("Presentations")
    )
    prepared = references.prepare(request(entry))
    content = json.loads(base64.b64decode(prepared.file.data_base64))
    assert content["context"] == "Project brief for a comparison."
    assert len(content["artifacts"]) == 3
    assert "no artifact bodies" in content["scope"]
    assert "Artifact source" not in json.dumps(content)
    assert content["project_id"] == entry.project_id
    assert content["path"] == entry.path
    assert not any(key.startswith("preview_") for key in content)


def test_artifact_reference_retains_dependencies_and_excludes_notes(
    references: ChatReferences,
) -> None:
    entry = next(entry for entry in references.catalog() if entry.kind == "artifact")
    prepared = references.prepare(request(entry))
    encoded = base64.b64decode(prepared.file.data_base64)
    content = json.loads(encoded)
    assert "PRIVATE ARTIFACT NOTES" not in encoded.decode()
    assert content["excluded_paths"] == ["notes/private.md"]
    assert content["files"][0]["path"] == "index.html"
    assert content["files"][-1]["path"] == "image.png"
    assert any(file.get("text") == "window.frozen='original'" for file in content["files"])
    assert content["context"] == "Artifact context"
    assert not any(key.startswith("preview_") for key in content)
    _ = references.artifacts.revise_artifact(
        entry.artifact_id,
        [ArtifactFileInput(path="app.js", text="new source")],
        1,
        "update-for-test",
    )
    with pytest.raises(ConflictError, match="changed"):
        references.prepare(request(entry))
    assert "window.frozen='original'" in encoded.decode()


def test_unstarred_slide_and_presentation_keep_audience_dependencies(
    references: ChatReferences,
) -> None:
    store = references.store
    slide = store.read(DECK).slides[0]
    _ = (store.folder(DECK) / "assets/widget.js").write_text("document.body.dataset.frozen='yes';")
    _ = store.revise(
        DECK,
        slide.id,
        digest(slide.model_dump_json()),
        "interactive-path-reference",
        SlidePatch(
            interactive_html='<h1>Unicode é</h1><script src="assets/widget.js"></script>',
            notes="PRIVATE SPEAKER NOTES",
        ),
    )
    entries = references.catalog()
    selected = next(
        entry for entry in entries if entry.kind == "slide" and entry.artifact_id == DECK
    )
    assert not selected.starred
    for entry in (selected, next(entry for entry in entries if entry.kind == "presentation")):
        prepared = references.prepare(request(entry))
        document = base64.b64decode(prepared.file.data_base64).decode()
        assert "PRIVATE SPEAKER NOTES" not in document
        assert "document.body.dataset.frozen" in document
        assert 'src=\\"assets/widget.js\\"' not in document
        assert '"preview_' not in document
    assert not references.favorites.index()["slides"]


def test_favorite_description_changes_invalidate_path_choice(references: ChatReferences) -> None:
    slide = references.store.read(DECK).slides[0]
    _ = references.favorites.set(
        StarInput(kind="slide", artifact_id=DECK, slide_id=slide.id, starred=True)
    )
    _ = references.favorites.describe(
        DECK, slide.id, DescriptionInput(description="Compare options", expected_description="")
    )
    entry = next(entry for entry in references.catalog() if entry.kind == "slide")
    assert entry.starred and entry.description == "Compare options"
    prepared = references.prepare(request(entry))
    assert "Compare options" in base64.b64decode(prepared.file.data_base64).decode()
    _ = references.favorites.describe(
        DECK,
        slide.id,
        DescriptionInput(description="A new purpose", expected_description="Compare options"),
    )
    with pytest.raises(ConflictError):
        references.prepare(request(entry))


def test_moving_source_rejects_old_paths_and_project_indexes(references: ChatReferences) -> None:
    entries = references.catalog()
    entry = next(entry for entry in entries if entry.kind == "artifact")
    project = next(
        item for item in entries if item.kind == "project" and item.project_id == entry.project_id
    )
    other = references.artifacts.create_project("Elsewhere")
    _ = references.artifacts.set_artifact_project(entry.artifact_id, other.id)
    for old in (entry, project):
        with pytest.raises(ConflictError):
            references.prepare(request(old))


def test_project_reference_is_readable_only_after_destination_batch_release(
    gateway: GatewayFixture,
) -> None:
    references = ChatReferences(gateway.store)
    entry = next(
        entry
        for entry in references.catalog()
        if entry.kind == "project" and entry.project_id == gateway.unrelated.project_id
    )
    frozen = references.prepare(request(entry))
    _ = references.artifacts.set_mode(gateway.selected.id, "batch")
    event = references.artifacts.add_feedback(
        gateway.selected.id,
        ArtifactFeedbackInput(
            message="Use this project as a reference",
            content_hash=gateway.selected.content_hash,
            request_id="project-reference-001",
            files=[frozen.file],
        ),
    )
    args: dict[str, object] = {
        "artifact_id": gateway.selected.id,
        "event_id": event.event_id,
        "file_index": 0,
    }
    assert call(gateway.client, "read_feedback_attachment", args)["isError"] is True
    _ = references.artifacts.release_batch(gateway.selected.id)
    page = object_value(data(call(gateway.client, "read_feedback_attachment", args)))
    assert "UNRELATED PRIVATE BRIEF" in str(page["text"])
    assert (
        call(gateway.client, "get_working_context", {"project_id": entry.project_id})["isError"]
        is True
    )


@pytest.mark.parametrize("kind", ["slide", "presentation"])
def test_suggestions_pin_actual_interactive_bytes_even_with_unchanged_file_stats(
    references: ChatReferences, kind: str
) -> None:
    store = references.store
    script = store.folder(DECK) / "widget.js"
    _ = script.write_text("window.value = 1;")
    slide = store.read(DECK).slides[0]
    _ = store.revise(
        DECK,
        slide.id,
        digest(slide.model_dump_json()),
        "byte-token-test",
        SlidePatch(interactive_html='<script src="widget.js"></script>'),
    )
    entry = next(entry for entry in references.catalog() if entry.kind == kind)
    before = script.stat()
    _ = script.write_text("window.value = 2;")
    os.utime(script, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ConflictError, match="changed"):
        references.prepare(request(entry))


def test_presentation_visibility_invalidates_suggestion(references: ChatReferences) -> None:
    entry = next(entry for entry in references.catalog() if entry.kind == "presentation")
    references.store.set_slide_hidden(DECK, "cover", hidden=True, expected_hidden=False)
    with pytest.raises(ConflictError, match="changed"):
        references.prepare(request(entry))


def test_one_missing_dependency_does_not_hide_other_slides(references: ChatReferences) -> None:
    _ = references.store.add_slide(
        DECK,
        Slide(
            id="broken",
            title="Missing image",
            html='<article class="slide"><img src="assets/missing.png"></article>',
        ),
    )
    entries = references.catalog()
    healthy = next(
        entry for entry in entries if entry.kind == "slide" and entry.slide_id == "cover"
    )
    broken = next(
        entry for entry in entries if entry.kind == "slide" and entry.slide_id == "broken"
    )
    assert not healthy.reference_error and broken.reference_error
    assert references.prepare(request(healthy)).file.data_base64
