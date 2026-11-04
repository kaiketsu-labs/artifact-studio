# Revision recovery verification

The baseline was main `ca99b0e`. Its `revise_slide` replay returned saved source but recomputed visual hashes using current theme/assets. After a lost HTTP response, a retry could therefore report successful verification despite a subsequent visual change.

Revisions now record their original source, hashes and review status in the existing operation record. Exact retries return that evidence before reading or embedding dependencies. The source-only storage API still returns a plain `Slide`. Legacy records without visual receipts remain readable through that API, while MCP requires a fresh read and reconciliation instead of reconstructing history from current assets.

Compact add/revise writes skip discussion entirely. Full responses combine original write-time source with current released discussion, resolving imported attachment paths and thread IDs. Stored snapshots contain no discussion. Older add snapshots with cached history also receive the current released discussion when requested. Withheld Batch feedback stays excluded.

## Actual MCP simulations

The integration tests start the real server and deliberately discard a response after a committed write. Replaying the identical calls file then verifies the original receipt through a fresh client and outline read.

- Unchanged revision: recovery succeeds without an extra revision.
- Changed theme or asset: readback reports drift.
- Later human notes: notes survive and readback reports the source change.
- Deleted slide: replay does not restore it and readback reports it missing.
- Existing add-response loss and partial-batch recovery remain covered.

Running the two new theme/asset cases against the baseline reproduced the bug: both wrongly returned `ok=True`. Both pass with this fix. Unit tests also cover cross-entry-point retries, conflicting payloads/embedding modes, deleted embedding dependencies, project imports with remapped attachments/replies/questions, legacy operations, and interrupted filesystem writes. Two independent Terra reviewers checked the implementation and privacy behavior.

A filesystem write and its operation record remain separate transactions. An unrecorded changed-source write conflicts on retry. An unrecorded no-op cannot establish a historical visual baseline and may be accepted as a fresh write. Both cases are simulated; see [FILE-CLIENT.md](FILE-CLIENT.md).

## Controlled efficiency comparison

The same fictional deck, request fields and 100 released comments were used before and after. These are serialized UTF-8 JSON byte counts, excluding transport framing. They are not model-token counts or billing estimates.

| Measurement | Before | After | Saving |
| --- | ---: | ---: | ---: |
| Revision request / compact receipt | 219 / 313 bytes | 219 / 313 bytes | Unchanged |
| Addition request / compact receipt | 317 / 317 bytes | 317 / 317 bytes | Unchanged |
| Discussion rows loaded by those two compact writes | 200 | 0 | 100% |
| Serialized equivalent of discussion materialized internally | 180,748 bytes | 0 bytes | 180,748 bytes |

The discussion saving is internal work avoided, not model-context savings: compact responses already omitted that history. Full source, notes and explicitly requested released feedback remain available. Earlier measured context savings and their limits are documented in [TOKEN-EFFICIENCY.md](TOKEN-EFFICIENCY.md).
