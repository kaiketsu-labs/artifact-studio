"""Reference discovery, revision identity, byte delivery and current connection grants."""

import asyncio
import base64
import io
from pathlib import Path
from typing import cast

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp.types import ImageContent
from PIL import Image

from artifact_studio import app as local
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.copilot_tools import ScopedTools
from artifact_studio.designs.pinned import DesignReferences, PinAccess, PinChange, ReferenceScope
from artifact_studio.models import Slide
from artifact_studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design
from tests.integration.multiplayer_fixture import running_multiplayer_app
from tests.test_hosted_design_authoring import connection, peer, value
from tests.test_multiplayer_server import human


def pin_fixture(store: Store, deck_id: str, scope: ReferenceScope) -> tuple[str, bytes]:
    """Store a known thumbnail alongside the normal fixture's audience source."""
    refs = DesignReferences(store)
    project = refs.presentation_project(deck_id)
    slide = store.read(deck_id).slides[0]
    change = PinChange(
        scope=scope,
        expected_pin_id="",
        slide_id=slide.id,
        content_hash=store.content_hash(deck_id, slide),
    )
    target, previous = refs.change_check(deck_id, project, change, PinAccess(store))
    selection = refs.prepare(deck_id, target, change)
    buffer = io.BytesIO()
    Image.new("RGB", (2, 1), "#ff9880").save(buffer, format="PNG")
    png = buffer.getvalue()
    refs.save(deck_id, project, change, target, previous, selection)
    # Isolate the MCP delivery contract from Chromium provisioning.
    with store.connect() as db:
        _ = db.execute(
            "UPDATE pinned_slides SET thumbnail=? WHERE pin_id=?", (png, selection.reference.pin_id)
        )
    return selection.reference.pin_id, png


def test_local_first_body_example_then_human_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "collection"
    make_design(root / TEMPLATE_ID)
    store = Store(root, tmp_path / "state" / "review.sqlite3")
    monkeypatch.setattr(local, "store", store)
    example = store.read(TEMPLATE_ID)
    example.slides = example.slides[:1]
    store.write(TEMPLATE_ID, example)
    assert "slide_reference" not in local.get_working_context(design_id="test-brand")
    _ = store.add_slide(
        TEMPLATE_ID, Slide(id="hidden", title="Private appendix", hidden=True, html="<p>hidden</p>")
    )
    _ = store.add_slide(
        TEMPLATE_ID, Slide(id="body", title="Anatomy of a wheel", html="<p>BODY EXAMPLE</p>")
    )
    deck = store.create("2026-11-21-bicycle", "Bicycle", "test-brand")
    ref = cast(dict[str, object], local.get_working_context(artifact_id=deck.id)["slide_reference"])
    assert ref["scope"] == "design_example"
    assert ref["source"] == {
        "tool": "get_slide",
        "arguments": {"deck_id": TEMPLATE_ID, "slide_id": "body"},
    }
    assert ref["approved"] is False and "BODY EXAMPLE" not in str(ref)
    wheel = store.add_slide(
        TEMPLATE_ID, Slide(id="wheel", title="Wheel assembly", html="<p>Spokes</p>")
    )
    approved_hash = store.content_hash(TEMPLATE_ID, wheel)
    store.approve(TEMPLATE_ID, wheel.id, approved_hash, True)
    selected = cast(
        dict[str, object], local.get_working_context(deck_id=deck.id)["slide_reference"]
    )
    assert selected["approved"] is True and selected["content_hash"] == approved_hash
    assert selected["title"] == wheel.title
    current = store.read(deck.id)
    current.slides[0].notes = "Private speaker reminder"
    store.write(deck.id, current)
    library = ArtifactStore(store)
    project = library.create_project("Bike safety").id
    _ = library.set_artifact_project(deck.id, project)
    pin_id, png = pin_fixture(store, deck.id, "project")
    ref = cast(dict[str, object], local.get_working_context(deck_id=deck.id)["slide_reference"])
    assert ref["scope"] == "project" and ref["arguments"] == {
        "target": "deck",
        "target_id": deck.id,
        "pin_id": pin_id,
    }
    pin_id_deck, _ = pin_fixture(store, deck.id, "deck")
    assert pin_id_deck != pin_id
    with pytest.raises(ToolError, match="unavailable or replaced"):
        _ = local.read_slide_reference("deck", deck.id, pin_id, "source")
    changed = store.read(deck.id)
    changed.slides[0].html = "<p>Changed after the human pinned this slide</p>"
    store.write(deck.id, changed)
    source = local.read_slide_reference("project", project, pin_id, "source").structured_content
    assert source is not None and source["html"] == deck.slides[0].html
    assert not {"notes", "feedback", "agent", "local_path"} & source.keys()
    assert "Private speaker reminder" not in str(source)
    image = local.read_slide_reference("deck", deck.id, pin_id_deck, "thumbnail").content[0]
    assert isinstance(image, ImageContent) and base64.b64decode(image.data) == png
    assert (
        source
        == ScopedTools(store, project, False)
        .read_slide_reference("project", project, pin_id, "source")
        .structured_content
    )
    other = ArtifactStore(store).create_project("Unrelated").id
    scoped = ScopedTools(store, other, False)
    with pytest.raises(ValueError):
        _ = scoped.read_slide_reference("project", project, pin_id, "source")
    assert "slide_reference" not in scoped.get_working_context()
    _ = ArtifactStore(store).set_artifact_project(deck.id, other)
    old_scope = ScopedTools(store, project, False)
    assert "slide_reference" not in old_scope.get_working_context()
    with pytest.raises(ValueError):
        _ = old_scope.read_slide_reference("project", project, pin_id, "thumbnail")


def test_hosted_project_pin_respects_artifact_binding_and_revocation(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(
            tmp_path / "app", tmp_path / "server.log", unified=True, startup_timeout=45
        ) as app,
        human(app, "owner") as owner,
    ):
        _ = ArtifactStore(app.store).set_artifact_project(app.deck_id, app.project_id)
        project_id, png = pin_fixture(app.store, app.deck_id, "project")
        project = connection(owner, "project", app.project_id)
        artifact = connection(owner, "artifact", app.deck_id)

        async def exercise() -> None:
            async with peer(app, project["token"]) as broad, peer(app, artifact["token"]) as narrow:
                selected = value(
                    await broad.call_tool("get_working_context", {"artifact_id": app.deck_id})
                )
                pointer = cast(dict[str, object], selected["slide_reference"])
                assert pointer["pin_id"] == project_id
                args = cast(dict[str, object], pointer["arguments"])
                captured = await broad.call_tool(
                    "read_slide_reference", {**args, "view": "thumbnail"}
                )
                image = captured.content[0]
                assert isinstance(image, ImageContent) and base64.b64decode(image.data) == png
                assert "slide_reference" not in value(
                    await narrow.call_tool("get_working_context", {})
                )
                assert (
                    await narrow.call_tool("read_slide_reference", {**args, "view": "source"})
                ).is_error
                assert (
                    await narrow.call_tool("get_working_context", {"project_id": app.project_id})
                ).is_error
                deck_id, _ = pin_fixture(app.store, app.deck_id, "deck")
                scoped = cast(
                    dict[str, object],
                    value(await narrow.call_tool("get_working_context", {}))["slide_reference"],
                )
                assert scoped["pin_id"] == deck_id
                authorized = {**cast(dict[str, object], scoped["arguments"]), "view": "source"}
                source = value(await narrow.call_tool("read_slide_reference", authorized))
                assert "html" in source and not {"notes", "feedback", "agent"} & source.keys()
                assert owner.post(f"/api/agents/{artifact['id']}/revoke").is_success
                with pytest.raises(MCPError):
                    _ = await narrow.call_tool("read_slide_reference", authorized)

        asyncio.run(exercise())
