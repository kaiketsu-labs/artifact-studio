"""Write retries preserve original evidence without restoring obsolete discussion state."""

import json
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from artifact_studio import app as service
from artifact_studio.models import (
    Deck,
    Feedback,
    FileInput,
    Slide,
    SlidePatch,
    SlideReceipt,
    SlideView,
)
from artifact_studio.projects import ClientState, open_project, save_project
from artifact_studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    """Keep the canonical deck and review records isolated from user content."""
    make_design(tmp_path / TEMPLATE_ID)
    result = Store(tmp_path, tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", result)
    return result


def test_compact_writes_and_retries_do_not_load_discussion(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compact writes must work without loading potentially large private feedback histories."""
    before = service.get_slide(TEMPLATE_ID, "cover")

    def forbidden_discussion(_deck_id: str) -> list[Feedback]:
        raise AssertionError("Compact writes must not load discussion")

    monkeypatch.setattr(store, "comments", forbidden_discussion)
    patch = SlidePatch(title="Compact revision")
    slide = Slide(id="compact", title="Compact", html="<article>Compact</article>")
    revised = service.revise_slide(
        TEMPLATE_ID, "cover", before.expected_hash, "compact-revision", patch
    )
    assert (
        service.revise_slide(TEMPLATE_ID, "cover", before.expected_hash, "compact-revision", patch)
        == revised
    )
    added = service.add_slide(TEMPLATE_ID, slide, request_id="compact-add")
    assert service.add_slide(TEMPLATE_ID, slide, request_id="compact-add") == added


def test_equivalent_scoped_html_returns_receipt_without_writing_deck(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recognize equivalent generated CSS scopes before writing source or review history."""
    source = '<article class="slide"><style>h1{color:blue}</style><h1>Plan</h1></article>'
    slide = store.add_slide(TEMPLATE_ID, Slide(id="styled", title="Styled", html=source))
    before = service.get_slide(TEMPLATE_ID, slide.id)
    store.approve(TEMPLATE_ID, slide.id, before.content_hash, True)

    def forbidden_write(_deck_id: str, _deck: Deck) -> None:
        raise AssertionError("Equivalent slide source must not rewrite the deck")

    monkeypatch.setattr(store, "write", forbidden_write)
    for request_id in ("equivalent-style", "equivalent-style"):
        receipt = service.revise_slide(
            TEMPLATE_ID, slide.id, before.expected_hash, request_id, SlidePatch(html=source)
        )
        assert receipt.approved and receipt.revision == before.revision
        assert receipt.expected_hash == before.expected_hash
        assert receipt.content_hash == before.content_hash


@pytest.mark.parametrize("drift", ["theme", "asset"])
def test_revision_replay_retains_visual_baseline_and_original_status(
    store: Store, drift: str
) -> None:
    """A retry must not bless visual changes or subsequent approval as the original write."""
    before = service.get_slide(TEMPLATE_ID, "cover")
    patch = SlidePatch(title="Recorded revision")
    first = service.revise_slide(TEMPLATE_ID, "cover", before.expected_hash, "visual-retry", patch)
    assert isinstance(first, SlideReceipt)
    store.approve(TEMPLATE_ID, "cover", first.content_hash, True)
    path = store.folder(TEMPLATE_ID) / ("theme.css" if drift == "theme" else "assets/custom.svg")
    _ = path.write_text(path.read_text() + "\n<!-- visual drift -->\n")
    retry = service.revise_slide(TEMPLATE_ID, "cover", before.expected_hash, "visual-retry", patch)
    current = service.get_slide(TEMPLATE_ID, "cover")
    assert retry == first and not retry.approved
    assert retry.expected_hash == current.expected_hash
    assert retry.content_hash != current.content_hash
    assert retry.revision == before.revision + 1


def test_revision_retry_preserves_later_human_edits_and_deletion(store: Store) -> None:
    """Recover original source and notes without reapplying them over newer human work."""
    before = service.get_slide(TEMPLATE_ID, "cover")
    patch = SlidePatch(title="Recorded title", notes="Recorded notes")
    first = service.revise_slide(
        TEMPLATE_ID, "cover", before.expected_hash, "source-retry", patch, include_content=True
    )
    assert isinstance(first, SlideView)
    store.save_notes(TEMPLATE_ID, "cover", "Later human notes", first.notes)
    store.set_slide_hidden(TEMPLATE_ID, "cover", hidden=True, expected_hidden=False)
    assert (
        service.revise_slide(
            TEMPLATE_ID, "cover", before.expected_hash, "source-retry", patch, include_content=True
        )
        == first
    )
    current = service.get_slide(TEMPLATE_ID, "cover")
    assert current.notes == "Later human notes" and current.hidden
    store.remove_slide(TEMPLATE_ID, "cover", current.expected_hash)
    assert (
        service.revise_slide(
            TEMPLATE_ID, "cover", before.expected_hash, "source-retry", patch, include_content=True
        )
        == first
    )
    assert "cover" not in [slide.id for slide in store.read(TEMPLATE_ID).slides]


@pytest.mark.parametrize("first_entry", ["storage", "mcp"])
def test_revision_entry_points_share_frozen_records_and_source_only_contract(
    store: Store, first_entry: str
) -> None:
    """Storage callers still receive plain slides and can safely cross-retry through MCP."""
    before = service.get_slide(TEMPLATE_ID, "cover")
    patch = SlidePatch(title="Shared revision")
    if first_entry == "storage":
        original = store.revise(TEMPLATE_ID, "cover", before.expected_hash, "cross-path", patch)
        assert type(original) is Slide
        baseline = service.get_slide(TEMPLATE_ID, "cover")
    else:
        baseline = service.revise_slide(
            TEMPLATE_ID, "cover", before.expected_hash, "cross-path", patch
        )
    _ = (store.folder(TEMPLATE_ID) / "theme.css").write_text(".slide{background:red;}")
    view = service.revise_slide(
        TEMPLATE_ID, "cover", before.expected_hash, "cross-path", patch, include_content=True
    )
    assert view.content_hash == baseline.content_hash
    plain = store.revise(TEMPLATE_ID, "cover", before.expected_hash, "cross-path", patch)
    assert type(plain) is Slide and plain.title == "Shared revision"
    assert "feedback" not in plain.model_dump()
    with pytest.raises(ToolError, match="another payload"):
        _ = service.revise_slide(
            TEMPLATE_ID, "cover", before.expected_hash, "cross-path", SlidePatch(title="Other")
        )
    with pytest.raises(ToolError, match="another payload"):
        _ = service.revise_slide(
            TEMPLATE_ID, "cover", before.expected_hash, "cross-path", patch, embed_assets=True
        )


def test_legacy_revision_replay_requires_reconciliation_without_fabricating_hashes(
    store: Store,
) -> None:
    """Old source-only operation records remain readable, but cannot prove historical visuals."""
    before = service.get_slide(TEMPLATE_ID, "cover")
    patch = SlidePatch(title="Legacy revision")
    original = store.revise(TEMPLATE_ID, "cover", before.expected_hash, "legacy-retry", patch)
    with store.connect() as db:
        _ = db.execute(
            "UPDATE operations SET result=? WHERE deck=? AND request_id=?",
            (original.model_dump_json(), TEMPLATE_ID, "legacy-retry"),
        )
    saved = (store.folder(TEMPLATE_ID) / "index.html").read_bytes()
    assert (
        store.revise(TEMPLATE_ID, "cover", before.expected_hash, "legacy-retry", patch) == original
    )
    for include_content in (False, True):
        with pytest.raises(ToolError, match="no recorded visual receipt.*reconcile"):
            _ = service.revise_slide(
                TEMPLATE_ID,
                "cover",
                before.expected_hash,
                "legacy-retry",
                patch,
                include_content=include_content,
            )
    assert (store.folder(TEMPLATE_ID) / "index.html").read_bytes() == saved
    with store.connect() as db:
        row = db.execute(
            "SELECT result FROM operations WHERE deck=? AND request_id=?",
            (TEMPLATE_ID, "legacy-retry"),
        ).fetchone()
    assert row is not None and row[0] == original.model_dump_json()


def test_unrecorded_changed_revision_interruption_conflicts_on_retry(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changed source after an unrecorded write must conflict on the original expected hash."""
    before = service.get_slide(TEMPLATE_ID, "cover")
    patch = SlidePatch(title="Interrupted write")
    original_write = store.write
    with monkeypatch.context() as scoped:

        def interrupted_write(deck_id: str, deck: Deck) -> None:
            original_write(deck_id, deck)
            raise OSError("Interrupted after canonical write")

        scoped.setattr(store, "write", interrupted_write)
        with pytest.raises(OSError, match="Interrupted"):
            _ = service.revise_slide(
                TEMPLATE_ID, "cover", before.expected_hash, "interrupted-retry", patch
            )
    assert store.read(TEMPLATE_ID).slides[0].title == "Interrupted write"
    with pytest.raises(ToolError, match="Slide changed"):
        _ = service.revise_slide(
            TEMPLATE_ID, "cover", before.expected_hash, "interrupted-retry", patch
        )


def test_unrecorded_noop_interruption_can_only_start_a_fresh_visual_baseline(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unchanged source cannot distinguish a never-received call from an unrecorded no-op."""
    before = service.get_slide(TEMPLATE_ID, "cover")
    patch = SlidePatch(title=before.title)
    with monkeypatch.context() as scoped:

        def interrupted_receipt(*_args: object) -> SlideView:
            raise OSError("Interrupted before receipt commit")

        scoped.setattr(store, "_write_view", interrupted_receipt)
        with pytest.raises(OSError, match="Interrupted"):
            _ = service.revise_slide(
                TEMPLATE_ID, "cover", before.expected_hash, "interrupted-noop", patch
            )
    with store.connect() as db:
        assert (
            db.execute(
                "SELECT 1 FROM operations WHERE deck=? AND request_id=?",
                (TEMPLATE_ID, "interrupted-noop"),
            ).fetchone()
            is None
        )
    _ = (store.folder(TEMPLATE_ID) / "theme.css").write_text(".slide{background:red;}")
    result = service.revise_slide(
        TEMPLATE_ID, "cover", before.expected_hash, "interrupted-noop", patch
    )
    assert result.expected_hash == before.expected_hash and result.revision == before.revision
    assert result.content_hash != before.content_hash
    assert result.content_hash == service.get_slide(TEMPLATE_ID, "cover").content_hash


@pytest.mark.parametrize("operation", ["add", "revise"])
def test_project_retry_refreshes_only_released_discussion_and_remapped_references(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Imported retries retain write hashes while resolving current file paths and question IDs."""
    slide = Slide(id="portable", title="Portable", html='<article class="slide">Portable</article>')
    before = service.get_slide(TEMPLATE_ID, "cover")
    patch = SlidePatch(title="Portable revision")

    def replay(deck_id: str) -> SlideView | SlideReceipt:
        if operation == "add":
            return service.add_slide(
                deck_id, slide, request_id="portable-operation", include_content=True
            )
        return service.revise_slide(
            deck_id,
            "cover",
            before.expected_hash,
            "portable-operation",
            patch,
            include_content=True,
        )

    original = replay(TEMPLATE_ID)
    assert isinstance(original, SlideView)
    event_id = store.add_feedback(
        TEMPLATE_ID,
        original.id,
        "Released reference",
        original.content_hash,
        "portable-feedback",
        files=[FileInput(name="reference.txt", data_base64="cmVmZXJlbmNl")],
    )
    _ = store.add_feedback(
        TEMPLATE_ID,
        original.id,
        "Agent reply",
        original.content_hash,
        "portable-reply",
        reply_to=int(event_id),
    )
    question = service.ask_slide_question(
        TEMPLATE_ID,
        original.id,
        "Choose emphasis?",
        original.expected_hash,
        "portable-question",
        ["Evidence", "Actions"],
    )
    original_question = store.comments(TEMPLATE_ID)[-1].question
    assert original_question is not None
    _ = store.set_mode(TEMPLATE_ID, "batch")
    _ = store.add_feedback(
        TEMPLATE_ID, original.id, "WITHHELD_PORTABLE", original.content_hash, "portable-withheld"
    )
    with store.connect() as db:
        row = db.execute(
            "SELECT result FROM operations WHERE deck=? AND request_id=?",
            (TEMPLATE_ID, "portable-operation"),
        ).fetchone()
        assert row is not None and json.loads(str(row[0]))["feedback"] == []
        if operation == "add":
            # Older releases cached absolute paths, event IDs and question keys in add snapshots.
            legacy = original.model_copy(update={"feedback": store.comments(TEMPLATE_ID)})
            _ = db.execute(
                "UPDATE operations SET result=? WHERE deck=? AND request_id=?",
                (legacy.model_dump_json(), TEMPLATE_ID, "portable-operation"),
            )
    archive = save_project(store, TEMPLATE_ID, ClientState())
    target = Store(tmp_path / "target", tmp_path / "target-state/review.sqlite3")
    make_design(target.root / TEMPLATE_ID)
    _ = target.add_feedback(TEMPLATE_ID, "cover", "Existing target event", "", "target-event")
    restored, _ = open_project(target, archive)
    monkeypatch.setattr(service, "store", target)
    result = replay(restored.id)
    assert isinstance(result, SlideView)
    assert result.expected_hash == original.expected_hash
    assert result.content_hash == original.content_hash
    assert result.feedback == [
        item
        for item in target.comments(restored.id)
        if item.slide_id == original.id and item.released
    ]
    assert "WITHHELD_PORTABLE" not in result.model_dump_json()
    attachment = result.feedback[0].files[0]
    assert Path(attachment.path).read_bytes() == b"reference"
    assert Path(attachment.path).is_relative_to(target.database.parent)
    assert result.feedback[1].reply_to == result.feedback[0].event_id
    assert result.feedback[0].event_id != int(event_id)
    current_question = service.get_slide_questions(restored.id, original.id)[0]
    assert current_question.event_id != question.event_id
    assert result.feedback[2].event_id == current_question.event_id
    assert result.feedback[2].question is not None
    assert result.feedback[2].question == original_question
