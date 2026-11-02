# PowerPoint-inspired usage evaluation

Three agents independently exercised an editor workflow, a presenter workflow and an authoring-agent workflow. They used actual browsers and MCP calls against isolated fictional presentations. No user presentation was changed by the evaluation.

The starting point was `d4191b7`. PowerPoint informed navigation, grouping and recovery expectations; this is not a claim of feature parity.

| Reproduced scenario | Observed problem | Improvement | Regression evidence |
| --- | --- | --- | --- |
| Add or restore a slide while its section is collapsed | The active thumbnail remains hidden | Reveal the destination section before focusing and scrolling to the slide ([#13](https://github.com/kaiketsu-labs/artifact-studio/pull/13)) | Section browser journeys cover new and restored active slides |
| Tab through controls when the current slide starts a section | Disabled Add section hides its Rename explanation from keyboard users | Offer a focusable contextual Rename action with matching icon and accessible name ([#13](https://github.com/kaiketsu-labs/artifact-studio/pull/13)) | Keyboard activation opens the rename dialog |
| Navigate down through a sorter with uneven section rows | ArrowDown moves sideways using the first section's column count | Choose the nearest tile in the adjacent visible row ([#13](https://github.com/kaiketsu-labs/artifact-studio/pull/13)) | Uneven, empty and collapsed section navigation in Playwright |
| Create, rename, remove or move a section; then Undo | Section changes are not recorded and erase earlier layout/deletion undo | Add section edits to guarded layout history; undo layout before falling back to deletion ([#15](https://github.com/kaiketsu-labs/artifact-studio/pull/15)) | Undo/redo, interleaved slide moves, delete–rename–undo, focus and concurrent-change rejection |
| Retry creation after losing a successful MCP response | Existing-ID error encourages another presentation instead of ownership-safe recovery | Record the original creation snapshot under an explicit retry ID ([#14](https://github.com/kaiketsu-labs/artifact-studio/pull/14)) | Lost-response MCP simulation; changed inputs/defaults/assets, later edits, missing/corrupt/replaced source and export tests |
| Add evidence into an earlier section | Append succeeds in the last section and requires a second guarded move | Allow guarded insertion after a slide or into a named section in the original canonical write | Positioned/empty-group additions, stale guards, exact retries, later moves/deletion, project import and real MCP recovery tests |

Cross-review found two additional defects before publication: creation replay needed to reject a missing canonical file even when its marker survived, and section history needed to retain deletion undo. Both have regression coverage and independent re-review approval.

The simulations also exercised working multi-selection dragging, ordinary move undo/redo, zoom, question choice submission, deleted-slide recovery, presentation advance and returning to the editor. Passing these paths does not establish every PowerPoint interaction, physical-display behavior or paid-model generation quality. Native Codex/Claude checks exercise configuration, hooks and MCP boundaries without model keys.

Creation records remain local ownership/recovery evidence. Project imports omit them, a complete filesystem copy can retain a marker, and a filesystem write plus its SQLite receipt is not one transaction. Unrecorded or unreadable creations require inspection and reconciliation. Positioned additions have the same storage boundary as existing additions; exact recorded retries never reapply a later move. Source/visual hash readback does not verify current placement; read the outline for order and grouping.

Browser traces, screenshots and simulation logs are retained locally under `artifact-studio-dev/artifacts/usage-editor`, `usage-presenter`, `usage-agent` and the focused verification directories. PR screenshots contain fictional data only.
