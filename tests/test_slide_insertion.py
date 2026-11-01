"""Guarded slide placement shares the original add acknowledgement and canonical write."""

from pathlib import Path
from typing import TypedDict

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from studio import app as service
from studio.models import DeckSection, Slide, SlideReceipt
from studio.projects import ClientState, open_project, save_project
from studio.sections import Sections, sections_hash
from studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    """Use a fictional design with two named groups and an empty group between them."""
    make_design(tmp_path / TEMPLATE_ID)
    result = Store(tmp_path, tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", result)
    ids = [slide.id for slide in result.read(TEMPLATE_ID).slides]
    _ = Sections(result).replace(
        TEMPLATE_ID,
        ids,
        [
            DeckSection(id="intro", title="Introduction", slide_ids=ids[:1]),
            DeckSection(id="empty", title="Empty", slide_ids=[]),
            DeckSection(id="body", title="Body", slide_ids=ids[1:]),
        ],
        ids,
        sections_hash([]),
    )
    return result


class InsertionGuards(TypedDict):
    """Exact named concurrency inputs returned by the outline."""

    expected_slide_ids: list[str]
    expected_sections_hash: str


def guards(store: Store, deck_id: str = TEMPLATE_ID) -> InsertionGuards:
    """Freeze structural tokens just as an author reads them from the outline."""
    deck = store.read(deck_id)
    return {
        "expected_slide_ids": [item.id for item in deck.slides],
        "expected_sections_hash": sections_hash(deck.sections),
    }


@pytest.mark.parametrize(
    "placement,expected,group_id",
    [
        ({"after_slide_id": "cover"}, ["cover", "evidence", "message"], "intro"),
        ({"section_id": "intro"}, ["cover", "evidence", "message"], "intro"),
        ({"section_id": "empty"}, ["cover", "evidence", "message"], "empty"),
        ({"after_slide_id": "", "section_id": "body"}, ["cover", "evidence", "message"], "body"),
        ({"after_slide_id": ""}, ["evidence", "cover", "message"], "intro"),
    ],
)
def test_targeted_add_updates_order_and_membership_together(
    store: Store, placement: dict[str, str], expected: list[str], group_id: str
) -> None:
    """Insert within earlier/empty groups or at the start without disturbing existing slides."""
    before = {item.id: item.model_dump() for item in store.view(TEMPLATE_ID).slides}
    slide = Slide(id="evidence", title="Evidence", html="<article>Evidence</article>")
    result = service.add_slide(
        TEMPLATE_ID,
        slide,
        request_id="targeted-add-001",
        **guards(store),
        after_slide_id=placement.get("after_slide_id"),
        section_id=placement.get("section_id", ""),
    )
    assert isinstance(result, SlideReceipt)
    current = store.read(TEMPLATE_ID)
    assert [item.id for item in current.slides] == expected
    assert "evidence" in next(group for group in current.sections if group.id == group_id).slide_ids
    assert {
        item.id: item.model_dump()
        for item in store.view(TEMPLATE_ID).slides
        if item.id != "evidence"
    } == before


def test_replay_never_moves_revises_or_recreates_a_later_slide(store: Store) -> None:
    """A lost-response retry preserves later ordering, section names and user notes."""
    slide = Slide(id="evidence", title="Evidence", html="<article>Evidence</article>")
    baseline = guards(store)
    first = service.add_slide(
        TEMPLATE_ID, slide, request_id="targeted-replay", after_slide_id="cover", **baseline
    )
    assert isinstance(first, SlideReceipt)
    current = store.read(TEMPLATE_ID)
    current.sections[0].slide_ids.remove("evidence")
    current.sections[-1].slide_ids.append("evidence")
    current.sections[0].title = "New introduction"
    item = next(item for item in current.slides if item.id == "evidence")
    item.notes = "Later human notes"
    current.slides.remove(item)
    current.slides.append(item)
    store.write(TEMPLATE_ID, current)
    assert (
        service.add_slide(
            TEMPLATE_ID, slide, request_id="targeted-replay", after_slide_id="cover", **baseline
        )
        == first
    )
    assert store.read(TEMPLATE_ID).slides[-1].notes == "Later human notes"
    current_hash = service.get_slide(TEMPLATE_ID, "evidence").expected_hash
    store.remove_slide(TEMPLATE_ID, "evidence", current_hash)
    assert (
        service.add_slide(
            TEMPLATE_ID, slide, request_id="targeted-replay", after_slide_id="cover", **baseline
        )
        == first
    )
    assert "evidence" not in [item.id for item in store.read(TEMPLATE_ID).slides]


def test_stale_unknown_or_incomplete_placement_fails_before_embedding(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never prepare source assets or partially append a slide for an invalid destination."""
    slide = Slide(
        id="evidence",
        title="Evidence",
        html="<article>Evidence</article>",
        interactive_html="<article>Custom</article>",
    )
    baseline = guards(store)
    before = store.read(TEMPLATE_ID).model_dump_json()

    def unexpected(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("Embedding must follow placement validation")

    monkeypatch.setattr(service, "prepare_document", unexpected)
    for anchor, section, expected_ids, expected_hash in (
        ("unknown", "", baseline["expected_slide_ids"], baseline["expected_sections_hash"]),
        (None, "unknown", baseline["expected_slide_ids"], baseline["expected_sections_hash"]),
        ("cover", "body", baseline["expected_slide_ids"], baseline["expected_sections_hash"]),
        ("cover", "", ["wrong"], baseline["expected_sections_hash"]),
        ("cover", "", None, ""),
    ):
        with pytest.raises(ToolError):
            _ = service.add_slide(
                TEMPLATE_ID,
                slide,
                request_id="invalid-insertion",
                embed_assets=True,
                after_slide_id=anchor,
                section_id=section,
                expected_slide_ids=expected_ids,
                expected_sections_hash=expected_hash,
            )
        assert store.read(TEMPLATE_ID).model_dump_json() == before
    with pytest.raises(ToolError, match="request_id"):
        _ = service.add_slide(TEMPLATE_ID, slide, after_slide_id="cover", **baseline)


def test_placement_changes_conflict_on_retry_and_portable_receipts_survive(store: Store) -> None:
    """The retry identity includes position and guards and works after private project import."""
    slide = Slide(id="evidence", title="Evidence", html="<article>Evidence</article>")
    baseline = guards(store)
    first = service.add_slide(
        TEMPLATE_ID, slide, request_id="portable-insertion", section_id="intro", **baseline
    )
    with pytest.raises(ToolError, match="another payload"):
        _ = service.add_slide(
            TEMPLATE_ID, slide, request_id="portable-insertion", section_id="body", **baseline
        )
    opened, _ = open_project(store, save_project(store, TEMPLATE_ID, ClientState()))
    replay = service.add_slide(
        opened.id, slide, request_id="portable-insertion", section_id="intro", **baseline
    )
    assert isinstance(first, SlideReceipt) and isinstance(replay, SlideReceipt)
    assert replay.deck_id == opened.id and replay.expected_hash == first.expected_hash
    assert [item.id for item in opened.slides].count("evidence") == 1


def test_prepending_keeps_presentation_messages_on_the_original_slide(store: Store) -> None:
    """A new opening slide must not acquire an older presentation chat's viewing hint."""
    with store.editing(TEMPLATE_ID) as db:
        event_id = store.append_feedback(
            db, TEMPLATE_ID, "", "user", "Original presentation context", "", released=True
        )
    assert (
        next(
            item for item in store.comments(TEMPLATE_ID) if item.event_id == int(event_id)
        ).context_slide_id
        is None
    )
    _ = service.add_slide(
        TEMPLATE_ID,
        Slide(id="new-opening", title="New opening", html="<article>New opening</article>"),
        request_id="prepend-presentation-context",
        after_slide_id="",
        **guards(store),
    )
    assert store.read(TEMPLATE_ID).slides[0].id == "new-opening"
    assert (
        next(
            item for item in store.comments(TEMPLATE_ID) if item.event_id == int(event_id)
        ).context_slide_id
        == "cover"
    )
