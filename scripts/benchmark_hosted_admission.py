"""Measure idle admission reconciliation using fictional identities and mock HTTP only."""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from artifact_studio.artifacts import ArtifactStore
from artifact_studio.authentication import Principal
from artifact_studio.deployment import DeploymentConfig
from artifact_studio.invitation_provisioning import InvitationProvisioning
from artifact_studio.multiplayer_access import Scope, Sharing
from artifact_studio.storage import Store
from tests.test_cloudflare_admission import CloudflareFixture
from tests.test_invitation_provisioning import FictionalAdmission


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticks", type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.ticks <= 10_000:
        parser.error("--ticks must be between 1 and 10000")
    with tempfile.TemporaryDirectory(prefix="studio-admission-benchmark-") as temporary:
        root = Path(temporary)
        store = Store(root / "collection", root / "state/review.sqlite3")
        sharing = Sharing(root / "sharing", store, "owner@example.test")
        owner = sharing.human(Principal("owner", "owner@example.test"))
        project = ArtifactStore(store).create_project("Fictional admission benchmark")
        config = DeploymentConfig(
            "hosted",
            "https://studio.example.test",
            "https://preview.example.test",
            "cloudflare",
            {"team_domain": "fictional.cloudflareaccess.com", "audience": "fictional-audience"},
            ("owner@example.test",),
            (),
        )
        adapter = FictionalAdmission()
        admission = InvitationProvisioning(sharing, config, adapter)
        sharing.invite(owner, Scope("default", "project", project.id), "alice@example.test", "view")
        admission.tick()
        initial_calls = len(adapter.calls)
        started = time.perf_counter()
        for _ in range(args.ticks):
            admission.tick()
        elapsed = time.perf_counter() - started
        idle_calls = len(adapter.calls) - initial_calls
        assert initial_calls == 1 and idle_calls == 0

        # Repeating the provider operation is also safe if the response was lost.
        cloudflare = CloudflareFixture()
        provider = cloudflare.adapter()
        for _ in range(100):
            provider.reconcile("guest@example.test", True, "fixture-operation", timeout=10)
        writes = sum(request.method == "PUT" for request in cloudflare.requests)
        assert writes == 1
        assert all(
            request.method == "GET" or "/access/groups/" in request.url.path
            for request in cloudflare.requests
        )
        print(
            json.dumps(
                {
                    "fixture": "fictional identities and httpx.MockTransport; no external calls",
                    "idle_ticks": args.ticks,
                    "initial_admission_calls": initial_calls,
                    "idle_admission_calls": idle_calls,
                    "idle_seconds": round(elapsed, 6),
                    "mean_idle_tick_ms": round(elapsed * 1000 / args.ticks, 6),
                    "same_provider_operation_retries": 100,
                    "mock_provider_requests": len(cloudflare.requests),
                    "mock_provider_group_writes": writes,
                    "mock_provider_policy_writes": 0,
                    "real_network_requests": 0,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
