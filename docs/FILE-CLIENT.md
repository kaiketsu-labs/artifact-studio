# Apply reviewed files without copying HTML through the model

For a local agent with patch JSON on disk, `artifact_studio.agent_client` prepares concurrency tokens and sends the file through the existing stateless MCP tools. The model receives compact receipts instead of reprinting and retransmitting slide HTML or copying hashes by hand. It works with Codex, Claude Code or another harness that can run local commands.

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
      "patch": { "html": "<article class=\"slide\"><h1>Reviewed headline</h1></article>" }
    }
  }
]
```

Before drafting, read the slide with `get_slide` or request its source with `get_presentation_outline(source_slide_ids=[...])`. The outline used by CLI preparation supplies the concurrency guards. Use only intended patch fields and preserve notes, visibility and unrelated content. If your draft already has a known baseline `expected_hash`, retain it in the arguments; preparation never replaces explicit hashes or request IDs. Do not prepare an old draft against newer content merely to force an overwrite.

Prepare missing guards into a **new** file. This makes read calls only and does not change the presentation:

```sh
uv run python -m artifact_studio.agent_client \
  --calls-file /absolute/path/draft-calls.json \
  --prepare /absolute/path/prepared-calls.json
```

Preparation fetches one compact outline per required deck, generates missing retry IDs, and persists the results with your payload. When the calls already contain every required guard, preparation runs offline. It refuses to replace an existing output file, including a symlink, so repeated preparation cannot silently refresh a retry baseline. Its compact report includes the path, file SHA-256, call identities, supplied field names and changed authoring rules. It does not print submitted HTML, CSS, notes or other source values.

Execute that file, then reuse the same file if the connection fails:

```sh
uv run python -m artifact_studio.agent_client \
  --calls-file /absolute/path/prepared-calls.json \
  --verify
```

`--describe` remains a wholly local summary. Optional `--sha256 HASH` checks that the file still matches previously inspected bytes before any network access. Without that option, execution uses the bytes read once from the specified file; it never reloads the file during the batch. Neither a file hash nor preparation proves that the draft is correct or authorized.

| Prepared operation | Missing values supplied |
| --- | --- |
| `revise_slide` | Current slide `expected_hash`, stable `request_id` |
| `add_slide` | Stable `request_id`; current order and section guards for positioned adds |
| `create_presentation` | Stable `request_id` |
| Theme or presentation-context update/append | Current `expected_hash` |
| `write_design_file` for an existing file | Current `expected_hash` from `get_design_file` |
| `sync_presentation_design` | Current preview `expected_hash`; explicit `overwrite` is preserved |

For a new design file, explicitly set `expected_hash: ""` to request creation; an omitted guard means preparation must read an existing file. Unknown tools pass through unchanged. Preparation permits one write per slide, theme, context or design file, and one positioned addition per deck without other adds in the same batch. Split dependent operations and prepare each after its predecessor succeeds.

`--url` defaults to `http://127.0.0.1:8790/mcp`. Only local HTTP is accepted, and redirects are refused. Use a regular JSON file of up to 32 MB and 100 calls. A symlink to a regular input file is supported. Split larger batches. Every verified `add_slide` requires a persisted `request_id` of 8–100 characters, supplied by preparation or explicitly by the caller.

## Verification and failures

`--verify` accepts one `add_slide` or `revise_slide` write per slide. After the writes, it makes one compact outline read per deck and compares both canonical-source and visual-content hashes with the receipts. It forwards changed workspace rules; pass `--known-rules-hash` only while retaining that exact guidance. This readback does not replace rendered slide/animation review or establish long-term durability.

Calls run sequentially and stop on the first error. **A batch is not an atomic transaction:** completed changes remain saved if a later call conflicts. The report retains completed results and the next call index. Reusing the unchanged prepared file preserves request IDs and the server's existing idempotency checks. Execution never generates retry IDs, refreshes write hashes, retries conflicts, or reapplies content after a failed readback. A concurrent human edit or asset change can make verification fail; read and reconcile before proceeding. Do not prepare a replacement file simply to retry a failed call.

For both preparation and verified execution, `--known-rules-hash` can omit rules
the agent actually retains. Changed rules still return in full. This avoids
reprinting unchanged guidance without weakening concurrency checks. Omit the
hash after context loss. Large source already in files is the useful CLI case:
initial authoring and visual inspection still need context, and the HTTP request
still carries the complete authored content. A CLI does not itself reduce billed
tokens or make an unreviewed draft safe to apply.

`create_presentation` accepts a unique `request_id` for exact-input retries. Recorded retries return the original creation snapshot; read the deck for current content. A filesystem failure can leave a folder without its receipt, and imported copies do not inherit creation ownership. If an ID exists without a matching record, inspect its ownership and contents before continuing. Missing or unreadable decks and folders lacking the creation marker require reconciliation; ordinary replacement or import does not preserve that marker, though a full filesystem copy can retain it. A retry never recreates the folder.

For `revise_slide` and `add_slide` with a request ID, a recorded identical retry returns the original write-time source, hashes and review status without recreating a deleted slide, overwriting human changes or re-embedding changed assets. Changed payloads or embedding modes under the same ID conflict. The original visual hash is retained, so verified readback can detect later theme/asset changes. `include_content=True` adds the original source and current released discussion, resolving attachment paths and thread IDs in the current workspace. Use `get_slide` for current editable content and review status. Additions without an ID retain their one-shot behavior and reject duplicate slide IDs; `--verify` rejects them before any writes.

Revisions recorded by older versions contain source without a historical visual receipt. Retrying those through MCP reports that no visual receipt was recorded and requires a fresh read and reconciliation. Current assets cannot reconstruct the missing baseline; the tool does not upgrade old records or reapply their source.

A filesystem write and its operation record are not one atomic transaction. If storage fails after the canonical write but before its record commits, an unchanged retry can report an existing-ID or outdated-source conflict. If a revision leaves the source unchanged and no receipt was recorded, a retry may be accepted as a fresh write using the current visual state; it cannot prove the earlier visual baseline. Read and reconcile known storage failures manually. Ordinary lost HTTP responses after a recorded write and batches interrupted after an acknowledged prefix are safe to retry with the original file. Do not replace request IDs or hashes merely to force completion.

Use `--result-file /path/to/new-result.json` when a local script needs the full response. The client creates a private file before submitting calls and prints a short receipt with its location, success status and completed count. The file contains the same report as ordinary execution, including completed calls and error details after a partial failure. Read the relevant fields from it before drafting. You can combine this option with `--verify`.

Without `--verify`, a file can contain other explicitly authorized text-result tools, including `update_presentation_context(mode="append")` or `get_presentation_outline`.

Local captures can also use `"return_image": false` in their arguments. The tool
still renders and saves the image, while this client receives its paths, capture
identity and version metadata without an image block. Use the returned capture ID
for later text checks or inspect the saved file with a permitted image tool.
Metadata alone does not constitute visual review. Default inline-image responses
remain intended for the host's visual tools. [Capture delivery](VISUAL-REVIEW.md#scope-and-storage).

For a positioned `add_slide`, keep `after_slide_id` and/or `section_id` at the top level of its arguments. Preparation supplies omitted `request_id`, `expected_slide_ids` and `expected_sections_hash`; callers may also supply explicit reviewed values. A named section alone appends within that group; an empty `after_slide_id` inserts at the start of the group or deck. Order and section membership change in the same canonical file write. Missing or stale structural guards fail before preparing embedded assets. Exact retries retain the original acknowledgement and never reposition a later move. `--verify` compares source and visual hashes; inspect a fresh outline separately to verify current order and section membership.

## When connected tools are missing

Refreshing server code does not prove an existing chat has rediscovered the tool catalogue. Check the callable tools in the **agent host**, not just the server response. Current Artifact Studio includes `get_presentation_outline`, `update_presentation_context`, `get_authoring_guide` and retained-rule hash arguments. If they are absent, use the host's supported MCP connection refresh controls and recheck; do not add duplicate implementations.

OpenAI's [app-server documentation](https://learn.chatgpt.com/docs/app-server) describes `config/mcpServer/reload` as queuing a refresh for loaded chats. The [MCP setup guide](https://learn.chatgpt.com/docs/extend/mcp) also documents host restart controls. Those are host operations; this client does not modify Codex configuration or refresh its in-memory tools.

## Hosted design reference files

The reviewed-call client above remains loopback-only. For binary design references
on an authenticated hosted workspace, use
`python -m artifact_studio.designs.upload --url https://studio.example.com --design-id fictional-orbit --file ./master.pptx`.
Set `ARTIFACT_STUDIO_AGENT_TOKEN` in the agent environment outside chat. The helper
sends up to 50 MB directly from the file and returns one metadata receipt, keeping
the source bytes and credential out of model messages. See
[design source uploads](DESIGN-PACKS.md#hosted-agents-and-original-powerpoint-sources)
for ownership, validation and optional SHA-256 guards.

To inspect an original design source locally, use `python -m
artifact_studio.designs.download` with the source's inventoried `--path` and
`--sha256`, an explicit `--output`, and the same HTTPS origin/environment token.
It streams and verifies up to 50 MB, refuses to overwrite the output, and prints
only a filename/size/hash receipt. Read compact design guidance and selected
examples first; download the original source only when needed.
