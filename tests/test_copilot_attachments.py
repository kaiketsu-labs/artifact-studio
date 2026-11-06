"""Exercise saved chat references through the authenticated, scoped MCP transport."""

import base64
import json
from pathlib import Path
from typing import cast

import pytest

from artifact_studio.artifact_models import ArtifactFeedbackInput
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.favorites import DescriptionInput, Favorites, StarInput
from artifact_studio.models import FileInput, SlidePatch
from artifact_studio.storage import digest
from tests.test_copilot import DECK, GatewayFixture, call, data, object_value, rpc
from tests.test_copilot import gateway as gateway


def attach(gateway: GatewayFixture, target: str, file: FileInput) -> int:
    """Attach a file through the existing human feedback storage contract."""
    artifacts = ArtifactStore(gateway.store)
    item = artifacts.get_artifact(target)
    if item.legacy_deck_id:
        return int(
            gateway.store.add_feedback(
                target, None, "Use this reference", "", "reference", files=[file]
            )
        )
    return artifacts.add_feedback(
        target,
        ArtifactFeedbackInput(
            message="Use this reference",
            content_hash=item.content_hash,
            request_id="reference",
            files=[file],
        ),
    ).event_id


def read(gateway: GatewayFixture, **args: object) -> dict[str, object]:
    """Read through MCP schema validation, authentication and scope checks."""
    return object_value(data(call(gateway.client, "read_feedback_attachment", args)))


@pytest.mark.parametrize("legacy", [False, True])
def test_reference_is_frozen_and_delivered_only_in_destination_scope(
    gateway: GatewayFixture, legacy: bool
) -> None:
    store = gateway.store
    artifacts = ArtifactStore(store)
    source_id = "2026-11-06-outside-reference"
    _ = store.create(source_id, "Reference source outside the gateway")
    _ = artifacts.set_artifact_project(source_id, gateway.unrelated.project_id)
    slide = store.read(source_id).slides[0]
    favorites = Favorites(store)
    bookmark = StarInput(kind="slide", artifact_id=source_id, slide_id=slide.id, starred=True)
    _ = favorites.set(bookmark)
    _ = favorites.describe(
        source_id,
        slide.id,
        DescriptionInput(
            description="A comparison layout with clear hierarchy.", expected_description=""
        ),
    )
    reference = favorites.reference(source_id, slide.id, favorites.slides()[0].content_hash)
    expected = base64.b64decode(reference.data_base64).decode()
    target = DECK if legacy else gateway.selected.id
    _ = artifacts.set_mode(target, "batch")
    event_id = attach(gateway, target, reference)
    args: dict[str, object] = {"artifact_id": target, "event_id": event_id, "file_index": 0}
    assert call(gateway.client, "read_feedback_attachment", args)["isError"] is True
    _ = store.revise(
        source_id,
        slide.id,
        digest(slide.model_dump_json()),
        "change-original",
        SlidePatch(
            html='<article class="slide">A new unrelated idea</article>', notes="PRIVATE NOTES"
        ),
    )
    _ = favorites.describe(
        source_id,
        slide.id,
        DescriptionInput(
            description="Changed description",
            expected_description="A comparison layout with clear hierarchy.",
        ),
    )
    _ = favorites.set(bookmark.model_copy(update={"starred": False}))
    assert (
        call(gateway.client, "get_slide", {"deck_id": source_id, "slide_id": slide.id})["isError"]
        is True
    )
    _ = artifacts.release_batch(target)
    before = artifacts.feedback_feed(target).model_dump()
    parts: list[str] = []
    offset = 0
    for _ in range(100):
        page = read(gateway, **args, offset=offset, max_bytes=4096)
        assert "path" not in page and "download_url" not in page
        assert str(store.database) not in json.dumps(page)
        parts.append(cast(str, page["text"]))
        if not offset:
            sections = object_value(page["section_offsets"])
            start = cast(int, sections["slide"])
            assert expected.encode()[start:].startswith(b'<main id="capture-stage"')
            markup = read(gateway, **args, offset=start)
            assert cast(str, markup["text"]).startswith('<main id="capture-stage"')
        if page["next_offset"] is None:
            break
        offset = cast(int, page["next_offset"])
    else:
        pytest.fail("Attachment pagination did not finish")
    assert "".join(parts) == expected
    assert "A comparison layout with clear hierarchy." in parts[0]
    assert "Changed description" not in "".join(parts)
    assert artifacts.feedback_feed(target).model_dump() == before
    _ = artifacts.set_artifact_project(target, gateway.unrelated.project_id)
    assert call(gateway.client, "read_feedback_attachment", args)["isError"] is True


def test_text_pages_preserve_unicode_and_binary_pages_are_bounded(gateway: GatewayFixture) -> None:
    content = "abc🙂é漢字 and the final word".encode()
    target = gateway.selected.id
    event = attach(
        gateway,
        target,
        FileInput(name="reference.txt", data_base64=base64.b64encode(content).decode()),
    )
    args: dict[str, object] = {"artifact_id": target, "event_id": event, "file_index": 0}
    offset = 0
    pages: list[str] = []
    while True:
        page = read(gateway, **args, offset=offset, max_bytes=4)
        pages.append(cast(str, page["text"]))
        assert len(pages[-1].encode()) <= 4
        if page["next_offset"] is None:
            break
        assert cast(int, page["next_offset"]) > offset
        offset = cast(int, page["next_offset"])
    assert "".join(pages).encode() == content
    assert read(gateway, **args, offset=len(content))["text"] == ""
    assert (
        read(gateway, **args, encoding="base64", offset=4, max_bytes=4)["data_base64"]
        == base64.b64encode(content[4:8]).decode()
    )
    assert call(gateway.client, "read_feedback_attachment", args | {"offset": 4})["isError"] is True
    for invalid in (
        {"offset": len(content) + 1},
        {"offset": -1},
        {"max_bytes": 0},
        {"max_bytes": 65537},
        {"file_index": -1},
        {"file_index": 1},
    ):
        assert call(gateway.client, "read_feedback_attachment", args | invalid)["isError"] is True


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("damage", ["changed", "missing", "symlink", "directory_symlink"])
def test_attachment_storage_changes_are_rejected(
    gateway: GatewayFixture, legacy: bool, damage: str, tmp_path: Path
) -> None:
    target = DECK if legacy else gateway.selected.id
    event = attach(
        gateway,
        target,
        FileInput(name="reference.txt", data_base64=base64.b64encode(b"original").decode()),
    )
    artifact_store = ArtifactStore(gateway.store)
    file = next(item for item in artifact_store.feedback(target) if item.event_id == event).files[0]
    path = Path(file.path)
    if damage == "changed":
        _ = path.write_bytes(b"tampered")
    elif damage == "missing":
        path.unlink()
    elif damage == "symlink":
        other = tmp_path / "private.txt"
        _ = other.write_bytes(b"PRIVATE SECRET")
        path.unlink()
        path.symlink_to(other)
    else:
        parent = path.parent
        moved = tmp_path / "redirected"
        parent.rename(moved)
        parent.symlink_to(moved, target_is_directory=True)
    result = call(
        gateway.client,
        "read_feedback_attachment",
        {"artifact_id": target, "event_id": event, "file_index": 0},
    )
    assert result["isError"] is True
    assert "PRIVATE SECRET" not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)


def test_attachment_reader_never_uses_other_events_or_targets(gateway: GatewayFixture) -> None:
    file = FileInput(
        name="reference.txt", data_base64=base64.b64encode(b"private context").decode()
    )
    event = attach(gateway, gateway.unrelated.id, file)
    for target in (gateway.unrelated.id, gateway.selected.id, DECK, "../private"):
        assert (
            call(
                gateway.client,
                "read_feedback_attachment",
                {"artifact_id": target, "event_id": event, "file_index": 0},
            )["isError"]
            is True
        )
    tools = cast(list[dict[str, object]], rpc(gateway.client, "tools/list")["tools"])
    tool = next(tool for tool in tools if tool["name"] == "read_feedback_attachment")
    assert object_value(tool["annotations"])["readOnlyHint"] is True
    context = object_value(data(call(gateway.client, "get_working_context")))
    assert object_value(context["capabilities"])["feedback_attachments"] is True


@pytest.mark.parametrize("legacy", [False, True])
def test_large_binary_attachment_is_paged_and_requires_a_delivery(
    gateway: GatewayFixture, legacy: bool
) -> None:
    target = DECK if legacy else gateway.selected.id
    content = b"\xff" * 2_000_000 + b"last bytes"
    event = attach(
        gateway,
        target,
        FileInput(name="reference.bin", data_base64=base64.b64encode(content).decode()),
    )
    args: dict[str, object] = {"artifact_id": target, "event_id": event, "file_index": 0}
    assert call(gateway.client, "read_feedback_attachment", args)["isError"] is True
    first = read(gateway, **args, encoding="base64", max_bytes=65536)
    assert base64.b64decode(cast(str, first["data_base64"])) == content[:65536]
    assert first["next_offset"] == 65536 and first["size"] == len(content)
    last = read(gateway, **args, offset=2_000_000, encoding="base64")
    assert base64.b64decode(cast(str, last["data_base64"])) == b"last bytes"
    assert last["next_offset"] is None
    with gateway.store.connect() as db:
        table = "delivery" if legacy else "artifact_delivery"
        _ = db.execute(f"DELETE FROM {table} WHERE event_id=?", (event,))
    assert call(gateway.client, "read_feedback_attachment", args)["isError"] is True


def test_reference_section_hints_handle_unicode_split_and_ambiguous_markers(
    gateway: GatewayFixture,
) -> None:
    metadata = b'<script type="application/json" id="artifact-studio-slide-reference">'
    slide = b'<main id="capture-stage" class="stage-slot">'
    interactive = b'<script id="interactive-data" type="application/json">'
    prefix = metadata + b'{"description":"' + "é🙂".encode() + b'"}</script><style>'
    # A large style payload puts the opening main marker across a verification chunk.
    content = prefix + b" " * (65530 - len(prefix)) + slide
    content += b"<main>nested content</main></main>" + interactive + b'""</script>'
    # Repeated markers must not send an agent to a guessed section.
    content += b"<p>Marker-looking reference text: " + interactive + b"</p>"
    event = attach(
        gateway,
        DECK,
        FileInput(name="reference.html", data_base64=base64.b64encode(content).decode()),
    )
    page = read(gateway, artifact_id=DECK, event_id=event, file_index=0)
    sections = object_value(page["section_offsets"])
    assert sections["metadata"] == 0
    assert sections["slide"] == 65530
    assert "interactive_html" not in sections
    assert sections["styles"] == content.index(b"<style>")
