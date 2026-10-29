"""Verify persistent human rules reach agents without living in a slide chat."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import app as service
from studio.storage import Store, digest
from tests.design_factory import TEMPLATE_ID, make_design


def test_rules_persist_and_accompany_agent_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expose the same saved instructions in onboarding, snapshots and released feedback."""
    make_design(tmp_path / TEMPLATE_ID)
    store = Store(tmp_path, tmp_path / "review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    initial = client.get("/api/instructions").json()
    assert "Visually verify every chart" in initial["markdown"]
    rules = "Always visually verify charts. Keep the language concise."
    saved = client.post(
        "/api/instructions", json={"markdown": rules, "expected_hash": initial["expected_hash"]}
    )
    assert saved.status_code == 200
    assert saved.json() == {
        "markdown": rules,
        "expected_hash": digest(rules),
        "learned_preferences": "",
    }
    assert (store.root / "INSTRUCTIONS.md").read_text() == rules
    assert service.get_working_context()["authoring_instructions"] == rules
    assert store.view(TEMPLATE_ID).authoring_instructions == rules
    assert store.feedback_feed(TEMPLATE_ID).authoring_instructions == rules
    assert Store(store.root, store.database).authoring_instructions() == rules
    assert not store.comments(TEMPLATE_ID)
    _ = (store.root / "LEARNED-PREFERENCES.md").write_text("Prefer concise diagram labels.")
    assert service.get_working_context()["learned_preferences"] == "Prefer concise diagram labels."
    assert "primary" in str(service.get_working_context()["instruction_policy"])
    assert store.feedback_feed(TEMPLATE_ID).learned_preferences == "Prefer concise diagram labels."
    assert store.authoring_instructions() == rules
    _ = (store.root / "INSTRUCTIONS.md").write_text("New instructions edited directly in Markdown.")
    assert store.feedback_feed(TEMPLATE_ID).authoring_instructions.startswith("New instructions")
    assert (
        client.post(
            "/api/instructions", json={"markdown": "Stale dialog", "expected_hash": digest(rules)}
        ).status_code
        == 409
    )


def test_rule_edits_detect_conflicts_and_reject_other_origins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep an old instructions dialog from silently overwriting another human edit."""
    store = Store(tmp_path, tmp_path / "review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    original = client.get("/api/instructions").json()["expected_hash"]
    assert (
        client.post(
            "/api/instructions", json={"markdown": "First edit", "expected_hash": original}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/instructions", json={"markdown": "Stale edit", "expected_hash": original}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/instructions",
            json={"markdown": "Other origin", "expected_hash": digest("First edit")},
            headers={"Origin": "https://other.test"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/instructions", json={"markdown": "x" * 20_001, "expected_hash": original}
        ).status_code
        == 422
    )
    assert store.authoring_instructions() == "First edit"
