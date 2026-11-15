# Efficient browser observation

Apply these practices through the browser tools already available to the host.
They adapt ideas from [turbo-browser-mcp](https://github.com/Tthanh2k6/turbo-browser-mcp)
at commit [`41c16f1`](https://github.com/Tthanh2k6/turbo-browser-mcp/tree/41c16f177aa27ecdbdfe04ff7dae45b1d5d385fe).
The project is [MIT licensed, copyright 2026 Trung Thanh Tran](https://github.com/Tthanh2k6/turbo-browser-mcp/blob/41c16f177aa27ecdbdfe04ff7dae45b1d5d385fe/LICENSE).
No upstream implementation is vendored here. Copying substantial code later
requires retaining that copyright and license notice.

## Reuse sessions and keep personas independent

Reuse a browser process where the host supports it, with a distinct context and
account for each person. Retain each person's context across that person's task;
do not restart the browser after every tool call. Close owned contexts/processes
at completion. Two tabs in a shared context share storage and are not independent
personas. Never attach these fictional users to an unrelated signed-in profile.

If turbo-browser tools are already exposed, `tabs_batch` with named
`isolatedContext` values can provide this separation. Otherwise use the host's
equivalent contexts. Inspect its live schemas and verify identity through the
product UI. A directory named after a person does not prove browser isolation.

## Observe narrowly, then act

Start with a bounded interactive/accessibility snapshot of the active area.
Inspect the relevant dialog, form, slide rail or chat instead of returning an
entire HTML document. Request bounds or frame details only when needed. Use a
small initial output budget, for example 4,000 characters, but treat truncation
as missing evidence: narrow the query or explicitly inspect the remaining area.
Never infer an absent control from a truncated result.

Keep screenshot pixels for visual judgments: text snapshots cannot establish
layout, clipping, typography, animation or contrast. Save full diagnostics and
images to files; return a focused summary and image/path instead of base64 or
unrelated console/network history. Start diagnostics with relevant errors, then
expand when the question requires success events or timing. Keep credentials and
private content out of shared reports.

## Batch known actions, retain verification

Group a short sequence (usually two to five steps) when its targets and intended
effects are already established: focus a known field, fill it, choose an option,
then inspect the resulting state. Execute steps sequentially inside the call,
stop at the first failure, and include one focused post-action observation.

With turbo-browser, `batch_actions` supports `continueOnError: false` and
`returnSnapshot: true`; an ordinary Playwright script can do the same using
normal locator/keyboard operations and assertions. This reduces host round trips
without changing the underlying UI behavior. Do not use internal app handlers or
synthetic property setters as evidence that normal keyboard entry works.

Keep unknown navigation, target discovery, concurrent-edit barriers and human
review decisions outside blind batches. Inspect a changed state before choosing
the next dependent action. A batch is not a transaction: record completed steps
and verify state after a failure instead of replaying already-sent messages or
uploads. Counts of tool calls and individual actions should remain separate.

Sending fictional feedback within the authorized disposable fixture is part of
the simulation. If a tool has a submit guard, authorize that specific intended
action; do not turn off guards globally or introduce a new human approval prompt
for every already-authorized fixture action.

## Treat deltas as hints until their baseline is sound

A delta is useful only if the caller retains a complete baseline for the exact
person, page/document, frame, mode and filter. Reset after navigation, context
loss, scope/filter changes or truncation. A valid implementation must also report
removed elements and avoid confusing duplicate labels or replaced DOM nodes.
Verify critical results against fresh targeted state, especially removal,
permission changes and recovery after another participant's edit.

Review of the pinned upstream `inspectCompact` found two limits worth testing
before copying its delta algorithm:

- It records all scanned signatures before applying the output cap. A
  deterministic probe of the actual function with synthetic frame results
  returned **4 of 20 controls** at a 350-character cap; the following unchanged
  delta at a 4,000-character cap returned **0**, omitting the 16 unseen controls.
- After a complete snapshot of 20 controls, removing one produced a delta with
  zero element lines and a total of 19. The removed control's identity was not
  reported. A changed total alone cannot reconstruct the current UI.

These were function-level probes, not an end-to-end browser assessment. The code
is in [dom-engine.mjs](https://github.com/Tthanh2k6/turbo-browser-mcp/blob/41c16f177aa27ecdbdfe04ff7dae45b1d5d385fe/src/dom-engine.mjs#L1310).
When using that version, request a fresh focused snapshot after a truncated
baseline or a relevant structural change; `onlyChanged` is not a complete state
or deletion protocol.

## Preserve rendering fidelity and honest measurements

Keep images, fonts, CSS and necessary scripts enabled for visual review and
presentation performance measurements. The upstream `turbo`/media-blocking presets
change what is rendered; use them only for a separately labeled network probe.
Do not replace Studio's isolated offline renderer with a signed-in browser.

Direct disk uploads and screenshot files avoid binary text in context. Studio
already offers file-input authoring, compact mutation receipts and retained-guide
hashes; use those existing contracts instead of duplicating them with a new MCP
server or adding a runtime dependency for this skill.

Measure the new observation policy on the same task and correctness checks.
The upstream benchmark's cumulative-token replay model is an assumption about
history reuse, not provider billing; its fallback character divisor is not a
tokenizer. Do not transfer its advertised savings to Studio. Follow
[the measurement guide](measurement.md) and report actual provider usage,
named-tokenizer estimates and transport bytes separately.
