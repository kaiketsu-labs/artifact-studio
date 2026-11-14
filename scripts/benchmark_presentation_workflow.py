"""Compare equivalent safe presentation workflows over isolated, real MCP transport.

Counts compact JSON UTF-8 representations, not HTTP wire bytes or billed tokens.
Run: uv run python -m scripts.benchmark_presentation_workflow --repeats 3
"""

import argparse
import hashlib
import importlib
import json
import platform
import statistics
import subprocess
import tempfile
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast

from artifact_studio.models import Slide
from tests.integration.support import DECK_ID, PRIVATE_NOTE, markup, running_app


def encoded_bytes(value: object) -> int:
    """Use the same compact, Unicode-preserving JSON convention for both policies."""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def measure(policy: str, tokenizer: Any = None) -> dict[str, Any]:
    """Run identical edits, conflicts and validation; vary only redundant context."""
    rows: list[dict[str, Any]] = []
    efficient = policy == "efficient"
    with tempfile.TemporaryDirectory(prefix="studio-workflow-") as directory:
        root = Path(directory)
        with running_app(root, root / "server.log") as live:
            for index in range(10):
                live.store.add_slide(
                    DECK_ID,
                    Slide(
                        id=f"evidence-{index}",
                        title=f"Evidence {index}",
                        html=markup(f"Evidence {index}")
                        + "<table>"
                        + "".join(
                            f"<tr><td>Fictional cohort {row}</td><td>{row + index}%</td>"
                            "<td>Illustrative measured operational adoption</td></tr>"
                            for row in range(24)
                        )
                        + "</table>",
                    ),
                )
            for index in range(20):
                live.store.create(
                    f"2026-11-01-reference-{index}", f"Reference {index}", "test-brand"
                )
            client = live.client()
            started = time.perf_counter()

            def rpc(label: str, tool: str, *, expect_error: bool = False, **arguments: Any) -> Any:
                params: dict[str, object] = {"name": tool, "arguments": arguments}
                before = time.perf_counter()
                result = client.rpc("tools/call", params)
                elapsed = (time.perf_counter() - before) * 1000
                assert bool(result.get("isError")) == expect_error, result
                texts = [
                    str(part["text"])
                    for part in cast(list[dict[str, object]], result.get("content", []))
                    if part["type"] == "text"
                ]
                rows.append(
                    {
                        "label": label,
                        "tool": tool,
                        "elapsed_ms": round(elapsed, 3),
                        "request_parameters_utf8_bytes": encoded_bytes(params),
                        "response_text_utf8_bytes": sum(len(text.encode()) for text in texts),
                        "response_mcp_json_utf8_bytes": encoded_bytes(result),
                        "expected_error": expect_error,
                        **(
                            {
                                "request_token_estimate": len(
                                    tokenizer.encode(
                                        json.dumps(
                                            params, ensure_ascii=False, separators=(",", ":")
                                        )
                                    )
                                ),
                                "response_text_token_estimate": sum(
                                    len(tokenizer.encode(text)) for text in texts
                                ),
                            }
                            if tokenizer
                            else {}
                        ),
                    }
                )
                if expect_error:
                    assert any(
                        "conflict" in text.lower() or "changed" in text.lower() for text in texts
                    ), texts
                    return None
                value = json.loads(texts[0])
                return value["result"] if set(value) == {"result"} else value

            context = rpc(
                "fresh_context",
                "get_working_context",
                deck_id=DECK_ID,
                include_discovery=not efficient,
            )
            retained = {"known_rules_hash": context["rules_hash"]} if efficient else {}
            rpc(
                "retained_context",
                "get_working_context",
                deck_id=DECK_ID,
                include_discovery=not efficient,
                **({**retained, "known_guide_hash": context["guide_hash"]} if efficient else {}),
            )
            guide = rpc("fresh_guide", "get_authoring_guide", topic="live_review")
            rpc(
                "retained_guide",
                "get_authoring_guide",
                topic="live_review",
                **({"known_hash": guide["hash"]} if efficient else {}),
            )
            rpc(
                "brief",
                "get_presentation_outline",
                deck_id=DECK_ID,
                include_context=True,
                **retained,
            )
            untouched = {
                slide.id: slide.model_dump_json() for slide in live.store.read(DECK_ID).slides[1:]
            }
            current = live.store.read(DECK_ID).slides[0]
            for index in range(12):
                live.store.add_feedback(
                    DECK_ID,
                    current.id,
                    f"Fictional reviewer evidence {index}",
                    live.store.content_hash(DECK_ID, current),
                    f"feedback-{index}",
                )
            feed = rpc("feedback_first", "read_feedback", deck_id=DECK_ID, **retained)
            assert len(feed["events"]) == 12
            assert len({event["event_id"] for event in feed["events"]}) == 12
            poll = rpc(
                "feedback_poll",
                "read_feedback",
                deck_id=DECK_ID,
                after_delivery_id=feed["next_cursor"] if efficient else 0,
                **(
                    {**retained, "known_design_requests_hash": feed["design_requests_hash"]}
                    if efficient
                    else {}
                ),
            )
            assert len(poll["events"]) == (0 if efficient else 12)
            if not efficient:
                rpc("broad_read", "get_presentation", deck_id=DECK_ID)
            source = rpc("source", "get_slide", deck_id=DECK_ID, slide_id=current.id)
            human_note = PRIVATE_NOTE + " Human added evidence provenance."
            live.store.save_notes(DECK_ID, current.id, human_note, current.notes)
            patch = {"title": "Evidence-led decision", "html": markup("Evidence-led decision")}
            rpc(
                "stale_rejected",
                "revise_slide",
                expect_error=True,
                deck_id=DECK_ID,
                slide_id=current.id,
                expected_hash=source["expected_hash"],
                request_id="fictional-stale",
                patch=patch,
                include_content=not efficient,
            )
            fresh = rpc("rebase_source", "get_slide", deck_id=DECK_ID, slide_id=current.id)
            assert fresh["notes"] == human_note
            assert fresh["title"] == source["title"] and fresh["html"] == source["html"]
            receipt = rpc(
                "revision",
                "revise_slide",
                deck_id=DECK_ID,
                slide_id=current.id,
                expected_hash=fresh["expected_hash"],
                request_id="fictional-rebase",
                patch=patch,
                include_content=not efficient,
            )
            assert receipt["title"] == patch["title"]
            final = rpc("validation_source", "get_slide", deck_id=DECK_ID, slide_id=current.id)
            outline = rpc(
                "validation_outline", "get_presentation_outline", deck_id=DECK_ID, **retained
            )
            assert final["notes"] == human_note and final["html"] == patch["html"]
            assert len(live.store.read(DECK_ID).slides) == 12
            assert untouched == {
                slide.id: slide.model_dump_json() for slide in live.store.read(DECK_ID).slides[1:]
            }
            assert outline and final["title"] == patch["title"]
            catalog_before = time.perf_counter()
            catalog = client.rpc("tools/list", {})
            catalog_ms = (time.perf_counter() - catalog_before) * 1000
            return {
                "policy": policy,
                "final_state": {
                    "title": final["title"],
                    "html": final["html"],
                    "notes": final["notes"],
                },
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "samples": rows,
                "totals": {
                    key: sum(row[key] for row in rows)
                    for key in (
                        "request_parameters_utf8_bytes",
                        "response_text_utf8_bytes",
                        "response_mcp_json_utf8_bytes",
                    )
                },
                "tool_catalog": {
                    "mcp_json_utf8_bytes": encoded_bytes(catalog),
                    "elapsed_ms": round(catalog_ms, 3),
                    **(
                        {
                            "json_token_estimate": len(
                                tokenizer.encode(
                                    json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
                                )
                            )
                        }
                        if tokenizer
                        else {}
                    ),
                },
                "assertions": [
                    "stale revision rejected",
                    "human notes preserved after rebase",
                    "requested title and HTML saved",
                    "12 slides preserved",
                    "full source and brief inspected; final source and outline validated",
                ],
            }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokenizer", choices=["o200k_base"])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument(
        "--output", type=Path, default=Path("test-results/presentation-workflow.json")
    )
    args = parser.parse_args()
    if args.repeats < 1 or args.warmups < 0:
        parser.error("repeats must be positive; warmups must be nonnegative")
    try:
        tokenizer = (
            importlib.import_module("tiktoken").get_encoding(args.tokenizer)
            if args.tokenizer
            else None
        )
    except (ImportError, OSError, ValueError) as error:
        parser.exit(2, f"Tokenizer unavailable: {error}. Omit --tokenizer for byte measurements.\n")
    for _ in range(args.warmups):
        for policy in ("baseline", "efficient"):
            measure(policy, tokenizer)
    runs = [
        measure(policy, tokenizer)
        for repeat in range(args.repeats)
        for policy in (("baseline", "efficient") if repeat % 2 == 0 else ("efficient", "baseline"))
    ]
    assert all(run["final_state"] == runs[0]["final_state"] for run in runs)
    result = {
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "git_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True)),
        "elapsed_distribution_ms": {
            policy: {
                "min": min(run["elapsed_ms"] for run in runs if run["policy"] == policy),
                "max": max(run["elapsed_ms"] for run in runs if run["policy"] == policy),
                "call_min": min(
                    row["elapsed_ms"]
                    for run in runs
                    if run["policy"] == policy
                    for row in run["samples"]
                ),
                "call_max": max(
                    row["elapsed_ms"]
                    for run in runs
                    if run["policy"] == policy
                    for row in run["samples"]
                ),
            }
            for policy in ("baseline", "efficient")
        },
        "source_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__),
                Path("docs/AGENT-QUICKSTART.md"),
                Path("agent/guides/artifacts.md"),
                Path("agent/bootstrap.md"),
                Path("docs/LIVE-REVIEW.md"),
                Path("tests/integration/support.py"),
                Path("uv.lock"),
                *sorted(Path("artifact_studio").rglob("*.py")),
            )
            if path.exists()
        },
        "tokenizer": args.tokenizer,
        "tokenizer_status": (
            "estimated"
            if tokenizer
            else "not requested; see methodology for recorded audit download limitation"
        ),
        "measurement": "Compact JSON UTF-8 bytes; not HTTP wire bytes or billed tokens",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "mcp": version("mcp"),
            "playwright": version("playwright"),
        },
        "warmups_per_policy": args.warmups,
        "repeats_per_policy": args.repeats,
        "fixture": (
            "12 fictional slides, 20 unrelated decks, 12 released comments, concurrent notes"
        ),
        "unavailable": [
            "provider prompt/cache/billed tokens",
            "reasoning tokens",
            "image tokens",
            "model inference latency",
            "visual render/capture metrics",
        ],
        "runs": runs,
        "median_elapsed_ms": {
            policy: statistics.median(run["elapsed_ms"] for run in runs if run["policy"] == policy)
            for policy in ("baseline", "efficient")
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps({"output": str(args.output), "median_elapsed_ms": result["median_elapsed_ms"]})
    )


if __name__ == "__main__":
    main()
