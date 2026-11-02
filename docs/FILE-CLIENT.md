# Apply reviewed files without copying HTML through the model

For a local agent that already has reviewed patch JSON on disk, `artifact_studio.agent_client` sends those bytes through the existing stateless MCP tools. The model receives compact receipts instead of reprinting and retransmitting the slide HTML. It works with Codex, Claude Code or another harness that can run local commands.

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
uv run python -m artifact_studio.agent_client --calls-file /absolute/path/reviewed-calls.json --describe
```

The description includes `slide.id` for additions, the explicit `request_id`, and the names of supplied `slide_fields` or `patch_fields`; it never prints HTML, notes or other source values. Every `add_slide` in a verified batch must carry a unique `request_id` of 8–100 characters at the top level of its `arguments`, alongside `deck_id` and `slide`. Preserve it across retries just as for revisions.

Then supply that exact SHA-256:

```sh
uv run python -m artifact_studio.agent_client \
  --calls-file /absolute/path/reviewed-calls.json \
  --sha256 HASH_FROM_DESCRIBE \
  --verify
```

`--url` defaults to `http://127.0.0.1:8790/mcp`. Only local HTTP is accepted, and redirects are refused. Files are limited to 32 MB and 100 calls; split larger batches. The hash binds execution to the inspected bytes; it does not prove their content is correct or authorized.

## Verification and failures

`--verify` accepts one `add_slide` or `revise_slide` write per slide. After the writes, it makes one compact outline read per deck and compares both canonical-source and visual-content hashes with the receipts. It forwards changed workspace rules; pass `--known-rules-hash` only while retaining that exact guidance. This readback does not replace rendered slide/animation review or establish long-term durability.

Calls run sequentially and stop on the first error. **A batch is not an atomic transaction:** completed changes remain saved if a later call conflicts. The report retains completed results and the next call index. Reusing the unchanged file preserves request IDs and the server's existing idempotency checks. The client never invents new retry IDs, refreshes write hashes, retries conflicts, or reapplies content after a failed readback. A concurrent human edit or asset change can make verification fail; read and reconcile before proceeding.

`create_presentation` accepts a unique `request_id` for exact-input retries. Recorded retries return the original creation snapshot; read the deck for current content. A filesystem failure can leave a folder without its receipt, and imported copies do not inherit creation ownership. If an ID exists without a matching record, inspect its ownership and contents before continuing. Missing or unreadable decks and folders lacking the creation marker require reconciliation; ordinary replacement or import does not preserve that marker, though a full filesystem copy can retain it. A retry never recreates the folder.

For `revise_slide` and `add_slide` with a request ID, a recorded identical retry returns the original write-time source, hashes and review status without recreating a deleted slide, overwriting human changes or re-embedding changed assets. Changed payloads or embedding modes under the same ID conflict. The original visual hash is retained, so verified readback can detect later theme/asset changes. `include_content=True` adds the original source and current released discussion, resolving attachment paths and thread IDs in the current workspace. Use `get_slide` for current editable content and review status. Additions without an ID retain their one-shot behavior and reject duplicate slide IDs; `--verify` rejects them before any writes.

Revisions recorded by older versions contain source without a historical visual receipt. Retrying those through MCP reports that no visual receipt was recorded and requires a fresh read and reconciliation. Current assets cannot reconstruct the missing baseline; the tool does not upgrade old records or reapply their source.

A filesystem write and its operation record are not one atomic transaction. If storage fails after the canonical write but before its record commits, an unchanged retry can report an existing-ID or outdated-source conflict. If a revision leaves the source unchanged and no receipt was recorded, a retry may be accepted as a fresh write using the current visual state; it cannot prove the earlier visual baseline. Read and reconcile known storage failures manually. Ordinary lost HTTP responses after a recorded write and batches interrupted after an acknowledged prefix are safe to retry with the original file. Do not replace request IDs or hashes merely to force completion.

Without `--verify`, a file can contain other explicitly authorized text-result tools, including `append_presentation_context` or `get_presentation_outline`. Large read results still consume model context if printed; prefer the ordinary compact reads.

For a positioned `add_slide`, keep `after_slide_id` and/or `section_id` at the top level of its arguments, together with `request_id`, `expected_slide_ids` and `expected_sections_hash` from the reviewed outline. A named section alone appends within that group; an empty `after_slide_id` inserts at the start of the group or deck. Order and section membership change in the same canonical file write. Missing or stale structural guards fail before preparing embedded assets. Exact retries retain the original acknowledgement and never reposition a later move. `--verify` compares source and visual hashes; inspect a fresh outline separately to verify current order and section membership.

## When connected tools are missing

Refreshing server code does not prove an existing chat has rediscovered the tool catalogue. Check the callable tools in the **agent host**, not just the server response. Current Artifact Studio includes `get_presentation_outline`, `append_presentation_context`, `get_authoring_guide` and retained-rule hash arguments. If they are absent, use the host's supported MCP connection refresh controls and recheck; do not add duplicate implementations.

OpenAI's [app-server documentation](https://learn.chatgpt.com/docs/app-server) describes `config/mcpServer/reload` as queuing a refresh for loaded chats. The [MCP setup guide](https://learn.chatgpt.com/docs/extend/mcp) also documents host restart controls. Those are host operations; this client does not modify Codex configuration or refresh its in-memory tools.
