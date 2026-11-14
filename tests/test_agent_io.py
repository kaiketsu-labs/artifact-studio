"""Efficient MCP communication preserves source access, rule updates and privacy."""

import asyncio
import json
import time
from pathlib import Path
from typing import Literal, cast

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from artifact_studio import app as service
from artifact_studio.models import (
    Deck,
    DeckReceipt,
    DesignRequestInput,
    Slide,
    SlidePatch,
    SlideReceipt,
    SlideView,
)
from artifact_studio.storage import Store, digest
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    """Use an isolated fictional design and private review database."""
    make_design(tmp_path / TEMPLATE_ID)
    result = Store(tmp_path, tmp_path / "private/review.sqlite3")
    monkeypatch.setattr(service, "store", result)
    return result


def test_compact_bootstrap_preserves_complete_rules_and_loadable_guides(store: Store) -> None:
    """Retain authority and creative freedom while deferring unrelated documentation."""
    full = service.get_working_context(detail="full")
    compact = service.get_working_context()
    assert compact["authoring_instructions"] == full["authoring_instructions"]
    assert compact["learned_preferences"] == full["learned_preferences"]
    assert compact["preferences"] == full["preferences"]
    assert compact["rules_hash"] == full["rules_hash"]
    assert "interactive_html" in str(compact["authoring_capabilities"])
    capabilities = cast(dict[str, object], compact["authoring_capabilities"])
    sizing = cast(dict[str, str], capabilities["sizing"])
    assert sizing["aspect_ratio"] == "16:9"
    assert "100%" in sizing["layout"] and "cqw" in sizing["layout"]
    assert compact["asset_inventory_included"] is False
    assert "live_review_guide" not in compact and "visual_review_guide" not in compact
    assert "Read get_slide" in str(compact["guide"])
    assert len(json.dumps(compact)) < len(json.dumps(full)) / 2
    guide = service.get_authoring_guide("interactive_html")
    assert "JavaScript" in guide["markdown"]
    assert guide["hash"] == digest(guide["markdown"])
    design = service.get_authoring_guide("design_context")
    assert design["markdown"] == (store.folder(TEMPLATE_ID) / "README.md").read_text()
    assets = service.get_design_assets()
    assert assets and all("/api/designs//" not in str(item["url"]) for item in assets)


def test_retained_workflow_hash_deduplicates_only_identical_brand_guidance(store: Store) -> None:
    """Share retained text across topics without hiding distinct brand-specific requirements."""
    workflow = service.get_authoring_guide("workflow")
    duplicate = service.get_authoring_guide(
        "design_guide", deck_id=TEMPLATE_ID, known_hash=workflow["hash"]
    )
    assert duplicate["topic"] == "design_guide" and duplicate["markdown"] == ""
    assert duplicate["hash"] == workflow["hash"] and duplicate["content_status"] == "unchanged"
    guide_path = store.folder(TEMPLATE_ID) / "AGENT-GUIDE.md"
    _ = guide_path.write_text(workflow["markdown"] + "\nBrand-only rule: keep blue accents.\n")
    distinct = service.get_authoring_guide(
        "design_guide", deck_id=TEMPLATE_ID, known_hash=workflow["hash"]
    )
    assert (
        distinct["markdown"] == guide_path.read_text() and distinct["content_status"] == "included"
    )
    assert "Brand-only rule" in distinct["markdown"]


@pytest.mark.parametrize("detail", ["compact", "full"])
def test_resume_uses_deck_design_instead_of_workspace_default(
    store: Store, detail: Literal["compact", "full"]
) -> None:
    """Resolve brand preferences and full assets from the deck even after a default change."""
    orange = store.root / "2026-11-01-orange"
    make_design(orange, "orange")
    _ = (orange / "PREFERENCES.md").write_text("Use orange accents.")
    _ = (store.folder(TEMPLATE_ID) / "PREFERENCES.md").write_text("Use blue accents.")
    deck = store.create("2026-11-01-blue-story", "Blue story", "test-brand", "Blue brief")
    _ = store.set_default_design("orange")
    assert service.get_working_context()["preferences"] == "Use orange accents."
    selected = service.get_working_context(deck_id=deck.id, detail=detail)
    assert selected["deck_id"] == deck.id and selected["selected_design_id"] == "test-brand"
    assert selected["default_design_id"] == "orange"
    assert selected["preferences"] == "Use blue accents."
    assert selected["workspace_url"] == f"/?deck={deck.id}"
    assert selected["mcp_url"] == "/mcp"
    retained = service.get_working_context(
        deck_id=deck.id, detail=detail, known_rules_hash=str(selected["rules_hash"])
    )
    assert retained["rules_included"] is False
    assert retained["preferences"] == selected["preferences"]
    assert service.get_working_context(design_id="test-brand")["preferences"] == "Use blue accents."
    assert service.get_working_context(deck_id=deck.id, design_id="test-brand")[
        "selected_design_id"
    ] == ("test-brand")
    if detail == "full":
        assert selected["design_assets"] == service.get_design_assets("test-brand")
    with pytest.raises(ToolError, match="does not match"):
        _ = service.get_working_context(deck_id=deck.id, design_id="orange", detail=detail)
    with pytest.raises(ToolError):
        _ = service.get_working_context(deck_id="missing-deck", detail=detail)
    deck.design_id = "uninstalled"
    store.write(deck.id, deck)
    portable = service.get_working_context(deck_id=deck.id, detail=detail)
    assert portable["design_status"] == "missing"
    assert portable["preferences"] == "Use blue accents."
    deck.design_id = ""
    store.write(deck.id, deck)
    (store.folder(deck.id) / "PREFERENCES.md").unlink()
    unbranded = service.get_working_context(deck_id=deck.id, detail=detail)
    assert unbranded["selected_design_id"] is None and unbranded["preferences"] == ""
    assert "This deck has no design selected" in str(unbranded["design"])


def test_outline_keeps_order_and_tokens_without_source_or_history(store: Store) -> None:
    """Choose full context intentionally and fetch slide source only when needed."""
    original = store.view(TEMPLATE_ID)
    outline = service.get_presentation_outline(TEMPLATE_ID)
    assert outline["context_hash"] == original.context_hash
    assert "context_markdown" not in outline
    slides = cast(list[dict[str, object]], outline["slides"])
    assert isinstance(slides, list)
    assert [item["id"] for item in slides] == [item.id for item in original.slides]
    assert all("html" not in item and "feedback" not in item for item in slides)
    expanded = service.get_presentation_outline(TEMPLATE_ID, include_context=True)
    assert expanded["context_markdown"] == original.context_markdown
    assert service.get_slide(TEMPLATE_ID, "cover").html == original.slides[0].html


def test_targeted_slide_read_omits_only_interactive_body(store: Store) -> None:
    """Large embedded dependencies stay optional without weakening guards or erasing source."""
    before = store.read(TEMPLATE_ID).slides[0]
    interactive = "<style>/*" + "embedded-font-data-" * 30_000 + "*/</style><p>Exact text</p>"
    _ = store.revise(
        TEMPLATE_ID,
        before.id,
        digest(before.model_dump_json()),
        "large-interactive-fixture",
        SlidePatch(interactive_html=interactive, notes="Human speaker note"),
    )
    full = service.get_slide(TEMPLATE_ID, "cover")
    _ = store.add_feedback(
        TEMPLATE_ID, "cover", "Released review", full.content_hash, "read-release"
    )
    _ = store.set_mode(TEMPLATE_ID, "batch")
    _ = store.add_feedback(
        TEMPLATE_ID, "cover", "Withheld review", full.content_hash, "read-withheld"
    )
    discussion = service.get_slide(
        TEMPLATE_ID, "cover", include_feedback=True, include_interactive_html=False
    )
    assert "Released review" in discussion.model_dump_json()
    assert "Withheld review" not in discussion.model_dump_json()
    targeted = service.get_slide(TEMPLATE_ID, "cover", include_interactive_html=False)
    assert targeted.interactive_html == "" and not targeted.interactive_html_included
    assert targeted.interactive_html_bytes == len(interactive.encode())
    assert targeted.interactive_html_hash == digest(interactive)
    assert targeted.expected_hash == full.expected_hash
    assert targeted.content_hash == full.content_hash
    assert targeted.notes == full.notes == "Human speaker note"
    assert targeted.html == full.html and targeted.approved == full.approved
    assert len(targeted.model_dump_json()) < len(full.model_dump_json()) / 50
    _ = service.revise_slide(
        TEMPLATE_ID,
        "cover",
        targeted.expected_hash,
        "targeted-title-edit",
        SlidePatch(title="Updated title"),
    )
    current = service.get_slide(TEMPLATE_ID, "cover")
    assert current.interactive_html == interactive and current.interactive_html_included
    assert current.notes == "Human speaker note"
    with pytest.raises(ToolError, match="changed"):
        _ = service.revise_slide(
            TEMPLATE_ID,
            "cover",
            targeted.expected_hash,
            "targeted-stale-edit",
            SlidePatch(title="Stale title"),
        )


def test_guides_and_discovery_can_be_retained_independently_of_rules(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skip only explicitly retained guides and optional discovery; user guidance stays complete."""
    docs = tmp_path / "app/docs"
    docs.mkdir(parents=True)
    quickstart = docs / "AGENT-QUICKSTART.md"
    _ = quickstart.write_text("Complete test quickstart")
    _ = (docs / "LIVE-REVIEW.md").write_text("Complete test live guide")
    monkeypatch.setattr(service, "PROJECT", docs.parent)
    queued = store.post_design_request(
        DesignRequestInput(message="A user design request", request_id="retain-guide-request")
    )
    first = service.get_working_context(deck_id=TEMPLATE_ID)
    assert first["guide_included"] and first["discovery_included"] is False
    discovered = service.get_working_context(deck_id=TEMPLATE_ID, include_discovery=True)
    assert discovered["discovery_included"] is True
    assert discovered["designs"] and discovered["presentations"]
    assert discovered["preferences"] == first["preferences"]
    assert discovered["authoring_instructions"] == first["authoring_instructions"]
    assert discovered["design_inbox"] == first["design_inbox"]
    retained = service.get_working_context(
        deck_id=TEMPLATE_ID, known_guide_hash=str(first["guide_hash"]), include_discovery=False
    )
    assert retained["guide"] == "" and retained["guide_included"] is False
    assert retained["rules_included"] is True
    assert retained["authoring_instructions"] == first["authoring_instructions"]
    assert retained["preferences"] == first["preferences"]
    assert retained["design_references"] == first["design_references"]
    assert retained["discovery_included"] is False
    assert retained["designs"] == retained["presentations"] == retained["libraries"] == []
    assert str(queued.event_id) in json.dumps(retained["design_inbox"])
    _ = (store.folder(TEMPLATE_ID) / "PREFERENCES.md").write_text("Updated brand guidance")
    store.save_instructions("New complete user rule", digest(store.authoring_instructions()))
    changed_rules = service.get_working_context(
        deck_id=TEMPLATE_ID,
        known_guide_hash=str(first["guide_hash"]),
        known_rules_hash=str(first["rules_hash"]),
    )
    assert changed_rules["guide_included"] is False
    assert changed_rules["rules_included"] is True
    assert changed_rules["authoring_instructions"] == "New complete user rule"
    assert changed_rules["preferences"] == "Updated brand guidance"
    _ = quickstart.write_text("Changed complete quickstart")
    changed_guide = service.get_working_context(known_guide_hash=str(first["guide_hash"]))
    assert (
        changed_guide["guide_included"] is True and changed_guide["guide"] == quickstart.read_text()
    )
    assert service.get_working_context()["guide"] == quickstart.read_text()
    guide = service.get_authoring_guide("live_review")
    cached = service.get_authoring_guide("live_review", known_hash=guide["hash"])
    assert cached["markdown"] == "" and cached["content_status"] == "unchanged"
    _ = (docs / "LIVE-REVIEW.md").write_text("Changed complete live guide")
    assert service.get_authoring_guide("live_review", known_hash=guide["hash"])["markdown"] == (
        "Changed complete live guide"
    )


def test_opening_agent_messages_cannot_reuse_a_human_message_identity(store: Store) -> None:
    """Keep authorship in retry identity while preserving genuine human-originated briefs."""
    deck = store.create("2026-11-04-authorship", "Authorship", brief="A real human brief")
    assert store.comments(deck.id)[0].author == "user"
    _ = store.add_feedback(deck.id, None, "Same text", "", "human-message-identity")
    with pytest.raises(ToolError, match="another action"):
        _ = service.reply_to_presentation_feedback(deck.id, "Same text", "human-message-identity")
    assert [item.author for item in store.comments(deck.id)] == ["user", "user"]


def test_deck_mutations_return_order_without_echoing_source(store: Store) -> None:
    """Large source, notes and binaries stay canonical through compact creation and ordering."""
    created = service.create_presentation("2026-11-01-receipt", "Receipt", brief="Complete brief")
    assert [item.author for item in store.comments(created.id)] == ["agent"]
    assert not store.feedback_feed(created.id).events
    assert isinstance(created, DeckReceipt) and created.slide_ids == ["cover"]
    assert "slides" not in created.model_dump()
    full_created = service.create_presentation("2026-11-01-full", "Full", include_content=True)
    assert isinstance(full_created, Deck) and full_created.slides[0].html
    _ = store.add_slide(
        created.id,
        Slide(
            id="large",
            title="Large",
            html="<article>" + "Source " * 5000 + "</article>",
            interactive_html="<script>" + "/* embedded asset */" * 2000 + "</script>",
            notes="Human notes",
        ),
    )
    original = store.read(created.id)
    before = [slide.id for slide in original.slides]
    after = list(reversed(before))
    receipt = service.reorder_slides(created.id, after, before)
    assert isinstance(receipt, DeckReceipt) and receipt.slide_ids == after
    assert len(receipt.model_dump_json()) < 250
    assert service.reorder_slides(created.id, after, before) == receipt
    full = service.reorder_slides(created.id, after, include_content=True)
    assert isinstance(full, Deck) and full.slides == list(reversed(original.slides))
    with pytest.raises(ToolError, match="changed"):
        _ = service.reorder_slides(created.id, before, before)


def test_slide_edit_read_does_not_repeat_discussion_or_expose_withheld_feedback(
    store: Store,
) -> None:
    """Source and note hashes stay identical while history is an explicit opt-in."""
    before = service.get_slide(TEMPLATE_ID, "cover")
    _ = store.add_feedback(
        TEMPLATE_ID, before.id, "Released discussion", before.content_hash, "read-slide-history"
    )
    _ = store.set_mode(TEMPLATE_ID, "batch")
    with store.connect() as db:
        _ = store.append_feedback(
            db, TEMPLATE_ID, before.id, "user", "Withheld", "", released=False
        )
    compact = service.get_slide(TEMPLATE_ID, before.id)
    full = service.get_slide(TEMPLATE_ID, before.id, include_feedback=True)
    assert compact.feedback == [] and compact.feedback_included is False
    assert full.feedback_included is True and [item.message for item in full.feedback] == [
        "Released discussion"
    ]
    assert compact.html == full.html and compact.notes == full.notes
    assert compact.expected_hash == full.expected_hash == before.expected_hash
    assert compact.content_hash == full.content_hash == before.content_hash


def test_retained_design_requests_do_not_force_a_polling_loop_or_acknowledge_work(
    store: Store,
) -> None:
    """Wait on a retained queue; deliver changes and rules without acknowledging work."""
    request = store.post_design_request(
        DesignRequestInput(message="Queued request", request_id="cached-design-request")
    )
    initial = service.read_feedback(TEMPLATE_ID)
    token = initial.design_requests_hash
    cached = service.read_feedback(TEMPLATE_ID, known_design_requests_hash=token)
    assert cached.design_requests == [] and cached.design_requests_included is False
    assert cached.rules_included and cached.authoring_instructions == initial.authoring_instructions
    assert store.design_inbox().messages[0].status == "queued"
    start = time.monotonic()
    waited = asyncio.run(
        service.wait_for_feedback(TEMPLATE_ID, timeout_seconds=1, known_design_requests_hash=token)
    )
    assert time.monotonic() - start >= 0.8
    assert not waited.design_requests_included and waited.design_requests == []
    _ = store.reply_design(request.event_id, "Working", "cached-design-working", "working")
    removed = asyncio.run(
        service.wait_for_feedback(TEMPLATE_ID, timeout_seconds=1, known_design_requests_hash=token)
    )
    assert removed.design_requests_included and removed.design_requests == []
    assert removed.design_requests_hash != token
    _ = store.post_design_request(
        DesignRequestInput(message="New request", request_id="cached-design-new")
    )
    added = service.read_feedback(
        TEMPLATE_ID, known_design_requests_hash=removed.design_requests_hash
    )
    assert added.design_requests_included and added.design_requests[0].message == "New request"
    assert service.read_feedback(TEMPLATE_ID).design_requests == added.design_requests


def test_revision_receipt_does_not_echo_html_or_history(store: Store) -> None:
    """Keep exact concurrency tokens available and full-content reads opt-in."""
    before = service.get_slide(TEMPLATE_ID, "cover")
    receipt = service.revise_slide(
        TEMPLATE_ID,
        before.id,
        before.expected_hash,
        "small-receipt-test",
        SlidePatch(title="Clearer title"),
    )
    assert isinstance(receipt, SlideReceipt)
    assert receipt.revision == before.revision + 1
    assert "html" not in receipt.model_dump() and "feedback" not in receipt.model_dump()
    assert receipt.expected_hash == service.get_slide(TEMPLATE_ID, before.id).expected_hash
    expanded = service.revise_slide(
        TEMPLATE_ID,
        before.id,
        receipt.expected_hash,
        "expanded-receipt-test",
        SlidePatch(title="Final title"),
        include_content=True,
    )
    assert isinstance(expanded, SlideView) and expanded.html == before.html


def test_add_retry_retains_original_receipt_and_never_rewrites_newer_content(store: Store) -> None:
    """A recorded add survives lost responses, visual drift, later edits and deletion."""
    slide = Slide(
        id="retry-add", title="Retry add", html='<article class="slide">Initial</article>'
    )
    first = service.add_slide(TEMPLATE_ID, slide, request_id="add-retry-operation")
    assert isinstance(first, SlideReceipt)
    assert service.add_slide(TEMPLATE_ID, slide, request_id="add-retry-operation") == first
    with pytest.raises(ToolError, match="another payload"):
        _ = service.add_slide(
            TEMPLATE_ID,
            slide.model_copy(update={"title": "Different"}),
            request_id="add-retry-operation",
        )
    with pytest.raises(ToolError, match="another payload"):
        _ = service.add_slide(
            TEMPLATE_ID, slide, embed_assets=True, request_id="add-retry-operation"
        )
    (store.folder(TEMPLATE_ID) / "theme.css").write_text(".slide {background: white;}")
    assert service.add_slide(TEMPLATE_ID, slide, request_id="add-retry-operation") == first
    current = service.get_slide(TEMPLATE_ID, slide.id)
    assert current.content_hash != first.content_hash
    _ = service.revise_slide(
        TEMPLATE_ID,
        slide.id,
        current.expected_hash,
        "human-change-after-add",
        SlidePatch(title="Human revision", notes="Human notes"),
    )
    assert service.add_slide(TEMPLATE_ID, slide, request_id="add-retry-operation") == first
    assert store.locate(store.read(TEMPLATE_ID), slide.id).notes == "Human notes"
    changed = service.get_slide(TEMPLATE_ID, slide.id)
    store.remove_slide(TEMPLATE_ID, slide.id, changed.expected_hash)
    assert service.add_slide(TEMPLATE_ID, slide, request_id="add-retry-operation") == first
    assert slide.id not in [item.id for item in store.read(TEMPLATE_ID).slides]
    with pytest.raises(ToolError, match="already exists"):
        _ = service.add_slide(
            TEMPLATE_ID, store.read(TEMPLATE_ID).slides[0], request_id="unrecorded-add"
        )


def test_embedded_add_retry_does_not_reread_assets(store: Store) -> None:
    """Hash caller source before embedding and return the saved view on retry."""
    slide = Slide(
        id="embedded-add",
        title="Embedded",
        html='<article class="slide">Fallback</article>',
        interactive_html='<img src="assets/custom.svg">',
    )
    first = service.add_slide(
        TEMPLATE_ID,
        slide,
        embed_assets=True,
        include_content=True,
        request_id="embedded-add-operation",
    )
    assert isinstance(first, SlideView) and "base64" in first.interactive_html
    (store.folder(TEMPLATE_ID) / "assets/custom.svg").unlink()
    retry = service.add_slide(
        TEMPLATE_ID,
        slide,
        embed_assets=True,
        include_content=True,
        request_id="embedded-add-operation",
    )
    assert retry == first


def test_full_add_acknowledgement_keeps_released_history_but_never_pending_comments(
    store: Store,
) -> None:
    """Reusing a deleted ID preserves the full-response discussion contract at write time."""
    before = service.get_slide(TEMPLATE_ID, "cover")
    _ = store.add_feedback(
        TEMPLATE_ID,
        before.id,
        "Released older discussion",
        before.content_hash,
        "older-add-discussion",
    )
    _ = store.set_mode(TEMPLATE_ID, "batch")
    _ = store.add_feedback(
        TEMPLATE_ID,
        before.id,
        "WITHHELD_ADD_HISTORY",
        before.content_hash,
        "older-withheld-discussion",
    )
    store.remove_slide(TEMPLATE_ID, before.id, before.expected_hash)
    slide = Slide(
        id=before.id, title="Reused ID", html='<article class="slide">New content</article>'
    )
    added = service.add_slide(
        TEMPLATE_ID, slide, include_content=True, request_id="reused-slide-add"
    )
    assert isinstance(added, SlideView)
    assert [item.message for item in added.feedback] == ["Released older discussion"]
    assert "WITHHELD_ADD_HISTORY" not in added.model_dump_json()
    assert (
        service.add_slide(TEMPLATE_ID, slide, include_content=True, request_id="reused-slide-add")
        == added
    )


def test_unrecorded_storage_interruption_never_guesses_add_ownership(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A canonical write without its committed operation record needs explicit reconciliation."""
    original = store.seed_slide_views

    def interrupted(*_: object) -> None:
        raise OSError("Interrupted after file write")

    monkeypatch.setattr(store, "seed_slide_views", interrupted)
    slide = Slide(
        id="interrupted-add", title="Interrupted", html='<article class="slide">Saved</article>'
    )
    with pytest.raises(OSError, match="Interrupted"):
        _ = service.add_slide(TEMPLATE_ID, slide, request_id="interrupted-operation")
    monkeypatch.setattr(store, "seed_slide_views", original)
    assert store.locate(store.read(TEMPLATE_ID), slide.id).html == slide.html
    with pytest.raises(ToolError, match="already exists"):
        _ = service.add_slide(TEMPLATE_ID, slide, request_id="interrupted-operation")


def test_assets_embed_and_store_without_binary_roundtrip(store: Store) -> None:
    """Send small local references and receive a receipt rather than encoded dependencies."""
    before = service.get_slide(TEMPLATE_ID, "cover")
    source = '<h1>Custom</h1><img src="assets/custom.svg"><script>window.test=true;</script>'
    result = service.revise_slide(
        TEMPLATE_ID,
        before.id,
        before.expected_hash,
        "server-embed-test",
        SlidePatch(interactive_html=source),
        embed_assets=True,
    )
    assert isinstance(result, SlideReceipt)
    saved = service.get_slide(TEMPLATE_ID, before.id)
    assert "data:image/svg+xml;base64," in saved.interactive_html
    assert "base64" not in result.model_dump_json()
    assert saved.html == before.html
    repeated = service.revise_slide(
        TEMPLATE_ID,
        before.id,
        before.expected_hash,
        "server-embed-test",
        SlidePatch(interactive_html=source),
        embed_assets=True,
    )
    assert repeated == result
    (store.folder(TEMPLATE_ID) / "assets/custom.svg").unlink()
    retry = service.revise_slide(
        TEMPLATE_ID,
        before.id,
        before.expected_hash,
        "server-embed-test",
        SlidePatch(interactive_html=source),
        embed_assets=True,
    )
    assert retry == result
    assert service.get_slide(TEMPLATE_ID, before.id).interactive_html == saved.interactive_html
    with pytest.raises(ToolError, match="another payload"):
        service.revise_slide(
            TEMPLATE_ID,
            before.id,
            before.expected_hash,
            "server-embed-test",
            SlidePatch(interactive_html=source),
        )


def test_cached_rules_are_omitted_only_when_the_exact_version_is_retained(store: Store) -> None:
    """Deliver edited primary or secondary rules immediately without exposing Batch comments."""
    first = service.read_feedback(TEMPLATE_ID)
    assert first.rules_included and first.authoring_instructions
    store.set_mode(TEMPLATE_ID, "batch")
    slide = service.get_slide(TEMPLATE_ID, "cover")
    store.add_feedback(TEMPLATE_ID, slide.id, "Withheld", slide.content_hash, "private-batch-test")
    same = service.read_feedback(TEMPLATE_ID, known_rules_hash=first.rules_hash)
    assert (
        not same.rules_included and not same.authoring_instructions and not same.learned_preferences
    )
    assert same.events == []
    store.save_instructions("New explicit rule", digest(store.authoring_instructions()))
    changed = service.read_feedback(TEMPLATE_ID, known_rules_hash=first.rules_hash)
    assert changed.rules_included and changed.authoring_instructions == "New explicit rule"
    assert changed.rules_hash != first.rules_hash and changed.events == []
    (store.root / "LEARNED-PREFERENCES.md").write_text("A new secondary preference")
    learned = service.read_feedback(TEMPLATE_ID, known_rules_hash=changed.rules_hash)
    assert learned.rules_included and learned.learned_preferences == "A new secondary preference"


@pytest.mark.parametrize("reader", ["working", "expanded", "outline", "brief", "presentation"])
def test_rules_retention_applies_to_every_agent_context_read(store: Store, reader: str) -> None:
    """Reuse guidance across read tools without hiding changes or losing the deck context."""
    (store.root / "LEARNED-PREFERENCES.md").write_text("Prefer a clear visual hierarchy.")

    def read(known_rules_hash: str = "") -> dict[str, object]:
        """Exercise the same public hash contract on each context surface."""
        if reader == "working":
            return service.get_working_context(known_rules_hash=known_rules_hash)
        if reader == "expanded":
            return service.get_working_context(detail="full", known_rules_hash=known_rules_hash)
        if reader in {"outline", "brief"}:
            return service.get_presentation_outline(
                TEMPLATE_ID, include_context=reader == "brief", known_rules_hash=known_rules_hash
            )
        return service.get_presentation(TEMPLATE_ID, known_rules_hash=known_rules_hash).model_dump()

    initial = read()
    token = str(initial["rules_hash"])
    assert initial["rules_included"] and initial["authoring_instructions"]
    retained = read(token)
    assert retained["rules_hash"] == token and retained["rules_included"] is False
    assert retained["authoring_instructions"] == retained["learned_preferences"] == ""
    if reader in {"brief", "presentation"}:
        assert retained["context_markdown"] == initial["context_markdown"]
    assert read()["authoring_instructions"] == initial["authoring_instructions"]
    assert read("unknown-version")["rules_included"] is True
    store.save_instructions(
        "Always inspect rendered charts.", digest(store.authoring_instructions())
    )
    changed = read(token)
    assert changed["rules_included"] is True and changed["rules_hash"] != token
    assert changed["authoring_instructions"] == "Always inspect rendered charts."
    assert changed["learned_preferences"] == "Prefer a clear visual hierarchy."
    (store.root / "LEARNED-PREFERENCES.md").write_text("Keep diagrams legible.")
    changed_again = read(str(changed["rules_hash"]))
    assert changed_again["rules_included"] is True
    assert changed_again["learned_preferences"] == "Keep diagrams legible."


def test_context_append_retries_do_not_duplicate_or_overwrite(store: Store) -> None:
    """Reuse full-update concurrency protection without retransmitting prior context."""
    original = store.view(TEMPLATE_ID)
    result = service.append_presentation_context(
        TEMPLATE_ID, "A concise continuation.", original.context_hash
    )
    assert (
        service.append_presentation_context(
            TEMPLATE_ID, "A concise continuation.", original.context_hash
        )
        == result
    )
    current = store.view(TEMPLATE_ID)
    assert current.context_markdown.count("A concise continuation.") == 1
    assert current.context_markdown.startswith(original.context_markdown)
    with pytest.raises(ToolError, match="context changed"):
        service.append_presentation_context(
            TEMPLATE_ID, "A different continuation.", original.context_hash
        )
    with pytest.raises(ValueError, match="nonempty"):
        store.append_context(TEMPLATE_ID, "  ", result)
