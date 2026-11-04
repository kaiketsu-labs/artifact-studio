"""Opt-in provenance references retain the exact same-response source identities."""

import base64
import re
from pathlib import Path
from typing import cast

import pytest

from artifact_studio import app as service
from artifact_studio.agent_client import object_value
from artifact_studio.agent_io import compact_source_references
from artifact_studio.artifact_context import artifact_context
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.design_library import DesignCreate, DesignLibrary
from artifact_studio.design_references import ReferenceInput, RemoveReferenceInput
from artifact_studio.models import FileInput
from artifact_studio.storage import Store, digest
from tests.design_factory import TEMPLATE_ID, make_design


def upload(name: str) -> FileInput:
    """Use independent tiny source files, without reading a real collection."""
    return FileInput(name=name, data_base64=base64.b64encode(name.encode()).decode())


def test_receipt_hashes_the_guidance_returned_in_the_same_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent guidance change cannot make the receipt identify different text."""
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    instructions = iter(["First rule", "Changed rule"])
    preferences = iter(["First preference", "Changed preference"])
    monkeypatch.setattr(store, "authoring_instructions", lambda: next(instructions))
    monkeypatch.setattr(store, "learned_preferences", lambda: next(preferences))

    context = artifact_context(store, record_delivery=True, include_discovery=False)
    sources = cast(list[dict[str, str]], context["sources"])
    assert context["authoring_instructions"] == "First rule"
    assert context["learned_preferences"] == "First preference"
    assert sources[0]["hash"] == digest("First rule")
    assert sources[1]["hash"] == digest("First preference")
    assert object_value(context["receipt"])["sources"] == sources


def restored_sources(context: dict[str, object]) -> list[dict[str, object]]:
    """Resolve only the advertised same-response path, never a later library lookup."""
    files = cast(list[dict[str, object]], object_value(context["design_context"])["files"])
    result: list[dict[str, object]] = []
    for source in cast(list[dict[str, object]], context["sources"]):
        reference = source.get("file_ref")
        if reference is None:
            result.append(source)
            continue
        assert isinstance(reference, str)
        match = re.fullmatch(r"design_context\.files\[(\d+)\]", reference)
        assert match is not None
        file = files[int(match[1])]
        result.append(
            {key: value for key, value in source.items() if key != "file_ref"}
            | {"title": file["name"], "path": file["local_path"], "hash": file["sha256"]}
        )
    return result


@pytest.mark.parametrize("scope", ["design", "project", "artifact", "legacy"])
def test_opt_in_sources_reconstruct_exact_inventory_and_leave_rules_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    """Every source identity/delivery survives; default and raw receipts keep their old shape."""
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    library = DesignLibrary(store)
    selected = library.create(
        DesignCreate(name="Selected", files=[upload("a.md"), upload("b.csv")])
    )
    artifacts = ArtifactStore(store)
    project = artifacts.create_project("Evidence", "Complete project brief with unique figures.")
    source = tmp_path / "page"
    source.mkdir()
    _ = (source / "index.html").write_text("<h1>Evidence</h1>")
    artifact = artifacts.register_artifact(project.id, "Evidence", source, "index.html")
    project_id = project.id if scope == "project" else ""
    artifact_id = artifact.id if scope == "artifact" else ""
    if scope == "legacy":
        make_design(store.root / TEMPLATE_ID)
        deck = store.create("2026-11-02-ref-test", "Evidence", "test-brand")
        artifact_id = deck.id
    full = service.get_working_context(
        design_id=selected.id,
        include_discovery=False,
        artifact_id=artifact_id,
        project_id=project_id,
    )
    compact = service.get_working_context(
        design_id=selected.id,
        include_discovery=False,
        compact_sources=True,
        artifact_id=artifact_id,
        project_id=project_id,
    )
    assert restored_sources(compact) == full["sources"]
    assert compact["design_context"] == full["design_context"]
    assert compact["authoring_instructions"] == full["authoring_instructions"]
    assert compact["learned_preferences"] == full["learned_preferences"]
    assert compact["guide"] == full["guide"]
    assert compact["project"] == full["project"]
    assert compact["artifact"] == full["artifact"]
    assert not any("file_ref" in entry for entry in cast(list[dict[str, str]], full["sources"]))
    assert any("file_ref" in entry for entry in cast(list[dict[str, str]], compact["sources"]))
    raw = artifact_context(
        store, design_id=selected.id, artifact_id=artifact_id, project_id=project_id
    )
    assert not any("file_ref" in entry for entry in cast(list[dict[str, str]], raw["sources"]))
    receipt = object_value(raw["receipt"])
    assert all("file_ref" not in entry for entry in cast(list[dict[str, str]], receipt["sources"]))


def test_references_follow_each_response_after_removal_without_hiding_changed_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An old index is not a durable reference; changed inventories and rules remain visible."""
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    library = DesignLibrary(store)
    design = library.create(DesignCreate(name="Brand", files=[upload("a.md"), upload("z.md")]))
    original = service.get_working_context(design_id=design.id, compact_sources=True)
    first = design.files[0]
    _ = library.remove_file(
        design.id, RemoveReferenceInput(path=first.path, expected_version=first.version)
    )
    _ = library.attach(design.id, ReferenceInput(files=[upload("zz-new.csv")]))
    (store.root / "INSTRUCTIONS.md").write_text("A new complete workspace rule.")
    updated = service.get_working_context(
        design_id=design.id,
        compact_sources=True,
        known_rules_hash=str(original["rules_hash"]),
        known_guide_hash=str(original["guide_hash"]),
    )
    identities = restored_sources(updated)
    assert first.local_path not in [entry.get("path") for entry in identities]
    assert any(entry.get("title") == "zz-new.csv" for entry in identities)
    assert updated["authoring_instructions"] == "A new complete workspace rule."
    assert updated["rules_included"] and not updated["guide_included"]
    assert restored_sources(original) != identities


def test_reordered_inventory_resolves_source_identity_instead_of_reusing_an_old_index() -> None:
    """References are meaningful within their own response even if file order changes."""
    first = {"name": "A", "local_path": "/a", "sha256": "aaa"}
    second = {"name": "B", "local_path": "/b", "sha256": "bbb"}
    sources = [
        {
            "title": "A",
            "scope": "design · x",
            "path": "/a",
            "hash": "aaa",
            "delivery": "pointer returned",
        }
    ]
    context: dict[str, object] = {
        "design_context": {"id": "x", "files": [second, first]},
        "sources": sources,
    }
    compact_source_references(context)
    assert restored_sources(context) == sources
    assert (
        cast(list[dict[str, object]], context["sources"])[0]["file_ref"]
        == "design_context.files[1]"
    )


def test_mismatched_or_unrelated_sources_never_gain_an_incorrect_reference() -> None:
    """Malformed or independent provenance stays complete rather than matching by path alone."""
    sources = [
        {"title": "Other", "scope": "design · x", "path": "/same", "hash": "abc"},
        {"title": "File", "scope": "workspace", "path": "/same", "hash": "abc"},
        {"title": "File", "scope": "design · x", "path": "/same", "hash": "changed"},
    ]
    context: dict[str, object] = {
        "design_context": {
            "id": "x",
            "files": [{"name": "File", "local_path": "/same", "sha256": "abc"}],
        },
        "sources": sources,
    }
    compact_source_references(context)
    assert context["sources"] == sources
