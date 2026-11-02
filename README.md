<p align="center">
  <img src="web/brand/mark.svg" width="104" height="104" alt="Artifact Studio logo">
</p>

<h1 align="center">Artifact Studio</h1>

<p align="center">
  A local artifact workspace for you and your agent.<br>
  Keep presentations, pages, documents and images together. Review the work where it lives.
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#connect-your-agent">Connect an agent</a> ·
  <a href="#live-or-batch">Live &amp; Batch</a> ·
  <a href="docs/GETTING-STARTED.md">Full guide</a>
</p>

![Artifact Studio library with fictional projects, cached previews and format filters.](docs/assets/artifact-hub.png)

**Local files · Free composition · Stateless MCP · Human approval**

The **Library** is the home screen. Browse all your work, filter by project or format, and open an artifact with its own conversation. Projects hold a shared brief and related artifacts. Existing presentations open in the slide editor, with their files, links, notes and review history preserved.

| Artifact | Review it in its own format |
| --- | --- |
| Presentations | Slide thumbnails, element feedback, sections, notes, animations and a private presenter view. |
| HTML pages and browser bundles | Responsive page preview with authored JavaScript, isolated from private workspace APIs. |
| Markdown | Rendered document or original source, with text selections. |
| Images and SVG | Original proportions, zoom and region selections. |
| Other files | Original files and downloads, with artifact-level discussion. A native editor is not supplied for every format. |

Agents choose their tools, code and layouts. Import a file or folder, or have the agent register an authored bundle. A type selects the viewer; it does not prescribe components or require a design pack. General HTML bundles can contain their own CSS, JavaScript, SVG, fonts and data. External services are not enabled automatically, and opening a preview never runs installation or build scripts.

## Quick start

**Prefer guided setup?** Open this checkout in your agent and run **`/setup` in Claude Code** or **`$setup` in Codex**. The bundled [setup skill](.agents/skills/setup/SKILL.md) checks existing settings and connects the agent. [First-run details](docs/GETTING-STARTED.md#guided-setup).

Requires [Git](https://git-scm.com/), [uv](https://docs.astral.sh/uv/getting-started/installation/) and access to this private repository. Your existing agent supplies the AI connection; the app needs no separate AI API key.

```sh
git clone https://github.com/kaiketsu-labs/artifact-studio.git
cd artifact-studio
uv python install 3.14
uv sync --locked --python 3.14
uv run playwright install chromium --only-shell
```

Choose **one** way to run it:

| Mac menu-bar app | Terminal |
| --- | --- |
| On macOS 13+ with Apple's Command Line Tools, run `uv run --no-sync python scripts/install_macos.py --launch-at-login`, then open `~/Applications/Artifact Studio.app`. | Run `uv run --no-sync uvicorn artifact_studio.app:app --host 127.0.0.1 --port 8790` and keep the Terminal open. Stop with **Ctrl+C**. |
| Status, Open, Start/Stop/Restart and launch at login. [Mac setup](docs/MACOS.md). | On macOS, `./start.command` is a shortcut after setup. |

Open **[127.0.0.1:8790](http://127.0.0.1:8790/)**. Stop a Terminal server before switching to the Mac controller. Chromium enables cached rendered previews and agent capture tools; the library can show format fallbacks when no preview is available.

The supported installation is this source checkout. The Mac controller needs the checkout and `.venv` to remain in place. No standalone notarized download, npm package or PyPI release is published by this project yet.

## Connect your agent

Both clients use **Streamable HTTP** at `http://127.0.0.1:8790/mcp`.

**Codex**

```sh
codex mcp add artifact-studio --url http://127.0.0.1:8790/mcp
```

**Claude Code** — run from the agent's working project.

```sh
claude mcp add --transport http artifact-studio http://127.0.0.1:8790/mcp
```

Reload the agent session if the connection is not visible, then start with:

> List the projects and artifacts in Artifact Studio. Read the working context for the selected project, then help me make a page and a matching handout from my brief.

Use `get_working_context(artifact_id=...)` for selected work or `project_id=...` before creating related work. Existing decks also accept `deck_id=...` and retain their design guidance. The [agent contract map](agent/README.md) explains the canonical bootstrap, guides, tools and context receipts.

**Microsoft 365 Copilot:** use a Copilot Studio agent with the optional [authenticated gateway](docs/MICROSOFT-COPILOT.md). Select one project or explicitly allow the workspace. The gateway needs an approved HTTPS endpoint; the local editor stays private. Start from **Connect an agent → Microsoft 365 Copilot**, or the hub menu. Microsoft sign-in, connector policies and an actual Copilot tool call must be verified separately. Automatic Copilot wake-up is not included.

Open **Designs → Add design**, enter a name, then drop in reference files or choose them from your computer. Master slides, guidelines, PDFs, logos, images, fonts and other file types are supported. The description is collapsed and optional; files can also be added later. **Add design** saves the collection and opens its context files. A design does not need example slides.

![Adding a design with named reference files and an optional description.](docs/assets/add-design.png)

Click a design card to see the context files, add more, download a source or remove an attachment. **Use with agent** prepares a message you can copy into your connected agent's chat to use the design for your next task. If clipboard access is blocked, the message remains selected for manual copying. Registered slide design packs also offer **Open example slides**; the workspace default is marked. Normal presentations using a design remain in the artifact library.

![Design library with slide design packs and reference file collections.](docs/assets/design-library.png)

The trash icon removes a design from the library; **Deleted designs** restores it. Existing presentations and source files are preserved. Agents discover designs with `list_design_contexts` and inspect their file inventory with `get_design_context`. For project work, pass `design_id` to `get_working_context` alongside the project or artifact ID. File pointers show what is available; the agent must open the actual files. Attaching references does not automatically apply their styling.

Cards preview a pack's example slide or an attached image/SVG. File collections without a visual source show a compact document cover. Previews are read-only; clicking still opens the context file inventory.

![A design's context files, with add, download and remove controls.](docs/assets/design-context.png)

### Your first project

Choose **New project** and save its name and optional shared brief. **Import** accepts files or a folder; choose the entrypoint the viewer should open first. Alternatively, ask the agent to author local files and call `register_artifact` with the project ID. The hub keeps an owned copy and leaves the originals intact.

Use a card's folder action to **Move to project**. This groups related work while preserving stable source paths. Changes to one artifact do not implicitly authorize changes to its neighbours.

For a presentation, open the hub's **… → New presentation**. Bring a PPTX, POTX, website or image as a design reference and let the agent create the design. PowerPoint files are reference inputs; PPTX export is not currently available. The existing editor exports HTML/PDF and saves editable presentation projects. [First presentation](docs/GETTING-STARTED.md#make-your-first-presentation).

## Point to the change

![Artifact review with fictional content, selected elements and a conversation beside the preview.](docs/assets/artifact-review.png)

In the slide editor, click an object directly. In other viewers, choose **Select**, then point to an element, document text or image region. Hold **⌘, Ctrl or Shift** to add or remove targets. Each selected object has an outline and a removable context chip. The slide's **Objects** picker also reaches overlapping elements. One message can refer to all selected elements, with their original version and coordinates or text anchors retained. Direct text editing and positioning apply to one object at a time.

The conversation belongs to the artifact. **Enter** sends; **Shift+Enter** adds a line. Attach files through the paperclip, drag-and-drop or paste, including file-only messages. Limits are five files, 25 MB each and 50 MB together. Unsent file selections stay in browser memory; sent attachments are stored locally. Draft text and targets stay with their artifact.

**Auto-update** is on by default: incoming revisions appear while preserving feedback drafts and their original targets. Direct element edits, moves and animation previews finish before the slide changes. Turn it off to load new versions manually; the choice is remembered in this browser. General artifact viewers check for changes periodically, so updates can take a few seconds.

**Updated** is an orange revision marker until you open that version. **Approve** records the exact version you reviewed. Source files, the content hash and context are available in **Sources & files**. The **Agent context** inspector shows scope, canonical paths, hashes and recorded delivery; an available pointer is never labelled as proof that an agent read it.

`capture_artifact` returns an actual rendered image with revision and viewport metadata. It can sample elapsed animation time in a fresh offline runtime. Use a browser for user-driven interaction states; use the existing slide capture tools for presentations. Preview cards use cached images, not a running iframe per artifact.

## Live or Batch

| Live | Batch |
| --- | --- |
| Each submitted comment is released immediately. | Comments and their attachments stay private until **Send batch**. |
| The agent can read released feedback using its delivery cursor. | The agent receives the released batch together. |

Automatic host continuation currently applies to **presentation review only**, through the supported Codex/Claude Code hooks. General artifact feedback is durable, but does not automatically wake an agent. The Live availability indicator and tooltip distinguish these capabilities. Plain MCP cannot wake a closed chat.

For a deck, open the editor's **Menu → Connect an agent → Automatic feedback → Copy setup prompt**, follow the [hook setup](docs/LIVE-REVIEW.md), then call `start_live_review`. Copying a prompt alone does not configure or activate the host.

## Present with context

![Private presenter view showing current and next slides, notes, timer, clock and filmstrip.](docs/assets/presenter.png)

Existing decks keep the full slide workflow: sections, sorter, hidden slides, multi-slide moves, recovered deletions, notes and per-slide discussion. **Present** starts the full-size audience slide directly. Where browser support, permissions and hardware allow, use a second display for audience slides and keep current/next slides, notes, timer and navigation private. [Editor controls](docs/GETTING-STARTED.md#review-and-present) · [Verification limits](QA.md).

**Speaker notes save automatically**, including when switching slides. Failed saves retain a browser draft; conflicting edits offer **Review changes**. Present and project export wait for pending saves.

The hub's **… → Presentation tools** opens the existing editor menu for design references, reference libraries, instructions and agent connection. **← All artifacts** at the top of the presentation sidebar returns to the hub, including from slide overview. Existing `/?deck=...` links remain valid.

## Your files stay local

| What | Default location |
| --- | --- |
| Presentations, designs, project briefs and owned artifact bundles | `~/Documents/Artifacts/` |
| New artifact sources and immutable revisions | `projects/<project-id>/artifacts/<artifact-id>/` inside the collection |
| Review database and workspace settings | `.state/` inside this checkout |
| Unsent text and target drafts | Browser-local storage |

Back up **both the collection and the state folder**. Set `ARTIFACT_STUDIO_COLLECTION` and `ARTIFACT_STUDIO_DATABASE` to choose other locations. New artifact revisions publish a complete snapshot through an atomic database pointer; a readable `artifact.json` sits beside the working sources. Out-of-band source edits are reconciled into a new revision before review.

HTML, Markdown, images and presentations have a **View** action that removes Studio’s surrounding controls without requiring fullscreen. Move to the top-right corner to reveal the return button, or press **Esc**; interactive pages retain their state. Collapse either side panel with its edge arrow or by dragging its divider outward, then use the arrow to reopen it. Drag library cards onto project folders to organize related work; the keyboard-accessible Move action remains available.

Select a slide or HTML element to change its text, size, weight, alignment or color. Familiar size numbers and Smaller/Larger buttons handle CSS units internally; labels and alignment icons use document language. Changes preview locally; **Send edit** records a structured request against the original version for the agent to apply, while Cancel restores the preview. Authored HTML/SVG/JavaScript remains unrestricted. Preview downloads support generated HTML, JSON and CSV.

**Share** exports one artifact or a project ZIP. Choose **HTML** or **PDF** for presentations, then open an Outlook draft with the file attached or save it locally. HTML presentation files embed local assets and keep animations; PDF includes all slides. Multi-file HTML artifacts retain their bundle in a ZIP. Private review chats and speaker notes are omitted. Outlook handoff is available on macOS with Outlook installed; nothing is sent automatically. [Sharing and exports](docs/PROJECTS.md#share-an-artifact-or-project).

Presentation **Save/Open project** remains the portable editing archive, including private review state. A shared project ZIP contains audience files, not a restorable Studio workspace. Opted-in repository backup includes current artifact bundles, project briefs and manifests alongside presentations; creating a project does not opt it into uploads. Repository connections default to reference only. [Reference libraries and sync](docs/REPOSITORY-SYNC.md).

The **storage icon** in the library and editor shows the working folder and actual GitHub delivery status. **Back up all artifacts** enables one-way backup to a selected private repository; review chat, approvals and revision history stay local. The Mac menu’s **GitHub Backup** submenu exposes the same setting, last delivery, Sync Now and recovery controls.

Workspace rules remain primary; learned preferences are secondary. Project and artifact context refine the work within that scope. The app ships without corporate designs or assets. [Preference learning](docs/PREFERENCE-LEARNING.md).

## Documentation

| Guide | Covers |
| --- | --- |
| [Getting started](docs/GETTING-STARTED.md) | Installation, projects, imports, review, presentations and troubleshooting. |
| [What agents receive](agent/README.md) | Canonical bootstrap, tool catalogue, guides, skills and context receipts. |
| [Artifact architecture and remaining scope](docs/ARCHITECTURE.md) | Implemented foundation and subsequent work. |
| [Artifact authoring](agent/guides/artifacts.md) | Free file composition, versioned edits and scoped feedback. |
| [Mac menu-bar app](docs/MACOS.md) | Server controls, startup and updates. |
| [Automatic presentation feedback](docs/LIVE-REVIEW.md) | Host hooks, activation and limitations. |
| [Agent token efficiency](docs/TOKEN-EFFICIENCY.md) | Compact context and targeted reads. |
| [Presentation authoring](docs/AGENT-GUIDE.md) | Slide edits, notes and visual review. |
| [Presentation visual review](docs/VISUAL-REVIEW.md) | Captures, contact sheets and reveal timing. |
| [Design packs](docs/DESIGN-PACKS.md) | Import a design from a supplied reference. |
| [Verification](QA.md) | Automated checks and application/display test scope. |

<details>
<summary><strong>Development checks</strong></summary>

```sh
uv sync --locked --python 3.14
uv run pytest
uv run basedpyright
uv run ruff check artifact_studio tests
uv run ruff format --check artifact_studio tests
node --test tests/*.cjs
```

Node.js is needed only for frontend behavior tests. Documentation screenshots use fictional projects, not personal files. [CI](docs/CI.md) · [Screenshot inventory](docs/screenshots/README.md).

</details>
