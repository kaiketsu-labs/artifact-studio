# Documentation screenshots

The current [single-send chat in light](chat-single-send-light.png) and [dark](chat-single-send-dark.png) uses compact agent icons, connection dots and Copy conversation. The rendered fictional fixture seeds a review lease through the API; no agent CLI or provider is connected. [Capture provenance](chat-single-send-captures.json) records browser, source and image hashes. Older screenshots may show the retired Live/Batch controls.

The [default-design controls](default-design.png) were reviewed in actual Chromium at 1440 × 844 with two fictional slide design packs and a reference-only collection. The filled star and Default badge identify the saved workspace preference.

The [version menu](app-version-menu.png), [current version](app-version-current.png),
[nightly option](app-version-nightly.png) and [narrow About dialog](app-version-mobile.png)
use the actual UI with fictional data. Stable and nightly release responses are
simulated at the network boundary; channel changes use the real persisted settings.
These images are not a release announcement. [capture_app_version.py](../../scripts/capture_app_version.py)
maintains the [capture inventory and accessibility audit](app-version-captures.json).
Native Mac menu compilation and appearance require macOS.

App screenshots use the fictional **Studio Blue** presentation and **Team launch** artifact project. They contain no user files, corporate masters, external fonts or AI-generated app mockups.

The repository retains four audience-facing slides from the product presentation **Working with agents**, with the user's explicit permission: the [cover](../assets/agents-cover.png), [workflow](../assets/agents-loop.png), [agent context](../assets/agents-context.png) and [Live/Batch](../assets/agents-modes.png). These are static illustrations in the presentation's Pixel Studio styling, distinct from app screenshots. The Live/Batch illustration records the earlier delivery UI and is no longer shown in the README. Completed 1600 × 900 captures preserve the original typography, colours and pixel artwork; notes, chats, source documents and design assets are excluded. [Illustration provenance](presentation-illustrations.json) records source slide IDs, revision hashes and image hashes. The canonical presentation remains local and unchanged. GitHub displays the completed frames; these PNGs do not contain the presentation's animations.

Design reference captures also include the [formatted text preview](design-text-preview.png) and VS Code-style editor in [light](../assets/design-text-editor.png), [dark](design-text-editor-dark.png) and [narrow](design-text-editor-mobile.png) layouts. They use fictional Markdown and actual Edit, Save and Cancel actions.

From the repository root, after the normal development installation:

```sh
uv run playwright install chromium --only-shell
uv run python -m scripts.capture_screenshots
uv run python -m scripts.capture_design_library
uv run python -m scripts.capture_sharing
uv run python -m scripts.capture_slide_designs
uv run python -m scripts.capture_element_alternatives
uv run python -m scripts.capture_full_backups
uv run python -m scripts.capture_chat_references
uv run python -m scripts.capture_app_version
```

The command starts the real server on a free loopback port with a temporary collection and database, then uses real UI actions and MCP replies. It never opens the configured user collection or connects an agent CLI. The server, browser and temporary files are cleaned up when it exits. To review captures outside the checkout, add `--output /tmp/artifact-studio-docs`.

The editor screenshot source is [capture_screenshots.py](../../scripts/capture_screenshots.py); the fictional slide content is [screenshot_fixture.py](../../scripts/screenshot_fixture.py). Its generated [capture inventory](captures.json) records 33 editor and library outputs, UTC capture times, pixel sizes, viewports and SHA-256 hashes. Most captures use a 1440 × 1000 viewport; the chat drawer also uses 320 × 568. Each uses light or dark appearance, reduced motion and a fixed browser clock. Chat timestamps come from the temporary server's real clock. Notes are expanded through the normal Notes control before editor captures. The command checks its own inventory before copying the finished set to the destination.

The [README capture audit](readme-captures.json) checks README PNG references against actual file bytes and current capture inventories. The slide-alternative captures were added on 4 November 2026. Images are captured directly from the UI; no DOM replacement or retouching is used for app screenshots.

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
| [Quick project access](../assets/quick-access.png), [dark presentation menu](quick-access-dark.png) | Connected work, green project connection icons and the Recent projects submenu. The documentation fixture seeds a local review lease; no agent CLI is connected. |
| [Context menu](../assets/context-menu.png) | A real artifact-card menu with open, view, comment, history, move, sharing, copy-link and context commands. |
| [Workspace](../assets/workspace.png) | Sections, approvals, Markdown chat and expanded speaker notes. |
| [Element review](../assets/element-review.png) | Selected slide element, contextual controls and a Batch draft. |
| [Slide chat](../assets/slide-chat.png), [light](chat-sender-icons-light.png), [dark](chat-sender-icons-dark.png), [320px drawer](chat-mobile-dark.png) | Wider chat, user/robot icons, a file attachment and a Markdown agent reply. |
| [Sorter](../assets/slide-sorter.png), [navigation](section-sorter-navigation.png) | Multi-selection and keyboard navigation across section rows. |
| [Sorter presentation title](sorter-header.png) | Project-backed presentation context stays visible above the slide overview. |
| [Grouped history](history-grouped.png) | Latest versions remain visible; earlier rapid saves expand on request. |
| [Slide management](../assets/slide-management.png) | Thumbnail context menu. |
| [Sections](../assets/slide-sections.png), [light](slide-sections-light.png), [dark](slide-sections-dark.png) | A folded group and visible question indicators. |
| [Active slide](section-active-slide.png) | A new slide reveals its previously folded section. |
| [Question, light](slide-question-light.png), [dark](slide-question-dark.png) | Suggested answers and a free-text answer in the slide's chat. |
| [Design references](design-references.png), [pending files](reference-controls.png), [saved files](reference-files.png), [removed file](reference-removed.png) | A fictional PowerPoint source plus attach, save and remove controls. |
| [New presentation references](new-presentation-references.png) | Managing shared design sources while preparing a new deck. |
| [Presentation files](../assets/projects.png) | Editable project save/open and audience export choices. |
| [Presenter](../assets/presenter.png) | Current/next slides, private notes, timer and filmstrip. |
| [Interactive slide](../assets/interactive-slide.png) | The result of clicking Compare inside an actual sandboxed interactive slide. |
| [Notes conflict](notes-conflict.png), [retry](notes-retry.png) | Actual HTTP conflict handling and a simulated connection failure, with both recovery paths exercised. |
| [Storage and backup](storage-backup.png) | The temporary working folder and reference/backup settings. No live repository is opted in. |
| [Retry backup](storage-backup-retry.png) | Upload and reference-refresh actions have distinct icons; the tooltip explains retry. |
| [Full GitHub backup](full-github-backup.png), [dark](full-github-backup-dark.png), [narrow](full-github-backup-mobile.png), [unchanged](full-github-backup-unchanged.png), [restore](full-github-backup-restore.png) | Separate full-state opt-in, schedule, retention, skipped unchanged checks and another-computer recovery. |
| [Visual review](../assets/visual-review.png) | Four sanitized slide renders composed by the app's contact-sheet helper. |

The visual-review example uses the production snapshot sanitizer, readiness hook and contact-sheet composer with documentation Playwright captures. It does not exercise or bypass the production renderer's OS isolation. Dedicated renderer checks cover that boundary; see [visual-review verification](../VISUAL-REVIEW.md) and [CI](../CI.md). Screenshots document appearance and exercised paths, not complete application test coverage.

[capture_element_alternatives.py](../../scripts/capture_element_alternatives.py) captures the pending-review control, headline/text/graphic choices, comparison, saved choices, narrow layout, sorter title, grouped history and backup retry. Its [inventory](element-alternatives-captures.json) records 14 captures and their accessibility audits. The grouped timeline's thumbnail endpoint receives browser captures of the exact fictional revisions; the earlier element-history capture uses a placeholder PNG. Both leave production renderer isolation unchanged. Backup status uses a fictional repository and connection failure without running a backup.

The logo and conceptual [workflow diagram](../assets/workflow.svg) are artwork, not app screenshots, and are kept separately.

[capture_chat_references.py](../../scripts/capture_chat_references.py) captures
the actual [cursor preview](chat-reference-hover.png), [compact draft pills](chat-reference-chips.png),
[sent pills](chat-reference-sent.png) and [dark narrow artifact composer](chat-reference-artifact-mobile.png)
plus saved-pill thumbnails in [light](chat-reference-pill-hover.png) and [dark](chat-reference-pill-hover-dark.png)
using fictional projects and slides. The [inventory](chat-reference-captures.json)
records viewports, hashes and accessibility findings; pass `--axe /path/to/axe.min.js`
to include the audit. Cover caches contain browser captures of the fixture because
the managed host cannot launch the production renderer's OS isolation. The slide
preview itself uses the actual iframe endpoint. No user collection is opened.

[capture_slide_designs.py](../../scripts/capture_slide_designs.py) publishes three fictional whole-slide directions through the real MCP tool, then uses the real preview, hover, compare, choose and History controls. The [inventory](slide-designs-captures.json) records seven captures with viewports and byte hashes: [editor](../assets/slide-designs.png), [hover](slide-designs-hover.png), [comparison](slide-designs-compare.png), [narrow](slide-designs-mobile.png), [dark](slide-designs-dark.png), [chosen](slide-designs-chosen.png) and [History](slide-designs-history.png). Frozen proposal documents are real. Only the unrelated existing revision-timeline thumbnail uses a fictional PNG because its production renderer requires OS isolation unavailable on managed capture hosts. The earlier [UX prototype](../prototypes/slide-alternatives/README.md) records design exploration; these captures show the implementation.
