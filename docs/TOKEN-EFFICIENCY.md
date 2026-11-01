# Agent token efficiency

MCP is stateless at the transport level. Agents can still retain context between calls: sending the same guide, slide history or encoded image on every request is unnecessary.

## Read only what the task needs

| Need | Call |
| --- | --- |
| Resume a deck | `get_working_context(deck_id=...)` — complete user rules, that deck's design preferences, short workflow, available designs and presentations. |
| Create a deck | `get_working_context(design_id=...)`, then `create_presentation` with the selected design and brief. Omit design_id to use the default. |
| Detailed instructions | `get_authoring_guide(topic=...)` — workflow, live_review, visual_review, interactive_html, design_import, design_guide or design_context. |
| Asset inventory | `get_design_assets(design_id=...)`. |
| Read a deck's brief | `get_presentation_outline(deck_id=..., include_context=True)` — slide order, hashes/status and the full brief. |
| Check order/status later | `get_presentation_outline(deck_id=...)` — no slide HTML or conversation history. |
| Edit one slide | `get_slide`, then `revise_slide` with only changed fields. |
| Continue reviewing | `read_feedback`/`wait_for_feedback` with `after_delivery_id=next_cursor`. |
| Add continuation context | `append_presentation_context` with a short Markdown addition and current `context_hash`. |

Read the full brief and relevant `brief_files` attachments before authoring. Attachment metadata remains available in the outline. Compact responses do not replace source inspection, factual checks or visual review. `get_presentation` still returns the complete presentation; `get_working_context(detail="full")` returns the expanded guides and inventory.

## Mutation response compatibility

`add_slide` and `revise_slide` now return a **compact receipt by default**: deck/slide identity, title, revision, concurrency/content hashes, approval, visibility and unseen-change status. This changes the previous default response shape. Clients expecting HTML, notes, sources, agent explanations or feedback must set `include_content=True`, or call `get_slide`. Update integrations that assumed the old response before deploying this version. HTTP workspace responses are unchanged.

An agent already knows the source it just submitted. Echoing that source and the whole discussion wastes input tokens on its next turn. A receipt acknowledges the saved revision without this repetition. It is not proof of visual correctness.

## Retain rules explicitly

An initial working-context, presentation, outline or feedback read returns complete primary/secondary rules plus `rules_hash`. Subsequent `get_working_context`, `get_presentation`, `get_presentation_outline`, `read_feedback` and `wait_for_feedback` calls may send that `known_rules_hash` **only while the agent retains those exact rules**. The same hash is reusable across these tools and presentations in the workspace. An exact match sets `rules_included=false` and returns empty `authoring_instructions`/`learned_preferences` fields instead of repeating their text. Presentation briefs, slide content and other requested fields are still returned normally. A different hash always delivers the new rules in full. Omit the hash after compaction, restart or loss of context until the rules have been read again. Primary instructions are never summarized or truncated.

Delivery cursors still govern released feedback; unchanged-rule omission never releases Batch comments or unsent drafts. Design preferences remain available in working context and its full variant.

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
