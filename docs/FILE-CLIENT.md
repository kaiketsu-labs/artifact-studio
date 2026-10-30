# Apply reviewed files without copying HTML through the model

For a local agent that already has reviewed patch JSON on disk, `studio.agent_client` sends those bytes through the existing stateless MCP tools. The model receives compact receipts instead of reprinting and retransmitting the slide HTML. It works with Codex, Claude Code or another harness that can run local commands.

This does not edit canonical files directly, add server tools, change permissions or grant extra authority. Follow the same user authorization and host restrictions as ordinary MCP calls. Never use it to route around denied access. Image results still belong in the host's visual-review tools.

## A call file

Save an array of explicit tool calls in a local JSON file:

```json
[
  {
    "tool": "revise_slide",
    "arguments": {
      "deck_id": "my-presentation",
      "slide_id": "overview",
      "expected_hash": "hash-from-the-reviewed-baseline",
      "request_id": "unique-id-kept-for-retries",
      "patch": { "html": "<article class=\"slide\"><h1>Reviewed headline</h1></article>" }
    }
  }
]
```

Build this file from the reviewed local draft and its baseline; do not print large HTML into the model just to copy it into another tool. Use only intended patch fields. Preserve notes, visibility and unrelated content. Retain the baseline's `expected_hash`; fetching a fresh hash just to force an old draft over newer work defeats concurrency protection.

Inspect the call identities/fields and obtain the file hash without sending anything:

```sh
uv run python -m studio.agent_client --calls-file /absolute/path/reviewed-calls.json --describe
```

Then supply that exact SHA-256:

```sh
uv run python -m studio.agent_client \
  --calls-file /absolute/path/reviewed-calls.json \
  --sha256 HASH_FROM_DESCRIBE \
  --verify
```

`--url` defaults to `http://127.0.0.1:8790/mcp`. Only local HTTP is accepted, and redirects are refused. Files are limited to 32 MB and 100 calls; split larger batches. The hash binds execution to the inspected bytes; it does not prove their content is correct or authorized.

## Verification and failures

`--verify` accepts one `add_slide` or `revise_slide` write per slide. After the writes, it makes one compact outline read per deck and compares both canonical-source and visual-content hashes with the receipts. It forwards changed workspace rules; pass `--known-rules-hash` only while retaining that exact guidance. This readback does not replace rendered slide/animation review or establish long-term durability.

Calls run sequentially and stop on the first error. **A batch is not an atomic transaction:** completed changes remain saved if a later call conflicts. The report retains completed results and the next call index. Reusing the unchanged file preserves request IDs and the server's existing idempotency checks. The client never invents new retry IDs, refreshes write hashes, retries conflicts, or reapplies content after a failed readback. A concurrent human edit or asset change can make verification fail; read and reconcile before proceeding.

Without `--verify`, a file can contain other explicitly authorized text-result tools, including `append_presentation_context` or `get_presentation_outline`. Large read results still consume model context if printed; prefer the ordinary compact reads.

## When connected tools are missing

Refreshing server code does not prove an existing chat has rediscovered the tool catalogue. Check the callable tools in the **agent host**, not just the server response. Current Slide Studio includes `get_presentation_outline`, `append_presentation_context`, `get_authoring_guide` and retained-rule hash arguments. If they are absent, use the host's supported MCP connection refresh controls and recheck; do not add duplicate implementations.

OpenAI's [app-server documentation](https://learn.chatgpt.com/docs/app-server) describes `config/mcpServer/reload` as queuing a refresh for loaded chats. The [MCP setup guide](https://learn.chatgpt.com/docs/extend/mcp) also documents host restart controls. Those are host operations; this client does not modify Codex configuration or refresh its in-memory tools.
