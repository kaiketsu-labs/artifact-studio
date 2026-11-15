"""Repeated saves and legacy aliases must not consume or destroy offline work."""

import hashlib
import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest

from artifact_studio.authentication import Principal
from artifact_studio.models import SlidePatch
from artifact_studio.multiplayer_access import AccessDenied, Scope, Sharing
from artifact_studio.offline_delivery import OfflineDelivery, OfflineReceiptInput
from artifact_studio.offline_receipts import ReceiptSource, retain
from artifact_studio.storage import Store
from tests.design_factory import TEMPLATE_ID, make_design


def test_repeated_concurrent_saves_reuse_source_and_preserve_second_device(tmp_path: Path) -> None:
    make_design(tmp_path / "collection" / TEMPLATE_ID)
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    store.create("2026-11-15-fictional", "Fictional", "test-brand")
    sharing = Sharing(tmp_path / "sharing", store, "owner@example.test")
    owner = sharing.human(Principal("owner", "owner@example.test"))
    scope = Scope("default", "artifact", "2026-11-15-fictional")
    delivery = OfflineDelivery(sharing)
    slide = store.view(scope.resource).slides[0]
    request = OfflineReceiptInput(
        slide_id=slide.id, content_hash=slide.content_hash, expected_hash=slide.expected_hash
    )

    def save(_: int) -> str:
        result = delivery.issue_snapshot(owner, scope, "fictional-namespace", [request])
        return cast(dict[str, str], result["receipts"])[slide.id]

    # Includes request retries and two devices saving the same immutable source.
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(save, range(510)))
    assert len(set(receipts)) == 1
    with sharing.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM offline_receipt_sources").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM offline_receipt_tokens").fetchone()[0] == 1
    restarted = OfflineDelivery(sharing)
    assert restarted.issue_snapshot(owner, scope, "fictional-namespace", [request])["receipts"] == {
        slide.id: receipts[0]
    }
    changed = store.revise_view(
        scope.resource,
        slide.id,
        slide.expected_hash,
        "first-device-change",
        SlidePatch(html="<h1>Online</h1>"),
    )
    _ = delivery.issue_snapshot(
        owner,
        scope,
        "fictional-namespace",
        [
            OfflineReceiptInput(
                slide_id=changed.id,
                content_hash=changed.content_hash,
                expected_hash=changed.expected_hash,
            )
        ],
    )
    conflict = delivery.edit(
        owner, scope, "fictional-namespace", receipts[-1], "second-device-edit", "<h1>Offline</h1>"
    )
    assert conflict["status"] == "conflict"
    assert conflict["base"] == slide.html
    assert conflict["current"] == "<h1>Online</h1>"
    assert conflict["draft"] == "<h1>Offline</h1>"
    comment = delivery.comment(
        owner,
        scope,
        "fictional-namespace",
        receipts[-1],
        "second-device-comment",
        "Original slide",
        "shared",
    )
    assert comment["id"]
    with pytest.raises(AccessDenied):
        _ = delivery.comment(
            owner,
            scope,
            "different-namespace",
            receipts[-1],
            "wrong-namespace-comment",
            "No",
            "shared",
        )


def test_legacy_quota_is_reclaimed_without_invalidating_any_token(tmp_path: Path) -> None:
    make_design(tmp_path / "collection" / TEMPLATE_ID)
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    store.create("2026-11-15-legacy", "Fictional", "test-brand")
    sharing = Sharing(tmp_path / "sharing", store, "owner@example.test")
    owner = sharing.human(Principal("owner", "owner@example.test"))
    scope = Scope("default", "artifact", "2026-11-15-legacy")
    from artifact_studio.artifacts import ArtifactStore

    artifact = ArtifactStore(store).get_artifact(scope.resource)
    slide = store.view(scope.resource).slides[0]
    raw = store.locate(store.read(scope.resource), slide.id)
    source = json.dumps(
        raw.model_dump(include={"id", "title", "html", "interactive_html", "sources", "revision"}),
        ensure_ascii=False,
    )
    old_tokens = [secrets.token_urlsafe(32) for _ in range(500)]
    with sharing.connect() as db:
        db.execute(
            "CREATE TABLE offline_receipts (token_hash TEXT PRIMARY KEY, "
            "namespace TEXT, person TEXT, "
            "workspace TEXT, deck TEXT, slide TEXT, content_hash TEXT, expected_hash TEXT, "
            "source TEXT, channel TEXT, aggregate_hash TEXT)"
        )
        db.executemany(
            "INSERT INTO offline_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    "namespace",
                    owner.person,
                    "default",
                    scope.resource,
                    slide.id,
                    slide.content_hash,
                    slide.expected_hash,
                    source,
                    "shared",
                    artifact.content_hash,
                )
                for token in old_tokens
            ],
        )
    # A failure after source normalization must roll the whole upgrade back.
    with sharing.connect() as db:
        db.execute("CREATE TABLE offline_signing_key (id INTEGER PRIMARY KEY, key BLOB NOT NULL)")
        db.execute("INSERT INTO offline_signing_key VALUES (1,?)", (b"invalid",))
    with pytest.raises(RuntimeError, match="Invalid offline receipt signing key"):
        OfflineDelivery(sharing)
    with sharing.connect() as db:
        assert (
            db.execute("SELECT type FROM sqlite_master WHERE name='offline_receipts'").fetchone()[0]
            == "table"
        )
        assert db.execute("SELECT COUNT(*) FROM offline_receipts").fetchone()[0] == 500
        assert (
            db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='offline_receipt_sources'"
            ).fetchone()
            is None
        )
        db.execute("UPDATE offline_signing_key SET key=? WHERE id=1", (b"f" * 32,))
    delivery = OfflineDelivery(sharing)
    with sharing.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM offline_receipt_sources").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM offline_receipt_tokens").fetchone()[0] == 501
        retained = db.execute(
            "SELECT SUM(length(CAST(source AS BLOB))) FROM offline_receipt_sources"
        ).fetchone()[0]
        assert retained == len(source.encode())
        aliases = {row[0] for row in db.execute("SELECT token_hash FROM offline_receipts")}
        assert all(hashlib.sha256(token.encode()).hexdigest() in aliases for token in old_tokens)
    request = OfflineReceiptInput(
        slide_id=slide.id, content_hash=slide.content_hash, expected_hash=slide.expected_hash
    )
    saved = delivery.issue_snapshot(owner, scope, "namespace", [request])
    assert saved["receipts"]
    result = delivery.edit(
        owner, scope, "namespace", old_tokens[0], "legacy-edit-retry", "<h1>Legacy draft</h1>"
    )
    assert result["status"] == "applied"
    restarted = OfflineDelivery(sharing)
    assert (
        restarted.edit(
            owner, scope, "namespace", old_tokens[0], "legacy-edit-retry", "<h1>Legacy draft</h1>"
        )
        == result
    )
    assert restarted.comment(
        owner,
        scope,
        "namespace",
        old_tokens[-1],
        "legacy-comment-retry",
        "Original source",
        "shared",
    )["id"]


def test_exact_anchor_deduplication_and_atomic_quota(tmp_path: Path) -> None:
    from dataclasses import replace

    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    sharing = Sharing(tmp_path / "sharing", store, "owner@example.test")
    _ = OfflineDelivery(sharing)
    base = ReceiptSource(
        "namespace",
        "person",
        "default",
        "deck",
        "slide",
        "content",
        "expected",
        "source",
        "shared",
        "aggregate",
    )
    with sharing.connect() as db:
        token = retain(db, [base])[0]
        for field in (
            "namespace",
            "person",
            "workspace",
            "deck",
            "slide",
            "content_hash",
            "expected_hash",
            "source",
            "channel",
            "aggregate_hash",
        ):
            assert retain(db, [replace(base, **{field: "different"})])[0] != token
        # Fill one identity exactly; reuse must still work at the quota.
        count = db.execute(
            "SELECT COUNT(*) FROM offline_receipt_sources "
            "WHERE namespace='namespace' AND person='person'"
        ).fetchone()[0]
        retain(db, [replace(base, slide=f"slide-{i}") for i in range(500 - count)])
        assert retain(db, [base])[0] == token
        with pytest.raises(ValueError, match="storage is full"):
            retain(db, [base, replace(base, slide="over-quota")])
        assert (
            db.execute(
                "SELECT COUNT(*) FROM offline_receipt_sources WHERE slide='over-quota'"
            ).fetchone()[0]
            == 0
        )
