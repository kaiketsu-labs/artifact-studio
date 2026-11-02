"""Real ASGI transport checks for the opt-in, project-scoped Copilot gateway."""

import asyncio
import json
import os
import socket
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

import httpx2
import pytest
import uvicorn
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from artifact_studio.artifact_models import ArtifactFeedbackInput, ArtifactFileInput, ArtifactView
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.copilot import (
    KEY_HEADER,
    GatewayConfig,
    create_gateway,
    initialize_key,
    read_key,
)
from artifact_studio.design_library import DesignDelete, DesignLibrary
from artifact_studio.models import Slide, SlidePatch
from artifact_studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design

HOST = "copilot.example.test"
DECK = "2026-11-02-fictional-review"


@dataclass
class GatewayFixture:
    """Own fictional sources, a private gateway credential and its real ASGI client."""

    client: TestClient
    config: GatewayConfig
    store: Store
    selected: ArtifactView
    unrelated: ArtifactView


def object_value(value: object) -> dict[str, object]:
    """Narrow decoded protocol objects without assuming unvalidated JSON shapes."""
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def list_value(value: object) -> list[object]:
    """Narrow decoded protocol arrays for assertions."""
    assert isinstance(value, list)
    return cast(list[object], value)


def rpc(
    client: TestClient, method: str, params: dict[str, object] | None = None
) -> dict[str, object]:
    """Send a real JSON-RPC request through authentication and the official MCP transport."""
    response = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    )
    assert response.status_code == 200, response.text
    envelope = object_value(response.json())
    assert "error" not in envelope, envelope
    return object_value(envelope["result"])


def call(
    client: TestClient, name: str, arguments: Mapping[str, object] | None = None
) -> dict[str, object]:
    """Call a listed tool without bypassing its public input schema."""
    return rpc(client, "tools/call", {"name": name, "arguments": arguments or {}})


def data(result: dict[str, object]) -> object:
    """Unwrap SDK structured results while failing on meaningful tool errors."""
    assert result.get("isError") is not True, result
    structured = object_value(result["structuredContent"])
    return structured.get("result", structured)


@pytest.fixture
def gateway(tmp_path: Path) -> Iterator[GatewayFixture]:
    """Bind a standalone gateway to fictional projects and one scoped legacy presentation."""
    store = Store(tmp_path / "collection", tmp_path / "state" / "review.sqlite3")
    artifacts = ArtifactStore(store)
    selected_project = artifacts.create_project("Selected fictional project", "Selected brief")
    unrelated_project = artifacts.create_project(
        "Unrelated secret project", "UNRELATED PRIVATE BRIEF"
    )
    source = tmp_path / "source"
    source.mkdir()
    _ = (source / "index.html").write_text('<h1>Fictional page</h1><script src="app.js"></script>')
    _ = (source / "app.js").write_text("document.body.dataset.example='initial'")
    selected = artifacts.register_artifact(
        selected_project.id, "Selected page", source, "index.html"
    )
    unrelated = artifacts.register_artifact(
        unrelated_project.id, "UNRELATED TITLE", source, "index.html"
    )
    make_design(store.root / TEMPLATE_ID)
    _ = store.create(DECK, "Scoped legacy deck")
    _ = artifacts.set_artifact_project(DECK, selected_project.id)
    key_file = tmp_path / "gateway.key"
    initialize_key(key_file)
    config = GatewayConfig(
        collection=store.root,
        database=store.database,
        key_file=key_file,
        public_host=HOST,
        project_id=selected_project.id,
    )
    with TestClient(create_gateway(config, store=store), base_url=f"https://{HOST}") as client:
        client.headers.update(
            {
                KEY_HEADER: read_key(key_file).decode(),
                "Accept": "application/json, text/event-stream",
            }
        )
        yield GatewayFixture(client, config, store, selected, unrelated)


def test_initialization_catalog_flat_schemas_and_bootstrap(gateway: GatewayFixture) -> None:
    """Negotiate MCP, inspect useful cloud instructions and keep unsafe tools undiscoverable."""
    initialized = rpc(
        gateway.client,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "fictional-copilot", "version": "1"},
        },
    )
    assert object_value(initialized["serverInfo"])["name"] == "Artifact Studio Copilot"
    assert "no shell" in str(initialized["instructions"])
    listed = [object_value(item) for item in list_value(rpc(gateway.client, "tools/list")["tools"])]
    names = {str(item["name"]) for item in listed}
    assert {
        "get_working_context",
        "create_artifact",
        "revise_artifact",
        "get_slide",
        "revise_slide",
        "add_slide",
        "reorder_slides",
        "capture_artifact",
    } <= names
    assert not names & {
        "register_artifact",
        "read_library_file",
        "sync_presentations",
        "start_live_review",
        "register_design",
        "set_default_design",
        "prepare_interactive_html",
    }
    for tool in listed:
        schema = object_value(tool["inputSchema"])
        assert schema["type"] == "object"
        assert "$defs" not in schema and "anyOf" not in json.dumps(schema)
        assert "source_path" not in json.dumps(schema)
    context = object_value(data(call(gateway.client, "get_working_context")))
    serialized = json.dumps(context)
    assert gateway.selected.id in serialized
    assert gateway.unrelated.id not in serialized
    assert "UNRELATED" not in serialized
    assert str(gateway.store.database) not in serialized
    assert object_value(context["project"])["id"] == gateway.selected.project_id
    projects = list_value(data(call(gateway.client, "list_projects")))
    assert [object_value(item)["id"] for item in projects] == [gateway.selected.project_id]


@pytest.mark.parametrize("method", ["initialize", "tools/list", "tools/call"])
@pytest.mark.parametrize("key", ["", "incorrect-key"])
def test_authentication_precedes_protocol(gateway: GatewayFixture, method: str, key: str) -> None:
    """Reject both absent and invalid keys for initialization, discovery and execution."""
    headers = dict(gateway.client.headers)
    headers.pop(KEY_HEADER.lower(), None)
    if key:
        headers[KEY_HEADER] = key
    gateway.client.headers.clear()
    response = gateway.client.post(
        "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": method}
    )
    assert response.status_code == 401
    assert read_key(gateway.config.key_file).decode() not in response.text


def test_boundary_hosts_origins_routes_and_limits(gateway: GatewayFixture) -> None:
    """Keep UI, APIs and files private and reject browser, host-confusion and oversized requests."""
    for path in ("/", "/api/health", "/api/hub", "/decks/example/index.html", "/mcp/"):
        assert gateway.client.get(path).status_code == 404
    for origin in ("null", "https://copilot.example.test", "https://unrelated.test"):
        assert gateway.client.post("/mcp", json={}, headers={"Origin": origin}).status_code == 403
    assert gateway.client.post("/mcp", json={}, headers={"Host": "evil.test"}).status_code == 421
    assert gateway.client.get("/mcp").status_code == 405
    assert (
        gateway.client.post(
            "/mcp", content=b"x" * (gateway.config.max_request_bytes + 1)
        ).status_code
        == 413
    )
    assert (
        gateway.client.post("/mcp", content=b"x", headers={"Content-Encoding": "gzip"}).status_code
        == 415
    )


def test_current_project_scope_is_checked_on_each_call(gateway: GatewayFixture) -> None:
    """Reject unrelated IDs and stop access immediately after local project reassignment."""
    for name, args in (
        ("get_artifact", {"artifact_id": gateway.unrelated.id}),
        ("get_working_context", {"artifact_id": gateway.unrelated.id}),
        ("list_artifacts", {"project_id": gateway.unrelated.project_id}),
        ("read_artifact_files", {"artifact_id": gateway.unrelated.id, "paths": ["index.html"]}),
        ("capture_artifact", {"artifact_id": gateway.unrelated.id}),
    ):
        result = call(gateway.client, name, args)
        assert result["isError"] is True
        assert "UNRELATED" not in json.dumps(result)
    _ = ArtifactStore(gateway.store).set_artifact_project(
        gateway.selected.id, gateway.unrelated.project_id
    )
    denied = call(gateway.client, "get_artifact", {"artifact_id": gateway.selected.id})
    assert denied["isError"] is True
    assert gateway.selected.id not in json.dumps(data(call(gateway.client, "list_artifacts")))


def test_owned_file_revision_roundtrip_and_conflict(gateway: GatewayFixture) -> None:
    """Publish arbitrary source while retaining concurrency checks and immutable reads."""
    args: dict[str, object] = {
        "artifact_id": gateway.selected.id,
        "files_json": json.dumps(
            [
                {"path": "app.js", "text": "document.body.dataset.example='changed'"},
                {"path": "styles.css", "text": "body { background: #234; }"},
                {"path": "graphic.svg", "text": '<svg xmlns="http://www.w3.org/2000/svg"/>'},
            ]
        ),
        "expected_revision": 1,
        "request_id": "cloud-revision-001",
    }
    receipt = object_value(data(call(gateway.client, "revise_artifact", args)))
    assert receipt["revision"] == 2
    assert data(call(gateway.client, "revise_artifact", args)) == receipt
    latest = list_value(
        data(
            call(
                gateway.client,
                "read_artifact_files",
                {"artifact_id": gateway.selected.id, "paths": ["app.js"]},
            )
        )
    )
    assert "changed" in str(object_value(latest[0])["text"])
    original = list_value(
        data(
            call(
                gateway.client,
                "read_artifact_files",
                {"artifact_id": gateway.selected.id, "paths": ["app.js"], "revision": 1},
            )
        )
    )
    assert "initial" in str(object_value(original[0])["text"])
    stale = call(gateway.client, "revise_artifact", args | {"request_id": "cloud-revision-stale"})
    assert stale["isError"] is True
    traversal = call(
        gateway.client,
        "read_artifact_files",
        {"artifact_id": gateway.selected.id, "paths": ["../review.sqlite3"]},
    )
    assert traversal["isError"] is True
    assert (
        len(
            list_value(
                data(
                    call(
                        gateway.client,
                        "list_artifact_revisions",
                        {"artifact_id": gateway.selected.id},
                    )
                )
            )
        )
        == 2
    )


def test_inline_create_retries_survive_lost_reply_and_later_edits(gateway: GatewayFixture) -> None:
    """Persist a creation identity and recover its initial receipt after a response is lost."""
    args: dict[str, object] = {
        "project_id": gateway.selected.project_id,
        "title": "Cloud authored",
        "entrypoint": "index.html",
        "request_id": "cloud-create-001",
        "files_json": json.dumps([{"path": "index.html", "text": "<h1>Cloud</h1>"}]),
    }
    created = object_value(data(call(gateway.client, "create_artifact", args)))
    artifact_id = str(created["id"])
    _ = ArtifactStore(gateway.store).revise_artifact(
        artifact_id,
        [ArtifactFileInput(path="index.html", text="<h1>Later</h1>")],
        1,
        "later-local-change",
    )
    assert data(call(gateway.client, "create_artifact", args)) == created
    assert call(gateway.client, "create_artifact", args | {"title": "Different"})["isError"] is True
    assert (
        len(
            [
                item
                for item in ArtifactStore(gateway.store).list_artifacts()
                if item.title == "Cloud authored"
            ]
        )
        == 1
    )
    assert (
        call(
            gateway.client, "create_artifact", args | {"project_id": gateway.unrelated.project_id}
        )["isError"]
        is True
    )


def test_batch_privacy_and_released_reply(gateway: GatewayFixture) -> None:
    """Withheld artifact comments remain unreadable and cannot be inferred through replies."""
    artifacts = ArtifactStore(gateway.store)
    _ = artifacts.set_mode(gateway.selected.id, "batch")
    comment = artifacts.add_feedback(
        gateway.selected.id,
        ArtifactFeedbackInput(
            message="WITHHELD PRIVATE REQUEST",
            content_hash=gateway.selected.content_hash,
            request_id="batch-private-001",
        ),
    )
    args: dict[str, object] = {"artifact_id": gateway.selected.id}
    for name in ("get_artifact", "get_working_context", "read_artifact_feedback"):
        assert "WITHHELD" not in json.dumps(data(call(gateway.client, name, args)))
    reply = args | {
        "event_id": comment.event_id,
        "message": "Handled",
        "content_hash": gateway.selected.content_hash,
        "request_id": "batch-reply-001",
    }
    assert call(gateway.client, "reply_to_artifact_feedback", reply)["isError"] is True
    _ = artifacts.release_batch(gateway.selected.id)
    assert "WITHHELD PRIVATE REQUEST" in json.dumps(
        data(call(gateway.client, "read_artifact_feedback", args))
    )
    assert (
        object_value(data(call(gateway.client, "reply_to_artifact_feedback", reply)))["author"]
        == "agent"
    )


def test_slide_authoring_and_legacy_file_privacy(gateway: GatewayFixture) -> None:
    """Round-trip slide changes and ordering while never allowing a legacy directory read."""
    client = gateway.client
    outline = object_value(data(call(client, "get_presentation_outline", {"deck_id": DECK})))
    first = object_value(list_value(outline["slides"])[0])
    slide_id = str(first["id"])
    source = object_value(data(call(client, "get_slide", {"deck_id": DECK, "slide_id": slide_id})))
    revised = object_value(
        data(
            call(
                client,
                "revise_slide",
                {
                    "deck_id": DECK,
                    "slide_id": slide_id,
                    "expected_hash": source["expected_hash"],
                    "request_id": "cloud-slide-revise",
                    "patch_json": SlidePatch(title="Updated safely").model_dump_json(
                        exclude_unset=True
                    ),
                },
            )
        )
    )
    assert revised["title"] == "Updated safely"
    ids = [str(object_value(item)["id"]) for item in list_value(outline["slides"])]
    addition: dict[str, object] = {
        "deck_id": DECK,
        "slide_json": Slide(
            id="cloud-new", title="New slide", html='<article class="slide"><h1>New</h1></article>'
        ).model_dump_json(),
        "request_id": "cloud-slide-add",
        "after_slide_id": ids[-1],
        "expected_slide_ids": ids,
        "expected_sections_hash": outline["sections_hash"],
    }
    _ = data(call(client, "add_slide", addition))
    _ = data(call(client, "add_slide", addition))
    order = ids + ["cloud-new"]
    moved = object_value(
        data(
            call(
                client,
                "reorder_slides",
                {"deck_id": DECK, "slide_ids": list(reversed(order)), "expected_slide_ids": order},
            )
        )
    )
    assert moved["slide_ids"] == list(reversed(order))
    assert (
        call(client, "read_artifact_files", {"artifact_id": DECK, "paths": ["index.html"]})[
            "isError"
        ]
        is True
    )
    assert call(client, "capture_artifact", {"artifact_id": DECK})["isError"] is True
    assert object_value(data(call(client, "get_artifact", {"artifact_id": DECK})))["files"] == []


def test_key_permissions_explicit_scope_and_whole_workspace(
    gateway: GatewayFixture, tmp_path: Path
) -> None:
    """Fail closed on unsafe setup while allowing deliberate whole-workspace discovery."""
    with pytest.raises(FileExistsError):
        initialize_key(gateway.config.key_file)
    gateway.config.key_file.chmod(0o644)
    with pytest.raises(ValueError, match="owner-only"):
        _ = read_key(gateway.config.key_file)
    gateway.config.key_file.chmod(0o600)
    linked = tmp_path / "linked-key"
    linked.symlink_to(gateway.config.key_file)
    with pytest.raises(OSError):
        _ = read_key(linked)
    with pytest.raises(ValueError, match="exactly one"):
        _ = replace(gateway.config, project_id="")
    with pytest.raises(ValueError, match="exactly one"):
        _ = replace(gateway.config, whole_workspace=True)
    config = replace(gateway.config, project_id="", whole_workspace=True)
    with TestClient(
        create_gateway(config, store=gateway.store), base_url=f"https://{HOST}"
    ) as client:
        client.headers.update(dict(gateway.client.headers))
        result = data(call(client, "list_artifacts"))
        assert gateway.unrelated.id in json.dumps(result)


def test_deleted_design_examples_stay_out_of_gateway_artifacts(gateway: GatewayFixture) -> None:
    library = DesignLibrary(gateway.store)
    pack = library.get("test-brand")
    library.delete(pack.id, DesignDelete(expected_version=pack.version))
    config = replace(gateway.config, project_id="", whole_workspace=True)
    with TestClient(
        create_gateway(config, store=gateway.store), base_url=f"https://{HOST}"
    ) as client:
        client.headers.update(dict(gateway.client.headers))
        assert TEMPLATE_ID not in json.dumps(data(call(client, "list_artifacts")))
        assert call(client, "get_artifact", {"artifact_id": TEMPLATE_ID}).get("isError") is True
    assert gateway.store.read(DECK).title == "Scoped legacy deck"


def test_released_feedback_does_not_leak_prior_project_membership(gateway: GatewayFixture) -> None:
    """Present retained discussion inside current scope after a human moves an artifact."""
    artifacts = ArtifactStore(gateway.store)
    _ = artifacts.add_feedback(
        gateway.unrelated.id,
        ArtifactFeedbackInput(
            message="A retained public request",
            content_hash=gateway.unrelated.content_hash,
            request_id="previous-project-comment",
        ),
    )
    _ = artifacts.set_artifact_project(gateway.unrelated.id, gateway.selected.project_id)
    result = data(
        call(gateway.client, "read_artifact_feedback", {"artifact_id": gateway.unrelated.id})
    )
    assert gateway.unrelated.project_id not in json.dumps(result)
    assert gateway.selected.project_id in json.dumps(result)


def test_legacy_batch_privacy_replies_and_symlinks(gateway: GatewayFixture) -> None:
    """Keep pending legacy discussion private and refuse linked source trees."""
    store = gateway.store
    _ = store.set_mode(DECK, "batch")
    event = store.add_feedback(DECK, None, "PRIVATE LEGACY DRAFT", "", "legacy-private-draft")
    args: dict[str, object] = {"deck_id": DECK}
    assert "PRIVATE LEGACY" not in json.dumps(data(call(gateway.client, "read_feedback", args)))
    reply = args | {"event_id": int(event), "message": "Reply", "request_id": "legacy-agent-reply"}
    assert call(gateway.client, "reply_to_feedback", reply)["isError"] is True
    _ = store.release_batch(DECK)
    assert "PRIVATE LEGACY" in json.dumps(data(call(gateway.client, "read_feedback", args)))
    assert data(call(gateway.client, "reply_to_feedback", reply))
    linked = store.folder(DECK) / "linked-source.txt"
    linked.symlink_to(store.database)
    assert call(gateway.client, "get_slide", args | {"slide_id": "cover"})["isError"] is True


def test_streamed_body_and_duplicate_credentials_are_rejected(gateway: GatewayFixture) -> None:
    """Apply the same budget to chunked bodies and reject ambiguous header credentials."""
    response = gateway.client.post("/mcp", content=iter([b"x" * 1_100_000, b"y" * 1_100_000]))
    assert response.status_code == 413
    key = read_key(gateway.config.key_file).decode()
    response = gateway.client.post(
        "/mcp", content=b"{}", headers=[(KEY_HEADER, key), (KEY_HEADER, key)]
    )
    assert response.status_code == 401


def test_existing_local_mcp_remains_independent(
    gateway: GatewayFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check local registrations without consuming the shared server's single-use lifespan."""
    from artifact_studio import app as local

    monkeypatch.setattr(local, "store", gateway.store)
    client = TestClient(local.app, base_url="http://127.0.0.1:8790")
    try:
        assert client.get("/api/health").status_code == 200
    finally:
        client.close()
    names = {item.name for item in asyncio.run(local.mcp.list_tools())}
    assert {
        "register_artifact",
        "sync_presentations",
        "start_live_review",
        "capture_slide",
    } <= names
    assert "create_artifact" not in names
    context = local.get_working_context(artifact_id=gateway.unrelated.id)
    assert object_value(context["artifact"])["id"] == gateway.unrelated.id


@pytest.mark.skipif(
    os.environ.get("ARTIFACT_STUDIO_RENDER_TESTS") != "1", reason="Opt-in local Chromium rendering"
)
def test_actual_capture_images_cross_the_authenticated_mcp_transport(
    gateway: GatewayFixture,
) -> None:
    """Deliver real artifact and full-size slide pixels through the same cloud-facing endpoint."""
    calls: list[tuple[str, dict[str, object]]] = [
        ("capture_artifact", {"artifact_id": gateway.selected.id, "width": 480, "height": 320}),
        ("capture_slide", {"deck_id": DECK, "slide_id": "cover", "stage": 0}),
    ]
    for name, arguments in calls:
        result = call(gateway.client, name, arguments)
        assert result.get("isError") is not True, result
        content = [object_value(item) for item in list_value(result["content"])]
        assert any(item["type"] == "image" and item["mimeType"] == "image/png" for item in content)
        metadata = object_value(result["structuredContent"])
        assert '"path"' not in json.dumps(metadata)
        assert str(gateway.store.root) not in json.dumps(metadata)


def test_official_sdk_over_real_loopback_http(gateway: GatewayFixture) -> None:
    """Exercise the real SDK handshake, optional GET stream and tool calls over loopback TCP."""

    async def connect() -> None:
        """Serve only this temporary fixture and always close its ephemeral listener."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            address = cast(tuple[str, int], listener.getsockname())
            config = replace(gateway.config, public_host=f"127.0.0.1:{address[1]}")
            server = uvicorn.Server(
                uvicorn.Config(
                    create_gateway(config, store=gateway.store),
                    access_log=False,
                    log_level="error",
                )
            )
            serving = asyncio.create_task(server.serve(sockets=[listener]))

            async def started() -> None:
                """Wait only until this local test server has completed its lifespan startup."""
                while not server.started:
                    if serving.done():
                        await serving
                        raise AssertionError("The fixture server stopped before startup.")
                    await asyncio.sleep(0.01)

            try:
                await asyncio.wait_for(started(), timeout=5)
                async with (
                    httpx2.AsyncClient(
                        headers={KEY_HEADER: read_key(config.key_file).decode()}
                    ) as http,
                    streamable_http_client(
                        f"http://{config.public_host}/mcp", http_client=http
                    ) as (read, write),
                    ClientSession(read, write, read_timeout_seconds=10) as session,
                ):
                    initialized = await session.initialize()
                    assert initialized.server_info.name == "Artifact Studio Copilot"
                    listed = await session.list_tools()
                    assert "get_working_context" in {item.name for item in listed.tools}
                    response = await session.call_tool(
                        "get_working_context", {"artifact_id": gateway.selected.id}
                    )
                    assert not response.is_error
                    assert response.structured_content is not None
                    assert gateway.selected.id in json.dumps(response.structured_content)
                    assert gateway.unrelated.id not in json.dumps(response.structured_content)
            finally:
                server.should_exit = True
                await asyncio.wait_for(serving, timeout=5)

    asyncio.run(connect())
