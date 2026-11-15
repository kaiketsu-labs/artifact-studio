"""Saved capture delivery preserves actual MCP metadata without encoding inline pixels."""

import asyncio
import base64
import io
import json
from pathlib import Path

import pytest
from mcp.server.mcpserver import Image as MCPImage
from mcp.types import CallToolResult
from PIL import Image

from artifact_studio import app as service
from artifact_studio import artifact_tools
from artifact_studio.agent_client import Client, Invocation
from artifact_studio.artifact_capture import ArtifactCapture
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.element_alternatives import ElementInput
from artifact_studio.models import SlidePatch
from artifact_studio.slide_designs import DesignInput
from artifact_studio.storage import Store, digest
from artifact_studio.visuals import CaptureOptions, Snapshot, VisualResult, snapshot
from tests.design_factory import TEMPLATE_ID, make_design

TOOLS = (
    "capture_slide",
    "capture_contact_sheet",
    "capture_slide_design",
    "capture_element_alternative",
    "capture_artifact",
)


@pytest.mark.parametrize("tool", TOOLS)
def test_registered_capture_can_return_saved_file_without_encoding_an_image(
    tool: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise schemas, proposal identity, metadata and encoding through the SDK boundary."""
    make_design(tmp_path / "collection" / TEMPLATE_ID)
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    current = store.read(TEMPLATE_ID).slides[0]
    _ = store.revise(
        TEMPLATE_ID,
        "cover",
        digest(current.model_dump_json()),
        "capture-delivery-fixture",
        SlidePatch(
            html='<article class="slide"><h1 id="headline">Fictional heading</h1></article>',
            notes="PRIVATE CAPTURE NOTES",
        ),
    )
    monkeypatch.setattr(service, "store", store)
    monkeypatch.setattr(service, "DATABASE", store.database)
    image_path = tmp_path / "capture.png"
    with Image.new("RGB", (32, 18), color=(31, 101, 205)) as image:
        image.save(image_path)
    pixels = image_path.read_bytes()
    current = store.read(TEMPLATE_ID).slides[0]
    frame = snapshot(store, TEMPLATE_ID, current, 1, CaptureOptions()).frame
    captured = VisualResult(
        capture_id="a" * 32,
        deck_id=TEMPLATE_ID,
        captured_at="2026-11-15T00:00:00Z",
        kind="slide",
        path=str(image_path),
        frames=[frame],
        total_slides=2,
        selected_slides=1,
    )
    renders: list[str] = []

    def fake_capture(*_args: object, **_kwargs: object) -> VisualResult:
        renders.append("slide")
        return captured

    def fake_snapshot(_prepared: Snapshot, _options: CaptureOptions) -> bytes:
        renders.append("proposal")
        return pixels

    monkeypatch.setattr(service, "capture", fake_capture)
    monkeypatch.setattr(service, "capture_snapshot", fake_snapshot)
    arguments: dict[str, object] = {"deck_id": TEMPLATE_ID}
    if tool != "capture_contact_sheet":
        arguments["slide_id"] = "cover"
    if tool == "capture_slide_design":
        group = service.publish_slide_designs(
            TEMPLATE_ID,
            "cover",
            frame.content_hash,
            "delivery-design",
            [DesignInput(name="Option", html='<article class="slide">Alternative</article>')],
        )
        arguments["design_id"] = group.options[1].id
    elif tool == "capture_element_alternative":
        group = service.publish_element_alternatives(
            TEMPLATE_ID,
            "cover",
            "headline",
            "headline",
            "Wording",
            frame.content_hash,
            "delivery-element",
            [ElementInput(name="Option", html='<h1 id="headline">Alternative</h1>')],
        )
        arguments["element_option_id"] = group.options[1].id
    elif tool == "capture_artifact":
        artifacts = ArtifactStore(store)
        project = artifacts.create_project("Fictional capture")
        source = tmp_path / "page.html"
        _ = source.write_text("<h1>Fictional artifact</h1>")
        artifact = artifacts.register_artifact(project.id, "Page", source, "page.html")
        arguments = {"artifact_id": artifact.id}

        def fake_artifact(*_args: object, **_kwargs: object) -> ArtifactCapture:
            renders.append("artifact")
            return ArtifactCapture(
                artifact_id=artifact.id,
                path=str(image_path),
                content_hash=artifact.content_hash,
                revision=artifact.revision,
                width=32,
                height=18,
                viewport_width=32,
                viewport_height=18,
                elapsed_ms=0,
                full_page=False,
                captured_at="2026-11-15T00:00:00Z",
            )

        monkeypatch.setattr(artifact_tools, "capture_artifact_image", fake_artifact)

    before = store.read(TEMPLATE_ID)
    schema = next(item for item in asyncio.run(service.mcp.list_tools()) if item.name == tool)
    assert schema.input_schema["properties"]["return_image"]["default"] is True
    assert schema.input_schema["properties"]["return_image"]["type"] == "boolean"
    inline = asyncio.run(service.mcp.call_tool(tool, arguments))
    assert isinstance(inline, CallToolResult) and not inline.is_error
    assert [item.type for item in inline.content] == ["text", "image"]
    assert inline.content[1].type == "image"
    assert base64.b64decode(inline.content[1].data) == pixels

    def reject_encoding(*_args: object, **_kwargs: object) -> None:
        pytest.fail("File delivery must not read/encode an inline image.")

    monkeypatch.setattr(MCPImage, "to_image_content", reject_encoding)
    saved = asyncio.run(service.mcp.call_tool(tool, {**arguments, "return_image": False}))
    assert isinstance(saved, CallToolResult) and not saved.is_error
    assert len(renders) == 2, "Both modes must still execute the capture."
    assert [item.type for item in saved.content] == ["text"]
    assert saved.content[0].type == "text"
    assert saved.structured_content == json.loads(saved.content[0].text)
    assert saved.structured_content is not None and inline.structured_content is not None
    for key, value in inline.structured_content.items():
        if key != "captured_at":
            assert saved.structured_content[key] == value
    assert Path(str(saved.structured_content["path"])).read_bytes() == pixels
    assert "PRIVATE CAPTURE NOTES" not in saved.model_dump_json()
    assert store.read(TEMPLATE_ID) == before
    client = Client("http://127.0.0.1:8790/mcp")

    def saved_response(_method: str, _params: dict[str, object]) -> dict[str, object]:
        return saved.model_dump(mode="json", by_alias=True)

    monkeypatch.setattr(client, "rpc", saved_response)
    assert client.call(Invocation(tool=tool, arguments=arguments)) == saved.structured_content
    with Image.open(io.BytesIO(pixels)) as image:
        assert image.getpixel((0, 0)) == (31, 101, 205)
