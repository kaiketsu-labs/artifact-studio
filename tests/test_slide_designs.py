"""Parallel slide designs must never become an implicit source write."""

import os
from pathlib import Path

import pytest

from artifact_studio.models import Deck, SlidePatch
from artifact_studio.slide_designs import DecideDesign, DesignInput, SlideDesigns, UndoDesign
from artifact_studio.storage import ConflictError, Store, digest
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def designs(tmp_path: Path) -> SlideDesigns:
    root = tmp_path / "collection"
    make_design(root / TEMPLATE_ID)
    return SlideDesigns(Store(root, tmp_path / "state/review.sqlite3"))


def publish(service: SlideDesigns, request_id: str = "publish-alternatives"):
    current = service.store.locate(service.store.read(TEMPLATE_ID), "cover")
    return service.publish(
        TEMPLATE_ID,
        "cover",
        service.store.content_hash(TEMPLATE_ID, current),
        request_id,
        [
            DesignInput(
                name="Editorial", html='<article class="slide"><h1>Editorial</h1></article>'
            ),
            DesignInput(
                name="Bold",
                html='<article class="slide" style="background:blue;color:white">'
                "<h1>Bold</h1></article>",
            ),
        ],
    )


def choose(
    service: SlideDesigns,
    group_id: str,
    option_id: str | None,
    request_id: str = "choose-direction",
    reviewed_changed_slide: bool = False,
):
    page = service.list(TEMPLATE_ID, "cover")
    group = next(item for item in page.sets if item.id == group_id)
    body = DecideDesign(
        set_id=group_id,
        option_id=option_id,
        expected_content_hash=page.content_hash,
        expected_decision_id=group.decision_id,
        request_id=request_id,
        reviewed_changed_slide=reviewed_changed_slide,
    )
    return service.decide(TEMPLATE_ID, "cover", body)


def test_publication_is_immutable_idempotent_and_never_changes_the_slide(designs: SlideDesigns):
    before = designs.store.read(TEMPLATE_ID)
    group = publish(designs)
    assert designs.store.read(TEMPLATE_ID) == before
    assert len(group.options) == 3 and group.pending and not group.stale
    assert group.current_option_id == group.options[0].id
    assert publish(designs) == group
    comments = designs.store.comments(TEMPLATE_ID)
    assert len(comments) == 1 and comments[0].design_set_id == group.id
    assert "html" not in group.model_dump_json()
    assert designs.store.view(TEMPLATE_ID).slide_designs["cover"]["pending"] is True
    with pytest.raises(ConflictError, match="Request ID"):
        designs.publish(
            TEMPLATE_ID,
            "cover",
            "0" * 64,
            "publish-alternatives",
            [DesignInput(name="Other", html='<article class="slide">Other</article>')],
        )


def test_keep_preserves_notes_source_revision_and_approval(designs: SlideDesigns):
    current = designs.store.locate(designs.store.read(TEMPLATE_ID), "cover")
    token = designs.store.content_hash(TEMPLATE_ID, current)
    designs.store.approve(TEMPLATE_ID, "cover", token, True)
    group = publish(designs)
    before = (designs.store.folder(TEMPLATE_ID) / "index.html").read_bytes()
    result = choose(designs, group.id, None)
    assert result.action == "keep" and not result.pending
    assert (designs.store.folder(TEMPLATE_ID) / "index.html").read_bytes() == before
    assert designs.store.view(TEMPLATE_ID).slides[0].approved
    assert not designs.list(TEMPLATE_ID, "cover").sets[0].pending
    assert "cover" not in designs.store.view(TEMPLATE_ID).slide_designs
    undo = designs.undo(
        TEMPLATE_ID,
        "cover",
        UndoDesign(
            decision_id=result.id,
            expected_content_hash=result.content_hash,
            request_id="undo-keep-choice",
        ),
    )
    assert undo.pending and designs.store.view(TEMPLATE_ID).slides[0].approved
    assert designs.store.view(TEMPLATE_ID).slide_designs["cover"]["pending"] is True


def test_choose_and_undo_preserve_current_private_and_structural_state(designs: SlideDesigns):
    current = designs.store.locate(designs.store.read(TEMPLATE_ID), "cover")
    designs.store.approve(
        TEMPLATE_ID, "cover", designs.store.content_hash(TEMPLATE_ID, current), True
    )
    group = publish(designs)
    _ = designs.store.save_notes(
        TEMPLATE_ID, "cover", "Human notes after publication", current.notes
    )
    designs.store.set_slide_hidden(TEMPLATE_ID, "cover", hidden=True, expected_hidden=False)
    result = choose(designs, group.id, group.options[1].id)
    after = designs.store.locate(designs.store.read(TEMPLATE_ID), "cover")
    assert "Editorial" in after.html and after.hidden
    assert after.notes == "Human notes after publication"
    assert after.revision == current.revision + 1
    assert not designs.store.view(TEMPLATE_ID).slides[0].approved
    assert len(designs.list(TEMPLATE_ID, "cover").sets[0].options) == 3
    _ = designs.store.save_notes(TEMPLATE_ID, "cover", "Even newer human notes", after.notes)
    body = UndoDesign(
        decision_id=result.id,
        expected_content_hash=result.content_hash,
        request_id="undo-source-choice",
    )
    restored = designs.undo(TEMPLATE_ID, "cover", body)
    assert designs.undo(TEMPLATE_ID, "cover", body) == restored
    actual = designs.store.locate(designs.store.read(TEMPLATE_ID), "cover")
    assert (
        actual.html == current.html and actual.hidden and actual.notes == "Even newer human notes"
    )
    assert designs.store.view(TEMPLATE_ID).slides[0].approved


def test_changed_sources_and_competing_reviewers_conflict(designs: SlideDesigns):
    group = publish(designs)
    page = designs.list(TEMPLATE_ID, "cover")
    stale = DecideDesign(
        set_id=group.id,
        option_id=group.options[1].id,
        expected_content_hash=page.content_hash,
        request_id="stale-human-choice",
    )
    current = designs.store.locate(designs.store.read(TEMPLATE_ID), "cover")
    _ = designs.store.revise(
        TEMPLATE_ID,
        "cover",
        digest(current.model_dump_json()),
        "another-editor",
        SlidePatch(html='<article class="slide">Another edit</article>'),
    )
    with pytest.raises(ConflictError, match="chosen slide changed"):
        designs.decide(TEMPLATE_ID, "cover", stale)
    with pytest.raises(ConflictError, match="Compare"):
        choose(designs, group.id, group.options[1].id)
    selected = choose(designs, group.id, group.options[1].id, reviewed_changed_slide=True)
    with pytest.raises(ConflictError, match="Another reviewer"):
        designs.decide(
            TEMPLATE_ID,
            "cover",
            stale.model_copy(
                update={
                    "expected_content_hash": selected.content_hash,
                    "request_id": "competing-reviewer",
                }
            ),
        )


def test_frozen_preview_and_asset_guard_include_preserved_mtime_edits(designs: SlideDesigns):
    asset = designs.store.folder(TEMPLATE_ID) / "assets/example.txt"
    asset.parent.mkdir(exist_ok=True)
    _ = asset.write_text("first")
    group = publish(designs)
    saved = designs.preview(TEMPLATE_ID, "cover", group.options[1].id)
    stamp = asset.stat()
    _ = asset.write_text("other")
    os.utime(asset, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert designs.preview(TEMPLATE_ID, "cover", group.options[1].id) == saved
    with pytest.raises(ConflictError, match="resources changed"):
        choose(designs, group.id, group.options[1].id)
    assert choose(designs, group.id, None).action == "keep"


def test_interrupted_write_recovers_once_and_keeps_proposal_feedback(
    designs: SlideDesigns, monkeypatch: pytest.MonkeyPatch
):
    group = publish(designs)
    source = designs.read(TEMPLATE_ID, "cover", group.options[1].id)
    _ = designs.store.add_feedback(
        TEMPLATE_ID,
        "cover",
        "Refine this proposal",
        source.option.source_hash,
        "proposal-feedback",
        design_option_id=source.option.id,
    )
    write = designs.store.write

    def interrupted(deck_id: str, deck: Deck) -> None:
        write(deck_id, deck)
        raise OSError("Simulated interruption after the atomic file replacement")

    monkeypatch.setattr(designs.store, "write", interrupted)
    page = designs.list(TEMPLATE_ID, "cover")
    body = DecideDesign(
        set_id=group.id,
        option_id=group.options[1].id,
        expected_content_hash=page.content_hash,
        request_id="recover-human-choice",
    )
    with pytest.raises(OSError, match="Simulated"):
        designs.decide(TEMPLATE_ID, "cover", body)
    recovered = SlideDesigns(Store(designs.store.root, designs.store.database))
    result = recovered.decide(TEMPLATE_ID, "cover", body)
    assert result.action == "choose" and not result.pending
    assert len(recovered.list(TEMPLATE_ID, "cover").decisions) == 1
    feedback = recovered.store.comments(TEMPLATE_ID)[-1]
    assert (
        feedback.design_option_id == source.option.id
        and feedback.content_hash == source.option.source_hash
    )


def test_history_paginates_independently_and_retained_groups_stay_addressable(
    designs: SlideDesigns,
):
    groups = [publish(designs, f"history-publication-{index}") for index in range(22)]
    for index in range(32):
        _ = choose(designs, groups[-1].id, None, f"history-decision-{index}")
    first = designs.list(TEMPLATE_ID, "cover")
    assert len(first.sets) == 20 and len(first.decisions) == 30
    assert first.next_cursor and first.next_decision_cursor
    # A concurrent new publication must not shift the earlier-page boundary.
    _ = publish(designs, "publication-between-pages")
    earlier = designs.list(
        TEMPLATE_ID,
        "cover",
        cursor=first.next_cursor,
        decision_cursor=first.next_decision_cursor,
    )
    assert [item.id for item in earlier.sets] == [groups[1].id, groups[0].id]
    assert len(earlier.decisions) == 2
    assert not earlier.next_cursor and not earlier.next_decision_cursor
    assert not ({item.id for item in first.decisions} & {item.id for item in earlier.decisions})
    retained = designs.list(TEMPLATE_ID, "cover", set_id=groups[0].id, decision_cursor=0)
    assert [item.id for item in retained.sets] == [groups[0].id] and not retained.decisions
    assert not designs.list(TEMPLATE_ID, "cover", cursor=0, decision_cursor=0).sets
