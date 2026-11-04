"""File-backed MCP calls preserve guarded writes without echoing submitted HTML."""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from artifact_studio import agent_client
from artifact_studio.agent_client import (
    Client,
    Invocation,
    execute,
    prepare_calls,
    read_calls,
    save_prepared,
)
from artifact_studio.models import SlideReceipt


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


def test_describe_additions_exposes_identity_and_fields_without_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review a new slide's nested identity and retry token before sending its bytes."""
    path = tmp_path / "add.json"
    path.write_text(
        json.dumps(
            [
                {
                    "tool": "add_slide",
                    "arguments": {
                        "deck_id": "fictional-deck",
                        "request_id": "reviewed-add-slide",
                        "slide": {
                            "id": "evidence",
                            "title": "Evidence",
                            "html": "PRIVATE_HTML_MARKER",
                            "notes": "PRIVATE_NOTES_MARKER",
                        },
                    },
                },
                invocation("existing").model_dump(),
            ]
        )
    )
    assert agent_client.main(["--calls-file", str(path), "--describe"]) == 0
    text = capsys.readouterr().out
    described = json.loads(text)["calls"]
    assert described[0]["slide_id"] == "evidence"
    assert described[0]["request_id"] == "reviewed-add-slide"
    assert described[0]["slide_fields"] == ["id", "title", "html", "notes"]
    assert described[1]["slide_id"] == "existing" and described[1]["patch_fields"] == ["html"]
    assert "PRIVATE_HTML_MARKER" not in text and "PRIVATE_NOTES_MARKER" not in text


def test_verified_add_needs_retry_identity_before_any_write() -> None:
    """Do not partially execute an add batch that cannot safely replay its prefix."""
    client = FakeClient(lambda _: receipt("added", "token"))
    add = Invocation(
        tool="add_slide",
        arguments={
            "deck_id": "fictional-deck",
            "slide": {"id": "added", "title": "Added", "html": "source"},
        },
    )
    with pytest.raises(ValueError, match="request_id"):
        _ = execute(client, [invocation("first"), add], verify=True)
    assert not client.initialized and not client.calls


def test_prepare_reads_one_outline_and_persists_replayable_guards(tmp_path: Path) -> None:
    """Fetch missing tokens once without echoing source or changing the unprepared draft."""
    client = FakeClient(
        lambda _: {
            "slides": [receipt("first", "current-first"), receipt("second", "current-second")],
            "rules_hash": "current-rules",
            "rules_included": True,
            "authoring_instructions": "Use the selected brand.",
        }
    )
    calls = [invocation(sid) for sid in ("first", "second")]
    for item in calls:
        del item.arguments["expected_hash"]
        del item.arguments["request_id"]
    prepared, guidance = prepare_calls(client, calls, "retained-rules")
    assert len(client.calls) == 1 and client.calls[0].tool == "get_presentation_outline"
    assert client.calls[0].arguments["known_rules_hash"] == "retained-rules"
    assert prepared[0].arguments["expected_hash"] == "current-first"
    assert prepared[1].arguments["expected_hash"] == "current-second"
    assert "request_id" not in calls[0].arguments
    assert prepared[0].arguments["request_id"] != prepared[1].arguments["request_id"]
    assert guidance[0]["authoring_instructions"] == "Use the selected brand."
    path = tmp_path / "prepared.json"
    token = save_prepared(path, prepared)
    restored, restored_token = read_calls(path, token)
    assert restored == prepared and restored_token == token
    with pytest.raises(FileExistsError):
        _ = save_prepared(path, calls)
    assert read_calls(path)[0] == prepared


def test_prepare_never_refreshes_explicit_guards_or_ids() -> None:
    """Even stale-looking caller tokens survive preparation and subsequent execution."""
    client = FakeClient(lambda _: pytest.fail("No read is needed for supplied guards"))
    original = invocation("first")
    prepared, guidance = prepare_calls(client, [original])
    assert prepared == [original] and not client.calls and not guidance


def test_prepare_positioned_add_uses_order_and_section_guard() -> None:
    """Additions keep stable IDs and get structural tokens from a shared outline."""
    client = FakeClient(
        lambda _: {"slides": [receipt("first", "hash")], "sections_hash": "section-version"}
    )
    call = Invocation(
        tool="add_slide",
        arguments={
            "deck_id": "fictional-deck",
            "slide": {"id": "added", "title": "Added", "html": "private-draft"},
            "after_slide_id": "first",
        },
    )
    prepared, _ = prepare_calls(client, [call])
    assert prepared[0].arguments["expected_slide_ids"] == ["first"]
    assert prepared[0].arguments["expected_sections_hash"] == "section-version"
    assert prepared[0].arguments["request_id"]
    second = call.model_copy(update={"arguments": {**call.arguments, "slide": {"id": "second"}}})
    blocked = FakeClient(lambda _: pytest.fail("Ambiguous plans must fail before reads"))
    with pytest.raises(ValueError, match="positioned additions separately"):
        _ = prepare_calls(blocked, [call, second])
    assert not blocked.initialized


def test_prepare_theme_context_and_design_hashes() -> None:
    """Authoring files use the exact guard returned by their corresponding read tool."""

    def handler(item: Invocation) -> object:
        return (
            {"context_hash": "context-token"}
            if item.tool == "get_presentation_outline"
            else {"expected_hash": item.tool + "-token"}
        )

    client = FakeClient(handler)
    calls = [
        Invocation(tool="update_presentation_theme", arguments={"deck_id": "fictional-deck"}),
        Invocation(tool="append_presentation_context", arguments={"deck_id": "fictional-deck"}),
        Invocation(tool="sync_presentation_design", arguments={"deck_id": "fictional-deck"}),
        Invocation(tool="write_design_file", arguments={"design_id": "brand", "path": "theme.css"}),
        Invocation(
            tool="write_design_file",
            arguments={"design_id": "brand", "path": "assets/new.svg", "expected_hash": ""},
        ),
    ]
    prepared, _ = prepare_calls(client, calls)
    assert [item.arguments["expected_hash"] for item in prepared] == [
        "get_presentation_theme-token",
        "context-token",
        "get_presentation_design_sync-token",
        "get_design_file-token",
        "",
    ]
    assert all(item.tool.startswith("get_") for item in client.calls)
    assert len(client.calls) == 4


def test_prepare_rejects_duplicate_targets_before_any_read() -> None:
    """Dependent writes cannot share one fetched baseline."""
    client = FakeClient(lambda _: pytest.fail("No read is expected"))
    with pytest.raises(ValueError, match="one write per slide"):
        _ = prepare_calls(client, [invocation("first"), invocation("first")])
    assert not client.initialized


def test_prepare_refuses_existing_output_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A repeated CLI preparation cannot replace the tokens required to retry safely."""
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps([invocation("first").model_dump()]))
    output = tmp_path / "prepared.json"
    output.write_text("KEEP_EXISTING_BASELINE")

    def forbidden(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("No network request is expected")

    monkeypatch.setattr(agent_client, "post_json", forbidden)
    assert agent_client.main(["--calls-file", str(draft), "--prepare", str(output)]) == 1
    assert "reuse it for retries" in capsys.readouterr().out
    assert output.read_text() == "KEEP_EXISTING_BASELINE"
