"""Measure scoped artifact context over real MCP using disposable fictional sources."""

import argparse
import base64
import json
import tempfile
from pathlib import Path
from typing import cast

from artifact_studio.agent_client import Client, object_value
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.design_library import DesignCreate, DesignLibrary
from artifact_studio.models import FileInput
from tests.integration.support import DECK_ID, PROJECT, running_app


def measure(
    *, baseline: bool, compact_sources: bool = False, discovery: bool = False
) -> dict[str, object]:
    """Count transport bytes without exposing sources or claiming billed token usage."""
    samples: dict[str, object] = {}

    def call(client: Client, label: str, **arguments: object) -> dict[str, object]:
        """Record a response while retaining decoded context for later calls."""
        if compact_sources:
            arguments["compact_sources"] = True
        parameters: dict[str, object] = {"name": "get_working_context", "arguments": arguments}
        response = client.rpc("tools/call", parameters)
        if response.get("isError"):
            raise RuntimeError(str(response))
        content = cast(list[dict[str, object]], response["content"])
        text = str(content[0]["text"])
        samples[label] = {
            "request_utf8_bytes": len(json.dumps(parameters, separators=(",", ":")).encode()),
            "text_utf8_bytes": len(text.encode()),
            "result_json_utf8_bytes": len(
                json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode()
            ),
        }
        return object_value(json.loads(text))

    with tempfile.TemporaryDirectory(prefix="artifact-context-audit-") as directory:
        root = Path(directory)
        with running_app(root, root / "server.log") as live:
            library = DesignLibrary(live.store)
            selected = library.create(
                DesignCreate(
                    name="Selected references",
                    files=[
                        FileInput(
                            name=f"reference-{index}.md",
                            data_base64=base64.b64encode(b"Fictional design source").decode(),
                        )
                        for index in range(5)
                    ],
                )
            )
            for index in range(12):
                _ = library.create(DesignCreate(name=f"Unrelated brand {index}"))
            service = ArtifactStore(live.store)
            project = service.create_project("Launch", "Audience: fictional product team.")
            source = root / "page"
            source.mkdir()
            _ = (source / "index.html").write_text("<h1>Fictional launch</h1>")
            artifact = service.register_artifact(project.id, "Launch page", source, "index.html")
            client = live.client()
            first = call(
                client,
                "artifact_first",
                artifact_id=artifact.id,
                design_id=selected.id,
                include_discovery=discovery,
            )
            _ = call(
                client,
                "artifact_retained",
                artifact_id=artifact.id,
                design_id=selected.id,
                include_discovery=discovery,
                known_rules_hash=first["rules_hash"],
                known_guide_hash=first["guide_hash"],
            )
            _ = call(client, "reference_only", design_id=selected.id, include_discovery=discovery)
            legacy = call(
                client, "deck_via_artifact_first", artifact_id=DECK_ID, include_discovery=discovery
            )
            nested = object_value(legacy["presentation_context"])
            retained = {} if baseline else {"known_presentation_guide_hash": nested["guide_hash"]}
            _ = call(
                client,
                "deck_via_artifact_retained",
                artifact_id=DECK_ID,
                include_discovery=discovery,
                known_rules_hash=legacy["rules_hash"],
                known_guide_hash=legacy["guide_hash"],
                **retained,
            )
            catalogue = client.rpc("tools/list", {})
            samples["tool_catalogue"] = {
                "json_utf8_bytes": len(json.dumps(catalogue, separators=(",", ":")).encode())
            }
    return {
        "measurement": (
            "UTF-8 bytes of MCP results, including text and structured copies; not billed tokens"
        ),
        "fixture": (
            "5 selected design files, 12 unrelated designs, an HTML artifact and a 2-slide deck"
        ),
        "baseline": baseline,
        "compact_sources": compact_sources,
        "discovery": discovery,
        "bootstrap_utf8_bytes": (PROJECT / "agent/bootstrap.md").stat().st_size,
        "samples": samples,
    }


def main() -> None:
    """Write aggregate measurements to an explicit local report destination."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--compact-sources", action="store_true")
    parser.add_argument("--discovery", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = measure(
        baseline=args.baseline, compact_sources=args.compact_sources, discovery=args.discovery
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _ = args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
