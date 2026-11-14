# Fictional presentation workflow study

This study used a disposable hosted unified test fixture, a scoped editor agent, and two LLM personas acting as owner and independent reviewer, rather than external human participants. The first pass used a fictional 14-slide product story because the original editable deck had not yet been located. The user then identified the original backup and rejected the draft's generic visual style. The final design pass uses the original Pixel Studio presentation; the measurements below describe the first pass, not the later design work. Only selected audience-facing illustrations are published; the original source and private material stay outside this repository.

## Observed collaboration

1. The owner requested a leadership narrative with scoped collaboration, exact-version review, backup versus sync, a hidden appendix and honest token claims. The agent built a 14-slide story through the scoped MCP connection. It initially failed to read working context and authoring guidance before drafting. This was an agent error; the result used fixed pixel dimensions that clipped in the actual slide viewport.
2. Alice reviewed in the UI and left ordinary shared comments. The agent's `read_feedback` returned only the owner's assigned request, not the unassigned comment. The owner assigned a follow-up incorporating the critique. The agent clarified comments versus assigned requests, the difference between one-way backup and configured hosted project sync, and exact-version approval.
3. Alice found the clipping on desktop and mobile. The agent read the working context and guide, changed the slide source to scale with its container, and Alice confirmed the desktop and narrow dark views. Mobile text remained small. These [before](presentation-workflow-study/review-before.png), [corrected](presentation-workflow-study/review-after.png) and [mobile](presentation-workflow-study/review-mobile.png) captures document the first pass; they are not the final visual direction.
4. The owner saved a presenter note while the agent held an earlier slide hash. The stale revision returned a conflict; a fresh read preserved both sentences of the note. A separate follow-up draft was rejected with a slide-changed response after the artifact changed. Reloading and re-entering the request succeeded. This preserved the revision guard but added recovery friction; no draft loss is asserted.
5. The export preview defaults to including hidden slides. Turning that option off and printing in Chromium produced a 13-page PDF; extracted text omitted the hidden appendix. The source retains it. Browser rendering was tested in headless Chromium, not a native app or physical display.

## Measurement and limits

The private local JSONL trace records MCP method, fictional arguments, request and response byte counts, elapsed time, content type and HTTP status; it does not contain bearer credentials or hidden reasoning. The public report contains only [aggregate counts](presentation-workflow-study/mcp-summary.json). HTTP success alone does not establish tool success: three observed schema-validation attempts and the intentional stale revision returned tool errors despite successful HTTP transport. The first attempted authoring used local rather than hosted argument names; a second omitted required hosted insertion guards; a third supplied nulls. Fresh outline reads supplied insertion order and revision guards, while repeated initialization and exploratory reads reflect avoidable subprocess restarts and discovery. The initial omission of guidance caused the extra layout repair cycle.

Bytes and timings measure transport in this one run; they are not model token counts or a controlled comparison. Provider usage for the collaborating agents was unavailable. Do not infer a token saving from this study. The companion benchmark reports its own methodology and results.


## Curated transcript

The following are concise paraphrases of public, fictional actions and messages, not hidden reasoning.

| Participant | Visible request, action or result |
| --- | --- |
| Owner persona | Create a leadership story; explain collaboration, exact-version approval, backup, sync and the hidden appendix. Avoid invented token savings. |
| Editor | Built 14 editable slides and replied to the assigned brief. |
| Alice persona | Shared comments: distinguish ordinary comments from agent requests; clarify the backup/sync comparison. Later reported desktop and mobile clipping. |
| Owner persona | Assigned a follow-up incorporating Alice's feedback. The first stale draft submission received a conflict; after reload and re-entry the request succeeded. |
| Editor | Revised the affected slides, acknowledged the assigned request, and changed the fixed-size layout to container-relative dimensions. |
| Owner persona and editor | The owner saved notes after the editor read a hash. The stale edit was rejected; a fresh read preserved the notes. |
| Alice persona | Rechecked the corrected desktop and narrow dark layouts; content fit, although mobile text is small. |
| Owner persona and editor | The owner approved the cover. A subsequent edit returned `approved: false` and preserved the note, demonstrating that approval belongs to the reviewed version. |

## Traffic and opportunities

The author's local MCP log recorded 117 JSON-RPC requests: 93,874 request-body bytes and 350,762 response-body bytes. These totals cover this editing run through the final revision, including 16 initializations, 19 outline reads, 34 slide reads, 14 add attempts, 25 revision attempts, three feedback reads, two claims, two replies, one working-context read and one guide read. The trace measures HTTP JSON bodies, not browser traffic, image data, model prompts, or provider usage. Initialization notifications were sent separately and are excluded. Counts include failed attempts and retries; the logger did not preserve tool `isError` for every response, so the four known errors above are an observed minimum rather than a complete failure count. No end-to-end timer was recorded, so workflow duration is unavailable.

| Hurdle | Observed effect | Next time |
| --- | --- | --- |
| Skipped working context and authoring guide | Fixed pixels clipped desktop and mobile slides; 14 source revisions and another review were needed. | Read the scoped context and relevant guide first; inspect a rendered sample before building the set. |
| Generic style substituted for the established design | The user rejected the technically working draft; another design pass was necessary. | Locate the existing deck and design before authoring, then review a few representative compositions before expanding the deck. Saving a context read is counterproductive when it causes a whole-deck rewrite. |
| Hosted MCP schema differs from local tool examples | Three validation attempts preceded the first successful add. | Discover the connected server's tool schema before writing. |
| Short-lived scripts restarted the session | 16 initializations and repeated exploratory outline reads added traffic. | Keep one client session and reuse a fresh outline when safe. Read again when ordering or revision guards require it. |
| Draft anchored to an old slide version | Submission was rejected; the persona reloaded and re-entered the request. | Make the current version and recovery step clear; preserve and offer the draft for review against the new version. |
| Screenshot returned as raw bytes | A reviewer REPL printed PNG bytes into its tool output, adding harness noise outside MCP. | Save the image to a file and return only its path or compact metadata. |
| Agent status showed unavailable | The fixture had a scoped MCP peer but no listening host hook, so a status indicator alone did not mean this editor was running. | Distinguish a connected credential from an active listener in the workflow and use assigned requests. |

These were observations and suggested improvements at the end of the first pass. The [follow-up implementation and rerun](workflow-iteration.md) records the resulting fixes, independent browser checks, agent workarounds and matched source comparison. The separate benchmarks have different units and scope; their results should not be added to these transport totals.

## Original design restored after user review

After the user identified the backup, the second pass updated a local copy of the original Pixel Studio deck. All 15 original slide IDs and their speaker notes were retained; factual clarifications were appended to notes where necessary. A response-byte benchmark slide brings the source to 16 slides. The original agenda is retained hidden, leaving 15 visible slides and a 15-page audience PDF. The editable archive was imported into a separate empty collection to check its structure and notes. The remote backup was not modified, and its source/design files are not published here.

Sol directed the update; Astra reviewed the visual hierarchy and factual claims, and two Terra personas checked the rendered result and browser interactions. The update preserves pixel typography, human/robot characters, the ink/lavender/coral palette and varied diagram layouts. It replaces obsolete Live/Batch content, clarifies optional design context and exact-version approval, and explains the different Claude/Codex idle-hook behavior. Embedded app screenshots were recaptured in the current editor with private notes collapsed. The [five README illustrations](../screenshots/presentation-illustrations.json) are selected completed frames, not app mockups.

Browser checks covered fonts, sampled slide bounds, reveal steps, arrow navigation, Notes, Sources, Present and Escape. At 390 pixels wide the slides fit but body text and footnotes remain small; full-size viewing is needed for detail. Source numbering includes the hidden slide. Static captures use the shared renderer; the copied presentation runtime was exercised separately, so capture metadata does not assert runtime equivalence. Synchronous Playwright routing must be pumped by Playwright waits; earlier pauses in a Python REPL did not establish app latency.

The second pass is not included in the 117-call first-pass trace or the controlled benchmark. Its model tokens and total tool traffic were not instrumented. The strongest practical lesson is to spend context early on the existing design, connected tool schema and one rendered sample: avoiding those reads produced more work and a rejected first draft.
