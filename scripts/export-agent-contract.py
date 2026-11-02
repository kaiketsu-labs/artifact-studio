"""Generate the discoverable MCP catalogue without opening a personal collection."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


async def main() -> None:
    """Initialize the server in a disposable workspace and serialize its real tool schemas."""
    with tempfile.TemporaryDirectory(prefix="artifact-contract-") as directory:
        os.environ["ARTIFACT_STUDIO_COLLECTION"] = str(Path(directory) / "collection")
        os.environ["ARTIFACT_STUDIO_DATABASE"] = str(Path(directory) / "review.sqlite3")
        sys.path.insert(0, str(PROJECT))
        from artifact_studio.app import mcp

        tools = await mcp.list_tools()
        document = {
            "generated_by": "uv run python scripts/export-agent-contract.py",
            "bootstrap": "agent/bootstrap.md",
            "tools": [tool.model_dump(mode="json", exclude_none=True) for tool in tools],
        }
        output = PROJECT / "agent/tools.json"
        _ = output.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
        print(f"{len(tools)} tools → {output}")


if __name__ == "__main__":
    asyncio.run(main())
