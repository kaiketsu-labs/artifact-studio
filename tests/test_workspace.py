"""Behavioral checks for durable review state, free composition and stateless MCP calls."""

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import app as service
from studio.models import AgentContext, ElementTarget, FileInput, Slide, SlidePatch, Source
from studio.storage import ConflictError, Store, digest
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """Create an isolated local collection from the design example."""
    make_design(tmp_path / TEMPLATE_ID)
    return Store(tmp_path, tmp_path / "review.sqlite3")


def test_design_discovery_and_brand_creation(store: Store) -> None:
    """Create a selected brand without copying registration or changing the source design."""
    target = store.root / "2026-10-28-blue-default-design"
    make_design(target, "blue")
    before = (target / "index.html").read_bytes()
    assert {item.id for item in store.designs()} == {"test-brand", "blue"}
    deck = store.create("2026-10-28-blue-story", "A blue story", "blue")
    assert deck.design_id == "blue"
    assert len(deck.slides) == 1
    assert "A blue story" in deck.slides[0].html
    assert 'src="assets/blue.svg"' in deck.slides[0].html
    assert not store.view(deck.id).slides[0].approved
    assert not (store.root / deck.id / "design.json").exists()
    assert len(store.designs()) == 2
    assert (target / "index.html").read_bytes() == before
    with pytest.raises(ConflictError):
        _ = store.create(deck.id, "Another story", "test-brand")


def test_unknown_design_leaves_no_partial_deck(store: Store) -> None:
    """Reject missing designs before creating a partially populated folder."""
    with pytest.raises(ValueError, match="list_designs"):
        _ = store.create("2026-10-28-missing-brand", "New story", "unknown")
    assert not (store.root / "2026-10-28-missing-brand").exists()


def test_creation_delivers_brief_and_preserves_brand(store: Store) -> None:
    """Persist the brief for continuation and deliver it through the agent's normal queue."""
    brief = "For team leads: explain how to review agent slides. End with a clear next action."
    deck = store.create("2026-10-28-brief-check", "Reviewing slides", brief=brief)
    snapshot = store.view(deck.id, include_pending=False)
    assert brief in snapshot.context_markdown
    assert "PREFERENCES.md" in snapshot.context_markdown
    assert snapshot.slides[0].notes == ""
    assert not snapshot.slides[0].approved
    assert len(snapshot.feedback) == 1
    assert snapshot.feedback[0].message == brief
    assert snapshot.feedback[0].scope == "presentation"
    assert snapshot.feedback[0].slide_id is None
    assert snapshot.feedback[0].released
    feed = store.feedback_feed(deck.id)
    assert [event.message for event in feed.events] == [brief]
    folder = store.folder(deck.id)
    template = store.folder(TEMPLATE_ID)
    for relative in ("theme.css", "assets/test-brand.svg", "assets/custom.svg"):
        assert (folder / relative).read_bytes() == (template / relative).read_bytes()
    with pytest.raises(ConflictError):
        _ = store.create(deck.id, deck.title, brief=brief)
    assert len(store.view(deck.id).feedback) == 1


def test_long_brief_leaves_no_partial_deck(store: Store) -> None:
    """Validate context before creating any files or feedback entries."""
    with pytest.raises(ValueError, match="brief"):
        _ = store.create("2026-10-28-long-brief", "New story", brief="x" * 10_001)
    assert not store.folder("2026-10-28-long-brief").exists()


@pytest.mark.parametrize("filename", ["../context.txt", "a" * 246 + ".txt", "ü" * 246 + ".txt"])
def test_brief_attachments_reach_agent_context_and_download(
    store: Store, monkeypatch: pytest.MonkeyPatch, filename: str
) -> None:
    """Keep original reference bytes with the deck and expose them to authoring agents."""
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    content = b"Audience: technical colleagues.\nKeep the recap short."
    response = client.post(
        "/api/decks",
        json={
            "deck_id": "2026-10-28-attached-brief",
            "title": "A recap",
            "brief": "Use the attached context.",
            "files": [{"name": filename, "data_base64": base64.b64encode(content).decode()}],
        },
    )
    assert response.status_code == 200
    snapshot = store.view("2026-10-28-attached-brief")
    assert snapshot.brief_files[0].name == Path(filename).name
    path = Path(snapshot.brief_files[0].path)
    assert path.is_relative_to(store.folder(snapshot.id))
    assert path.read_bytes() == content
    assert Path(filename).name in snapshot.context_markdown
    assert "brief-files/0" in snapshot.context_markdown
    assert service.get_presentation(snapshot.id).brief_files == snapshot.brief_files
    outline = service.get_presentation_outline(snapshot.id, include_context=True)
    assert outline["brief_files"] == [item.model_dump() for item in snapshot.brief_files]
    assert outline["context_markdown"] == snapshot.context_markdown
    download = client.get(f"/api/decks/{snapshot.id}/brief-files/0")
    assert download.content == content
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["content-disposition"].startswith("attachment;")
    assert client.get(f"/api/decks/{snapshot.id}/brief-files/1").status_code == 404


def test_invalid_brief_attachments_leave_no_partial_deck(store: Store) -> None:
    """Reject invalid data and attachment counts before creating the presentation."""
    deck_id = "2026-10-28-invalid-attachments"
    with pytest.raises(ValueError, match="could not be read"):
        _ = store.create(
            deck_id, "A recap", files=[FileInput(name="brief.pdf", data_base64="invalid!")]
        )
    assert not store.folder(deck_id).exists()
    with pytest.raises(ValueError, match="up to 5"):
        _ = store.create(
            deck_id, "A recap", files=[FileInput(name="brief.txt", data_base64="")] * 6
        )
    assert not store.folder(deck_id).exists()


def test_design_picker_api(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    """Expose design previews and creation through the same store as MCP."""
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    response = client.get("/api/designs")
    assert response.status_code == 200
    assert response.json()[0]["id"] == "test-brand"
    preview = client.get("/api/designs/test-brand/preview")
    assert "script-src 'none'" in preview.headers["content-security-policy"]
    created = client.post(
        "/api/decks",
        json={
            "deck_id": "2026-10-28-new-story",
            "title": "New story",
            "design_id": "test-brand",
            "brief": "Help a new colleague understand the workflow.",
        },
    )
    assert created.status_code == 200
    assert created.json()["design_id"] == "test-brand"
    assert store.view("2026-10-28-new-story").feedback[0].message == (
        "Help a new colleague understand the workflow."
    )


def test_selector_removal_is_durable_and_preserves_deck_state(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restore removed presentations with their source, approvals and feedback intact."""
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    deck = store.create("2026-10-28-removable", "A removable presentation", brief="Team recap")
    slide = store.view(deck.id).slides[0]
    store.approve(deck.id, slide.id, slide.content_hash, True)
    store.save_notes(deck.id, slide.id, "Private context", slide.notes)
    before = store.view(deck.id)
    source = (store.folder(deck.id) / "index.html").read_bytes()
    assert client.post(f"/api/decks/{deck.id}/archive", json={}).status_code == 200
    assert client.post(f"/api/decks/{deck.id}/archive", json={}).status_code == 200
    assert deck.id not in {item["id"] for item in client.get("/api/decks").json()}
    assert client.get("/api/removed-presentations").json() == [{"id": deck.id, "title": deck.title}]
    restarted = Store(store.root, store.database)
    assert restarted.list_decks(archived=True) == [{"id": deck.id, "title": deck.title}]
    assert client.get(f"/api/decks/{deck.id}").status_code == 200
    assert client.post(f"/api/decks/{deck.id}/restore", json={}).status_code == 200
    assert client.post(f"/api/decks/{deck.id}/restore", json={}).status_code == 200
    assert deck.id in {item["id"] for item in store.list_decks()}
    assert not store.list_decks(archived=True)
    assert store.view(deck.id) == before
    assert (store.folder(deck.id) / "index.html").read_bytes() == source


def test_removing_design_example_keeps_design_usable(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Treat selector removal independently from design registration or file deletion."""
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    assert client.post(f"/api/decks/{TEMPLATE_ID}/archive", json={}).status_code == 200
    assert not store.list_decks()
    assert store.designs()[0].id == "test-brand"
    assert store.create("2026-10-28-still-usable", "Still usable").design_id == "test-brand"
    assert client.post("/api/decks/missing/restore", json={}).status_code == 404
    assert client.post("/api/decks/missing/archive", json={}).status_code == 404


def test_approval_tracks_visible_changes_but_not_notes(store: Store) -> None:
    """Keep approval for note/context edits and invalidate it for changed slide content."""
    item = store.view(TEMPLATE_ID).slides[0]
    store.approve(TEMPLATE_ID, item.id, item.content_hash, True)
    store.save_notes(TEMPLATE_ID, item.id, "My own speaking notes", item.notes)
    assert store.view(TEMPLATE_ID).slides[0].approved
    updated = store.read(TEMPLATE_ID).slides[0]
    _ = store.revise(
        TEMPLATE_ID,
        item.id,
        digest(updated.model_dump_json()),
        "context-001",
        SlidePatch(agent=AgentContext(summary="A question for you")),
    )
    assert store.view(TEMPLATE_ID).slides[0].approved
    updated = store.read(TEMPLATE_ID).slides[0]
    _ = store.revise(
        TEMPLATE_ID,
        item.id,
        digest(updated.model_dump_json()),
        "content-001",
        SlidePatch(title="A new visible title"),
    )
    assert not store.view(TEMPLATE_ID).slides[0].approved
    with pytest.raises(ConflictError):
        store.approve(TEMPLATE_ID, item.id, item.content_hash, True)


def test_idempotency_and_conflicts(store: Store) -> None:
    """Retry a write exactly once and reject stale or conflicting updates."""
    item = store.view(TEMPLATE_ID).slides[0]
    patch = SlidePatch(title="Updated title")
    first = store.revise(TEMPLATE_ID, item.id, item.expected_hash, "revision-001", patch)
    second = store.revise(TEMPLATE_ID, item.id, item.expected_hash, "revision-001", patch)
    assert first == second
    assert store.read(TEMPLATE_ID).slides[0].revision == item.revision + 1
    with pytest.raises(ConflictError):
        _ = store.revise(TEMPLATE_ID, item.id, item.expected_hash, "revision-002", patch)
    with pytest.raises(ConflictError):
        _ = store.revise(
            TEMPLATE_ID, item.id, item.expected_hash, "revision-001", SlidePatch(title="Different")
        )


def test_element_feedback_survives_restart(store: Store) -> None:
    """Retain the selected element and original revision even when the slide later changes."""
    item = store.view(TEMPLATE_ID).slides[0]
    target = ElementTarget(
        selector="h1",
        label="Heading",
        quote="A clear idea.",
        html="<h1>A clear idea.</h1>",
        x=0.1,
        y=0.1,
        width=0.5,
        height=0.3,
    )
    event = store.add_feedback(
        TEMPLATE_ID, item.id, "Make this larger", item.content_hash, "feedback-001", target=target
    )
    assert (
        store.add_feedback(
            TEMPLATE_ID,
            item.id,
            "Make this larger",
            item.content_hash,
            "feedback-001",
            target=target,
        )
        == event
    )
    restarted = Store(store.root, store.database)
    comments = restarted.comments(TEMPLATE_ID)
    assert len(comments) == 1
    assert comments[0].target == target
    assert comments[0].content_hash == item.content_hash
    assert restarted.comments(TEMPLATE_ID, int(event)) == []


def test_new_deck_allows_arbitrary_composition(store: Store) -> None:
    """Start with one slide and freely add a new composition outside the example catalogue."""
    deck = store.create("2026-10-28-free-canvas", "Free canvas")
    assert len(deck.slides) == 1
    assert deck.slides[0].sources[0].url == f"../{TEMPLATE_ID}/Master.pptx"
    custom = Slide(
        id="custom",
        title="Custom composition",
        html=(
            '<article class="slide"><div data-element-id="new-box" '
            'style="position:absolute;left:10%;top:20%;width:35%;font-size:3cqw">'
            'A freely positioned box</div><svg viewBox="0 0 100 100">'
            '<path d="M10 10L50 50" stroke="#ff6428"/></svg></article>'
        ),
    )
    _ = store.add_slide(deck.id, custom)
    assert len(store.read(deck.id).slides) == 2
    assert not any(item.approved for item in store.view(deck.id).slides)
    assert store.reorder(deck.id, ["custom", "cover"]).slides[0].id == "custom"
    store.remove_slide(deck.id, "custom", digest(custom.model_dump_json()))
    assert len(store.read(deck.id).slides) == 1


def test_shared_design_change_requires_review(store: Store) -> None:
    """Changing shared theme styles invalidates the corresponding approval."""
    item = store.view(TEMPLATE_ID).slides[0]
    store.approve(TEMPLATE_ID, item.id, item.content_hash, True)
    theme = store.folder(TEMPLATE_ID) / "theme.css"
    _ = theme.write_text(theme.read_text() + "\n.slide { color: #555; }")
    assert not store.view(TEMPLATE_ID).slides[0].approved


def test_notes_conflict_and_invalid_markup(store: Store) -> None:
    """Reject stale note edits, executable slides and traversal outside the collection."""
    item = store.view(TEMPLATE_ID).slides[0]
    store.save_notes(TEMPLATE_ID, item.id, "New notes", item.notes)
    with pytest.raises(ConflictError):
        store.save_notes(TEMPLATE_ID, item.id, "Stale notes", item.notes)
    with pytest.raises(ValueError):
        _ = store.add_slide(
            TEMPLATE_ID, Slide(id="bad", title="Bad", html='<img onerror="alert(1)">')
        )
    with pytest.raises(ValueError):
        _ = store.folder("../escape")
    with pytest.raises(ValueError):
        _ = Source(label="Bad", url="javascript:alert(1)")


def test_http_and_stateless_mcp(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    """Read tools over independent HTTP requests and keep approval outside the MCP surface."""
    monkeypatch.setattr(service, "store", store)
    with TestClient(service.app, base_url="http://127.0.0.1:8791") as client:
        response = client.get(f"/api/decks/{TEMPLATE_ID}")
        assert response.status_code == 200
        headers = {"Accept": "application/json, text/event-stream"}
        init = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "studio-test", "version": "1"},
                },
            },
        )
        assert init.status_code == 200, init.text
        assert "mcp-session-id" not in init.headers
        listed = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert listed.status_code == 200, listed.text
        names = {item["name"] for item in listed.json()["result"]["tools"]}
        assert {
            "get_working_context",
            "revise_slide",
            "add_slide",
            "read_feedback",
            "capture_slide",
            "capture_contact_sheet",
        } <= names
        assert not any("approv" in name for name in names)
        context = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "get_working_context", "arguments": {}},
            },
        )
        assert context.status_code == 200, context.text
        assert "Compose freely" in context.text
        startup = json.loads(context.json()["result"]["content"][0]["text"])
        assert startup["rules_included"] is True
        for tool_name, retained_arguments in (
            ("get_working_context", {}),
            ("get_presentation", {"deck_id": TEMPLATE_ID}),
            ("get_presentation_outline", {"deck_id": TEMPLATE_ID}),
        ):
            retained_response = client.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": tool_name,
                        "arguments": {
                            **retained_arguments,
                            "known_rules_hash": startup["rules_hash"],
                        },
                    },
                },
            )
            assert retained_response.status_code == 200
            retained_result = retained_response.json()["result"]
            assert not retained_result.get("isError"), retained_result
            retained = json.loads(retained_result["content"][0]["text"])
            assert retained["rules_included"] is False
            assert retained["authoring_instructions"] == retained["learned_preferences"] == ""
        before = service.get_slide(TEMPLATE_ID, "cover")
        arguments: dict[str, object] = {
            "deck_id": TEMPLATE_ID,
            "slide_id": before.id,
            "expected_hash": before.expected_hash,
            "request_id": "transport-receipt-test",
            "patch": {"title": "Transport checked"},
        }
        for include_content in (False, True):
            response = client.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "revise_slide",
                        "arguments": {**arguments, "include_content": include_content},
                    },
                },
            )
            assert response.status_code == 200
            result = response.json()["result"]
            assert not result.get("isError"), result
            text = json.loads(result["content"][0]["text"])
            assert text["title"] == "Transport checked"
            assert ("html" in text) is include_content
            assert ("feedback" in text) is include_content
            structured = result["structuredContent"]
            assert structured.get("result", structured) == text

        blocked = client.post(
            f"/api/decks/{TEMPLATE_ID}/slides/cover/notes",
            headers={"Origin": "https://unrelated.example"},
            json={"notes": "No", "expected_notes": ""},
        )
        assert blocked.status_code == 403


def test_context_edit_is_available_to_remote_agents(store: Store) -> None:
    """Keep completed deck documentation accurate through MCP without local filesystem access."""
    deck = store.view(TEMPLATE_ID)
    updated = "# A real presentation\n\nPurpose: explain the reviewed proposal.\n"
    token = store.update_context(TEMPLATE_ID, updated, deck.context_hash)
    assert store.view(TEMPLATE_ID).context_markdown == updated
    assert store.update_context(TEMPLATE_ID, updated, deck.context_hash) == token
    with pytest.raises(ConflictError):
        _ = store.update_context(TEMPLATE_ID, "Different context", deck.context_hash)


def test_batch_feedback_releases_together_without_skipping_old_events(store: Store) -> None:
    """Keep a batch out of agent reads and deliver it even after a later live comment."""
    item = store.view(TEMPLATE_ID).slides[0]
    _ = store.set_mode(TEMPLATE_ID, "batch")
    first = store.add_feedback(TEMPLATE_ID, item.id, "Queued first", item.content_hash, "batch-001")
    second = store.add_feedback(
        TEMPLATE_ID, item.id, "Queued second", item.content_hash, "batch-002"
    )
    assert store.feedback_feed(TEMPLATE_ID).events == []
    assert store.view(TEMPLATE_ID, include_pending=False).slides[0].feedback == []
    assert len(store.view(TEMPLATE_ID).slides[0].feedback) == 2
    assert store.workflow(TEMPLATE_ID).pending == 2
    _ = store.set_mode(TEMPLATE_ID, "live")
    later = store.add_feedback(TEMPLATE_ID, item.id, "Live later", item.content_hash, "live-001")
    live = store.feedback_feed(TEMPLATE_ID)
    assert [event.event_id for event in live.events] == [int(later)]
    _ = store.release_batch(TEMPLATE_ID)
    batch = store.feedback_feed(TEMPLATE_ID, live.next_cursor)
    assert [event.event_id for event in batch.events] == [int(first), int(second)]
    assert len({event.batch_id for event in batch.events}) == 1
    assert all(event.released for event in batch.events)
    assert store.workflow(TEMPLATE_ID).pending == 0
    assert len(store.feedback_feed(TEMPLATE_ID).events) == 3
    _ = store.release_batch(TEMPLATE_ID)
    assert store.feedback_feed(TEMPLATE_ID, batch.next_cursor).events == []


def test_live_wait_wakes_on_feedback(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    """Release a live comment to an active bounded wait and clear listening status afterward."""
    import asyncio

    monkeypatch.setattr(service, "store", store)
    item = store.view(TEMPLATE_ID).slides[0]

    async def scenario() -> None:
        """Exercise a real asynchronous wait against the durable queue."""
        waiting = asyncio.create_task(service.wait_for_feedback(TEMPLATE_ID, timeout_seconds=2))
        await asyncio.sleep(0.1)
        assert store.workflow(TEMPLATE_ID).agent_waiting
        _ = store.add_feedback(
            TEMPLATE_ID, item.id, "Please change this", item.content_hash, "live-wait-001"
        )
        result = await waiting
        assert result.events[0].message == "Please change this"
        assert not store.workflow(TEMPLATE_ID).agent_waiting

    asyncio.run(scenario())


def test_design_chat_files_and_agent_delivery(store: Store) -> None:
    """Persist file handoffs, wake the normal feedback loop and show the agent's response."""
    import base64

    from studio.models import DesignRequestInput, FileInput

    content = b"Reference design: blue, spacious, diagrams rather than paragraphs."
    body = DesignRequestInput(
        request_id="design-request-001",
        message="Use this reference",
        files=[FileInput(name="reference.txt", data_base64=base64.b64encode(content).decode())],
    )
    request = store.post_design_request(body)
    assert store.post_design_request(body).event_id == request.event_id
    assert Path(request.files[0].path).read_bytes() == content
    assert store.feedback_feed(TEMPLATE_ID).design_requests[0].event_id == request.event_id
    reply = store.reply_design(
        request.event_id, "I am building the design.", "design-reply-001", "working"
    )
    assert reply.reply_to == request.event_id
    assert not store.feedback_feed(TEMPLATE_ID).design_requests
    ready = store.reply_design(
        request.event_id, "The design is ready.", "design-reply-002", "ready", "test-brand"
    )
    assert ready.design_id == "test-brand"
    restored = Store(store.root, store.database).design_inbox()
    assert len(restored.messages) == 3
    assert restored.messages[0].status == "ready"
    with pytest.raises(ConflictError):
        _ = store.post_design_request(body.model_copy(update={"message": "Different content"}))


def test_design_chat_validation_and_presence(store: Store) -> None:
    """Reject unusable files and unknown completed designs, and expire live availability."""
    from studio.models import DesignRequestInput, FileInput

    with pytest.raises(ValueError, match="message"):
        _ = store.post_design_request(DesignRequestInput(request_id="empty-request"))
    with pytest.raises(ValueError, match="attachment"):
        _ = store.post_design_request(
            DesignRequestInput(
                request_id="invalid-file",
                files=[FileInput(name="reference.txt", data_base64="not base64!")],
            )
        )
    request = store.post_design_request(
        DesignRequestInput(request_id="url-request", message="Use https://example.com/brand")
    )
    with pytest.raises(ValueError, match="Unknown design"):
        _ = store.reply_design(request.event_id, "Ready", "invalid-ready", "ready", "missing")
    assert not store.design_inbox().agent_waiting
    store.mark_design_wait(10)
    assert store.design_inbox().agent_waiting
    store.mark_design_wait()
    assert not store.design_inbox().agent_waiting
    store.mark_agent_check(TEMPLATE_ID, 10)
    assert store.design_inbox().agent_waiting


def test_agent_registers_prepared_design(store: Store) -> None:
    """Make an agent-prepared bundle selectable without asking the user to configure it."""
    from studio.models import DesignManifest

    target = store.root / "2026-10-28-new-blue"
    make_design(target, "blue")
    (target / "design.json").unlink()
    manifest = DesignManifest(id="blue", name="Blue", description="Reference-based design")
    result = store.register_design(target.name, manifest)
    assert store.register_design(target.name, manifest) == result
    assert store.design("blue").folder_id == target.name
    assert (
        store.create("2026-10-28-from-agent-design", "Agent-created design", "blue").design_id
        == "blue"
    )


def test_general_feedback_survives_slide_changes_and_batching(store: Store) -> None:
    """Keep presentation instructions independent from the viewed slide and deliver batches."""
    _ = store.set_mode(TEMPLATE_ID, "batch")
    event_id = store.add_feedback(
        TEMPLATE_ID,
        None,
        "Add a summary slide and move it first",
        "",
        "general-feedback-001",
        context_slide_id="cover",
    )
    visible = store.view(TEMPLATE_ID).feedback[0]
    assert visible.scope == "presentation"
    assert visible.slide_id is None
    assert visible.context_slide_id == "cover"
    assert not store.view(TEMPLATE_ID, include_pending=False).feedback
    assert not store.feedback_feed(TEMPLATE_ID).events
    _ = store.release_batch(TEMPLATE_ID)
    delivered = store.feedback_feed(TEMPLATE_ID).events[0]
    assert delivered.event_id == int(event_id)
    assert delivered.batch_id
    _ = store.add_feedback(
        TEMPLATE_ID,
        None,
        "I will add the summary and place it first.",
        "",
        "general-reply-001",
        reply_to=int(event_id),
    )
    assert len(store.view(TEMPLATE_ID).feedback) == 2
    assert not store.view(TEMPLATE_ID).slides[0].feedback
    deck = store.read(TEMPLATE_ID)
    _ = store.reorder(TEMPLATE_ID, [slide.id for slide in reversed(deck.slides)])
    assert store.view(TEMPLATE_ID).feedback[0].context_slide_id == "cover"


def test_design_request_wakes_active_slide_agent(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deliver a design-chat handoff through an agent's existing slide-feedback wait."""
    import asyncio

    from studio.models import DesignRequestInput

    monkeypatch.setattr(service, "store", store)

    async def scenario() -> None:
        """Wait on the real queue and release a design reference while the agent listens."""
        waiting = asyncio.create_task(service.wait_for_feedback(TEMPLATE_ID, timeout_seconds=2))
        await asyncio.sleep(0.1)
        _ = store.post_design_request(
            DesignRequestInput(
                request_id="wake-design-001", message="Add a design from this reference"
            )
        )
        response = await waiting
        assert response.design_requests[0].message == "Add a design from this reference"
        assert not store.workflow(TEMPLATE_ID).agent_waiting

    asyncio.run(scenario())


def test_empty_workspace_and_saved_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Onboard without a corporate pack and persist an explicitly chosen default."""
    empty = Store(tmp_path / "empty", tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", empty)
    context = service.get_working_context()
    assert context["designs"] == [] and context["design_assets"] == []
    assert "No design installed" in str(context["design"])
    assert "runtime" in str(context["runtime_path"])
    with pytest.raises(ValueError, match="Add a design"):
        _ = empty.create("2026-10-28-first", "First")
    make_design(empty.root / TEMPLATE_ID)
    make_design(empty.root / "2026-10-28-blue", "blue")
    assert empty.set_default_design("test-brand") == "test-brand"
    restored = Store(empty.root, empty.database)
    assert restored.default_design_id() == "test-brand"
    assert restored.create("2026-10-28-story", "Story").design_id == "test-brand"
    assets = service.get_design_assets("test-brand")
    assert any(item["path"] == "Master.pptx" for item in assets)
    assert any(item["path"] == "assets/custom.svg" for item in assets)
