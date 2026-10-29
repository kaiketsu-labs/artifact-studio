<p align="center">
  <img src="web/brand/mark.png" width="104" height="104" alt="Slide Studio logo">
</p>

<h1 align="center">Slide Studio</h1>

<p align="center">
  A local slide workspace for you and your agent.<br>
  Draft with Codex or Claude Code. Give feedback on the slide. Approve and present.
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#connect-your-agent">Connect an agent</a> ·
  <a href="#live-or-batch">Live &amp; Batch</a> ·
  <a href="docs/GETTING-STARTED.md">Full guide</a>
</p>

![Slide Studio workspace: slide thumbnails and red/green approval states on the left, a visual slide in the centre, and the feedback conversation on the right.](docs/assets/workspace.png)

**Local HTML slides · Stateless MCP · Human approval · Private presenter notes**

Slides are editable HTML, CSS and SVG, saved on your machine. Bring a PPTX, website or image as a design reference; the agent turns it into a reusable design language. Layouts stay flexible. PPTX files are reference inputs; PowerPoint export is not currently available.

## From brief to presentation

![Workflow: provide a brief and design reference, let your agent draft, review and refine together, then present.](docs/assets/workflow.svg)

| Compose | Review | Present |
| --- | --- | --- |
| Move text, shapes, icons, charts and images freely through the agent. | Click an element and describe the change beside the slide. | Start full-screen slides directly, with optional private presenter controls. |
| Add reference files to the brief. Reuse your designs. | Red means unreviewed. Green means you approved that version. | Keep notes, sources and agent discussion off the audience screen. |

## Quick start

Requires [Git](https://git-scm.com/), [uv](https://docs.astral.sh/uv/getting-started/installation/) and access to this private repository. Your existing agent supplies the AI connection; the app needs no separate AI API key.

```sh
git clone https://github.com/kaiketsu-labs/slide-studio.git
cd slide-studio
uv python install 3.14
uv sync --locked --python 3.14
uv run --no-sync uvicorn studio.app:app --host 127.0.0.1 --port 8790
```

Open **[127.0.0.1:8790](http://127.0.0.1:8790/)** and keep the server running. Stop it with **Ctrl+C**. On macOS, `./start.command` is a shortcut after setup.

## Connect your agent

Both clients use the same **Streamable HTTP** endpoint: `http://127.0.0.1:8790/mcp`.

**Codex**

```sh
codex mcp add slide-studio --url http://127.0.0.1:8790/mcp
```

**Claude Code** — run from your agent's working project.

```sh
claude mcp add --transport http slide-studio http://127.0.0.1:8790/mcp
```

Other MCP clients can use the slide tools too. Reload the agent session if the new connection is not visible, then start with:

> Call `get_working_context` in Slide Studio and help me create a presentation. I'll provide the design reference, audience and key points.

The **plug icon → Copy agent prompt** resumes work on the selected deck. Copying a prompt does not configure MCP or start an idle agent.

### Your first deck

1. **Add a design.** Open **New presentation → + beside Design library**, then send a PPTX, image or link. Ask the connected agent to read the pending design request and build/register the design. An empty design library on first launch is expected.
2. **Give it context.** Choose the design, enter a title and brief, and attach reference files with the paperclip. Up to five files, 25 MB each and 50 MB total.
3. **Draft together.** Create the presentation, then use the copyable agent prompt to ask for a draft. Creation starts with one branded cover; the agent builds the rest from your brief.

## Point to the change

![A selected Review box is highlighted on the slide, with an element-specific comment in the feedback field and Batch mode selected.](docs/assets/element-review.png)

Hover to highlight; click to comment. Clear the selection for a general request such as “add a slide” or “move this section earlier.” Speaker notes are editable below the slide. Agent explanations and sources stay collapsed; questions remain visible.

## Live or Batch

| Live | Batch |
| --- | --- |
| Each submitted comment is released immediately. | Comments stay queued until **Send batch**. |
| A connected review session can continue between responses through host hooks. | The agent receives the released batch together. |

For automatic continuation in **Codex or Claude Code**, open **plug → Automatic feedback → Copy setup prompt**. Follow the one-time [project-local hook setup](docs/LIVE-REVIEW.md), including native host loading/trust, then call `start_live_review` in the connected agent. **Available** indicates an active connection; **End review** stops listening and preserves comments.

Plain MCP cannot wake a closed chat. Keep the review session open, or resume the agent to read saved feedback. Native host activation and physical display placement have separate [verification limits](QA.md).

## Present with context

![Private presenter view showing the current slide, next slide, editable notes, elapsed timer, clock, navigation and slide filmstrip.](docs/assets/presenter.png)

**Present** starts the full-size slide immediately. Arrow keys navigate; **Esc** ends the slideshow. Presenter view adds current/next slides, editable notes, a pause/reset timer, clock, filmstrip and display controls. When browser capabilities, permissions and a second display allow it, audience slides go on the other screen. On one screen, the default is the full-size audience slide.

To open presenter controls manually, move the pointer to the bottom toolbar and choose **Presenter view**. Use **Open audience window** to open or retry the audience display.

## Your files stay local

| What | Default location |
| --- | --- |
| Presentations, design packs and brief attachments | `~/Documents/Presentations/` |
| Approvals, feedback and workspace settings | `.state/` inside this checkout |
| Unsent feedback and note drafts | Browser-local storage |

Back up **both the presentation collection and state folder**. Set `SLIDE_STUDIO_COLLECTION` and `SLIDE_STUDIO_DATABASE` to choose other locations. Removing a presentation from the selector preserves its files and history; use **Undo** or the restore icon to bring it back.

The app ships without corporate masters, logos or fonts. Optional private-repository snapshots require an explicitly enabled destination; there are no automatic uploads. See [reference libraries and sync](docs/REPOSITORY-SYNC.md).

## Documentation

| Guide | Covers |
| --- | --- |
| [Getting started](docs/GETTING-STARTED.md) | Installation, storage options, first deck, controls and troubleshooting. |
| [Automatic feedback](docs/LIVE-REVIEW.md) | Codex/Claude Code hooks, activation, continuation and limitations. |
| [Agent guide](docs/AGENT-GUIDE.md) | Free composition, feedback cursors, conflict-safe edits and visual review. |
| [Design packs](docs/DESIGN-PACKS.md) | Import a design from the user's reference. |
| [Reference libraries & sync](docs/REPOSITORY-SYNC.md) | Reuse previous decks and opt into private snapshots. |
| [Verification](QA.md) | Automated checks, fresh installation and application test scope. |

<details>
<summary><strong>Development checks</strong></summary>

```sh
uv sync --locked --python 3.14
uv run pytest
uv run basedpyright
uv run ruff check studio tests
uv run ruff format --check studio tests
node --test tests/test_frontend.cjs
```

Node.js is needed only for frontend behavior tests. Test fixtures and README screenshots use original fictional designs; no personal decks or corporate assets are bundled.

</details>
