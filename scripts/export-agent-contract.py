"""Generate the discoverable MCP catalogue without opening a personal collection."""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


async def main(*, hosted: bool = False) -> None:
    """Initialize the server in a disposable workspace and serialize its real tool schemas."""
    with tempfile.TemporaryDirectory(prefix="artifact-contract-") as directory:
        os.environ["ARTIFACT_STUDIO_COLLECTION"] = str(Path(directory) / "collection")
        os.environ["ARTIFACT_STUDIO_DATABASE"] = str(Path(directory) / "review.sqlite3")
        sys.path.insert(0, str(PROJECT))
        if hosted:
            from artifact_studio.deck_presence import DeckPresence
            from artifact_studio.multiplayer_access import Sharing
            from artifact_studio.multiplayer_chat import Conversations
            from artifact_studio.multiplayer_tools import MultiplayerTools
            from artifact_studio.storage import Store
            from artifact_studio.tool_server import ToolServer

            store = Store(Path(directory) / "collection", Path(directory) / "review.sqlite3")
            sharing = Sharing(Path(directory) / "sharing", store, "owner@example.test")
            mcp = ToolServer[None]("Artifact Studio Multiplayer")
            MultiplayerTools(
                sharing,
                Conversations(sharing),
                "https://studio.example.test",
                DeckPresence(sharing),
            ).register(mcp)
        else:
            from artifact_studio.app import mcp

        tools = await mcp.list_tools()
        document = {
            "generated_by": "uv run python scripts/export-agent-contract.py"
            + (" --hosted" if hosted else ""),
            "bootstrap": "agent/bootstrap.md",
            "tools": [tool.model_dump(mode="json", exclude_none=True) for tool in tools],
        }
        output = PROJECT / ("agent/hosted-tools.json" if hosted else "agent/tools.json")
        _ = output.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
        print(f"{len(tools)} tools → {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hosted", action="store_true")
    asyncio.run(main(hosted=parser.parse_args().hosted))
