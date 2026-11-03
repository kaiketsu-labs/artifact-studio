# Independent authoring and PowerPoint-inspired review

This cycle reviewed main `f636d2b`. Three agents audited workflow, visual design and context efficiency; another agent checked their findings. Two separate authors then used the real MCP tools, and an independent reviewer checked all nine authored slides and their figures. Validated changes were re-reviewed, followed by another author-side exercise of the candidate.

All presentations, source files, notes and feedback were fictional and isolated under `/tmp/artifact-evaluation-2026-11-02`. Authoring decks remained unapproved. Private Batch feedback was not released. No user collection or connected repository contents were used as authoring data. Voice PR #21 remains a separate change; this cycle follows the renamed current main and its newer editor/layout implementation.

## Author tasks and independent results

| Task | Result and independent checks |
| --- | --- |
| Create a renewal-support recommendation | Five blue slides with orange as the default workspace brand. 208/320=65%, 234/300=78%, +13 percentage points; different cohorts and observational limits visible. First response 6→3.5 hours. $72,000 rollout and $180,000 estimated annual savings are distinct, with modeled assumptions and proposed gates labelled. |
| Resume a water-sensor pilot deck from an attached CSV | Four blue slides. 48/240=20% and 36/300=12%, −8 percentage points; sites 60→90, leakage 15→11 m³/day; $225,000 pilot cost is distinct from the unknown extension budget. Coverage/period changes and noncausal interpretation are explicit. Original human cover note and note revision remain exact. |

The independent reviewer inspected every 1600×900 image, source data, citations, outlines and preserved notes. No further slide correction was warranted. The new-deck author had already corrected chart label spacing and a clipped stop-rule paragraph during author review. The outputs are sanitized standalone HTML with no notes, agent discussion or conversation data, retained in the ignored evaluation directory alongside full-size images and aggregate exchange logs.

Advertised capture tools failed during authoring because their local Playwright renderer browser was unavailable. Authors used the permitted sanitized audience view in a standard browser for their own fictional decks. No production renderer sandbox was weakened. These checks establish browser readability and factual fidelity, not physical-display behavior, PowerPoint execution or PDF export.

## Findings validated and improved

| Finding | Change and verification |
| --- | --- |
| Move a slide, delete it, Undo deletion, then Undo the move loses the earlier action | Deletions now participate in the same chronological client history as order/section changes. Existing guarded restoration retains source and notes. Browser cases cover earlier sections/moves, consecutive deletions and a committed restore with a lost response. |
| Cross-review found an old deck's Undo or Retry callback could survive a deck switch | Undo history clears on actual deck changes; restore and common retry paths reject inactive-deck commands. Uncertain requests keep their original token for retry after returning. Two-deck tests verify no hidden mutation and unchanged payload on a valid retry. |
| Forward Tab after text editing skips the visible Send and Cancel controls | Text actions follow the input in DOM order; submit/cancel restore focus without losing the retained caret on resize. Real keyboard tests verify Text→Send→Cancel. |
| Floating text controls obscure much of a narrow slide | When visible space permits, the narrow-screen editor reflows below the slide. Viewport resizing preserves focus/caret, and controls fit above the footer in the 390px fixture. Desktop and intermediate sizes retain their contextual controls. |
| Repeated source provenance duplicates selected design-file metadata | Optional compact source references point into the complete inventory in the same response. Default clients, raw receipts, complete rules, guides, briefs and inventories retain their contract. Exact reconstruction and add/remove/reordered inventory cases are tested, including real MCP transport. |
| Authors reloaded identical guidance and repeatedly initialized short-lived clients | Scoped bootstrap/new-deck guidance now avoids rediscovery after selection. The follow-up uses one client per phase; guide hashes and existing file-input batching remain available. Real per-author context comparisons validate the compact representation without claiming that shorter follow-up tasks are an end-to-end saving. |

The broader chat drawer/default-panel suggestions were not treated as confirmed defects. They involve tradeoffs and were left outside this focused change. Undo for adding/duplicating slides remains unsupported, and a restored deletion has no redo entry. Very short visible mobile viewports can still force overlap; physical keyboard and pinch-zoom behavior were not verified.

## Exchange evidence

Initial new-deck work recorded 32 RPCs, 32,368 request bytes and 322,283 response JSON bytes. Resuming recorded 40 RPCs, 30,273 request bytes and 327,711 response bytes. Each author used eight initializations and one 107,739-byte catalogue read, and each encountered one capture error. These observations identify workflow overhead; they are not comparable task benchmarks or billed model-token counts.

Both follow-up authors used one initialized client and one catalogue refresh, reconstructed all fifteen provenance entries from a ten-file selected pack inventory, and verified the full brief, blue preferences, hashes and unchanged deck/notes. No additional MCP read was required to resolve references. A preliminary new-author stdin setup failure is separately retained rather than hidden in the controlled comparison.

The [efficiency table](TOKEN-EFFICIENCY.md#independent-authoring-cycle--2-november-2026) reports controlled response reductions and request/catalogue overhead. The catalogue's large repeated output-schema definitions were examined and retained: removing them without host compatibility evidence could weaken tool contracts. Measurements remain bytes; actual model exposure depends on host serialization and caching.

Evidence is retained under `test-results/evaluation-2026-11-02/`: `authors/`, `design/`, `workflow/`, `efficiency/` and `verification/`. Only this aggregate report and regression tests are committed; fictional deck files, private database contents and raw screenshots stay local.
