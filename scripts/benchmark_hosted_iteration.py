"""Fixed fictional 14-slide workload for comparison across hosted implementations."""

import argparse
import base64
import html
import json
import time
from pathlib import Path

import httpx

from scripts.workflow_iteration import RecordedPeer, compact, summarize

STORIES = [
    (
        "cover",
        "Make room for good judgment",
        "People steer. Agents compose. Review stays explicit.",
    ),
    ("next", "One editable surface", "Create → review → refine → present"),
    ("authoring", "Compose freely", "HTML, CSS and SVG remain editable source."),
    ("context", "Spend context early", "Read scope, working rules, schema and visual direction."),
    ("loop", "Review is a conversation", "Brief → draft → comment → assignment → revision"),
    (
        "multiplayer",
        "Access has a scope",
        "Ordinary comments are shared. Assigned requests reach the agent.",
    ),
    (
        "guards",
        "Protect concurrent work",
        "Revision guards reject stale writes. Reconcile human notes.",
    ),
    ("notes", "Keep delivery cues close", "Presenter notes remain separate from audience content."),
    (
        "backup",
        "Backup and sync differ",
        "Backup captures a recovery copy. Configured hosted project sync exchanges changes.",
    ),
    (
        "export",
        "Choose the audience version",
        "Source retains the hidden appendix. Inspect export settings and output.",
    ),
    (
        "tokens",
        "Measure what you observe",
        "Transport bytes are not billed model tokens. Separate text, schema and images.",
    ),
    (
        "practice",
        "Write a useful request",
        "Select a slide, describe the result and review the changed version.",
    ),
    (
        "limits",
        "Approval belongs to a version",
        "A later edit needs fresh review; previous approval covers its reviewed source.",
    ),
    (
        "appendix",
        "Check before sharing",
        "Check claims, composition, notes, scope and the audience file.",
    ),
]


def markup(index: int, title: str, message: str) -> str:
    """Original fictional composition using the public Pixel palette and motif."""
    accent = ["#c0abff", "#ff8979", "#d4f3ad"][index % 3]
    pixels = "".join(
        f'<rect x="{x * 20}" y="{y * 20}" width="17" height="17"/>'
        for x, y in [(0, 0), (1, 0), (2, 0), (0, 1), (2, 1), (0, 2), (1, 2), (2, 2), (1, 3)]
    )
    detail = ""
    if index == 5:
        detail = (
            '<svg viewBox="0 0 1100 130" style="width:82cqw;height:10cqw">'
            '<path d="M200 65H285M485 65H570M770 65H855" stroke="#c0abff" stroke-width="6"/>'
            '<g fill="#34313e"><rect width="200" height="125"/>'
            '<rect x="285" width="200" height="125"/>'
            '<rect x="570" width="200" height="125"/>'
            '<rect x="855" width="240" height="125"/></g>'
            '<g fill="white" font-size="27" text-anchor="middle">'
            '<text x="100" y="72">BRIEF</text><text x="385" y="72">DRAFT</text>'
            '<text x="670" y="72">REVIEW</text><text x="975" y="72">REFINE</text></g></svg>'
        )
    elif index == 9:
        detail = (
            '<table style="font:1.5cqw/1.4 sans-serif;width:82cqw;text-align:left">'
            "<tr><th>Need</th><th>Backup</th><th>Configured sync</th></tr>"
            "<tr><td>Recover</td><td>Restore a point in time</td>"
            "<td>Exchange reviewed changes</td></tr>"
            "<tr><td>Practice</td><td>Test the restore</td>"
            "<td>Inspect and reconcile conflicts</td></tr></table>"
        )
    elif index == 12:
        detail = (
            '<div style="font:1.5cqw/1.5 sans-serif;padding:1.5cqw;background:#34313e">'
            "Exercise · 60 seconds: select the backup slide. "
            "Ask for one concrete recovery example. "
            "Keep the source note. Assign the request, then review the exact changed version.</div>"
        )
    return (
        '<article class="slide" style="width:100%;height:100%;box-sizing:border-box;'
        "padding:5cqw 6cqw;background:#17171b;color:#faf8f4;position:relative;"
        'font-family:monospace;overflow:hidden">'
        f'<div style="color:{accent};font-size:1.5cqw">PIXEL / FICTIONAL FIELD GUIDE · '
        f"{index:02}</div>"
        '<div style="display:grid;grid-template-columns:3fr 1fr;gap:3cqw;margin-top:5cqw">'
        f'<h1 style="font-size:5.1cqw;line-height:1.04;margin:0">{html.escape(title)}</h1>'
        f'<svg viewBox="0 0 80 90" fill="{accent}" style="width:100%;height:16cqw">'
        f"{pixels}</svg></div>"
        f'<p style="font:2.4cqw/1.45 sans-serif;max-width:76cqw;margin-top:4cqw">'
        f"{html.escape(message)}</p>"
        + detail
        + '<div style="position:absolute;bottom:3cqw;font:1.2cqw sans-serif;color:#b0adb7">'
        "Illustrative public product narrative · no customer data</div></article>"
    )


def run(root: Path, barriers: bool, scripted_human: bool = False) -> None:
    peer = RecordedPeer(root, phase="context")
    started = time.perf_counter()

    def barrier(name: str) -> None:
        if barriers:
            (root / (name + ".ready")).write_text("ready")
            print(name + " ready", flush=True)
            while not (root / (name + ".continue")).exists():
                time.sleep(0.25)

    def capture(sid: str) -> None:
        result = peer.rpc(
            "tools/call",
            {"name": "capture_slide", "arguments": {"deck_id": peer.deck_id, "slide_id": sid}},
        )
        assert not result.get("isError"), result
        images = [p for p in result.get("content", []) if p["type"] == "image"]
        assert images, result
        (root / (sid + ".png")).write_bytes(base64.b64decode(images[0]["data"]))

    try:
        context = peer.call("get_working_context")
        guide = peer.call("get_authoring_guide", topic="workflow")
        (root / "retained-context.json").write_text(
            json.dumps({"context": context, "guide": guide})
        )
        human = None
        if scripted_human:
            config = json.loads((root / "connection.json").read_text())
            human = httpx.Client(
                base_url=config["url"],
                headers={
                    "host": "studio.example.test",
                    "x-fixture-auth": "owner",
                    "origin": "https://studio.example.test",
                },
                timeout=30,
            )
            base = f"/api/workspaces/default/artifacts/{peer.deck_id}"
            state = human.get(base + "/state").json()
            human.post(
                base + "/chat",
                json={
                    "message": "Fictional reviewer: add a recovery example.",
                    "request_id": "paired-reviewer-comment",
                    "content_hash": state["content_hash"],
                },
            ).raise_for_status()
            human.post(
                base + "/chat",
                json={
                    "message": "Create the 14-slide leadership field guide.",
                    "assigned": context["agent_id"],
                    "request_id": "paired-owner-brief",
                    "content_hash": state["content_hash"],
                    "include_shared_context": True,
                },
            ).raise_for_status()
        feed = peer.call("read_feedback", deck_id=peer.deck_id)
        claims = []
        for event in feed["events"]:
            claim = peer.call(
                "claim_feedback",
                artifact_id=peer.deck_id,
                event_id=event["id"],
                session_id="paired-editor",
            )
            claims.append((event["id"], claim["claim"]))
        (root / "initial-feedback.json").write_text(json.dumps(feed, indent=2))
        retained = guide.get("guide_hash", guide.get("hash", ""))
        outline = context["outline"]
        peer.phase = "authoring"
        for index, (sid, title, message) in enumerate(STORIES, 1):
            patch = {"title": title, "html": markup(index, title, message)}
            if sid in ("cover", "next"):
                source = peer.call("get_slide", deck_id=peer.deck_id, slide_id=sid)
                if sid == "cover":
                    barrier("held-cover")
                if source["html"] != patch["html"]:
                    peer.call(
                        "revise_slide",
                        deck_id=peer.deck_id,
                        slide_id=sid,
                        expected_hash=source["expected_hash"],
                        request_id="iteration-" + sid,
                        patch_json=compact(patch),
                    )
            else:
                ids = [s["id"] for s in outline["slides"]]
                peer.call(
                    "add_slide",
                    deck_id=peer.deck_id,
                    slide_json=compact(
                        {
                            "id": sid,
                            **patch,
                            "notes": "Fictional cue: " + title,
                            "hidden": sid == "appendix",
                        }
                    ),
                    after_slide_id=ids[-1],
                    expected_slide_ids=ids,
                    expected_sections_hash=outline["sections_hash"],
                    request_id="iteration-" + sid,
                )
            outline = peer.call("get_presentation_outline", deck_id=peer.deck_id)
            if index == 1:
                capture("cover")
                barrier("sample-review")
        peer.phase = "validation"
        assert len(outline["slides"]) == 14
        final = peer.call("get_slide", deck_id=peer.deck_id, slide_id="cover")
        assert final["html"] == markup(1, *STORIES[0][1:])
        capture("backup")
        capture("appendix")
        barrier("deck-review")
        resumed_guide = peer.call("get_authoring_guide", topic="workflow", known_hash=retained)
        if retained:
            assert resumed_guide["guide_hash"] == retained
            assert resumed_guide["guide_included"] is False
            assert resumed_guide["guide"] == ""
        feed = peer.call("read_feedback", deck_id=peer.deck_id)
        (root / "review-feedback.json").write_text(json.dumps(feed, indent=2))
        if barriers or scripted_human:
            peer.phase = "conflict"
            held = peer.call("get_slide", deck_id=peer.deck_id, slide_id="cover")
            (root / "held-cover.json").write_text(json.dumps(held))
            barrier("concurrent-note")
            if scripted_human:
                # Test-only simulated human race. The editor only writes through scoped MCP.
                from artifact_studio.storage import Store

                store = Store(root / "app/collection", root / "app/state/review.sqlite3")
                store.save_notes(
                    peer.deck_id, "cover", "Fictional human evidence cue.", held["notes"]
                )
            rejected = peer.call(
                "revise_slide",
                expect_error=True,
                deck_id=peer.deck_id,
                slide_id="cover",
                expected_hash=held["expected_hash"],
                request_id="iteration-stale",
                patch_json=compact({"title": "Reviewed judgment"}),
            )
            assert any(
                "changed" in p.get("text", "").lower() or "conflict" in p.get("text", "").lower()
                for p in rejected.get("content", [])
            )
            fresh = peer.call("get_slide", deck_id=peer.deck_id, slide_id="cover")
            assert fresh["notes"] != held["notes"], "Human note barrier did not change source"
            note = fresh["notes"]
            peer.call(
                "revise_slide",
                deck_id=peer.deck_id,
                slide_id="cover",
                expected_hash=fresh["expected_hash"],
                request_id="iteration-rebased",
                patch_json=compact({"title": "Reviewed judgment"}),
            )
            saved = peer.call("get_slide", deck_id=peer.deck_id, slide_id="cover")
            assert saved["notes"] == note
            barrier("stale-draft")
        if human is not None:
            old_anchor = state["content_hash"]
            current = human.get(base + "/state").json()
            stale = human.post(
                base + "/chat",
                json={
                    "message": "Fictional stale follow-up",
                    "assigned": context["agent_id"],
                    "request_id": "paired-stale-draft",
                    "content_hash": old_anchor,
                },
            )
            assert stale.status_code == 409, stale.text
            recovered = human.post(
                base + "/chat",
                json={
                    "message": "Fictional stale follow-up",
                    "assigned": context["agent_id"],
                    "request_id": "paired-recovered-draft",
                    "content_hash": current["content_hash"],
                },
            )
            recovered.raise_for_status()
            human.close()
        for event_id, claim in claims:
            peer.call(
                "reply_to_feedback",
                artifact_id=peer.deck_id,
                event_id=event_id,
                claim=claim,
                message="Built and verified the fictional 14-slide guide.",
                request_id=f"paired-response-{event_id}",
            )
        peer.call("read_feedback", deck_id=peer.deck_id, after=feed.get("next_cursor", 0))
        result = summarize(root)
        result.update(
            workflow_wall_ms=round((time.perf_counter() - started) * 1000, 3),
            final_slide_count=14,
            assertions=["14 slides", "source verified", "three visual samples"],
        )
        (root / "summary.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2), flush=True)
    finally:
        peer.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--barriers", action="store_true")
    parser.add_argument("--scripted-human", action="store_true")
    args = parser.parse_args()
    run(args.root, args.barriers, args.scripted_human)
