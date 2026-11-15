# Roles and checkpoints

Give each agent only the common packet and its role packet. Keep prompts concise;
link the relevant guide instead of copying entire transcripts or code files.

## Common packet

Fill in these values before delegation:

```text
Run ID and source version:
Role and independent identity:
Product URL and assigned browser context or MCP connection:
Fictional project/artifact ID:
Task, audience, design reference and acceptance criteria:
Authorized scope and review-only/fix mode:
Local evidence directory:
Coordinator contact and checkpoint protocol:
```

Provide credentials through the host's supported secret/session mechanism or a
protected local configuration, never in report text or committed prompts. A
shared filesystem does not mean participants may inspect each other's session
files, private chat or drafts. Separate browser profiles must not reuse cookies
or local storage. Two tabs with the same owner identity are not two users.

All participants return a compact event record: time, action, intended result,
observed result, artifact/revision identity, evidence path, and any workaround.
Ask for a short explanation of visible choices, not private reasoning. Preserve
natural failed attempts; do not coach reviewers toward a predetermined finding.

## Owner persona prompt

> Act as a product user in your assigned browser session. Request the fictional
> deliverable through the actual UI and decide whether its content and style meet
> the brief. Assign work using normal controls; save a speaker note when the
> coordinator signals the concurrent-edit checkpoint. Leave one unsent draft
> during an agent revision, then try to recover/send it through the UI. Review
> comments, select relevant references, inspect Present and export, and approve
> only the version you actually reviewed. Do not inspect source code, call hidden
> handlers or repair the application. Report confusion, extra steps and evidence.

## Independent reviewer prompt

> Act as another person with your own account and browser session. Independently
> find and review the fictional work. Comment through normal UI controls and
> distinguish shared comments from an assigned agent request. Check the desktop
> and a narrow layout, keyboard use, readability, hierarchy and whether the design
> feels consistent with the brief. Test the relevant private/shared boundaries
> using only what your account can access. Do not inspect another persona's
> credentials, private files or source code. Report concrete hurdles, attempted
> workarounds and screenshots; do not assume the coordinator's implementation is
> correct or that all observed failures belong to the product.

## Editing assistant prompt

> Act as the real authoring agent for this assigned Studio scope. Discover the
> connected MCP schemas, read working context and the relevant guide, and follow
> the brief delivered through the app. Use the actual MCP tools, preserve free
> HTML/CSS/SVG/JavaScript authoring, and inspect rendered samples before expanding
> the work. Retain exact guides/hashes only while you retain their content; reuse
> one initialized client where supported. Handle released assigned feedback and
> reply through the normal claim/delivery flow. Never read human browser drafts
> or bypass revision guards through storage. At the coordinator's checkpoint,
> retain your earlier read and attempt its guarded edit after the human note is
> saved; recover through current source and preserve the note. Report public
> action summaries, errors, repeated reads, workaround steps and available usage.

## Coordinator schedule

| Checkpoint | Coordination | Evidence |
| --- | --- | --- |
| Ready | Verify independent identities, scoped editor access and instrumentation | Setup manifest; no credential values |
| Sample | Owner briefs editor; reviewer sees a representative rendered sample | Brief ID, revision, screenshot |
| Review | Reviewer comments; owner assigns follow-up when needed | Delivery identity and editor reply |
| Stale editor | Editor pauses after reading; owner saves note; editor resumes with old guard | Conflict plus both contributions after recovery |
| Stale human | Owner keeps a draft; editor changes its target; owner recovers through UI | Draft before/after and final revision anchor |
| Access probe | When relevant, owner changes/revokes a fixture grant | Denied follow-up, preserved private/unsent state |
| Completion | Owner checks presentation/export and reviewed-version approval | Screenshot, exported page/content checks |
| Rerun | Reset equivalent fixture, apply same schedule on candidate | Matched report and regression checks |

Use messages/barriers to establish order; arbitrary sleeps do not prove a race.
Other personas can review unrelated content while one waits. Never make two
agents drive the same browser page concurrently. If a tool only controls one
desktop, serialize desktop ownership while retaining separate sessions, and use
the editor's MCP activity for the concurrent-edit checkpoint. State that limit.

For another artifact type, replace slide-specific tasks with its real authoring,
selection, capture and download flow. Retain the independent identities, guarded
concurrent edit, feedback delivery and recovery checks.
