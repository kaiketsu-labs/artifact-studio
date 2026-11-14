"""Offline partitions are available only for an explicitly enabled verified identity."""

from dataclasses import replace

import pytest

from artifact_studio.authentication import Principal
from artifact_studio.deployment import DeploymentConfig
from artifact_studio.offline_identity import cache_scope


def test_offline_cache_identity_is_scoped_and_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    config = DeploymentConfig(
        mode="hosted",
        public_url="https://studio.example.test",
        auth_provider="cloudflare",
        auth_options={"team_domain": "fictional.cloudflareaccess.com", "audience": "app"},
    )
    alice = Principal("fictional-alice", "alice@example.test")
    assert cache_scope(config, alice, "default") is None
    monkeypatch.setenv("ARTIFACT_STUDIO_EXPERIMENTAL_OFFLINE", "1")
    scope = cache_scope(config, alice, "default")
    assert scope and len(scope) == 64 and "alice" not in scope
    assert scope == cache_scope(config, alice, "default")
    assert scope != cache_scope(config, Principal("fictional-bob", "bob@example.test"), "default")
    assert scope != cache_scope(config, alice, "other-workspace")
    assert scope != cache_scope(
        replace(
            config, auth_options={"team_domain": "other.cloudflareaccess.com", "audience": "app"}
        ),
        alice,
        "default",
    )
    assert scope != cache_scope(
        replace(
            config,
            auth_options={"team_domain": "fictional.cloudflareaccess.com", "audience": "other"},
        ),
        alice,
        "default",
    )
    assert cache_scope(config, Principal("agent", service=True), "default") is None
    assert (
        cache_scope(DeploymentConfig(mode="hosted", auth_provider="custom"), alice, "default")
        is None
    )
