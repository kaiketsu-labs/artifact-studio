"""Send reviewed JSON files through stateless MCP without model-side HTML echoes."""

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast
from urllib.request import Request, build_opener

from mcp.types import LATEST_PROTOCOL_VERSION, CallToolResult, TextContent
from pydantic import Field, TypeAdapter

from .local_http import NoRedirect, validate_local_url
from .models import SlideReceipt, StrictModel


class Invocation(StrictModel):
    """An explicit existing MCP call with caller-supplied concurrency and retry tokens."""

    tool: str = Field(min_length=1, max_length=100)
    arguments: dict[str, object] = Field(default_factory=dict)


def object_value(value: object) -> dict[str, object]:
    """Require a JSON object at a protocol boundary."""
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object from the MCP server.")
    return cast(dict[str, object], value)


def post_json(
    url: str, payload: dict[str, object], protocol: str, timeout: int
) -> dict[str, object]:
    """Post one JSON-RPC request without following redirects."""
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if protocol:
        headers["MCP-Protocol-Version"] = protocol
    request = Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        source = response.read()
    return object_value(json.loads(source)) if source else {}


class Client:
    """Use the same tools and guards as connected agents, with no persistent session."""

    def __init__(self, url: str, timeout: int = 45) -> None:
        """Validate the destination before sending any payload."""
        validate_local_url(url)
        self.url = url
        self.timeout = timeout
        self.protocol = ""
        self.request_id = 0

    def rpc(self, method: str, params: dict[str, object]) -> dict[str, object]:
        """Reject protocol errors rather than treating them as successful results."""
        self.request_id += 1
        response = post_json(
            self.url,
            {"jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params},
            self.protocol,
            self.timeout,
        )
        if response.get("error") is not None:
            raise ValueError(f"MCP error: {response['error']}")
        if response.get("id") != self.request_id:
            raise ValueError("MCP response ID does not match the request.")
        return object_value(response.get("result"))

    def initialize(self) -> None:
        """Negotiate the protocol once for this invocation."""
        result = self.rpc(
            "initialize",
            {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "slide-studio-agent-client", "version": "1"},
            },
        )
        protocol = result.get("protocolVersion")
        if not isinstance(protocol, str) or not protocol:
            raise ValueError("Server did not negotiate an MCP protocol version.")
        self.protocol = protocol
        response = post_json(
            self.url,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            self.protocol,
            self.timeout,
        )
        if response.get("error") is not None:
            raise ValueError(f"MCP initialization failed: {response['error']}")

    def call(self, invocation: Invocation) -> object:
        """Decode text or structured results without printing submitted arguments."""
        result = CallToolResult.model_validate(
            self.rpc("tools/call", {"name": invocation.tool, "arguments": invocation.arguments})
        )
        messages = [item.text for item in result.content if isinstance(item, TextContent)]
        if result.is_error:
            raise ValueError("; ".join(messages) or "MCP tool failed.")
        if any(not isinstance(item, TextContent) for item in result.content):
            raise ValueError("Use the host's visual tools for image or other non-text results.")
        if result.structured_content is not None:
            value = result.structured_content
            return value["result"] if set(value) == {"result"} else value
        if len(messages) == 1:
            try:
                return json.loads(messages[0])
            except json.JSONDecodeError:
                return messages[0]
        return messages


def read_calls(path: Path, expected_hash: str = "") -> tuple[list[Invocation], str]:
    """Validate the complete local call file before any network request."""
    if path.stat().st_size > 32_000_000:
        raise ValueError("Keep a call file under 32 MB; split larger batches.")
    source = path.read_bytes()
    token = hashlib.sha256(source).hexdigest()
    if expected_hash and token != expected_hash:
        raise ValueError("Call file changed after review; inspect it again.")
    calls = TypeAdapter(list[Invocation]).validate_json(source)
    if not 1 <= len(calls) <= 100:
        raise ValueError("Provide 1–100 explicit tool calls.")
    return calls, token


def verify_writes(
    client: Client, receipts: list[SlideReceipt], known_rules_hash: str
) -> dict[str, object]:
    """Check current canonical hashes without echoing full slide source/history."""
    changed: list[dict[str, object]] = []
    guidance: list[dict[str, object]] = []
    for deck_id in dict.fromkeys(item.deck_id for item in receipts):
        outline = object_value(
            client.call(
                Invocation(
                    tool="get_presentation_outline",
                    arguments={"deck_id": deck_id, "known_rules_hash": known_rules_hash},
                )
            )
        )
        current = TypeAdapter(list[SlideReceipt]).validate_python(outline.get("slides"))
        lookup = {item.id: item for item in current}
        for expected in (item for item in receipts if item.deck_id == deck_id):
            actual = lookup.get(expected.id)
            if (
                actual is None
                or actual.expected_hash != expected.expected_hash
                or actual.content_hash != expected.content_hash
            ):
                changed.append(
                    {
                        "deck_id": deck_id,
                        "slide_id": expected.id,
                        "current": actual.model_dump() if actual else None,
                    }
                )
        rules = {
            key: outline[key]
            for key in (
                "authoring_instructions",
                "learned_preferences",
                "rules_hash",
                "rules_included",
            )
            if key in outline
        }
        guidance.append({"deck_id": deck_id, **rules})
        known_rules_hash = str(outline.get("rules_hash", ""))
    return {
        "matched": len(receipts) - len(changed),
        "changed_since_write": changed,
        "guidance": guidance,
    }


def execute(
    client: Client, calls: list[Invocation], verify: bool, known_rules_hash: str = ""
) -> dict[str, object]:
    """Stop on the first failure, preserve partial results and never refresh write hashes."""
    if verify:
        if any(item.tool not in {"add_slide", "revise_slide"} for item in calls):
            raise ValueError("--verify accepts only add_slide/revise_slide calls.")
        keys = [write_identity(item) for item in calls]
        if len(keys) != len(set(keys)):
            raise ValueError("--verify requires one write per slide.")
        for item in calls:
            request_id = item.arguments.get("request_id")
            if item.tool == "add_slide" and (
                not isinstance(request_id, str) or not 8 <= len(request_id) <= 100
            ):
                raise ValueError("--verify add_slide calls need a request_id of 8–100 characters.")
    completed: list[dict[str, object]] = []
    receipts: list[SlideReceipt] = []
    try:
        client.initialize()
        for item in calls:
            result = client.call(item)
            completed.append({"tool": item.tool, "result": result})
            if verify:
                fields = object_value(result)
                receipt = {
                    key: value for key, value in fields.items() if key in SlideReceipt.model_fields
                }
                receipt.setdefault("deck_id", item.arguments.get("deck_id"))
                receipts.append(SlideReceipt.model_validate(receipt))
        verification = verify_writes(client, receipts, known_rules_hash) if verify else None
        return {
            "ok": verification is None or not verification["changed_since_write"],
            "completed": completed,
            "verification": verification,
        }
    except (OSError, ValueError) as error:
        return {
            "ok": False,
            "completed": completed,
            "next_call_index": len(completed),
            "error": str(error)[:2000],
        }


def write_identity(invocation: Invocation) -> tuple[str, str]:
    """Require explicit slide identity before starting a verified write batch."""
    deck_id = invocation.arguments.get("deck_id")
    slide_id = (
        object_value(invocation.arguments.get("slide")).get("id")
        if invocation.tool == "add_slide"
        else invocation.arguments.get("slide_id")
    )
    if not isinstance(deck_id, str) or not isinstance(slide_id, str) or not deck_id or not slide_id:
        raise ValueError("Each verified write needs explicit deck and slide IDs.")
    return deck_id, slide_id


def describe_call(item: Invocation) -> dict[str, object]:
    """Show add/revision targets and supplied field names without printing source or notes."""
    slide = object_value(item.arguments.get("slide", {})) if item.tool == "add_slide" else {}
    return {
        "tool": item.tool,
        "deck_id": item.arguments.get("deck_id"),
        "slide_id": slide.get("id") if item.tool == "add_slide" else item.arguments.get("slide_id"),
        "request_id": item.arguments.get("request_id"),
        "patch_fields": list(object_value(item.arguments.get("patch", {}))),
        "slide_fields": list(slide),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Describe reviewed call files or execute them through the existing MCP surface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls-file", type=Path, required=True)
    parser.add_argument("--sha256", default="")
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--known-rules-hash", default="")
    parser.add_argument("--url", default="http://127.0.0.1:8790/mcp")
    args = parser.parse_args(argv)
    try:
        if not args.describe and not args.sha256:
            raise ValueError("Inspect the call file, then supply its --sha256 before execution.")
        calls, token = read_calls(args.calls_file, args.sha256)
        if args.describe:
            result = {
                "sha256": token,
                "calls": [describe_call(item) for item in calls],
            }
        else:
            result = execute(Client(args.url), calls, args.verify, args.known_rules_hash)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0 if result.get("ok", True) else 1
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)[:2000]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
