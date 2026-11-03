# Documentation screenshots

Screenshots use the fictional **Studio Blue** presentation and **Team launch** artifact project. They contain no user files, corporate masters, external fonts or AI-generated app mockups.

From the repository root, after the normal development installation:

```sh
uv run playwright install chromium --only-shell
uv run python -m scripts.capture_screenshots
uv run python -m scripts.capture_design_library
uv run python -m scripts.capture_sharing
```

The command starts the real server on a free loopback port with a temporary collection and database, then uses real UI actions and MCP replies. It never opens the configured user collection or connects an agent CLI. The server, browser and temporary files are cleaned up when it exits. To review captures outside the checkout, add `--output /tmp/artifact-studio-docs`.

The editor screenshot source is [capture_screenshots.py](../../scripts/capture_screenshots.py); the fictional slide content is [screenshot_fixture.py](../../scripts/screenshot_fixture.py). Its generated [capture inventory](captures.json) records 29 editor and library outputs, UTC capture times, pixel sizes, viewports and SHA-256 hashes. Most captures use a 1440 × 1000 viewport; the chat drawer also uses 320 × 568. Each uses light or dark appearance, reduced motion and a fixed browser clock. Chat timestamps come from the temporary server's real clock. Notes are expanded through the normal Notes control before editor captures. The command checks its own inventory before copying the finished set to the destination.

The [README capture audit](readme-captures.json) verifies every PNG screenshot linked from the root README against the generated inventories and the actual file bytes. The six existing README screenshots were refreshed on 3 November 2026; the new context-menu capture uses the same isolated library fixture. The inventories cover all 41 documentation screenshots, including desktop, mobile and dark views. Images are captured directly from the UI; no DOM replacement or retouching is used for app screenshots.

[capture_design_library.py](../../scripts/capture_design_library.py) captures the dedicated design overview, add dialog, agent handoff and reference context on desktop and mobile, using isolated fictional Markdown, SVG, CSV and JSON sources. Its [inventory](design-captures.json) records the viewports and hashes. Reference file collections do not contain synthetic slide decks. Existing library captures are maintained separately.

| Design library view | Screenshot |
| --- | --- |
| File-first overview with add and delete controls | [Overview](../assets/design-library.png) |
| Name, reference files and optional description | [Add design](../assets/add-design.png), [mobile](add-design-mobile.png) |
| Copyable message for the connected agent | [Use with agent](design-use-agent.png) |
| Context file inventory with add, download and remove controls | [Context](../assets/design-context.png), [dark](design-context-dark.png), [mobile](design-context-mobile.png) |
| Image and PowerPoint cover previews | [Enlarged cover](design-file-preview.png) |
| Slide design pack with separate example action | [Pack context](design-pack-context.png) |

[capture_sharing.py](../../scripts/capture_sharing.py) captures the real web Share panel on [desktop](../assets/sharing.png), [mobile](sharing-mobile.png), and with the [download fallback](sharing-fallback.png). Its [inventory](sharing-captures.json) records viewports and hashes. These captures and the design handoff simulate native API availability at the OS boundary; exports, files and web controls are real. They do not show or verify a native app picker. Installed destinations and an actual iPhone/macOS share require device testing.

| Images | Shows |
| --- | --- |
| [Library](../assets/artifact-hub.png), [artifact review](../assets/artifact-review.png) | Related artifacts, a project brief and an authored HTML page with its own conversation. |
| [Context menu](../assets/context-menu.png) | A real artifact-card menu with open, view, comment, history, move, sharing, copy-link and context commands. |
| [Workspace](../assets/workspace.png) | Sections, approvals, Markdown chat and expanded speaker notes. |
| [Element review](../assets/element-review.png) | Selected slide element, contextual controls and a Batch draft. |
| [Slide chat](../assets/slide-chat.png), [light](chat-sender-icons-light.png), [dark](chat-sender-icons-dark.png), [320px drawer](chat-mobile-dark.png) | Wider chat, user/robot icons, a file attachment and a Markdown agent reply. |
| [Sorter](../assets/slide-sorter.png), [navigation](section-sorter-navigation.png) | Multi-selection and keyboard navigation across section rows. |
| [Slide management](../assets/slide-management.png) | Thumbnail context menu. |
| [Sections](../assets/slide-sections.png), [light](slide-sections-light.png), [dark](slide-sections-dark.png) | A folded group and visible question indicators. |
| [Active slide](section-active-slide.png) | A new slide reveals its previously folded section. |
| [Question, light](slide-question-light.png), [dark](slide-question-dark.png) | Suggested answers and a free-text answer in the slide's chat. |
| [Design references](design-references.png), [pending files](reference-controls.png), [saved files](reference-files.png), [removed file](reference-removed.png) | A fictional PowerPoint source plus attach, save and remove controls. |
| [New presentation references](new-presentation-references.png) | Managing shared design sources while preparing a new deck. |
| [Project files](../assets/projects.png) | Editable project save/open and audience export choices. |
| [Presenter](../assets/presenter.png) | Current/next slides, private notes, timer and filmstrip. |
| [Interactive slide](../assets/interactive-slide.png) | The result of clicking Compare inside an actual sandboxed interactive slide. |
| [Notes conflict](notes-conflict.png), [retry](notes-retry.png) | Actual HTTP conflict handling and a simulated connection failure, with both recovery paths exercised. |
| [Storage and backup](storage-backup.png) | The temporary working folder and reference/backup settings. No live repository is opted in. |
| [Visual review](../assets/visual-review.png) | Four sanitized slide renders composed by the app's contact-sheet helper. |

The visual-review example uses the production snapshot sanitizer, readiness hook and contact-sheet composer with documentation Playwright captures. It does not exercise or bypass the production renderer's OS isolation. Dedicated renderer checks cover that boundary; see [visual-review verification](../VISUAL-REVIEW.md) and [CI](../CI.md). Screenshots document appearance and exercised paths, not complete application test coverage.

The logo and conceptual [workflow diagram](../assets/workflow.svg) are artwork, not app screenshots, and are kept separately.
