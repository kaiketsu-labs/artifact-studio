"""Receipt and optimistic edit checks with fictional collaborators."""

from pathlib import Path
from typing import cast

import pytest

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.authentication import Principal
from artifact_studio.models import SlidePatch
from artifact_studio.multiplayer_access import AccessDenied, Scope, Sharing
from artifact_studio.offline_delivery import OfflineDelivery, OfflineReceiptInput
from artifact_studio.storage import ConflictError, Store
from tests.design_factory import TEMPLATE_ID, make_design


def test_receipts_guard_conflicts_retries_and_revocation(tmp_path: Path) -> None:
    collection = tmp_path / "collection"
    make_design(collection / TEMPLATE_ID)
    store = Store(collection, tmp_path / "state" / "review.sqlite3")
    sharing = Sharing(tmp_path / "sharing", store, "owner@example.test")
    owner = sharing.human(Principal("owner", "owner@example.test"))
    guest = sharing.human(Principal("guest", "guest@example.test"))
    project = ArtifactStore(store).create_project("Fictional")
    deck_id = "2026-11-14-fictional"
    store.create(deck_id, "Fictional", "test-brand")
    _ = ArtifactStore(store).set_artifact_project(deck_id, project.id)
    scope = Scope("default", "artifact", deck_id)
    invite = sharing.invite(owner, scope, "guest@example.test", "edit")
    _ = sharing.accept(guest, invite["token"])
    delivery = OfflineDelivery(sharing)
    initial = store.view(deck_id).slides[0]
    _ = store.revise(
        deck_id,
        initial.id,
        initial.expected_hash,
        "private-notes-001",
        SlidePatch(notes="Private note"),
    )
    first = store.view(deck_id).slides[0]
    request = OfflineReceiptInput(
        slide_id=first.id, content_hash=first.content_hash, expected_hash=first.expected_hash
    )
    with pytest.raises(AccessDenied):
        _ = delivery.issue_snapshot(owner, scope, "issuer-owner", [request, request])
    with sharing.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM offline_receipts").fetchone()[0] == 0
    receipts = delivery.issue_snapshot(owner, scope, "issuer-owner", [request])
    assert set(receipts) == {first.id}
    owner_receipt = delivery.issue(
        owner, scope, "issuer-owner", first.id, first.content_hash, first.expected_hash, "shared"
    )
    guest_receipt = delivery.issue(
        guest, scope, "issuer-guest", first.id, first.content_hash, first.expected_hash, "shared"
    )
    _ = store.revise(
        deck_id, first.id, first.expected_hash, "online-title-001", SlidePatch(title="Online title")
    )
    with pytest.raises(ConflictError):
        _ = delivery.issue(
            owner,
            scope,
            "issuer-owner",
            first.id,
            first.content_hash,
            first.expected_hash,
            "shared",
        )
    result = delivery.edit(
        owner, scope, "issuer-owner", owner_receipt, "offline-edit-001", "<h1>Owner change</h1>"
    )
    assert result["status"] == "applied"
    assert store.view(deck_id).slides[0].title == "Online title"
    assert store.view(deck_id).slides[0].notes == "Private note"
    assert (
        delivery.edit(
            owner, scope, "issuer-owner", owner_receipt, "offline-edit-001", "<h1>Owner change</h1>"
        )
        == result
    )
    with pytest.raises(ConflictError):
        _ = delivery.edit(
            owner, scope, "issuer-owner", owner_receipt, "offline-edit-001", "<h1>Different</h1>"
        )
    conflict = delivery.edit(
        guest, scope, "issuer-guest", guest_receipt, "guest-edit-001", "<h1>Guest change</h1>"
    )
    assert conflict["status"] == "conflict" and conflict["current"] == "<h1>Owner change</h1>"
    assert conflict["draft"] == "<h1>Guest change</h1>"
    with pytest.raises(AccessDenied):
        _ = delivery.edit(
            guest, scope, "wrong-issuer", guest_receipt, "guest-edit-002", "<h1>Guest change</h1>"
        )
    comment = delivery.comment(
        guest,
        scope,
        "issuer-guest",
        guest_receipt,
        "guest-comment-001",
        "Original anchor",
        "shared",
    )
    assert (
        delivery.comment(
            guest,
            scope,
            "issuer-guest",
            guest_receipt,
            "guest-comment-001",
            "Original anchor",
            "shared",
        )
        == comment
    )
    with sharing.connect() as db:
        row = db.execute(
            "SELECT content_hash,slide_content_hash FROM conversations WHERE id=?", (comment["id"],)
        ).fetchone()
        assert row["slide_content_hash"] == first.content_hash
        assert row["content_hash"] != ArtifactStore(store).get_artifact(deck_id).content_hash
    with pytest.raises(AccessDenied):
        _ = delivery.comment(
            guest,
            scope,
            "issuer-guest",
            guest_receipt,
            "guest-comment-002",
            "Wrong channel",
            "private",
        )
    current = store.view(deck_id).slides[0]
    _ = store.revise(
        deck_id,
        current.id,
        current.expected_hash,
        "online-title-002",
        SlidePatch(title="Newer title"),
    )
    stale_choice = delivery.edit(
        guest,
        scope,
        "issuer-guest",
        guest_receipt,
        "guest-choice-001",
        "<h1>Guest change</h1>",
        cast(str, conflict["current_expected_hash"]),
    )
    assert stale_choice["status"] == "conflict"
    chosen = delivery.edit(
        guest,
        scope,
        "issuer-guest",
        guest_receipt,
        "guest-choice-002",
        "<h1>Guest change</h1>",
        cast(str, stale_choice["current_expected_hash"]),
    )
    assert chosen["status"] == "applied"
    assert store.view(deck_id).slides[0].title == "Newer title"
    grants = cast(list[dict[str, str]], sharing.access(owner, scope)["grants"])
    sharing.revoke(owner, scope, grants[0]["id"])
    with pytest.raises(AccessDenied):
        _ = delivery.edit(
            guest, scope, "issuer-guest", guest_receipt, "guest-edit-003", "<h1>Guest change</h1>"
        )
    with pytest.raises(AccessDenied):
        _ = delivery.comment(
            guest, scope, "issuer-guest", guest_receipt, "guest-comment-003", "Revoked", "shared"
        )


def test_simultaneous_edits_preserve_the_losing_draft(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    collection = tmp_path / "collection"
    make_design(collection / TEMPLATE_ID)
    store = Store(collection, tmp_path / "state" / "review.sqlite3")
    sharing = Sharing(tmp_path / "sharing", store, "owner@example.test")
    owner = sharing.human(Principal("owner", "owner@example.test"))
    deck_id = "2026-11-14-concurrent"
    store.create(deck_id, "Fictional", "test-brand")
    scope = Scope("default", "artifact", deck_id)
    delivery = OfflineDelivery(sharing)
    slide = store.view(deck_id).slides[0]
    receipt = delivery.issue(
        owner, scope, "issuer-owner", slide.id, slide.content_hash, slide.expected_hash, "shared"
    )

    def edit(number: int) -> dict[str, object]:
        return delivery.edit(
            owner, scope, "issuer-owner", receipt, f"concurrent-{number:03}", f"<h1>{number}</h1>"
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, (1, 2)))
    assert {result["status"] for result in results} == {"applied", "conflict"}
    conflict = next(result for result in results if result["status"] == "conflict")
    assert conflict["current"] != conflict["draft"]
    assert store.view(deck_id).slides[0].html == conflict["current"]


def test_retry_recovers_after_source_write_before_database_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collection = tmp_path / "collection"
    make_design(collection / TEMPLATE_ID)
    store = Store(collection, tmp_path / "state" / "review.sqlite3")
    sharing = Sharing(tmp_path / "sharing", store, "owner@example.test")
    owner = sharing.human(Principal("owner", "owner@example.test"))
    deck_id = "2026-11-14-retry"
    store.create(deck_id, "Fictional", "test-brand")
    scope = Scope("default", "artifact", deck_id)
    delivery = OfflineDelivery(sharing)
    slide = store.view(deck_id).slides[0]
    receipt = delivery.issue(
        owner, scope, "issuer-owner", slide.id, slide.content_hash, slide.expected_hash, "shared"
    )
    write = store.write

    def interrupted(deck: str, document: object) -> None:
        from artifact_studio.models import Deck

        assert isinstance(document, Deck)
        write(deck, document)
        raise OSError("Fictional interruption after source write")

    monkeypatch.setattr(store, "write", interrupted)
    with pytest.raises(OSError):
        _ = delivery.edit(
            owner, scope, "issuer-owner", receipt, "retry-write-001", "<h1>Saved</h1>"
        )
    monkeypatch.setattr(store, "write", write)
    result = delivery.edit(
        owner, scope, "issuer-owner", receipt, "retry-write-001", "<h1>Saved</h1>"
    )
    assert result["status"] == "applied"
    assert store.view(deck_id).slides[0].html == "<h1>Saved</h1>"
    with store.connect() as db:
        assert (
            db.execute("SELECT COUNT(*) FROM revisions WHERE deck=?", (deck_id,)).fetchone()[0] >= 2
        )
