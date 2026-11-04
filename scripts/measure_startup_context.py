"""Compare discovery-heavy and scoped startup over MCP using fictional work only."""

import argparse
import json
import tempfile
from pathlib import Path
from typing import cast

from artifact_studio.agent_client import Client, object_value
from artifact_studio.models import DesignRequestInput
from artifact_studio.storage import digest
from tests.design_factory import TEMPLATE_ID, make_design
from tests.integration.support import DECK_ID, running_app


def sample(client: Client, **arguments: object) -> tuple[dict[str, object], dict[str, int]]:
    """Count actual MCP result serialization without retaining source content in the report."""
    result = client.rpc("tools/call", {"name": "get_working_context", "arguments": arguments})
    if result.get("isError"):
        raise RuntimeError(str(result))
    blocks = cast(list[dict[str, object]], result["content"])
    text = str(blocks[0]["text"])
    return object_value(json.loads(text)), {
        "text_utf8_bytes": len(text.encode()),
        "result_json_utf8_bytes": len(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
        ),
    }


def measure() -> dict[str, object]:
    """Verify complete scoped guidance while measuring omitted unrelated discovery."""
    with tempfile.TemporaryDirectory(prefix="artifact-startup-measurement-") as directory:
        root = Path(directory)
        with running_app(root, root / "server.log") as live:
            orange = live.store.root / "2026-11-01-orange-design"
            make_design(orange, "orange")
            _ = (orange / "PREFERENCES.md").write_text("Use orange for the unrelated brand.")
            _ = (live.store.folder(TEMPLATE_ID) / "PREFERENCES.md").write_text(
                "Use blue accents for the selected brand. Keep labels specific."
            )
            _ = live.store.set_default_design("orange")
            live.store.save_instructions(
                "Keep the financial assumptions explicit. Preserve human speaker notes.",
                digest(live.store.authoring_instructions()),
            )
            _ = live.store.post_design_request(
                DesignRequestInput(
                    message="Keep a pending request for a larger logo.",
                    request_id="measurement-design-request",
                )
            )
            for index in range(19):
                _ = live.store.create(
                    f"2026-11-01-unrelated-{index:02}",
                    f"Unrelated project {index:02}",
                    "orange",
                    f"Fictional unrelated project brief {index:02}.",
                )
            client = live.client()
            before, before_sizes = sample(client, deck_id=DECK_ID, include_discovery=True)
            after, after_sizes = sample(client, deck_id=DECK_ID)
            assert before["discovery_included"] is True
            assert after["discovery_included"] is False
            for key in (
                "authoring_instructions",
                "learned_preferences",
                "rules_hash",
                "preferences",
                "guide",
                "guide_hash",
                "selected_design_id",
                "design_references",
                "design_inbox",
            ):
                assert before[key] == after[key], key
            assert after["selected_design_id"] == "test-brand"
            assert after["default_design_id"] == "orange"
            assert "blue accents" in str(after["preferences"])
            assert "larger logo" in str(after["design_inbox"])
            assert after["designs"] == after["presentations"] == after["libraries"] == []
            rows = {
                key: {
                    "before": before_sizes[key],
                    "after": after_sizes[key],
                    "saved": before_sizes[key] - after_sizes[key],
                    "saved_percent": round(
                        100 * (before_sizes[key] - after_sizes[key]) / before_sizes[key], 1
                    ),
                }
                for key in before_sizes
            }
            return {
                "measurement": "Actual UTF-8 bytes; not billed token counts.",
                "comparison": "include_discovery=True versus the new default, same server/guides.",
                "fixture": "2 reusable designs, 20 work decks, a pending design request.",
                "verified": "Selected brand, complete rules, guide and pending requests match.",
                "samples": rows,
            }


def main() -> None:
    """Write an aggregate report containing no user workspace or source content."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = measure()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _ = args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
