"""Compare matched fictional agent reads over real HTTP MCP.

Run: UV_CACHE_DIR=/tmp/uv-cache uv run python -m scripts.benchmark_agent_reads
Counts compact UTF-8 JSON, not wire bytes or tokens. Requires loopback access.
"""

import argparse
import json
import tempfile
from pathlib import Path

from artifact_studio.artifacts import ArtifactStore
from tests.integration.support import DECK_ID, PRIVATE_NOTE, running_app


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-only", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="agent-reads-") as directory:
        root = Path(directory)
        with running_app(root, root / "server.log") as live:
            store = ArtifactStore(live.store)
            for i in range(12):
                store.create_project(f"Fictional unrelated project {i}")
            project = store.create_project(
                "Fictional Orion research", "Audience: fictional collaborators"
            )
            source = root / "source"
            source.mkdir()
            (source / "index.html").write_text(
                "<h1>Fictional Orion</h1>\n" + "research evidence\n" * 400
            )
            (source / "app.js").write_text("console.log('fictional Orion');\n" * 200)
            artifact = store.register_artifact(
                project.id, "Fictional Orion report", source, "index.html"
            )
            for i in range(12):
                store.register_artifact(project.id, f"Unrelated report {i}", source, "index.html")
            client = live.client()
            slide_ids = [slide.id for slide in live.store.read(DECK_ID).slides]
            results = {}
            baselines = {}
            for policy in ["baseline"] if args.baseline_only else ["baseline", "selective"]:
                rows = []

                def call(label, tool, **arguments):
                    params = {"name": tool, "arguments": arguments}
                    result = client.rpc("tools/call", params)
                    assert not result.get("isError"), result
                    texts = [part["text"] for part in result["content"] if part["type"] == "text"]
                    rows.append(  # noqa: B023
                        {
                            "label": label,
                            "tool": tool,
                            "request_bytes": size(params),
                            "response_text_bytes": sum(len(t.encode()) for t in texts),
                            "response_json_bytes": size(result),
                        }
                    )
                    value = (
                        [json.loads(t) for t in texts]
                        if tool in {"list_projects", "list_artifacts", "read_artifact_files"}
                        else json.loads(texts[0])
                    )
                    return (
                        value["result"]
                        if isinstance(value, dict) and set(value) == {"result"}
                        else value
                    )

                selective = policy == "selective"
                projects = call(
                    "projects", "list_projects", **({"query": "Orion"} if selective else {})
                )
                selected_project = next(p for p in projects if p["id"] == project.id)
                items = call(
                    "artifacts",
                    "list_artifacts",
                    project_id=project.id,
                    **({"query": "Orion"} if selective else {}),
                )
                selected_artifact = next(a for a in items if a["id"] == artifact.id)
                context = call("context", "get_working_context", deck_id=DECK_ID)
                outline = call(
                    "outline",
                    "get_presentation_outline",
                    deck_id=DECK_ID,
                    include_context=True,
                    **({"source_slide_ids": slide_ids} if selective else {}),
                )
                slides = (
                    [outline["slide_sources"][sid] for sid in slide_ids]
                    if selective
                    else [
                        call("slide", "get_slide", deck_id=DECK_ID, slide_id=sid)
                        for sid in slide_ids
                    ]
                )
                assert slides[0]["notes"] == PRIVATE_NOTE
                assert all(s["html"] for s in slides)
                assert [s["id"] for s in outline["slides"]] == slide_ids
                assert all(s["expected_hash"] for s in outline["slides"])
                manifest = call("manifest", "get_artifact", artifact_id=artifact.id)
                paths = ["index.html", "app.js"]
                files = call("files", "read_artifact_files", artifact_id=artifact.id, paths=paths)
                hashes = {f["path"]: f["sha256"] for f in files}
                again = call(
                    "files_again",
                    "read_artifact_files",
                    artifact_id=artifact.id,
                    paths=paths,
                    **({"known_hashes": hashes} if selective else {}),
                )
                assert (
                    all(
                        f["unchanged"] and not f.get("text") and not f.get("data_base64")
                        for f in again
                    )
                    if selective
                    else files == again
                )
                assert (
                    manifest["revision"] == artifact.revision
                    and manifest["content_hash"] == artifact.content_hash
                )
                assert context["rules_hash"] and outline["context_included"]
                comparable = {
                    "project": selected_project,
                    "artifact": selected_artifact,
                    "outline": {k: v for k, v in outline.items() if k != "slide_sources"},
                    "sources": [
                        {
                            key: slide[key]
                            for key in (
                                "html",
                                "interactive_html",
                                "notes",
                                "notes_revision",
                                "sources",
                                "agent",
                            )
                        }
                        for slide in slides
                    ],
                    "files": files,
                    "repeat_hashes": {file["path"]: file["sha256"] for file in again},
                }
                if selective:
                    assert comparable == baselines
                else:
                    baselines = comparable
                results[policy] = {
                    "calls": len(rows),
                    "request_bytes": sum(r["request_bytes"] for r in rows),
                    "response_text_bytes": sum(r["response_text_bytes"] for r in rows),
                    "response_json_bytes": sum(r["response_json_bytes"] for r in rows),
                    "project_id": selected_project["id"],
                    "artifact_id": selected_artifact["id"],
                    "slides": slide_ids,
                    "rows": rows,
                }
            if not args.baseline_only:
                # Checks below are outside the measured workflow.
                def check(tool, **arguments):
                    return client.rpc("tools/call", {"name": tool, "arguments": arguments})

                page = check("list_artifacts", project_id=project.id, offset=0, limit=3)
                next_page = check("list_artifacts", project_id=project.id, offset=3, limit=3)
                first = [json.loads(part["text"])["id"] for part in page["content"]]
                second = [json.loads(part["text"])["id"] for part in next_page["content"]]
                assert len(first) == len(second) == 3 and not set(first) & set(second)
                missing = check(
                    "get_presentation_outline",
                    deck_id=DECK_ID,
                    source_slide_ids=["missing-fictional-slide"],
                )
                assert missing.get("isError"), missing
                stale = check(
                    "revise_artifact",
                    artifact_id=artifact.id,
                    expected_revision="0" * 64,
                    request_id="fictional-stale",
                    files=[{"path": "index.html", "text": "stale"}],
                )
                assert stale.get("isError"), stale
                assert store.get_artifact(artifact.id).content_hash == artifact.content_hash
                results["checks"] = [
                    "disjoint discovery pages",
                    "missing slide rejected",
                    "stale artifact revision rejected without mutation",
                ]
            print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
