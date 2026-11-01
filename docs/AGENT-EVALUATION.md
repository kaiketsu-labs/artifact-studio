# Multi-agent authoring evaluation

This evaluation used two independent authoring agents, an API reviewer, a product/design reviewer, an independent feedback validator, and a separate UI implementation agent. The reviewers checked the authors' output and process rather than accepting their self-reports. Confirmed findings were corrected, then the authors exercised the updated tools again. The final changes preserve main's newer general-reference upload/removal behavior.

All presentation data was fictional and stored in an isolated collection/database. No human approvals were issued, no real collection was modified, and no evaluation decks or screenshots are committed. Local evidence and sanitized HTML deliverables are under the ignored `test-results/agent-evaluation/` directory.

## Tasks and independent review

| Task | Output | Checks |
| --- | --- | --- |
| Create an executive onboarding recommendation | Five blue slides from a brief, with orange as the workspace default | 130/200 = 65%, 168/210 = 80%, +15 percentage points; support 40→25 per 100 activations; $48,000 supplied rollout cost and $160,000 estimated annual savings. Different cohorts and proposed gates are labeled. |
| Resume a council bus-pilot deck | Four blue slides, saved CSV inspected and human cover note preserved | Wait 12.5→8.0 minutes = −4.5; on-time arrivals 72→86% = +14 percentage points; $320,000 pilot cost is distinct from an unknown extension budget. Observational limits are explicit. |

The design reviewer inspected all nine slides at 1600×900 using sanitized audience exports. A small overflow found by the onboarding author was corrected before the final independent review. The reviewers found the resulting slides legible and unclipped, with charts consistent with the supplied figures. The final audience route was also checked directly in the browser, without a download. These checks do not establish physical-display behavior. Local production capture could not start Chromium's required OS sandbox; it was not weakened or represented as passing.

## Findings reviewed and improvements

| Finding | Independent validation | Improvement and verification |
| --- | --- | --- |
| An acknowledged `add_slide` blocks replay of a partially completed file batch | Reproduced duplicate-ID failure; ownership cannot be inferred from an existing ID | Explicit add `request_id` records the original write-time view. Exact retries do not recreate deleted slides, overwrite edits or re-embed assets. Real MCP tests cover lost responses, partial batches and visual drift. |
| Recomputing retry receipts could hide theme/asset changes | Validator challenged the initial implementation proposal | Persist the original content and source hashes with the acknowledgement. Readback detects later visual changes; conflicting payloads remain errors. |
| File preflight displayed `null` for added slide IDs | Confirmed nested `arguments.slide.id` was not read | `--describe` shows the nested ID, retry token and supplied field names without HTML or notes. Verified add batches require retry IDs before any call executes. |
| Creation ID format and preview routes were unclear | Confirmed tool metadata omitted the required dated shape; authors guessed routes | Advertise the dated ID pattern/length. Creation receipts return review and sanitized audience URLs. |
| Agent visual fallback entered a reviewer surface containing withheld Batch text | Author stopped using it; validator checked the actual response structure | An inline `audience_url` uses the existing sanitized export pipeline. Full-response sentinel and browser tests exclude notes, agent context and conversations. Canonical `/decks/` HTML remains distinct and is not advertised as sanitized. |
| Batch's initial plus icon did not explain two-step delivery | Design screenshots and a second reviewer confirmed it | Show “Add to batch” and a private-until-send helper. Browser tests cover attachments, keyboard entry, withholding/release, Live switching and light/dark narrow chat; independent visuals checked laptop widths. |
| Identical copied workflow and brand guides dominated exchanges | Controlled comparison used the same complete workflow text and two calls in each case | Document cross-topic `known_hash` reuse. Exact copied text can be omitted; distinct brand text and changed workflow guidance return in full. Current expanded guide was brought into line with the quickstart after the authors found it stale. |

An addition's canonical filesystem write and its operation record are not an atomic transaction. A storage interruption between them leaves an existing-ID conflict requiring read/reconciliation; the tool never guesses ownership. This limitation is explicit in [FILE-CLIENT.md](FILE-CLIENT.md). Returned write-time acknowledgements are not assertions that no subsequent edits occurred; verified readback remains necessary.

## Exchange measurements

These are UTF-8 byte counts of the trace helper’s serialized JSON (with ASCII escaping), excluding HTTP headers; they are not literal packet counts, model tokens, billing, or comparable end-to-end authoring costs. After-pass totals cover different tasks and must not be compared with initial authoring totals as a savings claim.

| Measurement | Result |
| --- | ---: |
| Initial onboarding authoring pass | 21 traced calls; 25,636 request bytes; 172,017 response bytes; no tool errors |
| Initial bus authoring pass | 20 traced calls; 27,721 request bytes; 146,606 response bytes; one sandbox capture error |
| Controlled guide pair: fresh workflow + full identical brand copy | 95,212 response bytes |
| Same pair: fresh workflow + exact retained hash for brand copy | 48,175 response bytes |
| Pair reduction | 47,037 response bytes / 49.40%; both cases use two calls and one complete uncached workflow |
| Pair reduction including request parameters | 46,885 bytes |

The guide-pair result depends on exact text equality. After the current workflow guide was updated, its old hash correctly returned the complete new guide; a distinct older brand guide also returned in full. No user rules or brand preferences were shortened to produce this saving. Startup/reference metadata, full briefs, source access, notes and concurrency hashes were checked independently.

Additional `tools/list`, initialization, browser and artifact traffic is excluded from the traced authoring totals. The authors each discovered the tool catalog separately; the baseline catalog was about 77 KB. The forwarding trace helper recorded aggregate call sizes and errors, not model billing. The broader synthetic token-efficiency benchmark and its limits remain in [TOKEN-EFFICIENCY.md](TOKEN-EFFICIENCY.md).
