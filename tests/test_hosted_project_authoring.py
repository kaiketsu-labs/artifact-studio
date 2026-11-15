"""Owner agents create and organize projects through the actual hosted MCP SDK."""

import asyncio
import json
from pathlib import Path
from typing import cast

import httpx

from tests.integration.multiplayer_fixture import running_multiplayer_app
from tests.test_hosted_design_authoring import connection, peer, value
from tests.test_multiplayer_server import accept, grant, human

TOOLS = {"get_project_tree", "create_project", "create_project_folder", "move_project_to_folder"}


def test_owner_creates_folder_project_and_presentation_without_browser(tmp_path: Path) -> None:
    with (
        running_multiplayer_app(
            tmp_path / "app", tmp_path / "server.log", unified=True, startup_timeout=45
        ) as app,
        human(app, "owner") as owner,
    ):
        own = connection(owner)

        async def exercise() -> None:
            async with peer(app, own["token"]) as session:
                catalog = await session.list_tools()
                assert {tool.name for tool in catalog.tools} >= TOOLS
                tree = value(await session.call_tool("get_project_tree"))
                folder_args = {
                    "title": "Fictional launches",
                    "expected_tree_hash": tree["tree_hash"],
                }
                folder = value(await session.call_tool("create_project_folder", folder_args))
                assert folder["parent_id"] is None
                assert (await session.call_tool("create_project_folder", folder_args)).is_error
                nested = value(
                    await session.call_tool(
                        "create_project_folder",
                        {
                            "title": "2027",
                            "parent_id": folder["id"],
                            "expected_tree_hash": folder["tree_hash"],
                        },
                    )
                )
                project = value(
                    await session.call_tool(
                        "create_project",
                        {"title": "Fictional orbit", "context": "Private fictional project brief"},
                    )
                )
                assert set(project) == {"id", "title", "context_hash", "tree_hash"}
                assert project["tree_hash"] != nested["tree_hash"]
                assert "Private fictional project brief" not in str(project)
                move = {
                    "project_id": project["id"],
                    "folder_id": nested["id"],
                    "expected_tree_hash": project["tree_hash"],
                }
                moved = value(await session.call_tool("move_project_to_folder", move))
                assert moved["folder_id"] == nested["id"]
                tree = value(await session.call_tool("get_project_tree"))
                nodes = cast(list[dict[str, object]], tree["nodes"])
                assert (
                    next(node for node in nodes if node["id"] == project["id"])["parent_id"]
                    == nested["id"]
                )
                assert (
                    next(node for node in nodes if node["id"] == project["id"])["title"]
                    == "Fictional orbit"
                )
                assert "Private fictional project brief" not in str(tree)
                assert "source_path" not in str(tree)
                deck_id = "2026-11-15-fictional-orbit-launch"
                made = value(
                    await session.call_tool(
                        "create_presentation",
                        {
                            "deck_id": deck_id,
                            "title": "Fictional launch",
                            "project_id": project["id"],
                            "request_id": "fictional-deck-creation",
                        },
                    )
                )
                assert made["id"] == deck_id
                slide = value(
                    await session.call_tool("get_slide", {"deck_id": deck_id, "slide_id": "cover"})
                )
                revised = value(
                    await session.call_tool(
                        "revise_slide",
                        {
                            "deck_id": deck_id,
                            "slide_id": "cover",
                            "expected_hash": slide["expected_hash"],
                            "request_id": "fictional-cover-update",
                            "patch_json": json.dumps(
                                {
                                    "html": (
                                        '<article class="slide">'
                                        "<h1>Original orbit launch</h1></article>"
                                    )
                                }
                            ),
                        },
                    )
                )
                assert revised["content_hash"] != slide["content_hash"]
                brief = value(
                    await session.call_tool("get_working_context", {"artifact_id": deck_id})
                )
                assert str(project["id"]) in str(brief)
                assert str(tmp_path) not in str(brief)
                root = value(
                    await session.call_tool(
                        "move_project_to_folder",
                        {"project_id": project["id"], "expected_tree_hash": tree["tree_hash"]},
                    )
                )
                assert root["folder_id"] is None
                invalid = await session.call_tool(
                    "create_project_folder",
                    {
                        "title": "Invalid child",
                        "parent_id": project["id"],
                        "expected_tree_hash": root["tree_hash"],
                    },
                )
                assert invalid.is_error
                assert (
                    value(await session.call_tool("get_project_tree"))["tree_hash"]
                    == root["tree_hash"]
                )

        asyncio.run(exercise())


def test_project_tree_tools_keep_owner_scope_and_revocation(tmp_path: Path) -> None:
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
        own = connection(owner)
        denied_tokens = [
            connection(owner, "project", app.project_id)["token"],
            connection(owner, "artifact", app.deck_id)["token"],
            connection(alice)["token"],
        ]
        workspaces = bob.get("/api/together/session").json()["workspaces"]
        other_workspace = next(item["id"] for item in workspaces if item["id"] != "default")
        foreign = bob.post(
            f"/api/workspaces/{other_workspace}/agents",
            json={"kind": "workspace", "resource": "", "name": "Other fictional owner"},
        )
        foreign.raise_for_status()

        async def exercise() -> None:
            async with peer(app, own["token"]) as session:
                tree = value(await session.call_tool("get_project_tree"))
                folder = value(
                    await session.call_tool(
                        "create_project_folder",
                        {"title": "Private folder", "expected_tree_hash": tree["tree_hash"]},
                    )
                )
                for token in denied_tokens:
                    async with peer(app, token) as denied:
                        for tool, args in (
                            ("get_project_tree", {}),
                            ("create_project", {"title": "Denied"}),
                            (
                                "create_project_folder",
                                {"title": "Denied", "expected_tree_hash": folder["tree_hash"]},
                            ),
                            (
                                "move_project_to_folder",
                                {
                                    "project_id": app.project_id,
                                    "folder_id": folder["id"],
                                    "expected_tree_hash": folder["tree_hash"],
                                },
                            ),
                        ):
                            assert (await denied.call_tool(tool, args)).is_error
                assert (
                    value(await session.call_tool("get_project_tree"))["tree_hash"]
                    == folder["tree_hash"]
                )
                async with peer(app, cast(str, foreign.json()["token"])) as other:
                    private = value(
                        await other.call_tool("create_project", {"title": "Other owner's project"})
                    )
                    assert (
                        await other.call_tool(
                            "create_project_folder",
                            {
                                "title": "Denied cross-workspace",
                                "parent_id": folder["id"],
                                "expected_tree_hash": private["tree_hash"],
                            },
                        )
                    ).is_error
                    assert (
                        await other.call_tool(
                            "move_project_to_folder",
                            {
                                "project_id": app.project_id,
                                "expected_tree_hash": private["tree_hash"],
                            },
                        )
                    ).is_error
                    assert str(folder["id"]) not in str(
                        value(await other.call_tool("get_project_tree"))
                    )
                    assert str(private["id"]) not in str(
                        value(await session.call_tool("get_project_tree"))
                    )
                before = value(await session.call_tool("get_project_tree"))
                headers = {
                    "host": "studio.example.test",
                    "authorization": "Bearer " + own["token"],
                    "accept": "application/json, text/event-stream",
                }
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "create_project", "arguments": {"title": "Denied"}},
                }
                for extra, status in (
                    ({"authorization": "Bearer invalid"}, 401),
                    ({"origin": "https://studio.example.test"}, 403),
                    ({"host": "wrong.example.test"}, 400),
                ):
                    assert (
                        httpx.post(
                            app.url + "/mcp", json=payload, headers={**headers, **extra}
                        ).status_code
                        == status
                    )
                assert value(await session.call_tool("get_project_tree")) == before
                assert owner.post(f"/api/agents/{own['id']}/revoke").is_success
                assert (
                    httpx.post(app.url + "/mcp", json=payload, headers=headers).status_code == 401
                )

        asyncio.run(exercise())
