"""Measure library metadata and cover requests against an isolated fictional workspace.

Run with the project's Python environment, for example:
  uv run python scripts/benchmark_library_previews.py --source . --output /tmp/previews.json

The warm-cache phase deliberately uses seeded fixture PNGs. It measures serving and
metadata costs, not capture fidelity. Cold requests always use the production
sandbox policy. No workspace, credentials, browser profile or existing server is used.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import ProxyHandler, build_opener

MIB = 1024 * 1024


def source_fingerprint(source: Path) -> dict[str, str]:
    """Record code identities without fixture data or private local state."""
    return {
        str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
        for folder, suffixes in (("artifact_studio", {".py"}), ("web", {".js", ".css", ".html"}))
        for path in sorted((source / folder).rglob("*"))
        if path.is_file() and path.suffix in suffixes
    }


def seed_fixtures(root: Path, source: Path) -> dict[str, object]:
    """Create sources before measurement, with realistic large unrelated dependencies."""
    sys.path.insert(0, str(source))
    from artifact_studio.artifacts import ArtifactStore
    from artifact_studio.models import Slide
    from artifact_studio.storage import Store
    from tests.design_factory import TEMPLATE_ID, make_design

    collection = root / "collection"
    store = Store(collection, root / "state/review.sqlite3")
    make_design(collection / TEMPLATE_ID)
    legacy: list[dict[str, object]] = []
    for index in range(12):
        identifier = f"2026-11-06-preview-benchmark-{index:02d}"
        deck = store.create(identifier, f"Fictional library deck {index + 1}", "test-brand")
        folder = store.folder(identifier)
        large_mib = 8 if index == 0 else 18 if index == 1 else 0
        source_count = 51 if large_mib else 1
        asset_bytes = round(2.4 * MIB) if large_mib else 2048
        for asset in range(source_count):
            prefix = (
                '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900">'
                f'<rect width="1600" height="900" fill="#{60 + asset:02x}7799"/>'
                '<circle cx="800" cy="450" r="250" fill="#e8c879"/><!--'
            )
            suffix = "--></svg>"
            payload = prefix + "x" * (asset_bytes - len(prefix) - len(suffix)) + suffix
            (folder / "assets" / f"image-{asset:02d}.svg").write_text(payload)
        cover = (
            '<article class="slide" style="background:#172b4d;color:white;padding:80px">'
            f"<h1>Fictional deck {index + 1}</h1>"
            '<img src="assets/image-00.svg" alt="Fictional gold circle" '
            'style="width:480px;height:270px"><p>Library preview benchmark</p></article>'
        )
        deck.slides = [Slide(id="cover", title="Cover", html=cover)]
        if large_mib:
            # Keep the visible cover small while the authored document carries many slides.
            supporting_slides = 48
            padding = "x" * (large_mib * MIB // supporting_slides)
            deck.slides.extend(
                Slide(
                    id=f"slide-{item:02d}",
                    title=f"Supporting slide {item}",
                    html=f'<article class="slide"><h1>Supporting {item}</h1></article>',
                    interactive_html=(
                        f"<html><body><h1>Supporting {item}</h1><!--{padding}--></body></html>"
                    ),
                )
                for item in range(1, supporting_slides + 1)
            )
        store.write(identifier, deck)
        legacy.append(
            {
                "id": identifier,
                "index_bytes": (folder / "index.html").stat().st_size,
                "asset_count": source_count,
                "asset_bytes_each": asset_bytes,
                "cover_asset_count": 1,
                "slides": len(deck.slides),
            }
        )
    artifacts = ArtifactStore(store)
    project = artifacts.create_project("Fictional preview performance")
    owned: list[dict[str, object]] = []
    for index in range(3):
        bundle = root / "imports" / f"owned-{index}"
        bundle.mkdir(parents=True)
        text = (
            '<!doctype html><html><meta charset="utf-8"><style>'
            "body{margin:0;background:#f4f0e8;color:#233d32;font:42px Arial;padding:64px}"
            "</style><h1>Fictional owned artifact "
            f"{index + 1}</h1><p>High quality preview retained.</p>"
            f"<!--{'x' * (512 * 1024)}--></html>"
        )
        (bundle / "index.html").write_text(text)
        view = artifacts.register_artifact(
            project.id, f"Owned example {index + 1}", bundle, "index.html"
        )
        owned.append({"id": view.id, "source_bytes": len(text.encode())})
    result: dict[str, object] = {
        "fictional": True,
        "legacy_count": len(legacy),
        "owned_count": len(owned),
        "legacy": legacy,
        "owned": owned,
        "collection_bytes": sum(
            path.stat().st_size for path in collection.rglob("*") if path.is_file()
        ),
        "collection": str(collection),
        "database": str(store.database),
    }
    (root / "fixtures.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def process_tree_rss(pid: int) -> tuple[int, int]:
    """Sample resident bytes in one process tree, including short-lived browser children."""
    pagesize = os.sysconf("SC_PAGE_SIZE")
    # Some container kernels omit task/*/children. PPID from stat works there
    # and finds subprocesses launched by any HTTP worker thread.
    records: dict[int, tuple[int, int]] = {}
    children: dict[int, list[int]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            identifier, parent, resident = int(entry.name), int(fields[1]), int(fields[21])
            records[identifier] = (parent, resident * pagesize)
            children.setdefault(parent, []).append(identifier)
        except OSError, ValueError, IndexError:
            pass
    total = 0
    pending, visited = [pid], set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        total += records.get(current, (0, 0))[1]
        pending.extend(children.get(current, []))
    return records.get(pid, (0, 0))[1], total


def request(url: str, timeout: float = 30) -> dict[str, object]:
    """Read the real local HTTP endpoint without forwarding through an HTTP proxy."""
    started = time.monotonic()
    try:
        opener = build_opener(ProxyHandler({}))
        try:
            response = opener.open(url, timeout=timeout)
        except HTTPError as error:
            response = error
        with response:
            body = response.read()
            result: dict[str, object] = {
                "status": response.status,
                "bytes": len(body),
                "seconds": round(time.monotonic() - started, 6),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
            if response.status >= 400:
                result["message"] = body.decode(errors="replace")[:600]
            if url.endswith("/api/hub") and response.status == 200:
                result["listing"] = json.loads(body)
            return result
    except (OSError, URLError, TimeoutError) as error:
        return {
            "status": "transport_error",
            "seconds": round(time.monotonic() - started, 6),
            "message": str(error),
        }


def percentiles(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    return {
        "count": len(values),
        "p50_ms": round(ordered[math.ceil(len(ordered) * 0.5) - 1] * 1000, 3),
        "p95_ms": round(ordered[math.ceil(len(ordered) * 0.95) - 1] * 1000, 3),
        "max_ms": round(ordered[-1] * 1000, 3),
    }


def run(args: argparse.Namespace, root: Path) -> dict[str, object]:
    """Run cold/failure/warm HTTP phases while independently observing responsiveness."""
    source = args.source.resolve()
    source_hashes = source_fingerprint(source)
    fixtures = json.loads((root / "fixtures.json").read_text())
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    environment = {
        **os.environ,
        "ARTIFACT_STUDIO_COLLECTION": fixtures["collection"],
        "ARTIFACT_STUDIO_DATABASE": fixtures["database"],
        "ARTIFACT_STUDIO_UPDATE_CHECKS": "0",
        "PYTHONPATH": str(source),
    }
    phase = "startup"
    lock, done = threading.Lock(), threading.Event()
    memory: dict[str, dict[str, int]] = {}
    health: dict[str, list[dict[str, object]]] = {}
    stop_reason = ""
    phases: list[dict[str, object]] = []
    started = time.monotonic()
    logs = args.output.with_suffix(".server.log")
    with logs.open("w") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "artifact_studio.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=source,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        def sample_memory(label: str) -> int:
            own, total = process_tree_rss(process.pid)
            with lock:
                item = memory.setdefault(
                    label, {"root_peak_bytes": 0, "tree_peak_bytes": 0, "samples": 0}
                )
                item["root_peak_bytes"] = max(item["root_peak_bytes"], own)
                item["tree_peak_bytes"] = max(item["tree_peak_bytes"], total)
                item["samples"] += 1
            return total

        def probe_health(label: str) -> None:
            result = request(base + "/api/health", timeout=2)
            with lock:
                health.setdefault(label, []).append(result)

        def monitor() -> None:
            nonlocal stop_reason
            while not done.wait(0.025):
                with lock:
                    label = phase
                total = sample_memory(label)
                if (
                    total > args.rss_limit_mib * MIB
                    or time.monotonic() - started > args.timeout_seconds
                ):
                    stop_reason = "rss_limit" if total > args.rss_limit_mib * MIB else "time_limit"
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    done.set()
                if process.poll() is not None:
                    done.set()

        def health_monitor() -> None:
            while not done.wait(0.15):
                with lock:
                    label = phase
                probe_health(label)

        watcher = threading.Thread(target=monitor, daemon=True)
        observer = threading.Thread(target=health_monitor, daemon=True)
        watcher.start()
        observer.start()
        try:
            ready = False
            for _attempt in range(150):
                if done.is_set():
                    break
                if request(base + "/api/health", timeout=0.5)["status"] == 200:
                    ready = True
                    break
                time.sleep(0.1)
            if not ready:
                raise RuntimeError(
                    "Isolated server failed to become ready; inspect the server log."
                )
            phase = "idle"
            time.sleep(0.5)
            idle_rss = process_tree_rss(process.pid)
            phase = "hub_listing"
            sample_memory(phase)
            with ThreadPoolExecutor(max_workers=2) as pool:
                listed_future = pool.submit(request, base + "/api/hub", 90)
                probe_future = pool.submit(probe_health, phase)
                listed = listed_future.result()
                probe_future.result()
            sample_memory(phase)
            listing = listed.pop("listing", {})
            phases.append({"name": phase, "requests": [listed]})
            print(
                json.dumps(
                    {"phase": phase, "status": listed["status"], "seconds": listed["seconds"]}
                ),
                flush=True,
            )
            artifacts = listing.get("artifacts", [])
            if len(artifacts) != 15:
                raise RuntimeError(f"Expected 15 fictional artifacts, got {len(artifacts)}.")
            urls = [
                base
                + (
                    item.get("preview_url")
                    or "/api/hub/previews/"
                    + quote(item["id"], safe="")
                    + "?v="
                    + quote(item["content_hash"], safe="")
                )
                for item in artifacts
            ]
            for label in ("cold_cover_burst", "immediate_repeat", "seeded_warm_cache"):
                phase = label
                if label == "seeded_warm_cache":
                    from PIL import Image, ImageDraw

                    fixture_image = Image.new("RGB", (1600, 900), "#172b4d")
                    ImageDraw.Draw(fixture_image).rectangle((80, 80, 1520, 820), fill="#e8c879")
                    encoded = io.BytesIO()
                    fixture_image.save(encoded, format="PNG")
                    cache = Path(fixtures["database"]).parent / "artifact-previews"
                    cache.mkdir(exist_ok=True)
                    for item, url in zip(artifacts, urls, strict=True):
                        token = parse_qs(urlsplit(url).query)["v"][0]
                        filename = (
                            f"{item['id']}-{token}.png"
                            if item.get("preview_url")
                            else f"{item['content_hash']}.png"
                        )
                        (cache / filename).write_bytes(encoded.getvalue())
                sample_memory(label)
                phase_started = time.monotonic()
                with (
                    ThreadPoolExecutor(max_workers=1) as probe_pool,
                    ThreadPoolExecutor(max_workers=args.concurrency) as pool,
                ):
                    probe_future = probe_pool.submit(probe_health, label)
                    results = list(pool.map(request, urls))
                    probe_future.result()
                    # An asynchronous acknowledgement is not completed work. Keep
                    # the cold phase open through the renderer outcome and sample
                    # its process tree, including failures, before the repeat phase.
                    settled = list(results)
                    polling_rounds = 0
                    deadline = time.monotonic() + 30
                    while any(item["status"] == 202 for item in settled):
                        if done.is_set() or time.monotonic() >= deadline:
                            break
                        time.sleep(0.1)
                        pending = [
                            index for index, item in enumerate(settled) if item["status"] == 202
                        ]
                        updates = list(pool.map(request, [urls[index] for index in pending]))
                        for index, update in zip(pending, updates, strict=True):
                            settled[index] = update
                        polling_rounds += 1
                sample_memory(label)
                row = {
                    "name": label,
                    "elapsed_seconds": round(time.monotonic() - phase_started, 3),
                    "requests": results,
                    "settled_requests": settled,
                    "settle_polling_rounds": polling_rounds,
                    "settled": not any(item["status"] == 202 for item in settled),
                }
                phases.append(row)
                print(
                    json.dumps(
                        {
                            "phase": label,
                            "statuses": dict(Counter(str(item["status"]) for item in results)),
                            "settled_statuses": dict(
                                Counter(str(item["status"]) for item in settled)
                            ),
                            "seconds": row["elapsed_seconds"],
                        }
                    ),
                    flush=True,
                )
                if done.is_set():
                    break
            return {
                "recorded_at": datetime.now(UTC).isoformat(),
                "source": str(source),
                "commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=source, text=True
                ).strip(),
                "dirty": bool(
                    subprocess.check_output(
                        ["git", "status", "--porcelain"], cwd=source, text=True
                    ).strip()
                ),
                "source_hashes": source_hashes,
                "source_changed_during_run": source_hashes != source_fingerprint(source),
                "python": sys.version.split()[0],
                "fixture": fixtures,
                "limits": {
                    "rss_mib": args.rss_limit_mib,
                    "seconds": args.timeout_seconds,
                    "sample_interval_ms": 25,
                    "cover_concurrency": args.concurrency,
                },
                "idle_rss_bytes": {"root": idle_rss[0], "tree": idle_rss[1]},
                "stop_reason": stop_reason or None,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "phases": [
                    {
                        **row,
                        "memory": memory.get(str(row["name"]), {}),
                        "latency": percentiles(
                            [float(item["seconds"]) for item in row["requests"]]
                        ),
                        "health_latency": percentiles(
                            [float(item["seconds"]) for item in health.get(str(row["name"]), [])]
                        ),
                        "health_statuses": dict(
                            Counter(
                                str(item["status"]) for item in health.get(str(row["name"]), [])
                            )
                        ),
                    }
                    for row in phases
                ],
                "notes": [
                    "All data is fictional. Fixture creation precedes measurement.",
                    "RSS sums current resident pages for the server and its descendants; "
                    "shared pages can be counted more than once.",
                    "Production capture sandbox settings are unchanged. Failed cold captures "
                    "do not establish successful-render performance or fidelity.",
                    "Warm phase seeds identical valid PNGs to isolate serving/metadata cost; "
                    "these are not screenshots of the source artifacts.",
                    "No browser is used in this HTTP benchmark; real browser scheduling "
                    "and fidelity need separate acceptance journeys.",
                ],
            }
        finally:
            done.set()
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            watcher.join(timeout=2)
            observer.join(timeout=3)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("/tmp/library-preview-benchmark.json"))
    parser.add_argument(
        "--fixture-root",
        type=Path,
        help="Create a new fictional fixture at this path and retain it.",
    )
    parser.add_argument(
        "--seed-only", action="store_true", help="Only create fixtures; requires --fixture-root."
    )
    parser.add_argument("--rss-limit-mib", type=int, default=1536)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--concurrency", type=int, default=15)
    parser.add_argument("--_seed", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._seed:
        seed_fixtures(args._seed, args.source.resolve())
        return
    if args.seed_only and not args.fixture_root:
        parser.error("--seed-only requires --fixture-root")
    if not 1 <= args.concurrency <= 32 or not 256 <= args.rss_limit_mib <= 4096:
        parser.error("Use concurrency 1–32 and RSS limit 256–4096 MiB")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="artifact-preview-benchmark-") as temporary:
        root = args.fixture_root.resolve() if args.fixture_root else Path(temporary)
        if args.fixture_root:
            root.mkdir(parents=True, exist_ok=False)
        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--source",
                str(args.source.resolve()),
                "--_seed",
                str(root),
            ],
            check=True,
        )
        if args.seed_only:
            print(json.dumps({"fixture_root": str(root), "manifest": str(root / "fixtures.json")}))
            return
        result = run(args, root)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(
            json.dumps({"output": str(args.output), "stop_reason": result["stop_reason"]}),
            flush=True,
        )


if __name__ == "__main__":
    main()
