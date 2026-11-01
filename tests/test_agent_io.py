"""Efficient MCP communication preserves source access, rule updates and privacy."""

import json
from pathlib import Path
from typing import Literal, cast

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from studio import app as service
from studio.models import SlidePatch, SlideReceipt, SlideView
from studio.storage import Store, digest
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
    assert retry.revision == result.revision
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
