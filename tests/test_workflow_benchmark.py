"""Opt-in real MCP correctness regression for both measured workflow policies."""

import json
import os

import pytest

from scripts.benchmark_presentation_workflow import measure


@pytest.mark.agents
@pytest.mark.skipif(
    os.environ.get("STUDIO_BENCHMARK_TEST") != "1"
    and os.environ.get("ARTIFACT_STUDIO_AGENT_TESTS") != "1",
    reason="opt-in MCP servers",
)
def test_guarded_workflows_reconcile_identically() -> None:
    baseline = measure("baseline")
    efficient = measure("efficient")
    assert baseline["final_state"] == efficient["final_state"]
    assert "Human added evidence provenance." in efficient["final_state"]["notes"]
    for result in (baseline, efficient):
        failures = [row for row in result["samples"] if row["expected_error"]]
        assert [row["label"] for row in failures] == ["stale_rejected"]
        recorded = json.dumps(result)
        assert "data:image/" not in recorded and "base64," not in recorded
        assert "Authorization" not in recorded and "api_key" not in recorded
    # The in-workflow assertions also check cursor de-duplication and untouched slides.
    assert (
        efficient["totals"]["response_text_utf8_bytes"]
        < baseline["totals"]["response_text_utf8_bytes"]
    )
