# Interface clarity review

Reviewed 14 November 2026 with two independent Sol UI reviewers, an Astra UI
reviewer, fresh Terra interaction reviews and a separate Astra security review.
Fixtures were fictional; the reviews exercised actual mouse and keyboard flows.

## Research and criteria

There is no visual test that establishes whether an interface was AI-generated.
The useful question is whether a repeated design convention makes this product
harder to use.

One primary-source example explains how generic visual defaults can arise:
[Bolt's design prompt](https://github.com/stackblitz-labs/bolt.diy/blob/acf91f99be1ff50138447740b0cf28f45335b89a/app/lib/common/prompts/new-prompt.ts#L195)
explicitly requests immersive headers, motion, gradients, glows and 16-pixel
corners. Its typography guidance includes 40-pixel headlines and 18-pixel body
text. Those instructions describe a particular builder's defaults; they are not
evidence that every generated interface uses those patterns. A working editor
needs task hierarchy and readable controls rather than a marketing-page header.

The review used these primary usability references:

- [Microsoft Fluent menu guidance](https://github.com/microsoft/fluentui/blob/b0af329db77175de60ea9ae3dc5fb253f8e1d7f4/packages/react-components/react-menu/stories/src/Menu/MenuBestPractices.md): keep menu content concise, group related choices, avoid more than two nested levels and keep popovers within the viewport.
- [W3C Focus Visible](https://github.com/w3c/wcag/blob/main/understanding/20/focus-visible.html): retain a visible, persistent keyboard-focus indicator. Removing a surrounding border must preserve another useful indicator.
- [W3C Labels or Instructions](https://github.com/w3c/wcag/blob/main/understanding/20/labels-or-instructions.html): provide enough context for the task without adding unnecessary instructions; familiar icon controls can work with clear context and accessible names.
- [W3C Target Size](https://github.com/w3c/wcag/blob/main/understanding/22/target-size-minimum.html): make controls easy to activate through size and spacing. The feedback launcher retains a 44-pixel target.

Some public design websites were inaccessible from the cloud environment.
The references above were read from their canonical public source repositories.

## Findings and changes

| Observed problem | Resulting behavior |
| --- | --- |
| HTML/PDF exports were hidden behind the vague “Presentation files” command. | **Export presentation…** opens the existing exports with HTML/PDF first. Editable archive operations form a separate section. |
| “Save presentation” downloaded an archive and said “Project saved,” suggesting it saved ongoing edits or a project container. | **Save a copy…** identifies the editable archive, its notes and feedback, and confirms that the copy downloaded. The existing shortcut and archive behavior remain available. |
| The Library destination “Presentation tools” did not describe where it navigated. | **Presentation editor** names the actual destination. Existing capabilities and direct Present action remain available. |
| Hover and keyboard focus could make an unselected agent icon look selected. | Only selection gives the icon its active color; a separate underline identifies keyboard focus. The icon has no enclosing border or backplate. |
| An agent-addressed Shared message used the same “Message your agent” wording as a private conversation. | The addressed Shared composer explicitly says **shared with others**. The private composer explicitly says **Private message**. Routing and permissions are unchanged. |
| Editor-generated requests exposed `replace_text` and other JSON fields in the normal chat transcript. | Known requests have readable change summaries. **Exact request** allows deliberate inspection; agents retain the original message and revision anchors. Copy uses the human-readable summary. Unknown or malformed formats are preserved. |
| Collapsing the phone-width Library sidebar left a 280-pixel blank block. | Collapsed navigation releases its layout space. Reopening restores project navigation and the version footer, including desktop behavior. |
| The feedback launcher used a wide persistent text button. | A speech bubble with an exclamation mark provides an icon-only action, with its tooltip, accessible name, keyboard return and 44-pixel target. |

The reviewers did not find evidence supporting a wholesale redesign of the
desktop header. Present already has appropriate primary emphasis, the canvas
remains central, formatting appears contextually, and the existing menu is short
enough to organize without extra nesting. Artifact cards and the compact list
view serve real browsing tasks; their use alone is not a usability defect.

The changes preserve arbitrary HTML/CSS/SVG/JavaScript authoring, original request
payloads, private conversations, revision checks and agent autonomy. The audit
does not introduce a template catalogue or reduce authoring capabilities.

## Verification

Reviews cover desktop and narrow light/dark layouts, keyboard and mouse, report
dialog focus and chat drafts, selected/unselected states, two-person message
visibility, exports and editable-copy round trips, and the collapsed sidebar.
Regression checks verify readable edit text, exact MCP payloads, unchanged source
hashes, escaped content, clipboard scope and reload behavior.

CI retains the same 86 browser module entries across one editor and three runtime
jobs, with the same Firefox coverage and failure gate. Before this split, the
successful [baseline run](https://github.com/kaiketsu-labs/artifact-studio/actions/runs/43576958662)
took 14m53s for runtime and 7m34s for editor, including setup. The new hosted
wall time must be measured before claiming a speedup; the split does not remove
tests or weaken the release gate.
