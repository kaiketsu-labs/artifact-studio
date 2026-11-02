# PowerPoint comparison

Reviewed on 30 October 2026. Three fresh Terra research passes covered slide management, canvas editing, and presenting/files. Separate Terra reviews checked the resulting data handling, everyday UI flows and accessibility. This comparison guides improvements; it is not a claim of full PowerPoint compatibility.

Updated on 2 November 2026 for sections, layout undo/redo, cursor-centred zoom and expanded notes. [Current UI screenshots](screenshots/README.md).

| Area | Slide Studio now | Remaining gap |
| --- | --- | --- |
| Slide management | Add after active slide, duplicate a selection, delete with Undo and durable recovery. Shift range and Cmd/Ctrl individual selection; grouped dragging and movement commands in the thumbnail list and zoomable slide sorter. | Text outline view. |
| Sections | Named, collapsible groups in the rail and sorter. Rename, move and remove groups; move slides between them. Shared layout undo/redo checks for conflicts. | No nested sections. |
| Visibility | Eye controls and selection-wide Hide/Unhide. Hidden slides remain editable and manually accessible during a show. | No per-show custom slide routes. |
| Canvas selection | Immediate hover/click, keyboard targeting and an object picker for text, images, diagrams and named groups. | No full layer-order, grouping or object-visibility editor. Objects follow document order, which can differ from visual stacking. |
| Canvas changes | Explicitly confirmed text, drag, keyboard-position, rotation, deletion and six slide-alignment suggestions with exact element and revision context. Positioning accounts for rotated/scaled parent groups. | Resize, alignment between objects/distribution, rich text and a persistent withdrawable proposal queue. Suggestions still require an agent to change canonical slide HTML. |
| Discussion | Resizable per-slide Markdown chat with user/robot icons, paperclip/drop/paste attachments and retained drafts. Compact agent questions offer suggested or written answers; yellow badges survive folded sections. Notes open by default; explanations stay collapsed. | Earlier-version comments have text context but no historical visual preview. |
| Presentation | Direct slideshow, reveal steps, progress, manual hidden-slide jumps, private current/next/notes, clock/timer and display controls. Display failures produce private recovery messages. | Physical display placement depends on browser permissions and hardware; not verified in this review. |
| Recovery | Local persistence, portable project archives, review/chat/draft state and recoverable deletions across import. | Save project downloads a snapshot; it does not establish a continuously updated external project file. |
| Audience exports | Self-contained HTML with local assets and animations; static PDF print layout with builds revealed. Private notes and chats omitted. | Native PPTX output. Native PDF output and direct offline HTML playback still need separate application checks. |

New slides are deliberately blank in the selected design language. The agent can compose freely rather than being constrained to a layout catalogue. Duplication copies saved content, sources, notes and visibility, plus browser note drafts when present; it starts with its own unapproved review state and empty conversation.

Deletion keeps slide IDs, source, notes and review history. Recovery places slides relative to their surviving original neighbours and refuses ambiguous reversed anchors rather than overwriting newer work. Structural commands retain request IDs for safe delivery retries and recover interrupted prepared file writes before subsequent content changes.

## Explicit workspace choices

- PDF includes **all slides**, including hidden ones.
- Printable keys and slide clicks advance the slideshow; editing fields and focused controls retain normal input.
- The agent applies canvas suggestions. The preview is not a complete native drawing editor.
- Workspace rules remain primary. Imported instruction snapshots and inferred preferences do not replace them.

## Reference behaviour

Microsoft documents inserting after the active slide, contextual duplicate/delete and grouped rearranging in [slide management](https://support.microsoft.com/en-gb/powerpoint/training/add-rearrange-duplicate-and-delete-slides-in-powerpoint?nochrome=true). Its [Selection pane](https://support.microsoft.com/en-gb/powerpoint/use-the-selection-pane-to-manage-objects-in-documents) also controls stacking, grouping and visibility; Slide Studio's current object picker only helps select objects.

See [QA evidence](../QA.md), [usage](GETTING-STARTED.md) and [project format](PROJECTS.md) for tested behaviour and verification boundaries.
