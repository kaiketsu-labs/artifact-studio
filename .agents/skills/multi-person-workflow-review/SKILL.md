---
name: multi-person-workflow-review
description: Simulate real Artifact Studio usage with independent agents acting as an owner, a human reviewer, and an editing assistant. Use when asked for multi-person user testing, a complex authoring/review simulation, multiplayer workflow testing, or a rerun that investigates friction, workarounds and token efficiency.
---

# Multi-person workflow review

Run an actual authoring and review loop, find reproducible obstacles, and repeat
the affected workflow after fixes. The human roles are LLM personas, not external
human research participants. A scripted benchmark complements this study; it does
not replace independent agents using the product.

Resolve the repository root from this directory (`../../..`). Read its `AGENTS.md`
and relevant implementation. Keep the main skill short in each agent's context;
load the linked role and measurement guides only when needed.

## Establish the run

Use the user's task, design and scope. If unspecified, use a fictional 12–14 slide
presentation with varied layouts, an editable diagram, sections, speaker notes
and a hidden appendix. Include another artifact type only when relevant. Locate
an authorized existing design before drafting; inspect a few representative
compositions before expanding the deck. Do not invent a generic replacement for
an established visual style.

Create a disposable collection and database. Use separate browser contexts and
identities for the owner and reviewer, plus a separately scoped MCP connection
for the editing agent. Keep live collections, backups and real recipients out of
the experiment. Fixture provisioning may use development helpers; record that
setup separately from product interactions.

Record the source commit (and dirty-file fingerprints), scenario, fixture,
environment, browser, agent models, baseline and acceptance criteria before
starting. Save local evidence under `test-results/workflow-review/<run-id>/`.
Use [roles and checkpoints](references/roles.md) for delegation prompts and
[measurement and report](references/measurement.md) for instrumentation.
Before browser work, read [efficient browser observation](references/browser-workflow.md)
for short action batches, focused snapshots and isolated session reuse.

## Delegate distinct participants

Spawn at least three actual agents with fresh contexts, using the host's agent
delegation tools. Give each a role brief, its own identity/session, the product
URL, task and evidence destination. Do not share the other personas' private
messages, implementation explanations or expected bug findings.

- **Owner persona:** creates the brief through the UI, assigns work, saves notes,
  handles review decisions and verifies the resulting presentation/export.
- **Reviewer persona:** independently explores the UI, comments on the work,
  checks usability and visual quality, and challenges unclear behavior.
- **Editing assistant:** performs the task through the real connected MCP tools,
  handles assigned feedback and preserves concurrent human work.

Use `gpt-6.1-terra` for browser personas, consistent with repository review
guidance; the editing agent can inherit the current model. Record any unavailable
model/tool and the actual replacement. With limited slots, stage additional
reviewers after these participants; preserve distinct contexts and identities.
If independent agents, browser control or a real MCP connection are unavailable,
report the missing coverage. Do not substitute a coordinator-written dialogue or
script and call it a completed multi-person simulation.

The coordinator provisions fixtures, instruments boundaries and coordinates
checkpoints. It does not secretly complete a persona's task. Participants should
use Studio's normal conversation for task feedback. Coordinator messages may
schedule races or report blockers without relaying private product content.

## Exercise the real workflow

1. **Author and inspect.** The owner sends the brief. The editor reads scoped
   context, relevant guides and the connected tool schemas, then creates a small
   rendered sample before completing the work. Keep a reusable MCP session.
2. **Independent review.** Both human personas use actual mouse and keyboard
   controls in separate sessions. Inspect desktop and narrow layouts, relevant
   light/dark states, slide selection, chat references and feedback delivery.
   Distinguish ordinary comments from assigned agent requests. The editor reads,
   claims and replies only through the supported scope and workflow.
3. **Concurrent work.** Coordinate a deterministic checkpoint: the editor reads
   a revision, the owner saves a note, then the editor attempts its earlier edit.
   Verify rejection of a stale write, deliberate recovery, and preservation of
   both contributions. Also test a human draft becoming stale and its recovery.
   For multiplayer scope, check unsent/private content isolation and revocation.
4. **Finish as a user.** Exercise Present, Enter/build advancement, return to the
   editor and the relevant export. Check hidden-slide behavior, readable layout,
   preserved notes and version-specific approval. Only the human persona approves.
5. **Explain hurdles.** Collect concise visible action/decision summaries, tool
   failures, retries and workarounds. Reproduce findings before changing code.
   Label product defects, discoverability, agent mistakes and infrastructure
   accommodations separately. Do not request or infer hidden chain-of-thought.

Adapt the checklist to the requested feature and list omitted coverage. Do not
expand a focused test into every app feature. Persona steps must use the public
UI; direct database edits or internal JavaScript handlers cannot count as a
successful user journey. Explicit fault injection belongs in a separately labeled
recovery probe, not an unreported workaround.

## Fix and repeat

Respect a review-only request. Otherwise fix confirmed issues within the user's
scope, keeping unrelated work intact. Add meaningful regression coverage for
data integrity, delivery, concurrency or lifecycle failures. Follow repository
checks and documentation requirements.

Repeat the failed journey with the original persona and a fresh independent
reviewer. After UI changes, use several fresh Terra reviewers on distinct affected
journeys as required by `AGENTS.md`. Request an independent Astra security review
when changing permissions, authentication, isolation, backup/restore or delivery
authorization. Security review supplements concrete negative-access tests.

Rerun the same workload and measurement boundaries on baseline and candidate;
do not compare a full first authoring run to a short correction pass as a saving.
If new defects appear, fix and repeat the affected checks. Stop when the agreed
acceptance checks pass and no reproducible in-scope defect remains, or report a
concrete blocker with preserved evidence. Do not claim unmeasured optimization.

## Deliver

Produce the report described in [measurement and report](references/measurement.md):
scenario and roles, verified findings, fixes, before/after screenshots, observed
workarounds, measured usage, rerun results and remaining limitations. Include a
short user-facing summary and links to evidence. Keep raw logs private; only
sanitized fictional evidence belongs in repository documentation.

This skill does not authorize a release, external messages or publication of
private sources. Follow the user's existing instructions for commits, PRs and
merges; do not add a new approval step when those actions are already authorized.
