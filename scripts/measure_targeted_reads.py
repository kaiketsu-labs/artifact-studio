"""Measure full and targeted slide reads over real MCP with a fictional bundled asset."""

import argparse
import json
import tempfile
from pathlib import Path
from typing import cast

from artifact_studio.agent_client import Client, object_value
from artifact_studio.storage import digest
from tests.integration.support import DECK_ID, RunningApp, running_app


def seed_bundled_slide(app: RunningApp) -> dict[str, object]:
    """Author a valid large local SVG through the ordinary staging and embedding workflow."""
    listing = app.call("list_presentation_assets", deck_id=DECK_ID)
    staging = Path(str(listing["import_directory"]))
    assert staging.is_dir(), "Returned staging directories must be immediately usable."
    source = staging / "bundled.svg"
    _ = source.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
        '<rect width="40" height="40" fill="#2459a9"/><!--'
        + "Fictional bundled asset padding. " * 10_000
        + "--></svg>"
    )
    _ = app.call(
        "import_presentation_asset",
        deck_id=DECK_ID,
        source_path=str(source),
        asset_path="assets/bundled.svg",
    )
    current = app.call("get_slide", deck_id=DECK_ID, slide_id="cover")
    document = (
        "<style>body{margin:80px;background:#f4f7fb;color:#173a65;font:32px Arial}"
        "img{width:80px;height:80px}button{font:inherit}</style>"
        '<h1>Bundled source remains intact</h1><img src="assets/bundled.svg" alt="Blue mark">'
        '<p id="state">Ready</p><button onclick="document.querySelector(\'#state\').textContent='
        "'Working'\">Change state</button>"
    )
    return app.call(
        "revise_slide",
        deck_id=DECK_ID,
        slide_id="cover",
        patch={"interactive_html": document},
        embed_assets=True,
        expected_hash=current["expected_hash"],
        request_id="targeted-read-bundle",
    )


def read_sample(
    client: Client, *, include_interactive_html: bool
) -> tuple[dict[str, object], dict[str, int]]:
    """Count actual text and serialized result separately, including MCP's two copies."""
    result = client.rpc(
        "tools/call",
        {
            "name": "get_slide",
            "arguments": {
                "deck_id": DECK_ID,
                "slide_id": "cover",
                "include_interactive_html": include_interactive_html,
            },
        },
    )
    assert not result.get("isError"), result
    blocks = cast(list[dict[str, object]], result["content"])
    source = str(blocks[0]["text"])
    return object_value(json.loads(source)), {
        "text_utf8_bytes": len(source.encode()),
        "result_json_utf8_bytes": len(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
        ),
    }


def assert_same_context(full: dict[str, object], targeted: dict[str, object]) -> None:
    """Prove omission is explicit and preserves every other returned field."""
    assert full["interactive_html_included"] is True
    assert targeted["interactive_html_included"] is False
    assert targeted["interactive_html"] == ""
    assert targeted["interactive_html_bytes"] == len(str(full["interactive_html"]).encode())
    assert targeted["interactive_html_hash"] == digest(str(full["interactive_html"]))
    for key in full:
        if key not in {"interactive_html", "interactive_html_included"}:
            assert targeted[key] == full[key], key


def measure() -> dict[str, object]:
    """Keep the benchmark independent of user files, fonts and external dependencies."""
    with tempfile.TemporaryDirectory(prefix="artifact-targeted-reads-") as directory:
        root = Path(directory)
        with running_app(root, root / "server.log") as app:
            _ = seed_bundled_slide(app)
            client = app.client()
            full, before = read_sample(client, include_interactive_html=True)
            targeted, after = read_sample(client, include_interactive_html=False)
            assert_same_context(full, targeted)
            return {
                "fixture": "One interactive slide with a valid, padded local SVG asset.",
                "measurement": "UTF-8 bytes, not model tokens or billed savings.",
                "verified": "All static source, notes, hashes and review fields remain identical.",
                "embedded_source_bytes": full["interactive_html_bytes"],
                "samples": {
                    key: {
                        "before": before[key],
                        "after": after[key],
                        "saved": before[key] - after[key],
                        "saved_percent": round(100 * (before[key] - after[key]) / before[key], 2),
                    }
                    for key in before
                },
            }


def main() -> None:
    """Write aggregate measurements without publishing fixture slide source."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = measure()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _ = args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
