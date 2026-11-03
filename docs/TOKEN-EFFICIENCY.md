# Agent token efficiency

MCP is stateless at the transport level. Agents can still retain context between calls: sending the same guide, slide history or encoded image on every request is unnecessary.

## Read only what the task needs

| Need | Call |
| --- | --- |
| Resume an artifact/project | `get_working_context(artifact_id=... or project_id=..., include_discovery=False)`. Retain exact rules/guide hashes for repeat reads; selected design and scoped context remain. |
| Resume a deck | `get_working_context(deck_id=..., include_discovery=False)` — complete rules, selected brand, source references and short workflow. Fetch discovery lists separately when needed. |
| Create a deck | `get_working_context(design_id=...)`, then `create_presentation` with the selected design and brief. Omit design_id to use the default. |
| Detailed instructions | `get_authoring_guide(topic=...)` — load a topic once; reuse its `hash` as `known_hash` only while retaining that exact text. |
| Asset inventory | `get_design_assets(design_id=...)`. |
| Read a deck's brief | `get_presentation_outline(deck_id=..., include_context=True)` — slide order, hashes/status and the full brief. |
| Check order/status later | `get_presentation_outline(deck_id=...)` — no slide HTML or conversation history. |
| Edit one slide | `get_slide`, then `revise_slide` with only changed fields. |
| Create or reorder | `create_presentation` / `reorder_slides` return deck identity and ordered `slide_ids`, without slide source. |
| Start automatic review | Read the live_review topic once, then `start_live_review`; its default response contains status and a guide pointer. |
| Continue reviewing | `read_feedback`/`wait_for_feedback` with `after_delivery_id=next_cursor`. |
| Add continuation context | `append_presentation_context` with a short Markdown addition and current `context_hash`. |

Read the full brief and relevant `brief_files` attachments before authoring. Attachment metadata remains available in the outline. Compact responses do not replace source inspection, factual checks or visual review. `get_presentation` still returns the complete presentation; `get_working_context(detail="full")` returns the expanded guides and inventory.

## Mutation response compatibility

`add_slide` and `revise_slide` return a **compact receipt by default**: deck/slide identity, title, revision, concurrency/content hashes, approval, visibility and unseen-change status. `create_presentation` and `reorder_slides` now also default to receipts containing `id`, `title`, `design_id` and ordered `slide_ids`. They no longer echo the template or the entire reordered deck. Set `include_content=True` for the previous full mutation response; `get_slide` and `get_presentation` remain available for source.

`get_slide` returns complete source, notes, assets and hashes, with `feedback_included=false` and an empty discussion list by default. Use cursor-based `read_feedback` for released comments, or `get_slide(include_feedback=True)` for the released slide history. `start_live_review` returns status without the setup guide; use `include_guide=True` for its previous guide field, or load the live_review topic. These are explicit default-response changes; update clients that relied on those fields. HTTP workspace responses and canonical files are unchanged.

An agent already knows the source it just submitted. Echoing that source and the whole discussion wastes input tokens on its next turn. A receipt acknowledges the saved revision without this repetition. It is not proof of visual correctness.

## Retain rules explicitly

An initial working-context, presentation, outline or feedback read returns complete primary/secondary rules plus `rules_hash`. Subsequent `get_working_context`, `get_presentation`, `get_presentation_outline`, `read_feedback` and `wait_for_feedback` calls may send that `known_rules_hash` **only while the agent retains those exact rules**. The same hash is reusable across these tools and presentations in the workspace. An exact match sets `rules_included=false` and returns empty `authoring_instructions`/`learned_preferences` fields instead of repeating their text. Presentation briefs, slide content and other requested fields are still returned normally. A different hash always delivers the new rules in full. Omit the hash after compaction, restart or loss of context until the rules have been read again. Primary instructions are never summarized or truncated.

Delivery cursors still govern released feedback; unchanged-rule omission never releases Batch comments or unsent drafts. Design preferences remain available in working context and its full variant.

## Retain guides and queued requests separately

Working context returns `guide_hash` and `guide_included`. Supply `known_guide_hash` to omit only the exact retained `guide` text. This does not suppress workspace rules, brand preferences, references or queued user requests. For a topic loaded through `get_authoring_guide`, pass its `hash` as `known_hash`: an exact match returns empty `markdown` with `content_status="unchanged"`. Changed or unknown hashes return the complete guide. Omit hashes after losing context. Expanded context remains explicitly available and still includes its requested guides and inventory.

`include_discovery=False` omits unrelated presentation, design and library lists and sets `discovery_included=false`. It preserves the selected design and its source references, full brand preferences, complete rules (unless explicitly retained), and queued user inbox messages. Use `list_presentations`, `list_designs` or `list_libraries` when discovering new work. The default remains `True` for existing integrations.

This also applies to general artifact/project and reference-only context. Related artifacts within the selected project remain as short pointers. For a presentation selected with `artifact_id`, the outer artifact guide and nested presentation guide are distinct: retain `guide_hash` with `known_guide_hash`, and `presentation_context.guide_hash` with `known_presentation_guide_hash`. A missing, unknown or changed hash returns the corresponding complete guide.

The MCP artifact context sends its source list once, with `receipt_sources="sources"` linking the compact delivery receipt. The complete receipt is still persisted and available through the HTTP context inspector.

Feedback reads and waits return `design_requests_hash` and `design_requests_included`. Supply `known_design_requests_hash` only while retaining that exact queued-request snapshot. The unchanged list is then omitted; additions, edits or removals return a new snapshot, including an empty list when the queue clears. An unchanged retained queue no longer makes a wait return immediately. This is not an acknowledgement: reply with `status="working"` when taking up a request. Never compare design event IDs with feedback delivery cursors. Feedback and design retention are independent of rule retention.

## Keep binaries out of model text

For custom content, send local image/font/script/style references in `interactive_html` and set `embed_assets=True` on the mutation. The server embeds those dependencies into the saved isolated document. The agent neither reads a large base64 result nor repeats it in a second tool call. The static `html` fallback remains required and selectable.

`prepare_interactive_html` remains available when prepared-source inspection is useful, but its returned embedded document can be expensive. Images used for visual inspection should arrive as image blocks through capture tools, rather than base64 text. Original files remain local.

Revision idempotency uses the original request and embedding mode. Identical retries return the saved revision without rereading dependencies; changing the payload or embedding mode with the same request ID is a conflict.

## Already-reviewed local drafts

Use the [file-input client](FILE-CLIENT.md) when patches are already on disk. It binds execution to a file hash, passes the JSON through existing MCP tools, and optionally checks a batch with one outline read per deck. This avoids a large draft print followed by the same HTML in tool arguments. Initial authoring, visual review and necessary source inspection still cost context; the saving is in duplicate transfer. The client stops on conflicts and reports partial completion without automatic rebasing or restoration.

## What this does and does not measure

Tokenization depends on the model. Use a named tokenizer as an estimate and compare like-for-like payloads; do not equate estimates with billed usage. Host prompt formatting, tool schemas, cache hits, image tokens and reasoning affect actual cost. MCP may expose both text and structured content; whether a host passes both copies to the model depends on that host.

Compact JSON often helps structured coordinates, but it is not always smaller than prose: “Delete this element” can be shorter than an action object. Prefer clear structured edit intent for precision; remove repeated HTML, history, guides and binary text first. Small HTML edits may still require submitting the changed slide's full HTML field. There is no arbitrary HTML fragment patch protocol in this version.

The test suite checks compact/full responses, transport serialization, exact rule-version handling, withheld feedback, safe context appends and embedded-asset retries. Performance audits should record aggregate counts without committing presentation content or private review data.

## Idle review work

The hook waits locally; each short server poll checks only whether a released delivery or queued design request exists. It does not load feedback bodies, attachments, full inboxes or workspace rules. Live leases renew near expiry instead of on every 0.2-second poll. The bounded 540-second host continuation remains: removing it without verifying host timeouts could silently end review. This improves local work without claiming fewer model wakeups or billed tokens.

The raw MCP tool catalogue does not repeat the bootstrap. Some hosts append the server's initialization instructions to every exposed tool description; the shorter canonical [bootstrap](../agent/bootstrap.md) reduces that repeated guidance. How much enters a prompt depends on the host's tool selection and caching.

## Measured authoring exchange

### Independent authoring cycle · 2 November 2026

A fresh review of `f636d2b` used two independent presentation authors, separate workflow/design/context auditors and cross-review of their findings. Both authors then exercised the candidate with one client per phase. See [the evaluation report](CONTINUOUS-EVALUATION-2026-11-02.md) for presentation accuracy, UI recovery tests and measurement limits.

Once a scope is selected, the bootstrap and new-deck handoff now recommend `include_discovery=False`. Discovery remains available by default. A new optional `compact_sources=True` avoids repeating a selected design file's name, local path and SHA-256 in provenance when the exact metadata is already present in `design_context.files`. Complete file inventory, scope and delivery status remain. Default responses and raw/persisted receipts keep their existing representation.

Each compact provenance entry has a `file_ref` such as `design_context.files[2]`. Resolve it in **that same response**: `title` is the file's `name`, `path` its `local_path`, and `hash` its `sha256`. Keep the entry's scope and delivery status. Indices are not durable IDs and must not be reused after another inventory read. Unmatched sources stay complete. This does not compact legacy `design_references`; omitting unrelated discovery and retaining actual guide/rule hashes are separate controls.

Controlled real MCP results used equal-length baseline/candidate checkout paths, five selected reference files, twelve unrelated designs, an artifact and a two-slide deck. Byte counts include serialized text and structured copies, excluding HTTP framing. Default response sizes matched baseline in all five cases.

| Matched context read | Before | After | Response reduction |
| --- | ---: | ---: | ---: |
| First selected-reference artifact, compact sources | 33,356 B | 30,516 B | 2,840 B / 8.51% |
| Retained-guidance artifact, compact sources | 21,215 B | 18,375 B | 2,840 B / 13.39% |
| Selected artifact, omit unrelated discovery | 42,016 B | 33,356 B | 8,660 B / 20.61% |
| Author's unchanged five-slide deck, retained context | 39,334 B | 35,942 B | 3,392 B / 8.62% |
| Author's unchanged four-slide deck, retained context | 38,341 B | 34,949 B | 3,392 B / 8.85% |

The compact flag adds 23 request bytes. It adds no response saving when there is no eligible `design_context.files` inventory. The advertised catalogue grows by 258 bytes (107,745→108,003); the clearer bootstrap grows by 29 bytes (1,393→1,422). Full schema definitions remain intact. Both authors reconstructed every source from the compact form and verified complete briefs, selected blue preferences and original notes. These are transport measurements, **not model-token counts or billed savings**; host selection of content copies and caching differ. The after-exercise totals describe different work and are not compared with initial authoring totals as a saving.

Run the benchmark with `--compact-sources` or `--discovery` to isolate the two controls. Reuse one initialized client within an authoring phase and batch already-reviewed local calls where useful; do not skip protocol initialization for a genuinely new client. Retain an identical workflow guide's hash when requesting the design guide. Do not reread unchanged full source or recapture unaffected slides just to copy them into another call.

### Scoped artifact context · 2 November 2026

A second isolated benchmark compares `2b2fd53` with the scoped-context changes. Both runs use five selected design files, twelve unrelated designs, one HTML artifact and one two-slide deck, with equal-length checkout paths. Counts include the text and structured copies in serialized MCP results; they are not model tokens or billed savings.

| Context read | Before | After | Reduction |
| --- | ---: | ---: | ---: |
| Selected HTML artifact | 50,595 B | 33,744 B | 33.3% |
| Same artifact, retained rules/guide | 39,950 B | 23,003 B | 42.4% |
| Reference collection only | 44,361 B | 29,032 B | 34.6% |
| Presentation opened through the hub | 52,990 B | 41,650 B | 21.4% |
| Same presentation, both guides retained | 42,347 B | 17,971 B | 57.6% |

The tool catalogue grows from 107,332 to 107,643 bytes to advertise the separate nested-guide hash. The initialization bootstrap shrinks from 1,817 to 1,393 bytes while retaining instruction authority, free HTML/CSS/SVG/JavaScript authoring and review boundaries. Host-added copies depend on tool exposure and caching.

In a separate controlled fixture with 100 released comments and one withheld comment, 100 idle polls over 19.8 simulated seconds made 100 lease updates before and one after. Full feedback/guidance loads are avoided. This measures local database work, not fewer model turns; the bounded idle continuation remains.

Run `uv run --no-sync python -m scripts.measure_artifact_context --output test-results/artifact-context.json`. For the old source, run the same script from a neutral directory with that checkout on `PYTHONPATH`, use its locked dependencies and add `--baseline` to omit the newly introduced nested-guide argument. The reports contain aggregate counts only.

### Earlier presentation exchange

The November 2026 audit compared main commit `dc4dbbf` with token-efficiency commit `e17043e` using disposable data: two extra designs, 30 reference decks, a 14-slide target, 18 released discussion messages and one queued design request. This is a synthetic stress fixture, not a claim about typical decks or billed tokens. Counts below are UTF-8 bytes of serialized MCP result JSON, including the compatible text and structured copies. Equal-length checkout paths avoid path-length bias. Each before/after pair uses the same operation and state; the retained-feedback pair uses the same delivery cursor and retained rules in both versions.

| Exchange | Before | After | Reduction | Change |
| --- | ---: | ---: | ---: | --- |
| Selected-deck startup | 29,904 | 20,720 | 30.7% | Shorter quickstart; request `include_discovery=False`. Brand, references, queued requests and rules remain. |
| Repeat startup, rules already retained | 23,428 | 6,443 | 72.5% | Also retain the exact quickstart with `known_guide_hash`; preferences and references still return. |
| Start live review | 19,008 | 1,390 | 92.7% | Status and guide pointer instead of repeating the loaded guide. |
| Create a presentation | 1,313 | 315 | 76.0% | Deck/slide identities instead of template source. |
| Reorder the target deck | 200,903 | 769 | 99.6% | Ordered IDs instead of every slide's source. |
| Read a slide for editing | 38,977 | 2,060 | 94.7% | Complete source/notes/hashes; released discussion read through its feed. |
| Repeat feedback at the same cursor | 6,800 | 846 | 87.6% | Retain the exact queued-design snapshot; do not redeliver it or acknowledge it. |
| Re-read an unchanged guide | 18,147 | 509 | 97.2% | Exact guide hash returns metadata rather than the text again. |

For this scripted sequence, including call parameter JSON and one tool catalog but excluding the diagnostic full-context read, combined exchange size fell from 476,744 to 175,749 bytes (63.1%). Hashes add a small request cost that is included in this total. The tool catalog grew from 73,838 to 77,190 bytes (4.5%) because the safe controls and inclusion metadata are advertised. Full-detail context and first feedback reads remain complete; their metadata adds a small cost. Slide revision receipts were already compact and stayed at 780 bytes. Do not translate these byte reductions directly into token or billing percentages: tokenization, host selection of content copies and caching differ.

Run the development benchmark with `uv run --no-sync python -m scripts.measure_agent_io --phase after`. It starts a temporary local server and collection and emits aggregate counts under `test-results/`; it never reads a user's collection. To reproduce the baseline, change to a neutral directory (outside both checkouts), then invoke the candidate benchmark script by absolute path with the baseline checkout on `PYTHONPATH` and `--phase before --output /path/to/before.json`. Use an environment with the project's locked dependencies and verify that `artifact_studio.__file__` resolves inside the baseline checkout, so you do not accidentally benchmark the candidate twice. Use equal-length checkout paths for strict byte comparisons. The real MCP integration test also records compact/full result sizes and checks that text and structured results agree.
