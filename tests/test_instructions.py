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
    assert initial["markdown"]
    assert initial["expected_hash"] == digest(initial["markdown"])
    rules = "Always visually verify charts. Keep the language concise."
    saved = client.post(
        "/api/instructions", json={"markdown": rules, "expected_hash": initial["expected_hash"]}
    )
    assert saved.status_code == 200
    assert saved.json() == {
        "markdown": rules,
        "expected_hash": digest(rules),
        "learned_preferences": "",
        "learned_preferences_hash": digest(""),
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


def test_preferences_save_to_files_and_reach_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Persist secondary edits across restarts without changing primary rules."""
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    initial = client.get("/api/instructions").json()
    preferences = "- Prefer short chart labels.\n"
    response = client.post(
        "/api/instructions",
        json={
            "markdown": initial["markdown"],
            "expected_hash": initial["expected_hash"],
            "learned_preferences": {
                "markdown": preferences,
                "expected_hash": initial["learned_preferences_hash"],
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["learned_preferences_hash"] == digest(preferences)
    assert (store.root / "LEARNED-PREFERENCES.md").read_text() == preferences
    fresh = Store(store.root, store.database)
    assert fresh.learned_preferences() == preferences
    assert fresh.authoring_instructions() == initial["markdown"]
    assert service.get_working_context()["learned_preferences"] == preferences
    saved = response.json()
    cleared = client.post(
        "/api/instructions",
        json={
            "markdown": saved["markdown"],
            "expected_hash": saved["expected_hash"],
            "learned_preferences": {
                "markdown": "",
                "expected_hash": saved["learned_preferences_hash"],
            },
        },
    )
    assert cleared.status_code == 200 and fresh.learned_preferences() == ""


@pytest.mark.parametrize("changed_file", ["INSTRUCTIONS.md", "LEARNED-PREFERENCES.md"])
def test_either_rule_conflict_preserves_both_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed_file: str
) -> None:
    """Check every baseline before writing and preserve concurrent filesystem edits."""
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    _ = (tmp_path / "INSTRUCTIONS.md").write_text("Primary rules")
    _ = (tmp_path / "LEARNED-PREFERENCES.md").write_text("Secondary rules")
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    initial = client.get("/api/instructions").json()
    _ = (tmp_path / changed_file).write_text("Changed externally")
    before = (store.authoring_instructions(), store.learned_preferences())
    response = client.post(
        "/api/instructions",
        json={
            "markdown": "Edited primary",
            "expected_hash": initial["expected_hash"],
            "learned_preferences": {
                "markdown": "Edited secondary",
                "expected_hash": initial["learned_preferences_hash"],
            },
        },
    )
    assert response.status_code == 409
    assert (store.authoring_instructions(), store.learned_preferences()) == before


def test_preference_payload_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject oversized or incomplete edits without changing either file."""
    store = Store(tmp_path, tmp_path / "state/review.sqlite3")
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app)
    initial = client.get("/api/instructions").json()
    for preferences in (
        {"markdown": "Missing baseline"},
        {"markdown": "x" * 20_001, "expected_hash": digest("")},
    ):
        response = client.post(
            "/api/instructions",
            json={
                "markdown": "Changed",
                "expected_hash": initial["expected_hash"],
                "learned_preferences": preferences,
            },
        )
        assert response.status_code == 422
    assert not (tmp_path / "INSTRUCTIONS.md").exists()
    assert not (tmp_path / "LEARNED-PREFERENCES.md").exists()
