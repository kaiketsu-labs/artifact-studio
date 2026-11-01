"""Measure disposable agent exchanges over real MCP; emit aggregate UTF-8 byte counts only."""

import argparse
import json
import tempfile
from pathlib import Path

from studio.models import DesignRequestInput, Slide
from tests.design_factory import make_design
from tests.integration.support import DECK_ID, PROJECT, running_app

parser = argparse.ArgumentParser()
parser.add_argument("--phase", choices=["before", "after"], required=True)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
samples = {}
with (
    tempfile.TemporaryDirectory(prefix="studio-token-audit-") as directory,
    running_app(Path(directory), Path(directory) / "server.log") as live,
):
    for color in ("orange", "blue"):
        folder = live.store.root / ("2026-11-01-" + color)
        make_design(folder, color)
        (folder / "PREFERENCES.md").write_text("Use " + color + " accents.")
    for index in range(30):
        live.store.create(f"2026-11-01-reference-{index}", "Reference " + str(index), "orange")
    for index in range(12):
        content = (
            '<article class="slide"><h1>Evidence '
            + str(index)
            + "</h1>"
            + ("<p>Trial result and supporting evidence.</p>" * 180)
            + "</article>"
        )
        live.store.add_slide(
            DECK_ID,
            Slide(id="evidence-" + str(index), title="Evidence " + str(index), html=content),
        )
    client = live.client()

    def call(label, tool, **arguments):
        value = client.rpc("tools/call", {"name": tool, "arguments": arguments})
        assert not value.get("isError"), value
        text = value["content"][0]["text"]
        data = json.loads(text)
        if set(data) == {"result"}:
            data = data["result"]
        samples[label] = {
            "call_parameters_utf8_bytes": len(
                json.dumps({"name": tool, "arguments": arguments}, separators=(",", ":")).encode()
            ),
            "text_utf8_bytes": len(text.encode()),
            "result_json_utf8_bytes": len(
                json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
            ),
        }
        return data

    extra = {"include_discovery": False} if args.phase == "after" else {}
    initial = call("startup_selected", "get_working_context", deck_id=DECK_ID, **extra)
    retained = {"known_rules_hash": initial["rules_hash"]}
    if args.phase == "after":
        retained["known_guide_hash"] = initial["guide_hash"]
    call("startup_retained", "get_working_context", deck_id=DECK_ID, **extra, **retained)
    call("startup_full", "get_working_context", deck_id=DECK_ID, detail="full")
    call("live_review_start", "start_live_review", deck_id=DECK_ID)
    call(
        "create",
        "create_presentation",
        deck_id="2026-11-01-new-story",
        title="New story",
        design_id="blue",
        brief="Brief for the board",
    )
    order = [slide.id for slide in live.store.read(DECK_ID).slides]
    call(
        "reorder",
        "reorder_slides",
        deck_id=DECK_ID,
        slide_ids=list(reversed(order)),
        expected_slide_ids=order,
    )
    current = live.store.read(DECK_ID).slides[-1]
    for index in range(18):
        live.store.add_feedback(
            DECK_ID,
            current.id,
            ("Released feedback with source context. " * 15) + str(index),
            live.store.content_hash(DECK_ID, current),
            "audit-comment-" + str(index),
        )
    call("slide_for_edit", "get_slide", deck_id=DECK_ID, slide_id=current.id)
    current = live.store.read(DECK_ID).slides[-1]
    from studio.storage import digest

    call(
        "revise",
        "revise_slide",
        deck_id=DECK_ID,
        slide_id=current.id,
        expected_hash=digest(current.model_dump_json()),
        request_id="audit-revision",
        patch={"title": "Clearer title"},
    )
    live.store.post_design_request(
        DesignRequestInput(
            message="Inspect a new design source. " * 100, request_id="audit-design-request"
        )
    )
    feed = call(
        "feedback_first",
        "read_feedback",
        deck_id=DECK_ID,
        known_rules_hash=initial["rules_hash"],
    )
    feed_extra = (
        {"known_design_requests_hash": feed["design_requests_hash"]}
        if args.phase == "after"
        else {}
    )
    call(
        "feedback_retained",
        "read_feedback",
        deck_id=DECK_ID,
        after_delivery_id=feed["next_cursor"],
        known_rules_hash=initial["rules_hash"],
        **feed_extra,
    )
    guide = call("guide_first", "get_authoring_guide", topic="live_review")
    guide_extra = {"known_hash": guide["hash"]} if args.phase == "after" else {}
    call("guide_retained", "get_authoring_guide", topic="live_review", **guide_extra)
    schema = client.rpc("tools/list", {})
    schema_text = json.dumps(schema, separators=(",", ":"))
    samples["tool_catalog"] = {"json_utf8_bytes": len(schema_text.encode())}
result = {
    "phase": args.phase,
    "measurement": "UTF-8 bytes; not billed tokens",
    "fixture": (
        "2 extra designs, 30 reference decks, 14-slide target, "
        "18 released comments, 1 queued design request"
    ),
    "samples": samples,
}
output = args.output or (PROJECT / "test-results" / ("token-audit-" + args.phase + ".json"))
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
