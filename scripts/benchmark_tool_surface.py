"""Compare a frozen source checkout with consolidated tools over real local HTTP MCP.

Run from the candidate checkout with --baseline PATH --output PATH. Workers load
both application and test fixture helpers exclusively from their selected checkout.
No model provider is called; UTF-8 byte counts are never treated as token usage.
"""

import argparse
import base64
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast

ALIASES = {
    "list_presentations",
    "list_designs",
    "wait_for_feedback",
    "wait_for_design_requests",
    "reply_to_presentation_feedback",
    "append_presentation_context",
}
FIXTURE = {
    "seed": "tool-surface-v1",
    "released": "RELEASED_FICTIONAL_REVIEW_71b03",
    "withheld": "WITHHELD_FICTIONAL_REVIEW_71b03",
    "design_message": "Fictional design request ready to read.",
    "continuation": "Fictional continuation: retain the original notes and hidden slide.",
    "reply": "Agent acknowledged the released general request.",
    "replacement": "# Fictional current context\n\nWhole-document replacement verified.\n",
}
BYTE_FIELDS = (
    "request_http_body_utf8_bytes",
    "request_parameters_compact_json_utf8_bytes",
    "response_http_body_utf8_bytes",
    "response_text_utf8_bytes",
    "response_mcp_result_compact_json_utf8_bytes",
    "image_base64_bytes",
    "image_decoded_bytes",
)


def compact(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def fingerprint(value: object) -> str:
    return hashlib.sha256(compact(value)).hexdigest()


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep overlapping size measures separate and include every attempted exchange."""
    return {
        "http_exchanges": len(rows),
        "jsonrpc_requests": sum(row["method"] != "notifications/initialized" for row in rows),
        "tool_calls": sum(row["method"] == "tools/call" for row in rows),
        "method_counts": dict(Counter(row["method"] for row in rows)),
        "tool_errors": sum(row.get("tool_is_error") is True for row in rows),
        "jsonrpc_errors": sum(row.get("jsonrpc_error") is not None for row in rows),
        "transport_errors": sum(row.get("transport_error") is not None for row in rows),
        "expected_tool_errors": sum(row.get("expected_error", False) for row in rows),
        "summed_http_elapsed_ms": round(sum(row["elapsed_ms"] for row in rows), 3),
        **{key: sum(row.get(key, 0) for row in rows) for key in BYTE_FIELDS},
    }


def summarize(values: list[float | int]) -> dict[str, Any]:
    return {
        "observations": values,
        "median": statistics.median(values),
        "range": [min(values), max(values)],
    }


def source_info(root: Path) -> dict[str, Any]:
    def git(*arguments: str) -> bytes:
        return subprocess.check_output(["git", "-C", str(root), *arguments])

    paths = sorted(
        [
            *root.glob("artifact_studio/**/*.py"),
            root / "tests/integration/support.py",
            root / "tests/design_factory.py",
            root / "uv.lock",
            root / "docs/AUTHORING-INSTRUCTIONS.md",
            root / "docs/AGENT-GUIDE.md",
            root / "agent/README.md",
            *(path for path in (root / "runtime").iterdir() if path.is_file()),
        ]
    )
    files = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    }
    return {
        "commit": git("rev-parse", "HEAD").decode().strip(),
        "tracked_diff_sha256": hashlib.sha256(git("diff", "HEAD", "--binary")).hexdigest(),
        "loaded_source_sha256": fingerprint(files),
        "source_files": files,
    }


class Recorder:
    """Observe the existing client's real HTTP opener without replacing its RPC behavior."""

    def __init__(self, client_module: Any) -> None:
        self.module = client_module
        self.original_post = client_module.post_json
        self.original_opener = client_module.build_opener
        self.rows: list[dict[str, Any]] = []
        self.label = "initialization"
        self.phase = "initialization"
        self.expected_error = False

    def install(self) -> None:
        self.module.post_json = self.post

    def restore(self) -> None:
        self.module.post_json = self.original_post

    def post(self, url: str, payload: dict[str, Any], protocol: str, timeout: int) -> Any:
        row: dict[str, Any] = {
            "label": self.label,
            "phase": self.phase,
            "method": payload["method"],
            "tool": payload.get("params", {}).get("name"),
            "expected_error": self.expected_error,
            "request_http_body_utf8_bytes": len(json.dumps(payload).encode()),
            "request_parameters_compact_json_utf8_bytes": len(compact(payload.get("params", {}))),
            "response_http_body_utf8_bytes": 0,
            "http_status": None,
            "jsonrpc_error": None,
            "tool_is_error": None,
            "transport_error": None,
        }
        original_opener = self.original_opener

        class ObservedResponse:
            def __init__(self, response: Any) -> None:
                self.response = response
                row["http_status"] = response.status

            def __enter__(self) -> Any:
                self.response.__enter__()
                return self

            def __exit__(self, *args: Any) -> Any:
                return self.response.__exit__(*args)

            def read(self) -> bytes:
                body: bytes = self.response.read()
                row["response_http_body_utf8_bytes"] += len(body)
                return body

        class ObservedOpener:
            def __init__(self, *args: Any) -> None:
                self.opener = original_opener(*args)

            def open(self, *args: Any, **kwargs: Any) -> ObservedResponse:
                return ObservedResponse(self.opener.open(*args, **kwargs))

        started = time.perf_counter()
        self.module.build_opener = ObservedOpener
        try:
            response = self.original_post(url, payload, protocol, timeout)
            result = response.get("result", {})
            row["jsonrpc_error"] = response.get("error")
            row["tool_is_error"] = result.get("isError")
            row["response_mcp_result_compact_json_utf8_bytes"] = (
                len(compact(result)) if "result" in response else 0
            )
            content = result.get("content", [])
            row["response_text_utf8_bytes"] = sum(
                len(item["text"].encode()) for item in content if item["type"] == "text"
            )
            images = [item["data"] for item in content if item["type"] == "image"]
            row["image_base64_bytes"] = sum(len(item.encode()) for item in images)
            row["image_decoded_bytes"] = sum(len(base64.b64decode(item)) for item in images)
            return response
        except Exception as error:
            row["transport_error"] = type(error).__name__
            row["http_status"] = getattr(error, "code", row["http_status"])
            raise
        finally:
            self.module.build_opener = original_opener
            row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            self.rows.append(row)


def decode(result: dict[str, Any]) -> Any:
    value = result.get("structuredContent")
    if value is None:
        text = next(part["text"] for part in result["content"] if part["type"] == "text")
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return text
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        return mapping["result"] if set(mapping) == {"result"} else mapping
    return value


def measure(source: Path, variant: str, output: Path) -> dict[str, Any]:
    """Run one fresh fixture, preserving existing production checks and real transport."""
    sys.path.insert(0, str(source))
    import artifact_studio.agent_client as client_module
    from artifact_studio.models import DesignRequestInput
    from tests.integration import support

    assert Path(client_module.__file__).resolve().is_relative_to(source)
    assert Path(support.__file__).resolve().is_relative_to(source)
    before_source = source_info(source)
    assertions: list[dict[str, Any]] = []

    def check(name: str, condition: bool) -> None:
        assertions.append({"name": name, "passed": bool(condition)})
        if not condition:
            raise AssertionError(name)

    recorder = Recorder(client_module)
    candidate = variant == "candidate"
    setup_started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="studio-tool-surface-") as temporary:
        root = Path(temporary)
        with support.running_app(root, output.with_suffix(".server.log")) as live:
            deck_id = support.DECK_ID
            deck = live.store.read(deck_id)
            cover = deck.slides[0]
            live.store.set_slide_hidden(deck_id, "next", hidden=True, expected_hidden=False)
            original_slides = [
                slide.model_dump(mode="json") for slide in live.store.read(deck_id).slides
            ]
            with live.store.connect() as db:
                released_id = int(
                    live.store.append_feedback(
                        db, deck_id, "", "user", FIXTURE["released"], "", released=True
                    )
                )
                live.store.append_feedback(
                    db, deck_id, cover.id, "user", FIXTURE["withheld"], "", released=False
                )
            design_message = live.store.post_design_request(
                DesignRequestInput(
                    message=FIXTURE["design_message"], request_id="fictional-design-ready"
                )
            )
            setup_ms = (time.perf_counter() - setup_started) * 1000
            fixture_content_sha256 = fingerprint(
                {
                    "slides": original_slides,
                    "context": live.store.view(deck_id).context_markdown,
                    "setup": FIXTURE,
                }
            )
            recorder.install()
            try:
                session_started = time.perf_counter()
                client = live.client()
                recorder.phase, recorder.label = "discovery", "tool_catalog"
                catalog = client.rpc("tools/list", {})
                tools = cast(list[dict[str, Any]], catalog["tools"])
                names = {tool["name"] for tool in tools}
                check("exact advertised tool count", len(names) == (66 if candidate else 72))
                check(
                    "compatibility aliases advertised only on baseline",
                    not (names & ALIASES) if candidate else names >= ALIASES,
                )
                catalog_metrics = {
                    "tool_count": len(tools),
                    "tool_names": sorted(names),
                    "catalog_compact_json_utf8_bytes": len(compact(catalog)),
                    "input_schemas_compact_json_utf8_bytes": sum(
                        len(compact(tool["inputSchema"])) for tool in tools
                    ),
                    "catalog_sha256": fingerprint(catalog),
                }
                workflow_started = time.perf_counter()

                def call(
                    label: str,
                    tool: str,
                    *,
                    phase: str = "workflow",
                    expect_error: bool = False,
                    **arguments: Any,
                ) -> Any:
                    recorder.phase, recorder.label = phase, label
                    recorder.expected_error = expect_error
                    result = client.rpc("tools/call", {"name": tool, "arguments": arguments})
                    check(
                        f"{label}: expected tool status",
                        bool(result.get("isError")) == expect_error,
                    )
                    check(
                        f"{label}: withheld feedback remains private",
                        FIXTURE["withheld"].encode() not in compact(result),
                    )
                    if expect_error:
                        check(
                            f"{label}: conflict explained",
                            b"changed" in compact(result).lower()
                            or b"conflict" in compact(result).lower(),
                        )
                        return None
                    return decode(result)

                presentation_filter: dict[str, Any] = {"kind": "presentation"} if candidate else {}
                decks = call(
                    "discover_presentations",
                    "list_artifacts" if candidate else "list_presentations",
                    **presentation_filter,
                )
                actual_decks = sorted((item["id"], item["title"]) for item in decks)
                check(
                    "presentation discovery preserves every editable ID/title",
                    actual_decks
                    == sorted((item["id"], item["title"]) for item in live.store.list_decks()),
                )
                design_filter: dict[str, Any] = {"kind": "slide_pack"} if candidate else {}
                designs = call(
                    "discover_designs",
                    "list_design_contexts" if candidate else "list_designs",
                    **design_filter,
                )
                actual_designs = sorted(
                    (item["id"], item["name"], item["folder_id"]) for item in designs
                )
                check(
                    "design discovery preserves selectable ID/name/folder",
                    actual_designs
                    == sorted(
                        (item.id, item.name, item.folder_id) for item in live.store.designs()
                    ),
                )
                if candidate:
                    check(
                        "design discovery filters slide packs",
                        all(item["kind"] == "slide_pack" for item in designs),
                    )
                full_learning = call(
                    "learning_full_presentation",
                    "get_presentation",
                    phase="learning",
                    deck_id=deck_id,
                )
                outline = call(
                    "read_context",
                    "get_presentation_outline",
                    phase="learning",
                    deck_id=deck_id,
                    include_context=True,
                )
                check(
                    "learning outline preserves full context and concurrency hash",
                    outline["context_markdown"] == full_learning["context_markdown"]
                    and outline["context_hash"] == full_learning["context_hash"],
                )
                wait_arguments: dict[str, Any] = (
                    {"wait_seconds": 1} if candidate else {"timeout_seconds": 1}
                )
                feed = call(
                    "wait_released_feedback",
                    "read_feedback" if candidate else "wait_for_feedback",
                    deck_id=deck_id,
                    **wait_arguments,
                )
                check(
                    "released feedback delivered with original anchor",
                    len(feed["events"]) == 1
                    and feed["events"][0]["message"] == FIXTURE["released"]
                    and feed["events"][0]["event_id"] == released_id,
                )
                inbox = call(
                    "wait_design_message",
                    "read_design_requests" if candidate else "wait_for_design_requests",
                    **wait_arguments,
                )
                check(
                    "ready design message delivered once",
                    len(inbox["messages"]) == 1
                    and inbox["messages"][0]["event_id"] == design_message.event_id
                    and inbox["messages"][0]["message"] == FIXTURE["design_message"],
                )
                reply_arguments: dict[str, Any] = {
                    "deck_id": deck_id,
                    "message": FIXTURE["reply"],
                    "request_id": "fictional-general-reply",
                    "event_id": released_id,
                }
                reply_tool = "reply_to_feedback" if candidate else "reply_to_presentation_feedback"
                call("general_reply", reply_tool, **reply_arguments)
                call("general_reply_retry", reply_tool, **reply_arguments)
                replies = [item for item in live.store.comments(deck_id) if item.author == "agent"]
                check(
                    "general reply is agent-authored, anchored, and retry-idempotent",
                    len(replies) == 1
                    and replies[0].slide_id is None
                    and replies[0].reply_to == released_id
                    and replies[0].message == FIXTURE["reply"],
                )
                append_tool = (
                    "update_presentation_context" if candidate else "append_presentation_context"
                )
                append_arguments: dict[str, Any] = {
                    "deck_id": deck_id,
                    "markdown": FIXTURE["continuation"],
                    "expected_hash": outline["context_hash"],
                    **({"mode": "append"} if candidate else {}),
                }
                receipt = call("append_context", append_tool, **append_arguments)
                retry = call("append_context_retry", append_tool, **append_arguments)
                context = live.store.view(deck_id).context_markdown
                check(
                    "append exact retry preserves receipt and one addition",
                    retry == receipt
                    and context.count(FIXTURE["continuation"]) == 1
                    and context.startswith(outline["context_markdown"]),
                )
                call(
                    "stale_append_rejected",
                    append_tool,
                    expect_error=True,
                    **{**append_arguments, "markdown": "Different stale continuation."},
                )
                call(
                    "stale_whole_write_rejected",
                    "update_presentation_context",
                    expect_error=True,
                    deck_id=deck_id,
                    markdown="Stale whole replacement.",
                    expected_hash=outline["context_hash"],
                )
                check(
                    "rejected stale writes leave context intact",
                    live.store.view(deck_id).context_markdown == context,
                )
                fresh = call(
                    "fresh_context",
                    "get_presentation_outline",
                    deck_id=deck_id,
                    include_context=True,
                )
                call(
                    "current_whole_write",
                    "update_presentation_context",
                    deck_id=deck_id,
                    markdown=FIXTURE["replacement"],
                    expected_hash=fresh["context_hash"],
                )
                final = call(
                    "verify_full_presentation",
                    "get_presentation",
                    phase="verification",
                    deck_id=deck_id,
                )
                check(
                    "whole-write default still replaces complete context",
                    final["context_markdown"] == FIXTURE["replacement"],
                )
                final_slides = [
                    slide.model_dump(mode="json") for slide in live.store.read(deck_id).slides
                ]
                check(
                    "original content, notes, hidden state and revision anchors preserved",
                    final_slides == original_slides,
                )
                check(
                    "speaker note marker preserved",
                    final_slides[0]["notes"] == support.PRIVATE_NOTE,
                )
                check("hidden slide remains hidden", final_slides[1]["hidden"] is True)
                final_feed = call(
                    "verify_feedback_cursor",
                    "read_feedback",
                    phase="verification",
                    deck_id=deck_id,
                    after_delivery_id=feed["next_cursor"],
                )
                check(
                    "feedback cursor yields no duplicate released comment",
                    all(item["event_id"] != released_id for item in final_feed["events"]),
                )
                check(
                    "withheld comment remains unreleased in storage",
                    live.store.workflow(deck_id).pending == 1,
                )
                check(
                    "waiting lease cleared after ready feedback",
                    live.store.workflow(deck_id).agent_waiting is False,
                )
                workflow_ms = (time.perf_counter() - workflow_started) * 1000
                session_ms = (time.perf_counter() - session_started) * 1000
                final_state = {
                    "presentations": actual_decks,
                    "designs": actual_designs,
                    "slides_sha256": fingerprint(final_slides),
                    "context": final["context_markdown"],
                    "reply_count": len(replies),
                    "withheld_count": live.store.workflow(deck_id).pending,
                }
            finally:
                recorder.restore()
                output.with_suffix(".trace.json").write_text(
                    json.dumps(recorder.rows, indent=2) + "\n"
                )
    after_source = source_info(source)
    check(
        "application source unchanged during worker",
        before_source["loaded_source_sha256"] == after_source["loaded_source_sha256"],
    )
    return {
        "variant": variant,
        "source": before_source,
        "loaded_modules": {
            "client": str(Path(client_module.__file__).relative_to(source)),
            "fixture": str(Path(support.__file__).relative_to(source)),
        },
        "fixture_content_sha256": fixture_content_sha256,
        "setup_elapsed_ms": round(setup_ms, 3),
        "workflow_elapsed_ms": round(workflow_ms, 3),
        "session_elapsed_ms": round(session_ms, 3),
        "idle_wait_ms": 0,
        "catalog": catalog_metrics,
        "learning_read_comparison": {
            row["tool"]: {key: row[key] for key in BYTE_FIELDS if key in row}
            for row in recorder.rows
            if row["phase"] == "learning"
        },
        "transport": aggregate(recorder.rows),
        "phases": {
            phase: aggregate([row for row in recorder.rows if row["phase"] == phase])
            for phase in {row["phase"] for row in recorder.rows}
        },
        "samples": recorder.rows,
        "correctness": assertions,
        "final_state": final_state,
        "final_state_sha256": fingerprint(final_state),
    }


def report(metrics: dict[str, Any]) -> str:
    sources = {
        variant: next(trial["source"] for trial in metrics["trials"] if trial["variant"] == variant)
        for variant in ("baseline", "candidate")
    }
    lines = [
        "# Matched local MCP tool-surface comparison",
        "",
        "This is a fixed source comparison using disposable fictional fixtures and real "
        "loopback HTTP MCP. It does not execute autonomous personas, browser review, model "
        "inference, or image capture.",
        "",
        *(
            f"{variant.capitalize()} commit: `{sources[variant]['commit']}`. "
            f"Loaded source SHA-256: `{sources[variant]['loaded_source_sha256']}`."
            for variant in ("baseline", "candidate")
        ),
        "",
        f"{metrics['scenario']['warmups_per_source']} warmup per source and "
        f"{metrics['scenario']['repeats']} alternating measured pairs use the same script, "
        "interpreter, fixture, and semantic checks. Setup injects fictional human comments "
        "(one released, one withheld), a design message, and a hidden slide through the "
        "fixture Store API. Those are labeled test setup, not autonomous UI actions.",
        "",
        "| Measured quantity (median; range) | Baseline | Candidate |",
        "| --- | ---: | ---: |",
    ]
    for key, label in (
        ("tool_count", "Advertised tool count"),
        ("catalog_compact_json_utf8_bytes", "Catalog compact JSON UTF-8 bytes"),
        ("input_schemas_compact_json_utf8_bytes", "Input schemas compact JSON UTF-8 bytes"),
        ("operation_request_http_body_utf8_bytes", "Operation request HTTP body UTF-8 bytes"),
        ("operation_response_text_utf8_bytes", "Operation response text UTF-8 bytes"),
        (
            "operation_response_mcp_result_compact_json_utf8_bytes",
            "Operation compact MCP result UTF-8 bytes",
        ),
        ("workflow_elapsed_ms", "Workflow elapsed including learning (ms)"),
    ):
        cells: list[str] = []
        for variant in ("baseline", "candidate"):
            value = metrics["summary"][variant][key]
            cells.append(f"{value['median']:,} ({value['range'][0]:,}–{value['range'][1]:,})")
        lines.append(f"| {label} | {' | '.join(cells)} |")
    lines.extend(
        [
            "",
            "The separate learning probe reads the full presentation and context-bearing "
            "outline on the same pre-edit fixture. Both retain identical context and hash; "
            "the outline omits slide bodies, so the saving applies to discovery/context "
            "learning and does not replace a source read before editing.",
            "",
            "| Learning response text (UTF-8 bytes, median) | Baseline | Candidate |",
            "| --- | ---: | ---: |",
            *(
                f"| {tool} | "
                + " | ".join(
                    str(
                        metrics["summary"][variant]["learning"][tool]["response_text_utf8_bytes"][
                            "median"
                        ]
                    )
                    for variant in ("baseline", "candidate")
                )
                + " |"
                for tool in ("get_presentation", "get_presentation_outline")
            ),
            "",
            "Every trial records initialization and initialized notification, one tools/list "
            "schema read, all operation calls including retries and intentional conflicts, "
            "actual HTTP status/body sizes, JSON-RPC errors and tool isError separately. "
            "Catalog and input-schema sizes overlap; response text and complete serialized "
            "MCP result sizes overlap. Do not add overlapping measures. HTTP headers/framing "
            "are excluded. Operation byte totals exclude initialization, catalog and the "
            "two learning reads. Each trial contains 19 HTTP exchanges: initialize, "
            "initialized notification, one catalog read, and 16 tool calls. Two tool errors "
            "are expected stale-write rejections; transport and JSON-RPC errors are zero.",
            "",
            "All semantic checks passed on both sources: discovery IDs preserved despite "
            "richer metadata; released feedback delivered and withheld feedback private; "
            "queued design message delivered; general reply agent-authored, anchored and "
            "idempotent; append retry adds once; distinct stale append and whole-write "
            "rejected; current whole-write still replaces; original source, speaker notes, "
            "hidden-slide state and revisions preserved. Final semantic fixture fingerprints "
            "agree across every measured trial.",
            "",
            "Timings are descriptive observations on a shared machine, with all trials "
            "retained; no speedup or reliable percentile is claimed. Ready messages exercise "
            "the wait API without idle polling. This script does not test delayed delivery, "
            "hosted grants, visual quality, or installed native clients. Fixtures are reset "
            "for each run. Provider/model/token usage and token estimates are null because "
            "no provider usage interface or tokenizer is used. No billed-token saving is inferred.",
            "",
            "metrics.json contains per-trial observations, byte scopes, source/fixture/script "
            "fingerprints, individual assertions, and initialization/discovery/workflow/"
            "verification aggregates. Individual worker JSON/trace files and server logs "
            "are local evidence beside this report.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--worker", choices=["baseline", "candidate"])
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    if args.worker:
        result = measure(args.source.resolve(), args.worker, args.output)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        return
    if args.baseline is None or args.warmups < 1 or args.repeats < 3:
        parser.error("provide --baseline, at least one warmup and at least three repeats")
    args.output.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve()
    script_sha256 = hashlib.sha256(script.read_bytes()).hexdigest()
    trials: list[dict[str, Any]] = []
    warmups: list[dict[str, Any]] = []
    roots = {"baseline": args.baseline.resolve(), "candidate": args.candidate.resolve()}
    for index in range(args.warmups + args.repeats):
        is_warmup = index < args.warmups
        pair = index if is_warmup else index - args.warmups
        order = ("baseline", "candidate") if pair % 2 == 0 else ("candidate", "baseline")
        for position, variant in enumerate(order):
            run_id = f"{'warmup' if is_warmup else 'trial'}-{pair + 1}-{variant}"
            target = args.output.resolve() / f"{run_id}.json"
            environment = {**os.environ, "PYTHONPATH": str(roots[variant])}
            subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--worker",
                    variant,
                    "--source",
                    str(roots[variant]),
                    "--output",
                    str(target),
                ],
                cwd=roots[variant],
                env=environment,
                check=True,
            )
            result = json.loads(target.read_text())
            result.update({"run_id": run_id, "pair": pair + 1, "position": position + 1})
            (warmups if is_warmup else trials).append(result)
            print(
                f"{run_id}: {result['catalog']['tool_count']} tools, "
                f"{result['workflow_elapsed_ms']} ms, "
                f"{len(result['correctness'])} checks passed",
                flush=True,
            )
    assert len({trial["fixture_content_sha256"] for trial in [*warmups, *trials]}) == 1
    assert len({trial["final_state_sha256"] for trial in [*warmups, *trials]}) == 1
    for variant in roots:
        assert (
            len(
                {
                    trial["source"]["loaded_source_sha256"]
                    for trial in [*warmups, *trials]
                    if trial["variant"] == variant
                }
            )
            == 1
        ), "Source changed between trials; repeat after edits finish."
    assert hashlib.sha256(script.read_bytes()).hexdigest() == script_sha256
    summary: dict[str, Any] = {}
    for variant in roots:
        selected = [trial for trial in trials if trial["variant"] == variant]
        result = {
            key: summarize([trial["catalog"][key] for trial in selected])
            for key in (
                "tool_count",
                "catalog_compact_json_utf8_bytes",
                "input_schemas_compact_json_utf8_bytes",
            )
        }
        for key in BYTE_FIELDS:
            result[f"operation_{key}"] = summarize(
                [
                    sum(trial["phases"][phase][key] for phase in ("workflow", "verification"))
                    for trial in selected
                ]
            )
        result["workflow_elapsed_ms"] = summarize(
            [trial["workflow_elapsed_ms"] for trial in selected]
        )
        result["learning"] = {
            tool: {
                key: summarize([trial["learning_read_comparison"][tool][key] for trial in selected])
                for key in BYTE_FIELDS
            }
            for tool in ("get_presentation", "get_presentation_outline")
        }
        summary[variant] = result
    metrics = {
        "run_id": args.output.name,
        "experiment": "matched source comparison, local MCP tool surface",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "mcp": version("mcp"),
            "browser": None,
            "mode": "local HTTP MCP",
            "concurrency": "one worker/server at a time; shared runner otherwise uncontrolled",
        },
        "scenario": {
            "warmups_per_source": args.warmups,
            "repeats": args.repeats,
            "fixture": FIXTURE,
            "fixture_seed_sha256": fingerprint(FIXTURE),
            "setup": "test-only Store fixture writes; not autonomous human/persona UI actions",
            "script_sha256": script_sha256,
        },
        "measurement_scope": {
            "http_bodies": "actual UTF-8 bytes; headers/framing excluded",
            "catalog": "compact JSON result with schemas; measured separately from operations",
            "input_schemas": "sum of compact inputSchema JSON only; subset of catalog",
            "results": "compact MCP result JSON; overlaps response text and actual HTTP body",
            "timing": "workflow timer includes learning and excludes setup, "
            "initialization and discovery; "
            "session timer includes initialization/discovery; summed HTTP times are separate",
            "idle_wait": "zero intentional waiting; released messages already queued",
            "tokens": "unavailable, never inferred from bytes",
        },
        "provider_usage": {
            "provider": None,
            "model": None,
            "input_tokens": None,
            "output_tokens": None,
            "cache_tokens": None,
            "reasoning_tokens": None,
            "reason": "No model provider calls or usage interface in this scripted benchmark.",
        },
        "token_estimates": None,
        "warmups": warmups,
        "trials": trials,
        "summary": summary,
        "correctness": {
            "all_individual_checks_passed": True,
            "identical_fixture_and_final_semantic_fingerprints": True,
        },
        "limitations": [
            "No autonomous persona or browser/visual test",
            "Local tools only; no hosted grant coverage",
            "Ready-message waits only; delayed delivery is outside this workload",
            "Three pairs on shared runner support descriptive median/range only",
            "No provider usage, tokenizer estimates or billing claims",
        ],
        "evidence": {
            "report": "report.md",
            "worker_results": [f"{trial['run_id']}.json" for trial in [*warmups, *trials]],
        },
    }
    (args.output / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    (args.output / "report.md").write_text(report(metrics))


if __name__ == "__main__":
    main()
