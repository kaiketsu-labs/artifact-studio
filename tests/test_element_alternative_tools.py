"""Registered agent contracts for immutable element proposals and retained visual context."""

import asyncio
import json
import os
from pathlib import Path
from typing import cast

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from artifact_studio import app as service
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.copilot_tools import ScopedTools
from artifact_studio.models import AgentContext, SlidePatch
from artifact_studio.storage import Store, digest
from tests.design_factory import TEMPLATE_ID, make_design

NAMES = {
    "publish_element_alternatives",
    "get_element_alternatives",
    "get_element_alternative",
    "capture_element_alternative",
}
DECK = "2026-10-29-element-fixture"
PROPOSAL = '<h1 id="headline">A clearer result.</h1>'


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    """Use fictional authoring source and keep all private state outside the repository."""
    make_design(tmp_path / "collection" / TEMPLATE_ID)
    result = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    _ = result.create(DECK, "Fictional element presentation")
    slide = result.read(DECK).slides[0]
    _ = result.revise(
        DECK,
        "cover",
        digest(slide.model_dump_json()),
        "element-tool-fixture",
        SlidePatch(
            html='<article class="slide"><h1 id="headline">Current headline</h1>'
            '<p id="body">Keep this text.</p></article>',
            notes="PRIVATE ELEMENT SPEAKER NOTES",
            agent=AgentContext(summary="PRIVATE ELEMENT AUTHOR CONTEXT"),
        ),
    )
    monkeypatch.setattr(service, "store", result)
    monkeypatch.setattr(service, "DATABASE", result.database)
    return result


@pytest.fixture
def scoped_tools(store: Store) -> ScopedTools:
    """Associate the real presentation with a fictional gateway project."""
    artifacts = ArtifactStore(store)
    project = artifacts.create_project("Fictional scoped project")
    _ = artifacts.set_artifact_project(DECK, project.id)
    return ScopedTools(store, project.id, False)


def test_scoped_element_read_requires_current_project_membership(
    store: Store,
    scoped_tools: ScopedTools,
) -> None:
    """Exercise the capture fixture's authorization without requiring a renderer."""
    before = store.read(DECK)
    group = publish(store)
    options = cast(list[dict[str, object]], group["options"])
    result = scoped_tools.get_element_alternative(
        DECK, "cover", str(options[1]["id"]), str(group["context_hash"])
    )
    assert result["html"] == PROPOSAL
    assert result["context_hash"] == group["context_hash"]
    assert store.read(DECK) == before
    artifacts = ArtifactStore(store)
    other = artifacts.create_project("Other fictional project")
    _ = artifacts.set_artifact_project(DECK, other.id)
    with pytest.raises(ValueError, match="scope"):
        _ = scoped_tools.get_element_alternative(DECK, "cover", str(options[1]["id"]))


def call(name: str, arguments: dict[str, object]) -> CallToolResult:
    """Use the registered SDK tool and its actual input/output conversion."""
    result = asyncio.run(service.mcp.call_tool(name, arguments))
    assert isinstance(result, CallToolResult) and not result.is_error
    return result


def data(result: CallToolResult) -> dict[str, object]:
    """Narrow the registered structured result without relying on text echoes."""
    structured = result.structured_content
    assert structured is not None
    return structured


def publish(store: Store) -> dict[str, object]:
    """Publish through the registered MCP handler, preserving the canonical audience source."""
    current = store.read(DECK).slides[0]
    return data(
        call(
            "publish_element_alternatives",
            {
                "deck_id": DECK,
                "slide_id": "cover",
                "target_id": "headline",
                "kind": "headline",
                "label": "Headline",
                "expected_content_hash": store.content_hash(TEMPLATE_ID, current),
                "request_id": "element-tool-publication",
                "options": [{"name": "Direct", "html": PROPOSAL}],
            },
        )
    )


def test_discovery_bootstrap_and_guide_advertise_only_human_decisions(store: Store) -> None:
    """An unfamiliar agent can discover the implemented capability and its boundaries."""
    tools = {tool.name: tool for tool in asyncio.run(service.mcp.list_tools())}
    assert tools.keys() >= NAMES
    assert not {"decide_element_alternative", "undo_element_alternative"} & tools.keys()
    for name in NAMES:
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.read_only_hint is (name != "publish_element_alternatives")
    properties = tools["publish_element_alternatives"].input_schema["properties"]
    assert isinstance(properties, dict)
    assert {"target_id", "kind", "expected_content_hash", "options"} <= properties.keys()
    assert "publish_element_alternatives" in (service.PROJECT / "agent/bootstrap.md").read_text()
    guide = service.get_authoring_guide("slide_designs")
    assert "target_id" in guide["markdown"] and "element_context_hash" in guide["markdown"]
    assert "ask_slide_question" in guide["markdown"]


def test_registered_publication_retry_and_exact_context_read_preserve_private_state(
    store: Store,
) -> None:
    """A tool publication is an immutable proposal, never an implicit source revision."""
    before = store.read(DECK)
    group = publish(store)
    assert publish(store) == group and store.read(DECK) == before
    raw_options = group["options"]
    assert isinstance(raw_options, list)
    options = cast(list[dict[str, object]], raw_options)
    assert len(options) == 2
    option = options[1]
    source = data(
        call(
            "get_element_alternative",
            {
                "deck_id": DECK,
                "slide_id": "cover",
                "element_option_id": option["id"],
                "context_hash": group["context_hash"],
            },
        )
    )
    assert source["html"] == PROPOSAL and source["target_id"] == "headline"
    assert source["context_hash"] == group["context_hash"]
    assert "PRIVATE" not in json.dumps(source)
    page = data(
        call(
            "get_element_alternatives",
            {"deck_id": DECK, "slide_id": "cover", "set_id": group["id"]},
        )
    )
    assert "PRIVATE" not in json.dumps(page)
    with pytest.raises(ToolError, match="context"):
        _ = call(
            "get_element_alternative",
            {
                "deck_id": DECK,
                "slide_id": "cover",
                "element_option_id": option["id"],
                "context_hash": "0" * 64,
            },
        )


@pytest.mark.skipif(
    os.environ.get("ARTIFACT_STUDIO_RENDER_TESTS") != "1", reason="Opt-in sandboxed rendering"
)
def test_actual_registered_and_scoped_captures_pin_context_without_changing_source(
    store: Store,
    scoped_tools: ScopedTools,
) -> None:
    """Both tool surfaces return real PNGs, exact context tokens and no private notes."""
    before = store.read(DECK)
    group = publish(store)
    options = cast(list[dict[str, object]], group["options"])
    option_id = str(options[1]["id"])
    context_hash = str(group["context_hash"])
    result = call(
        "capture_element_alternative",
        {
            "deck_id": DECK,
            "slide_id": "cover",
            "element_option_id": option_id,
            "context_hash": context_hash,
        },
    )
    metadata = data(result)
    assert metadata["context_hash"] == context_hash
    assert metadata["source_hash"] == options[1]["source_hash"]
    assert any(item.type == "image" and item.mime_type == "image/png" for item in result.content)
    assert "PRIVATE" not in json.dumps(metadata)
    scoped = scoped_tools.capture_element_alternative(DECK, "cover", option_id, context_hash)
    assert scoped.structured_content is not None
    assert scoped.structured_content["context_hash"] == context_hash
    assert any(item.type == "image" and item.mime_type == "image/png" for item in scoped.content)
    serialized = json.dumps(scoped.structured_content)
    assert "PRIVATE" not in serialized and '"path"' not in serialized
    assert store.read(DECK) == before
