"""Human fragment decisions preserve sibling content and retained review contexts."""

import os
from pathlib import Path

import pytest

from artifact_studio.element_alternatives import ElementAlternatives, ElementInput, ElementSet
from artifact_studio.models import SlidePatch
from artifact_studio.slide_designs import DecideDesign, DesignInput, SlideDesigns, UndoDesign
from artifact_studio.storage import ConflictError, Store, digest
from tests.design_factory import TEMPLATE_ID, make_design

SOURCE = (
    '<article class="slide">\n  <!-- preserve this exact source -->\n'
    '  <h1 id="headline" style="font-size:60px">Original headline</h1>\n'
    '  <section id="container"><p id="copy">Original supporting text.</p></section>\n'
    '  <svg id="graphic" viewBox="0 0 100 100"><circle cx="50" cy="50" r="20"/></svg>\n'
    "</article>"
)


@pytest.fixture
def alternatives(tmp_path: Path) -> ElementAlternatives:
    root = tmp_path / "collection"
    make_design(root / TEMPLATE_ID)
    store = Store(root, tmp_path / "state/review.sqlite3")
    deck = store.read(TEMPLATE_ID)
    deck.slides[0] = deck.slides[0].model_copy(update={"html": SOURCE, "notes": "Private notes"})
    store.write(TEMPLATE_ID, deck)
    return ElementAlternatives(store)


def publish(service: ElementAlternatives, target: str = "headline") -> ElementSet:
    slide = service.store.locate(service.store.read(TEMPLATE_ID), "cover")
    tag = "h1" if target == "headline" else "p"
    return service.publish(
        TEMPLATE_ID,
        "cover",
        target,
        "headline" if target == "headline" else "text",
        "Headline" if target == "headline" else "Supporting text",
        service.store.content_hash(TEMPLATE_ID, slide),
        "publish-" + target,
        [
            ElementInput(
                name="Clearer",
                html=f'<{tag} id="{target}">Better {target}</{tag}>',
            )
        ],
    )


def choice(
    service: ElementAlternatives,
    group: ElementSet,
    index: int | None = 1,
    request: str = "choose-element",
):
    page = service.list(TEMPLATE_ID, "cover", set_id=group.id)
    fresh = page.sets[0]
    return service.decide(
        TEMPLATE_ID,
        "cover",
        DecideDesign(
            set_id=group.id,
            option_id=fresh.options[index].id if index is not None else None,
            expected_content_hash=page.content_hash,
            expected_decision_id=fresh.decision_id,
            request_id=request,
        ),
    )


def test_publication_has_real_wording_and_retained_original_without_source_write(
    alternatives: ElementAlternatives,
):
    before = alternatives.store.read(TEMPLATE_ID)
    group = publish(alternatives)
    assert alternatives.store.read(TEMPLATE_ID) == before
    assert (
        group.options[0].html == '<h1 id="headline" style="font-size:60px">Original headline</h1>'
    )
    assert group.options[1].text == "Better headline"
    assert group.options[1].source_hash == digest(group.options[1].html)
    assert group.pending and not group.stale
    assert publish(alternatives) == group
    assert (
        "Private notes"
        not in alternatives.preview(TEMPLATE_ID, "cover", group.options[1].id).document
    )
    assert alternatives.store.comments(TEMPLATE_ID)[0].element_set_id == group.id
    assert alternatives.store.view(TEMPLATE_ID).element_alternatives["cover"][0]["pending"] is True


def test_disjoint_choices_compose_current_context_and_preserve_immutable_preview(
    alternatives: ElementAlternatives,
):
    headline = publish(alternatives)
    copy = publish(alternatives, "copy")
    frozen = alternatives.preview(TEMPLATE_ID, "cover", copy.options[1].id, copy.context_hash)
    headline_choice = choice(alternatives, headline)
    copy_now = alternatives.list(TEMPLATE_ID, "cover", set_id=copy.id).sets[0]
    assert copy_now.pending and not copy_now.stale
    assert copy_now.context_hash == headline_choice.content_hash != copy.context_hash
    assert (
        alternatives.preview(TEMPLATE_ID, "cover", copy.options[1].id, copy.context_hash) == frozen
    )
    composed = alternatives.preview(TEMPLATE_ID, "cover", copy.options[1].id)
    assert "Better headline" in composed.document and "Better copy" in composed.document
    _ = choice(alternatives, copy_now, request="choose-copy")
    current = alternatives.store.locate(alternatives.store.read(TEMPLATE_ID), "cover")
    assert current.html == SOURCE.replace(
        '<h1 id="headline" style="font-size:60px">Original headline</h1>',
        '<h1 id="headline">Better headline</h1>',
    ).replace('<p id="copy">Original supporting text.</p>', '<p id="copy">Better copy</p>')
    assert current.notes == "Private notes"


def test_keep_undo_and_concurrent_notes_preserve_approval_and_siblings(
    alternatives: ElementAlternatives,
):
    store = alternatives.store
    current = store.locate(store.read(TEMPLATE_ID), "cover")
    store.approve(TEMPLATE_ID, "cover", store.content_hash(TEMPLATE_ID, current), True)
    group = publish(alternatives)
    kept = choice(alternatives, group, None)
    assert kept.action == "keep" and store.view(TEMPLATE_ID).slides[0].approved
    _ = store.save_notes(TEMPLATE_ID, "cover", "New notes", "Private notes")
    selected = choice(alternatives, group, request="reopen-and-select")
    assert not store.view(TEMPLATE_ID).slides[0].approved
    after = store.locate(store.read(TEMPLATE_ID), "cover")
    _ = store.save_notes(TEMPLATE_ID, "cover", "Newest notes", after.notes)
    body = UndoDesign(
        decision_id=selected.id,
        expected_content_hash=selected.content_hash,
        request_id="undo-element-choice",
    )
    undone = alternatives.undo(TEMPLATE_ID, "cover", body)
    assert alternatives.undo(TEMPLATE_ID, "cover", body) == undone
    actual = store.locate(store.read(TEMPLATE_ID), "cover")
    assert actual.html == SOURCE and actual.notes == "Newest notes"
    assert store.view(TEMPLATE_ID).slides[0].approved


def test_unrelated_external_edits_require_refresh_even_if_target_still_matches(
    alternatives: ElementAlternatives,
):
    group = publish(alternatives)
    current = alternatives.store.locate(alternatives.store.read(TEMPLATE_ID), "cover")
    _ = alternatives.store.revise(
        TEMPLATE_ID,
        "cover",
        digest(current.model_dump_json()),
        "outside-source-edit",
        SlidePatch(html=current.html.replace("Original supporting", "Changed supporting")),
    )
    stale = alternatives.list(TEMPLATE_ID, "cover").sets[0]
    assert stale.stale and not stale.pending
    with pytest.raises(ConflictError, match="changed outside"):
        choice(alternatives, stale)
    assert choice(alternatives, group, None, "keep-new-current").action == "keep"


def test_whole_slide_choice_archives_elements_even_if_id_and_fragment_are_retained(
    alternatives: ElementAlternatives,
):
    group = publish(alternatives)
    service = SlideDesigns(alternatives.store)
    current = alternatives.store.locate(alternatives.store.read(TEMPLATE_ID), "cover")
    designs = service.publish(
        TEMPLATE_ID,
        "cover",
        alternatives.store.content_hash(TEMPLATE_ID, current),
        "whole-slide-designs",
        [
            DesignInput(
                name="New canvas",
                html=SOURCE.replace('class="slide"', 'class="slide" style="background:pink"'),
            )
        ],
    )
    _ = service.decide(
        TEMPLATE_ID,
        "cover",
        DecideDesign(
            set_id=designs.id,
            option_id=designs.options[1].id,
            expected_content_hash=group.context_hash,
            request_id="choose-new-canvas",
        ),
    )
    saved = alternatives.list(TEMPLATE_ID, "cover").sets[0]
    assert saved.stale and not saved.pending and "earlier slide design" in saved.stale_reason
    with pytest.raises(ConflictError, match="earlier slide design"):
        choice(alternatives, saved)


@pytest.mark.parametrize(
    "html",
    [
        '<h1 id="other">Wrong id</h1>',
        '<h1 id="headline">One</h1><p>Two</p>',
        '<h1 id="headline" onclick="alert(1)">Unsafe</h1>',
        '<h1 id="headline"><style>p{color:red}</style>Global effects</h1>',
        '<h1 id="headline"><b id="copy">Collision outside target</b></h1>',
        '<h1 id="headline"><b id="dup">One</b><i id="dup">Two</i></h1>',
        '<h1 id="headline"><b>Unbalanced</h1>',
    ],
)
def test_unsafe_or_ambiguous_fragments_never_write(alternatives: ElementAlternatives, html: str):
    before = alternatives.store.read(TEMPLATE_ID)
    current = before.slides[0]
    with pytest.raises(ValueError):
        alternatives.publish(
            TEMPLATE_ID,
            "cover",
            "headline",
            "headline",
            "Headline",
            alternatives.store.content_hash(TEMPLATE_ID, current),
            "invalid-fragment",
            [ElementInput(name="Invalid", html=html)],
        )
    assert alternatives.store.read(TEMPLATE_ID) == before


def test_overlapping_targets_and_interactive_slide_rejected(alternatives: ElementAlternatives):
    _ = publish(alternatives, "copy")
    current = alternatives.store.locate(alternatives.store.read(TEMPLATE_ID), "cover")
    with pytest.raises(ValueError, match="overlap"):
        alternatives.publish(
            TEMPLATE_ID,
            "cover",
            "container",
            "graphic",
            "Container",
            alternatives.store.content_hash(TEMPLATE_ID, current),
            "overlapping-targets",
            [ElementInput(name="New", html='<section id="container">New</section>')],
        )
    deck = alternatives.store.read(TEMPLATE_ID)
    deck.slides[0] = current.model_copy(
        update={"interactive_html": "<html><body>Interactive</body></html>"}
    )
    alternatives.store.write(TEMPLATE_ID, deck)
    with pytest.raises(ValueError, match="interactive_html"):
        publish(alternatives)


def test_same_size_resource_edit_with_preserved_mtime_blocks_choice(
    alternatives: ElementAlternatives,
):
    asset = alternatives.store.folder(TEMPLATE_ID) / "assets/local.txt"
    _ = asset.write_text("first")
    group = publish(alternatives)
    saved = alternatives.preview(TEMPLATE_ID, "cover", group.options[1].id)
    stamp = asset.stat()
    _ = asset.write_text("other")
    os.utime(asset, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert alternatives.preview(TEMPLATE_ID, "cover", group.options[1].id) == saved
    with pytest.raises(ConflictError, match="resources changed"):
        choice(alternatives, group)


def test_prepared_choice_recovers_once_after_canonical_write_crash(
    alternatives: ElementAlternatives, monkeypatch: pytest.MonkeyPatch
):
    group = publish(alternatives)
    store = alternatives.store
    real_write = store.write

    def crash_after_write(deck_id: str, deck: object) -> None:
        from artifact_studio.models import Deck

        assert isinstance(deck, Deck)
        real_write(deck_id, deck)
        raise OSError("Simulated crash after canonical write")

    monkeypatch.setattr(store, "write", crash_after_write)
    with pytest.raises(OSError, match="Simulated"):
        choice(alternatives, group)
    monkeypatch.setattr(store, "write", real_write)
    recovered = alternatives.list(TEMPLATE_ID, "cover")
    assert len(recovered.decisions) == 1 and not recovered.sets[0].pending
    assert store.locate(store.read(TEMPLATE_ID), "cover").revision == 2
    assert alternatives.list(TEMPLATE_ID, "cover") == recovered


def test_changed_resources_before_lazy_context_capture_retain_original_preview(
    alternatives: ElementAlternatives,
):
    asset = alternatives.store.folder(TEMPLATE_ID) / "assets/local.txt"
    _ = asset.write_text("first")
    headline = publish(alternatives)
    copy = publish(alternatives, "copy")
    frozen = alternatives.preview(TEMPLATE_ID, "cover", copy.options[1].id)
    _ = choice(alternatives, headline)
    stamp = asset.stat()
    _ = asset.write_text("other")
    os.utime(asset, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    saved = alternatives.list(TEMPLATE_ID, "cover", set_id=copy.id).sets[0]
    assert saved.stale and not saved.pending and saved.context_hash == copy.context_hash
    assert alternatives.preview(TEMPLATE_ID, "cover", copy.options[1].id) == frozen


def test_competing_decisions_retries_and_wrong_context_are_guarded(
    alternatives: ElementAlternatives,
):
    group = publish(alternatives)
    body = DecideDesign(
        set_id=group.id,
        option_id=group.options[1].id,
        expected_content_hash=group.context_hash,
        request_id="exact-choice-request",
    )
    selected = alternatives.decide(TEMPLATE_ID, "cover", body)
    assert alternatives.decide(TEMPLATE_ID, "cover", body) == selected
    with pytest.raises(ConflictError, match="Request ID"):
        alternatives.decide(TEMPLATE_ID, "cover", body.model_copy(update={"option_id": None}))
    with pytest.raises(ConflictError, match="Another reviewer"):
        alternatives.decide(
            TEMPLATE_ID,
            "cover",
            body.model_copy(
                update={
                    "expected_content_hash": selected.content_hash,
                    "request_id": "competing-choice-request",
                }
            ),
        )
    with pytest.raises(ValueError, match="preview context"):
        alternatives.read(TEMPLATE_ID, "cover", group.options[1].id, "0" * 64)
    with pytest.raises(ValueError, match="this slide"):
        alternatives.read(TEMPLATE_ID, "message", group.options[1].id)


def test_fresh_publication_supersedes_only_its_same_target_invitation(
    alternatives: ElementAlternatives,
):
    first = publish(alternatives)
    copy = publish(alternatives, "copy")
    frozen = alternatives.preview(TEMPLATE_ID, "cover", first.options[1].id)
    slide = alternatives.store.read(TEMPLATE_ID).slides[0]
    newer = alternatives.publish(
        TEMPLATE_ID,
        "cover",
        "headline",
        "headline",
        "Headline",
        alternatives.store.content_hash(TEMPLATE_ID, slide),
        "new-headline-publication",
        [ElementInput(name="More direct", html='<h1 id="headline">A new direction</h1>')],
    )
    groups = {item.id: item for item in alternatives.list(TEMPLATE_ID, "cover").sets}
    assert not groups[first.id].pending and groups[newer.id].pending and groups[copy.id].pending
    assert alternatives.preview(TEMPLATE_ID, "cover", first.options[1].id) == frozen
    assert (
        alternatives.read(TEMPLATE_ID, "cover", first.options[1].id).html == first.options[1].html
    )
    assert alternatives.store.read(TEMPLATE_ID).slides[0].html == SOURCE
