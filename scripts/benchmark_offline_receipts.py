"""Measure repeated offline snapshot storage using only disposable fictional data.

Run this script with --source pointing at either the baseline or candidate tree.
The two runs use the same request count and source fixture; no network is used.
"""

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--requests", type=int, default=600)
    parser.add_argument("--receipt-version", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    source = args.source.resolve()
    if not 1 <= args.requests <= 10_000:
        parser.error("--requests must be between 1 and 10000")
    sys.path.insert(0, str(source))
    from artifact_studio.authentication import Principal
    from artifact_studio.multiplayer_access import Scope, Sharing
    from artifact_studio.offline_delivery import OfflineDelivery, OfflineReceiptInput
    from artifact_studio.storage import Store
    from tests.design_factory import TEMPLATE_ID, make_design

    with tempfile.TemporaryDirectory(prefix="studio-offline-benchmark-") as temporary:
        root = Path(temporary)
        make_design(root / "collection" / TEMPLATE_ID)
        store = Store(root / "collection", root / "state/review.sqlite3")
        store.create("2026-11-15-benchmark", "Fictional benchmark", "test-brand")
        sharing = Sharing(root / "sharing", store, "owner@example.test")
        owner = sharing.human(Principal("owner", "owner@example.test"))
        scope = Scope("default", "artifact", "2026-11-15-benchmark")
        delivery = OfflineDelivery(sharing)
        slide = store.view(scope.resource).slides[0]
        request = OfflineReceiptInput(
            slide_id=slide.id, content_hash=slide.content_hash, expected_hash=slide.expected_hash
        )
        elapsed: list[float] = []
        failures = 0
        first_failure: int | None = None
        for index in range(args.requests):
            started = time.perf_counter()
            try:
                if args.receipt_version == 2:
                    _ = delivery.issue_snapshot(
                        owner, scope, "benchmark-identity", [request], receipt_version=2
                    )
                else:
                    _ = delivery.issue_snapshot(owner, scope, "benchmark-identity", [request])
            except ValueError as error:
                if "receipt storage is full" not in str(error):
                    raise
                failures += 1
                if first_failure is None:
                    first_failure = index + 1
            elapsed.append((time.perf_counter() - started) * 1000)
        with sharing.connect() as db:
            normalized = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='offline_receipt_sources'"
            ).fetchone()
            table = "offline_receipt_sources" if normalized else "offline_receipts"
            count, size = db.execute(
                "SELECT COUNT(*), SUM(length(CAST(source AS BLOB))) FROM " + table
            ).fetchone()
            aliases = db.execute("SELECT COUNT(*) FROM offline_receipts").fetchone()[0]
        revision = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "-C", str(source), "status", "--porcelain"], text=True
            ).strip()
        )
        print(
            json.dumps(
                {
                    "source_commit": revision,
                    "source_dirty": dirty,
                    "requests": args.requests,
                    "receipt_version": args.receipt_version,
                    "succeeded": args.requests - failures,
                    "quota_failures": failures,
                    "first_failed_request": first_failure,
                    "retained_sources": count,
                    "receipt_aliases": aliases,
                    "retained_source_bytes": size or 0,
                    "request_median_ms": round(statistics.median(elapsed), 3),
                    "request_p95_ms": round(sorted(elapsed)[int(len(elapsed) * 0.95) - 1], 3),
                    "total_seconds": round(sum(elapsed) / 1000, 3),
                    "timing_scope": (
                        "All attempts including quota failures; one local run, no warmup."
                    ),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
