"""Verify metric accounting only; the matched benchmark itself uses real HTTP MCP."""

from scripts.benchmark_tool_surface import aggregate, compact, decode, summarize


def test_metric_totals_separate_overlap_and_include_expected_failures() -> None:
    totals = aggregate(
        [
            {
                "method": "initialize",
                "elapsed_ms": 1.5,
                "request_http_body_utf8_bytes": 100,
                "response_http_body_utf8_bytes": 60,
                "response_mcp_result_compact_json_utf8_bytes": 30,
            },
            {"method": "notifications/initialized", "elapsed_ms": 0.5},
            {
                "method": "tools/call",
                "elapsed_ms": 2.0,
                "tool_is_error": True,
                "expected_error": True,
                "request_http_body_utf8_bytes": 50,
                "response_http_body_utf8_bytes": 80,
                "response_mcp_result_compact_json_utf8_bytes": 55,
                "response_text_utf8_bytes": 10,
                "image_base64_bytes": 4,
                "image_decoded_bytes": 3,
            },
            {
                "method": "tools/call",
                "elapsed_ms": 3.0,
                "transport_error": "HTTPError",
            },
            {
                "method": "tools/list",
                "elapsed_ms": 1.0,
                "jsonrpc_error": {"code": -32603},
            },
        ]
    )
    assert totals["http_exchanges"] == 5
    assert totals["jsonrpc_requests"] == 4
    assert totals["tool_calls"] == 2
    assert totals["tool_errors"] == totals["expected_tool_errors"] == 1
    assert totals["transport_errors"] == totals["jsonrpc_errors"] == 1
    assert totals["summed_http_elapsed_ms"] == 8.0
    assert totals["request_http_body_utf8_bytes"] == 150
    assert totals["response_http_body_utf8_bytes"] == 140
    assert totals["response_mcp_result_compact_json_utf8_bytes"] == 85
    assert totals["response_text_utf8_bytes"] == 10
    assert totals["image_base64_bytes"] == 4
    assert totals["image_decoded_bytes"] == 3


def test_json_measurement_keeps_multibyte_text_and_all_timing_observations() -> None:
    assert compact({"note": "é"}) == b'{"note":"\xc3\xa9"}'
    assert summarize([4.0, 1.0, 3.0]) == {
        "observations": [4.0, 1.0, 3.0],
        "median": 3.0,
        "range": [1.0, 4.0],
    }


def test_result_decoding_accepts_structured_receipts_and_plain_hashes() -> None:
    assert decode({"structuredContent": {"result": ["deck"]}}) == ["deck"]
    assert decode({"content": [{"type": "text", "text": "receipt-hash"}]}) == "receipt-hash"
    assert decode({"content": [{"type": "text", "text": '{"context_hash":"abc"}'}]}) == {
        "context_hash": "abc"
    }
