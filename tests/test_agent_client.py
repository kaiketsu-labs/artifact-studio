"""File-backed MCP calls preserve guarded writes without echoing submitted HTML."""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from studio import agent_client
from studio.agent_client import Client, Invocation, execute, read_calls
from studio.models import SlideReceipt


class FakeClient(Client):
    """Record calls while substituting deterministic server behavior."""

    def __init__(self, handler: Callable[[Invocation], object]) -> None:
        """Keep transport out of unit-level conflict and verification checks."""
        super().__init__("http://127.0.0.1:8790/mcp")
        self.handler = handler
        self.calls: list[Invocation] = []
        self.initialized = False

    def initialize(self) -> None:
        """Record that preflight validation allowed execution."""
        self.initialized = True

    def call(self, invocation: Invocation) -> object:
        """Return or fail according to the simulated server."""
        self.calls.append(invocation)
        return self.handler(invocation)


def receipt(slide_id: str, token: str) -> dict[str, object]:
    """Build a compact server acknowledgement with a current canonical hash."""
    return SlideReceipt(
        deck_id="fictional-deck",
        id=slide_id,
        title=slide_id,
        revision=2,
        expected_hash=token,
        content_hash=token,
        approved=False,
        hidden=False,
        has_unseen_changes=True,
    ).model_dump()


def invocation(slide_id: str) -> Invocation:
    """Use explicit baseline and retry tokens rather than refreshing them implicitly."""
    return Invocation(
        tool="revise_slide",
        arguments={
            "deck_id": "fictional-deck",
            "slide_id": slide_id,
            "expected_hash": "reviewed-baseline",
            "request_id": "reviewed-" + slide_id,
            "patch": {"html": '<article class="slide">Reviewed</article>'},
        },
    )


def test_changed_call_file_is_rejected_before_network_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bind execution to the reviewed bytes and leave describe mode entirely local."""
    path = tmp_path / "calls.json"
    path.write_text(json.dumps([invocation("first").model_dump()]))
    source = path.read_bytes()
    token = hashlib.sha256(source).hexdigest()

    def forbidden(*args: object, **kwargs: object) -> dict[str, object]:
        """Fail if local preflight unexpectedly sends anything."""
        raise AssertionError("No network request is expected")

    monkeypatch.setattr(agent_client, "post_json", forbidden)
    assert agent_client.main(["--calls-file", str(path), "--describe"]) == 0
    described = json.loads(capsys.readouterr().out)
    assert described["sha256"] == token
    assert "<article" not in json.dumps(described)
    path.write_bytes(source + b"\n")
    assert agent_client.main(["--calls-file", str(path), "--sha256", token]) == 1
    assert "changed after review" in capsys.readouterr().out
    with pytest.raises(ValueError):
        empty = tmp_path / "empty.json"
        empty.write_text("[]")
        _ = read_calls(empty)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/mcp",
        "http://127.0.0.1@evil.test/mcp",
        "http://localhost/mcp?token=secret",
    ],
)
def test_external_or_credentialed_destinations_are_rejected(url: str) -> None:
    """Do not send private patch files to an arbitrary destination."""
    with pytest.raises(ValueError, match="local HTTP"):
        _ = Client(url)


def test_batch_stops_on_conflict_and_retains_explicit_retry_tokens() -> None:
    """Report partial completion without applying later calls or silently rebasing."""

    def handler(item: Invocation) -> object:
        """Simulate an optimistic-concurrency conflict on the second write."""
        if item.arguments["slide_id"] == "second":
            raise ValueError("Slide changed. Read it again and merge your revision.")
        return receipt("first", "after-first")

    calls = [invocation(sid) for sid in ("first", "second", "third")]
    client = FakeClient(handler)
    result = execute(client, calls, verify=True)
    assert result["ok"] is False and result["next_call_index"] == 1
    assert len(client.calls) == 2
    assert client.calls[0].arguments["expected_hash"] == "reviewed-baseline"
    assert client.calls[0].arguments["request_id"] == "reviewed-first"
    assert result["completed"] == [
        {"tool": "revise_slide", "result": receipt("first", "after-first")}
    ]


@pytest.mark.parametrize("drift_field", ["expected_hash", "content_hash"])
def test_one_outline_detects_post_write_change_without_reapplying(drift_field: str) -> None:
    """Use fresh hashes to detect drift and forward changed rules instead of full source."""

    def handler(item: Invocation) -> object:
        """Expose a new canonical version after both acknowledged writes."""
        if item.tool == "get_presentation_outline":
            changed = receipt("second", "after-second")
            changed[drift_field] = "changed-by-human"
            return {
                "slides": [receipt("first", "after-first"), changed],
                "authoring_instructions": "New explicit instruction.",
                "learned_preferences": "",
                "rules_hash": "new-rules",
                "rules_included": True,
            }
        sid = str(item.arguments["slide_id"])
        return receipt(sid, "after-" + sid)

    client = FakeClient(handler)
    result = execute(
        client,
        [invocation("first"), invocation("second")],
        verify=True,
        known_rules_hash="retained-rules",
    )
    assert result["ok"] is False
    verification = agent_client.object_value(result["verification"])
    assert verification["matched"] == 1
    assert "changed-by-human" in json.dumps(verification["changed_since_write"])
    assert "New explicit instruction." in json.dumps(verification["guidance"])
    assert [item.tool for item in client.calls] == [
        "revise_slide",
        "revise_slide",
        "get_presentation_outline",
    ]
    assert client.calls[-1].arguments["known_rules_hash"] == "retained-rules"


def test_duplicate_verified_targets_fail_before_any_write() -> None:
    """Reject an ambiguous verification plan without partially applying it."""
    client = FakeClient(lambda item: receipt("first", "after-first"))
    with pytest.raises(ValueError, match="one write per slide"):
        _ = execute(client, [invocation("first"), invocation("first")], verify=True)
    assert not client.initialized and not client.calls
