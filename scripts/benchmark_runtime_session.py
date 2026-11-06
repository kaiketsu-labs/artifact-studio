"""Isolated representative browser/server runtime measurements; fictional data only."""

# Select the requested checkout on sys.path before importing application or fixture code.
# ruff: noqa: E402

import argparse
import contextlib
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

parser = argparse.ArgumentParser()
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--seconds", type=float, default=15)
args = parser.parse_args()
source = args.source.resolve()


def source_hashes():
    return {
        str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
        for folder, suffixes in [("artifact_studio", {".py"}), ("web", {".js", ".css", ".html"})]
        for path in sorted((source / folder).rglob("*"))
        if path.is_file() and path.suffix in suffixes
    }


initial_source_hashes = source_hashes()
sys.path.insert(0, str(source))
from PIL import Image
from playwright.sync_api import sync_playwright

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.models import Slide, SlidePatch
from artifact_studio.storage import Store, digest
from tests.design_factory import TEMPLATE_ID, make_design

MIB = 1048576
DECK_ID = "2026-11-06-runtime-review"
OUT = args.output.parent
OUT.mkdir(parents=True, exist_ok=True)


def markup(index):
    return (
        '<article class="slide" style="background:#f4f7fb;color:#172b4d;'
        'padding:80px;font-family:Arial">'
        f'<h1 style="font-size:72px">Fictional planning {index}</h1>'
        '<p style="font-size:34px">A representative working document</p>'
        '<div style="display:flex;gap:24px"><section style="padding:25px;background:#c6e4d6">'
        "<h2>Progress</h2><p>Review the plan and agree next steps.</p></section>"
        '<section style="padding:25px;background:#ead5ad"><h2>Next</h2>'
        "<p>Keep the key message clear.</p></section></div></article>"
    )


def seed(root):
    collection = root / "collection"
    store = Store(collection, root / "state/review.sqlite3")
    make_design(collection / TEMPLATE_ID)
    for number in range(4):
        name = DECK_ID if not number else f"2026-11-06-runtime-other-{number}"
        deck = store.create(
            name,
            "Fictional runtime review" if not number else f"Fictional project {number}",
            "test-brand",
        )
        deck.slides = [
            Slide(
                id="cover" if index == 0 else f"slide-{index}",
                title=f"Planning {index + 1}",
                html=markup(index + 1),
                notes="Fictional speaker note.",
            )
            for index in range(20 if not number else 4)
        ]
        store.write(name, deck)
    for version in range(8):
        current = store.read(DECK_ID).slides[0]
        store.revise(
            DECK_ID,
            "cover",
            digest(current.model_dump_json()),
            f"runtime-history-{version}",
            SlidePatch(html=markup(f"version {version + 1}")),
        )
    anchor = next(item for item in store.view(DECK_ID).slides if item.id == "cover").content_hash
    for message in range(15):
        event = store.add_feedback(
            DECK_ID,
            "cover",
            f"Fictional review {message + 1}: can we make the conclusion clearer?",
            anchor,
            f"runtime-question-{message}",
        )
        store.add_feedback(
            DECK_ID,
            "cover",
            f"### Proposal {message + 1}\n\nKeep the main point short.\n\n"
            "- Preserve the evidence\n- State the next step\n\nThis is fictional review text.",
            anchor,
            f"runtime-answer-{message}",
            reply_to=int(event),
        )
    registry = ArtifactStore(store)
    project = registry.create_project("Fictional runtime examples")
    bundle = root / "import-html"
    bundle.mkdir()
    (bundle / "index.html").write_text(
        "<!doctype html><html><head><style>body{font:24px Arial;background:#f3f7fa;"
        "color:#163f4b;padding:32px}section{padding:28px;background:#d0e9e3}</style>"
        "</head><body><h1>Fictional project brief</h1><section><h2>Plan</h2>"
        "<p>Write, review and share the same artifact.</p></section></body></html>"
    )
    html = registry.register_artifact(project.id, "Fictional web brief", bundle, "index.html")
    picture = root / "import-image"
    picture.mkdir()
    Image.new("RGB", (1280, 720), "#e6c674").save(picture / "overview.png")
    raster = registry.register_artifact(project.id, "Fictional image", picture, "overview.png")
    return (
        store,
        html.id,
        {
            "legacy_decks": 4,
            "main_deck_slides": 20,
            "chat_messages": 30,
            "saved_cover_revisions": 9,
            "owned_artifacts": 2,
            "collection_bytes": sum(p.stat().st_size for p in collection.rglob("*") if p.is_file()),
            "html_id": html.id,
            "image_id": raster.id,
        },
    )


def proc_records():
    page = os.sysconf("SC_PAGE_SIZE")
    hz = os.sysconf("SC_CLK_TCK")
    records = {}
    children = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            row = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            pid = int(entry.name)
            parent = int(row[1])
            start = int(row[19])
            records[pid] = {
                "parent": parent,
                "rss": int(row[21]) * page,
                "cpu": (int(row[11]) + int(row[12])) / hz,
                "key": (pid, start),
            }
            children.setdefault(parent, []).append(pid)
        except OSError, ValueError, IndexError:
            pass
    return records, children


def tree(root, records, children):
    queue = [root]
    seen = set()
    found = []
    while queue:
        pid = queue.pop()
        if pid in seen:
            continue
        seen.add(pid)
        if pid in records:
            found.append(records[pid])
        queue.extend(children.get(pid, []))
    return found


def local_get(url):
    with build_opener(ProxyHandler({})).open(url, timeout=2) as response:
        return json.load(response)


with tempfile.TemporaryDirectory(prefix="artifact-runtime-profile-") as temporary:
    root = Path(temporary)
    store, html_id, manifest = seed(root)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "PYTHONPATH": str(source),
        "ARTIFACT_STUDIO_COLLECTION": str(store.root),
        "ARTIFACT_STUDIO_DATABASE": str(store.database),
        "ARTIFACT_STUDIO_UPDATE_CHECKS": "0",
    }
    log = (OUT / (args.output.stem + ".server.log")).open("w")
    server = subprocess.Popen(
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
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    roots = {"server": server.pid}
    phase = "startup"
    done = threading.Event()
    samples = {}
    previous = {}
    errors = []
    requests = []
    phases = []
    started = time.monotonic()
    watchdog = []

    def monitor():
        while not done.wait(0.1):
            records, children = proc_records()
            label = phase
            for kind, pid in list(roots.items()):
                selected = tree(pid, records, children)
                rss = sum(item["rss"] for item in selected)
                cpu = 0
                for item in selected:
                    key = (kind, *item["key"])
                    now = item["cpu"]
                    cpu += max(0, now - previous.get(key, now))
                    previous[key] = now
                value = samples.setdefault(label, {}).setdefault(
                    kind,
                    {
                        "rss_peak_bytes": 0,
                        "rss_end_bytes": 0,
                        "cpu_seconds": 0,
                        "samples": 0,
                        "processes_peak": 0,
                    },
                )
                value["rss_peak_bytes"] = max(value["rss_peak_bytes"], rss)
                value["rss_end_bytes"] = rss
                value["cpu_seconds"] += cpu
                value["samples"] += 1
                value["processes_peak"] = max(value["processes_peak"], len(selected))
            if (
                sum(
                    sum(item["rss"] for item in tree(pid, records, children))
                    for pid in roots.values()
                )
                > 1536 * MIB
                or time.monotonic() - started > 300
            ):
                watchdog.append("rss_or_time_limit")
                done.set()
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(server.pid, signal.SIGKILL)
                break

    worker = threading.Thread(target=monitor, daemon=True)
    worker.start()
    try:
        for _attempt in range(100):
            try:
                if local_get(base + "/api/health")["status"] == "ready":
                    break
            except Exception:
                time.sleep(0.1)
        else:
            raise RuntimeError("server did not start")

        def server_phase(name, seconds):
            global phase
            phase = name
            t = time.monotonic()
            time.sleep(seconds)
            phases.append(
                {
                    "name": name,
                    "seconds": round(time.monotonic() - t, 3),
                    "processes": deepcopy(samples.get(name, {})),
                }
            )
            phase = "between_phases"
            print(json.dumps({"phase": name, "completed": True}), flush=True)

        server_phase("idle_server", args.seconds)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            browser_cdp = browser.new_browser_cdp_session()
            roots["browser"] = next(
                int(item["id"])
                for item in browser_cdp.send("SystemInfo.getProcessInfo")["processInfo"]
                if item["type"] == "browser"
            )
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page.set_default_timeout(15000)
            cdp = context.new_cdp_session(page)
            cdp.send("Performance.enable")
            cdp.send("Network.enable")
            request_map = {}

            def on_request(event):
                url = event["request"]["url"]
                if not url.startswith(base):
                    return
                item = {
                    "phase": phase,
                    "path": urlsplit(url).path,
                    "method": event["request"]["method"],
                    "type": event.get("type", ""),
                    "bytes": 0,
                }
                requests.append(item)
                request_map[event["requestId"]] = item

            def on_finished(event):
                if event["requestId"] in request_map:
                    request_map[event["requestId"]]["bytes"] = event.get("encodedDataLength", 0)

            def on_response(event):
                if event["requestId"] in request_map:
                    request_map[event["requestId"]]["status"] = event["response"]["status"]

            cdp.on("Network.requestWillBeSent", on_request)
            cdp.on("Network.loadingFinished", on_finished)
            cdp.on("Network.responseReceived", on_response)
            page.on("pageerror", lambda error: errors.append(str(error)))

            def snapshot():
                metrics = {
                    item["name"]: item["value"]
                    for item in cdp.send("Performance.getMetrics")["metrics"]
                }
                dom = cdp.send("Memory.getDOMCounters")
                return {
                    "heap_used_bytes": metrics.get("JSHeapUsedSize"),
                    "heap_total_bytes": metrics.get("JSHeapTotalSize"),
                    "task_duration_seconds": metrics.get("TaskDuration"),
                    "script_duration_seconds": metrics.get("ScriptDuration"),
                    "layout_duration_seconds": metrics.get("LayoutDuration"),
                    "dom": dom,
                    "frames": len(page.frames),
                    "iframes_in_dom": page.locator("iframe").count(),
                    "visibility_state": page.evaluate("document.visibilityState"),
                    "url_path": urlsplit(page.url).path,
                }

            def browser_phase(name, seconds, action=None):
                global phase
                phase = name
                start = time.monotonic()
                before = snapshot()
                if action:
                    action()
                page.wait_for_timeout(seconds * 1000)
                after = snapshot()
                counted = [item for item in requests if item["phase"] == name]
                phases.append(
                    {
                        "name": name,
                        "seconds": round(time.monotonic() - start, 3),
                        "browser_before": before,
                        "browser_after": after,
                        "requests": dict(Counter(item["path"] for item in counted)),
                        "request_count": len(counted),
                        "completed_response_bytes": sum(item["bytes"] for item in counted),
                        "statuses": dict(
                            Counter(str(item.get("status", "pending")) for item in counted)
                        ),
                        "processes": deepcopy(samples.get(name, {})),
                    }
                )
                phase = "between_phases"
                print(
                    json.dumps(
                        {
                            "phase": name,
                            "requests": len(counted),
                            "frames": after["frames"],
                            "heap_mib": round(after["heap_used_bytes"] / MIB, 2),
                        }
                    ),
                    flush=True,
                )
                if watchdog:
                    raise RuntimeError("watchdog stopped profile")

            def hidden(value):
                page.evaluate(
                    "value=>{Object.defineProperty(document,'hidden',"
                    "{configurable:true,get:()=>value});"
                    "Object.defineProperty(document,'visibilityState',"
                    "{configurable:true,get:()=>value?'hidden':'visible'});"
                    "document.dispatchEvent(new Event('visibilitychange'));}",
                    value,
                )

            browser_phase("idle_blank_browser", args.seconds)
            browser_phase(
                "library_load", 2, lambda: page.goto(base + "/", wait_until="networkidle")
            )
            browser_phase("library_visible_idle", args.seconds)
            browser_phase("library_simulated_hidden", args.seconds, lambda: hidden(True))
            hidden(False)
            browser_phase(
                "owned_artifact_load",
                2,
                lambda: page.goto(base + "/?artifact=" + html_id, wait_until="networkidle"),
            )
            browser_phase("owned_artifact_visible_idle", args.seconds)
            browser_phase(
                "slide_editor_load",
                2,
                lambda: page.goto(base + "/?deck=" + DECK_ID, wait_until="networkidle"),
            )
            page.locator("#preview").wait_for()
            browser_phase("slide_editor_visible_idle", args.seconds)
            browser_phase("slide_editor_simulated_hidden", args.seconds, lambda: hidden(True))
            hidden(False)
            page.wait_for_timeout(500)

            def history_open():
                page.locator("#version-history").click()
                page.locator(".version-history[open]").wait_for()
                page.wait_for_timeout(300)
                for summary in page.locator(".version-list details>summary").all():
                    summary.click()

            browser_phase("history_open_expanded", 5, history_open)
            browser_phase(
                "history_closed",
                3,
                lambda: page.get_by_role(
                    "button", name="Close version history", exact=True
                ).click(),
            )

            def chat_action():
                page.locator("#feedback-text").fill(
                    "Fictional profiling question: please keep the conclusion concise."
                )
                page.locator("#send-feedback").click()

            # Use normal UI only where this version exposes the stable composer IDs.
            if page.locator("#feedback-text").count() and page.locator("#send-feedback").count():
                browser_phase("chat_send_and_refresh", 3, chat_action)
            else:
                phases.append(
                    {
                        "name": "chat_send_and_refresh",
                        "skipped": (
                            "Composer IDs differ; seeded30 messages remain included "
                            "in editor measurements."
                        ),
                    }
                )

            def new_presentation():
                page.locator("#workspace-menu-button").click()
                page.get_by_role("menuitem", name="New presentation", exact=True).click()
                page.locator("#new-dialog[open]").wait_for()

            browser_phase("new_presentation_dialog_visible", 10, new_presentation)
            browser_phase("new_presentation_dialog_simulated_hidden", 10, lambda: hidden(True))
            hidden(False)
            page.locator("#new-dialog").evaluate("element=>element.close()")
            browser_phase("new_presentation_dialog_closed", 5)
            cdp.send("HeapProfiler.collectGarbage")
            phases.append({"name": "post_gc_checkpoint", "browser_after": snapshot()})
            browser.close()
    finally:
        done.set()
        worker.join(timeout=3)
        try:
            os.killpg(server.pid, signal.SIGTERM)
            server.wait(timeout=5)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            os.killpg(server.pid, signal.SIGKILL)
            server.wait(timeout=5)
        log.close()
        for item in phases:
            item.setdefault("processes", samples.get(item["name"], {}))
            for value in item["processes"].values():
                value["cpu_seconds"] = round(value["cpu_seconds"], 4)
                value["cpu_one_core_percent"] = (
                    round(100 * value["cpu_seconds"] / item["seconds"], 2)
                    if item.get("seconds")
                    else None
                )
        final_source_hashes = source_hashes()
        changed_source_files = [
            name
            for name in sorted(set(initial_source_hashes) | set(final_source_hashes))
            if initial_source_hashes.get(name) != final_source_hashes.get(name)
        ]
        result = {
            "source": str(source),
            "source_hashes": initial_source_hashes,
            "changed_source_files": changed_source_files,
            "commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=source, text=True
            ).strip(),
            "fictional_fixture": manifest,
            "python": sys.version.split()[0],
            "phases": phases,
            "page_errors": errors,
            "requests": requests,
            "watchdog": watchdog,
            "limitations": [
                "Single representative run on a shared managed host; "
                "no remote synchronization or native Mac shell.",
                "Production capture sandbox unchanged and unavailable here; "
                "library/history captures can fail closed.",
                "Visibility probes explicitly override document.hidden/visibilityState and "
                "dispatch visibilitychange because headless Chromium cannot hide tabs natively "
                "here. They test application guards, not OS/browser hidden-tab throttling.",
                "Browser heap is the main CDP target; process-tree RSS includes browser "
                "renderer/network/GPU descendants and can count shared pages more than once.",
                "CPU uses sampled deltas in /proc process user/system ticks; very short "
                "child-process CPU may be missed. One-core percentage can exceed100 "
                "for multiple processes.",
                "Completed transfer byte counts exclude unfinished SSE streams; "
                "request counts retain those connections.",
                "Post-GC checkpoint is diagnostic only; ordinary phase heap snapshots "
                "do not force garbage collection.",
            ],
        }
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(
            json.dumps({"output": str(args.output), "phases": len(phases), "page_errors": errors}),
            flush=True,
        )
