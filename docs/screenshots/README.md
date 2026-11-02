# Documentation screenshots

All screenshots use the original fictional **Studio Blue** design and **A clearer way to work** presentation. They contain no user files, corporate masters, external fonts or AI-generated app mockups.

From the repository root, after the normal development installation:

```sh
uv run playwright install chromium --only-shell
uv run python -m scripts.capture_screenshots
```

The command starts the real server on a free loopback port with a temporary collection and database, then uses real UI actions and MCP replies. It never opens the configured user collection or connects an agent CLI. The server, browser and temporary files are cleaned up when it exits. To review captures outside the checkout, add `--output /tmp/slide-studio-docs`.

The screenshot source is [capture_screenshots.py](../../scripts/capture_screenshots.py); the fictional slide content is [screenshot_fixture.py](../../scripts/screenshot_fixture.py). The generated [capture inventory](captures.json) records all 26 outputs, pixel sizes, viewports and SHA-256 hashes. Captures use a 1440 × 1000 viewport, light or dark appearance, reduced motion and a fixed browser clock. Chat timestamps come from the temporary server's real clock. The command checks that every documentation PNG was captured before copying the finished set to the destination.

| Images | Shows |
| --- | --- |
| [Workspace](../assets/workspace.png) | Sections, approvals, Markdown chat and expanded speaker notes. |
| [Element review](../assets/element-review.png) | Selected slide element, contextual controls and a Batch draft. |
| [Slide chat](../assets/slide-chat.png), [light](chat-sender-icons-light.png), [dark](chat-sender-icons-dark.png) | Wider chat, user/robot icons, a file attachment and a Markdown agent reply. |
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
