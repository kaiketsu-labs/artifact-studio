"""A hosted owner authors private designs through the real MCP SDK and binary transport."""

import asyncio
import base64
import hashlib
import io
import json
import os
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast
from zipfile import ZIP_STORED, ZipFile

import httpx
import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, ImageContent

from artifact_studio.copilot_tools import ScopedTools
from tests.design_factory import powerpoint_source
from tests.integration.multiplayer_fixture import MultiplayerApp, running_multiplayer_app
from tests.test_multiplayer_server import accept, grant, human


@asynccontextmanager
async def peer(app: MultiplayerApp, token: str) -> AsyncGenerator[ClientSession]:
    async with (
        httpx2.AsyncClient(
            headers={"host": "studio.example.test", "authorization": "Bearer " + token}
        ) as http,
        streamable_http_client(app.url + "/mcp", http_client=http) as (read, write),
        ClientSession(read, write, read_timeout_seconds=45) as session,
    ):
        _ = await session.initialize()
        yield session


def value(result: CallToolResult) -> dict[str, object]:
    assert not result.is_error, result
    assert result.structured_content is not None
    assert "local_path" not in str(result.structured_content)
    return result.structured_content


def connection(owner: httpx.Client, kind: str = "workspace", resource: str = "") -> dict[str, str]:
    response = owner.post(
        "/api/workspaces/default/agents",
        json={"kind": kind, "resource": resource, "name": "Fictional design author"},
    )
    response.raise_for_status()
    return cast(dict[str, str], response.json())


def test_owner_mcp_design_workflow_and_private_scope(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(
            tmp_path / "app",
            tmp_path / "server.log",
            unified=True,
            startup_timeout=45,
            personal_workspace_emails=("bob@example.test",),
        ) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
        human(app, "bob") as bob,
    ):
        accept(alice, grant(owner, "alice", "", kind="workspace"))
        own, project, guest = (
            connection(owner),
            connection(owner, "project", app.project_id),
            connection(alice),
        )

        artifact = connection(owner, "artifact", app.deck_id)
        workspaces = bob.get("/api/together/session").json()["workspaces"]
        other_workspace = next(item["id"] for item in workspaces if item["id"] != "default")
        other = bob.post(
            f"/api/workspaces/{other_workspace}/agents",
            json={"kind": "workspace", "resource": "", "name": "Other fictional owner"},
        )
        other.raise_for_status()
        foreign = cast(dict[str, str], other.json())

        async def exercise() -> None:
            async with peer(app, own["token"]) as session:
                catalog = await session.list_tools()
                assert "attach_design_reference" in {tool.name for tool in catalog.tools}
                ordinary = value(
                    await session.call_tool(
                        "get_slide", {"deck_id": app.deck_id, "slide_id": "cover"}
                    )
                )
                _ = value(
                    await session.call_tool(
                        "revise_slide",
                        {
                            "deck_id": app.deck_id,
                            "slide_id": "cover",
                            "expected_hash": ordinary["expected_hash"],
                            "request_id": "ordinary-edit",
                            "patch_json": json.dumps({"title": "Ordinary deck still editable"}),
                        },
                    )
                )
                assert (
                    value(
                        await session.call_tool(
                            "get_presentation_outline", {"deck_id": app.deck_id}
                        )
                    )["id"]
                    == app.deck_id
                )
                made = value(
                    await session.call_tool(
                        "create_design_pack",
                        {"design_id": "fictional-orbit", "name": "Fictional Orbit"},
                    )
                )
                assert "import_directory" not in made
                deck = made["folder_id"]
                with pytest.raises(ValueError, match="unavailable"):
                    ScopedTools(app.store, "", True).get_slide(cast(str, deck), "cover")
                outline = value(
                    await session.call_tool("get_presentation_outline", {"deck_id": deck})
                )
                slide = value(
                    await session.call_tool("get_slide", {"deck_id": deck, "slide_id": "cover"})
                )
                revised = value(
                    await session.call_tool(
                        "revise_slide",
                        {
                            "deck_id": deck,
                            "slide_id": "cover",
                            "expected_hash": slide["expected_hash"],
                            "request_id": "fictional-edit",
                            "patch_json": json.dumps(
                                {
                                    "html": (
                                        '<article class="slide"><h1>Fictional orbit</h1></article>'
                                    )
                                }
                            ),
                        },
                    )
                )
                assert revised["content_hash"] != slide["content_hash"]
                _ = value(
                    await session.call_tool(
                        "add_slide",
                        {
                            "deck_id": deck,
                            "slide_json": json.dumps(
                                {
                                    "id": "example",
                                    "title": "Example",
                                    "html": '<article class="slide">An original design</article>',
                                }
                            ),
                            "request_id": "fictional-add",
                            "after_slide_id": "cover",
                            "expected_slide_ids": ["cover"],
                            "expected_sections_hash": outline["sections_hash"],
                        },
                    )
                )
                theme = value(
                    await session.call_tool(
                        "get_design_file", {"design_id": "fictional-orbit", "path": "theme.css"}
                    )
                )
                _ = value(
                    await session.call_tool(
                        "write_design_file",
                        {
                            "design_id": "fictional-orbit",
                            "path": "theme.css",
                            "text": "h1 { color: #123456; }",
                            "expected_hash": theme["expected_hash"],
                        },
                    )
                )
                source = powerpoint_source("Fictional orbit master")
                arguments = {
                    "design_id": "fictional-orbit",
                    "name": "Fictional master.pptx",
                    "data_base64": base64.b64encode(source).decode(),
                }
                attached = value(await session.call_tool("attach_design_reference", arguments))
                assert (
                    value(await session.call_tool("attach_design_reference", arguments)) == attached
                )
                files = cast(list[dict[str, object]], attached["references"])
                reference = next(file for file in files if file["name"] == "Fictional master.pptx")
                assert reference["sha256"] == hashlib.sha256(source).hexdigest()
                context = value(
                    await session.call_tool("get_design_context", {"design_id": "fictional-orbit"})
                )
                assert str(reference["sha256"]) in str(context)
                content = value(
                    await session.call_tool(
                        "read_design_reference",
                        {
                            "design_id": "fictional-orbit",
                            "path": reference["path"],
                            "encoding": "base64",
                        },
                    )
                )
                assert base64.b64decode(cast(str, content["data_base64"])) == source
                for denied in (project, guest, artifact, foreign):
                    async with peer(app, denied["token"]) as other:
                        for tool, args in (
                            ("get_design_context", {"design_id": "fictional-orbit"}),
                            ("get_presentation_outline", {"deck_id": deck}),
                            ("get_slide", {"deck_id": deck, "slide_id": "cover"}),
                            ("capture_slide", {"deck_id": deck, "slide_id": "cover"}),
                            ("attach_design_reference", arguments),
                        ):
                            assert (await other.call_tool(tool, args)).is_error
                assert owner.post(f"/api/agents/{own['id']}/revoke").is_success
                # Authentication is checked on every request, even in an existing SDK session.
                denied = httpx.post(
                    app.url + "/mcp",
                    headers={
                        "host": "studio.example.test",
                        "authorization": "Bearer " + own["token"],
                    },
                    json={},
                )
                assert denied.status_code == 401

        asyncio.run(exercise())


def test_binary_reference_upload_is_bounded_owner_only_and_preserves_bytes(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(
            tmp_path / "app", tmp_path / "server.log", unified=True, startup_timeout=45
        ) as app,
        human(app, "owner") as owner,
        human(app, "alice") as alice,
    ):
        accept(alice, grant(owner, "alice", "", kind="workspace"))
        own, project, guest = (
            connection(owner),
            connection(owner, "project", app.project_id),
            connection(alice),
        )

        async def create() -> None:
            async with peer(app, own["token"]) as session:
                _ = value(
                    await session.call_tool(
                        "create_design_pack",
                        {"design_id": "binary-orbit", "name": "Fictional binary"},
                    )
                )

        asyncio.run(create())
        output = io.BytesIO(powerpoint_source("Fictional large master"))
        with ZipFile(output, "a", compression=ZIP_STORED) as archive:
            archive.writestr("ppt/media/fictional.bin", b"fictional-source" * 2_120_000)
        data = output.getvalue()
        assert 31_800_000 < len(data) < 50_000_000
        route = "/api/agent/designs/binary-orbit/references"
        headers = {
            "host": "studio.example.test",
            "authorization": "Bearer " + own["token"],
            "content-type": "application/octet-stream",
        }
        with httpx.Client(base_url=app.url, headers=headers, timeout=45) as client:
            response = client.post(route, params={"name": "Fictional large.pptx"}, content=data)
            assert response.status_code == 200, response.text
            receipt = response.json()
            assert "local_path" not in str(receipt)
            assert receipt["references"][0]["sha256"] == hashlib.sha256(data).hexdigest()
            assert (
                client.post(route, params={"name": "Fictional large.pptx"}, content=data).json()
                == receipt
            )
            for token in (project["token"], guest["token"]):
                assert (
                    client.post(
                        route,
                        params={"name": "denied.pptx"},
                        content=data,
                        headers={"authorization": "Bearer " + token},
                    ).status_code
                    == 404
                )
            assert (
                client.post(
                    route,
                    params={"name": "denied.pptx"},
                    content=data,
                    headers={"authorization": "Bearer invalid"},
                ).status_code
                == 401
            )
            assert (
                client.post(
                    route,
                    params={"name": "denied.pptx"},
                    content=data,
                    headers={"origin": "https://studio.example.test"},
                ).status_code
                == 403
            )
            assert (
                client.post(
                    route,
                    params={"name": "denied.pptx"},
                    content=data,
                    headers={"host": "wrong.example.test"},
                ).status_code
                == 400
            )
            assert (
                client.post(
                    route, params={"name": "bad.pptx"}, content=b"not an Office ZIP"
                ).status_code
                == 400
            )
            assert (
                client.post(
                    route,
                    params={"name": "bad.pptx"},
                    content=data,
                    headers={"content-type": "text/plain"},
                ).status_code
                == 415
            )
            assert client.post(route, params={"name": "empty.pptx"}, content=b"").status_code == 413
            assert (
                client.post(
                    route, params={"name": "large.bin"}, content=b"x" * 50_000_001
                ).status_code
                == 413
            )
            download = owner.get(receipt["references"][0]["url"])
            assert download.content == data
            reference = receipt["references"][0]
            query = {"path": reference["path"], "sha256": reference["sha256"]}
            file_route = "/api/agent/designs/binary-orbit/files"
            original = client.get(file_route, params=query)
            assert original.status_code == 200, original.text[:100]
            assert original.headers["content-type"] == "application/octet-stream"
            assert original.content == data
            assert client.get(file_route, params={**query, "sha256": "0" * 64}).status_code == 409
            assert client.get(file_route, params={**query, "path": "../private"}).status_code == 404
            for denied in (project, guest):
                assert (
                    client.get(
                        file_route,
                        params=query,
                        headers={"authorization": "Bearer " + denied["token"]},
                    ).status_code
                    == 404
                )
            assert (
                client.get(
                    file_route, params=query, headers={"authorization": "Bearer invalid"}
                ).status_code
                == 401
            )
            assert (
                client.get(
                    file_route, params=query, headers={"origin": "https://studio.example.test"}
                ).status_code
                == 403
            )
            assert (
                client.get(
                    file_route, params=query, headers={"host": "wrong.example.test"}
                ).status_code
                == 400
            )

            async def read_large_source_page() -> None:
                async with peer(app, own["token"]) as session:
                    page = value(
                        await session.call_tool(
                            "read_design_reference",
                            {
                                "design_id": "binary-orbit",
                                "path": reference["path"],
                                "offset": len(data) - 100,
                                "max_bytes": 100,
                                "encoding": "base64",
                            },
                        )
                    )
                    assert base64.b64decode(cast(str, page["data_base64"])) == data[-100:]
                    assert len(str(page)) < 1000

            asyncio.run(read_large_source_page())
            assert client.post("/mcp", content=b"x" * (2 * 1024 * 1024 + 1)).status_code == 413

            def revoked_stream() -> Generator[bytes]:
                yield data[:100]
                assert owner.post(f"/api/agents/{own['id']}/revoke").is_success
                yield data[100:]

            denied = client.post(route, params={"name": "revoked.pptx"}, content=revoked_stream())
            assert denied.status_code in {401, 404}
            assert not list(app.store.root.glob("*/reference/uploads/*/revoked.pptx"))


@pytest.mark.browser
@pytest.mark.skipif(
    os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in actual renderer"
)
def test_owner_captures_new_design_example_through_hosted_sdk(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(
            tmp_path / "app", tmp_path / "server.log", unified=True, startup_timeout=45
        ) as app,
        human(app, "owner") as owner,
    ):
        own = connection(owner)

        async def exercise() -> None:
            async with peer(app, own["token"]) as session:
                made = value(
                    await session.call_tool(
                        "create_design_pack",
                        {"design_id": "capture-orbit", "name": "Fictional Orbit"},
                    )
                )
                result = await session.call_tool(
                    "capture_slide", {"deck_id": made["folder_id"], "slide_id": "cover"}
                )
                assert not result.is_error, result
                ordinary = await session.call_tool(
                    "capture_slide", {"deck_id": app.deck_id, "slide_id": "cover"}
                )
                assert not ordinary.is_error, ordinary
                assert any(isinstance(item, ImageContent) for item in ordinary.content)
                image = next(item for item in result.content if isinstance(item, ImageContent))
                (tmp_path / "fictional-example.png").write_bytes(base64.b64decode(image.data))

        asyncio.run(exercise())


def test_reference_only_collection_accepts_mcp_and_binary_uploads(tmp_path: Path) -> None:
    """The same owner upload contract works for source collections without example slides."""
    with (
        running_multiplayer_app(
            tmp_path / "app", tmp_path / "server.log", unified=True, startup_timeout=45
        ) as app,
        human(app, "owner") as owner,
    ):
        own = connection(owner)
        created = owner.post("/api/design-library", json={"name": "Fictional source collection"})
        created.raise_for_status()
        design_id = created.json()["id"]

        async def attach() -> None:
            async with peer(app, own["token"]) as session:
                receipt = value(
                    await session.call_tool(
                        "attach_design_reference",
                        {
                            "design_id": design_id,
                            "name": "Guidance.txt",
                            "data_base64": base64.b64encode(b"Fictional brand guidance").decode(),
                        },
                    )
                )
                references = cast(list[dict[str, object]], receipt["references"])
                assert len(references) == 1 and references[0]["name"] == "Guidance.txt"

        asyncio.run(attach())
        data = powerpoint_source("Fictional original source")
        receipt = httpx.post(
            app.url + f"/api/agent/designs/{design_id}/references",
            params={"name": "Fictional.pptx"},
            content=data,
            headers={
                "host": "studio.example.test",
                "authorization": "Bearer " + own["token"],
                "content-type": "application/octet-stream",
            },
        )
        assert receipt.status_code == 200, receipt.text
        reference = receipt.json()["references"]
        assert len(reference) == 1 and reference[0]["sha256"] == hashlib.sha256(data).hexdigest()
        assert owner.get(reference[0]["url"]).content == data
        detail = owner.get(f"/api/design-library/{design_id}").json()
        assert {item["name"] for item in detail["files"]} >= {"Guidance.txt", "Fictional.pptx"}
