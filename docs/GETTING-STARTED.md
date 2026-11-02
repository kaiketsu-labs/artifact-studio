# Set up and use Slide Studio

Create and review related artifacts with an existing AI agent. The home page is the **Artifact Hub**: a visual library of presentations, HTML pages, Markdown documents, images and other files, grouped by project. Each artifact opens with its own preview and conversation. Agents connect through **stateless Streamable HTTP MCP**; local files and review history persist between connections.

General artifacts need no design pack or fixed template. The viewer supports authored HTML/CSS/JavaScript bundles, safe Markdown rendering and images at their original proportions. **Design packs and example presentations are not bundled.** For presentations, add a design from a PPTX, website, PDF or reference images. PowerPoint files remain reference inputs; the app creates HTML presentations and does not export PPTX.

## Guided setup

Clone the repository using Git, then open its folder as your agent's working project. Run:

| Agent | Command in the agent chat |
| --- | --- |
| Claude Code | `/setup` — bundled project command |
| Codex | `$setup` — bundled repository skill |

These use the same [setup workflow](../.agents/skills/setup/SKILL.md). It checks what is already installed, guides the missing setup, connects MCP and helps you start a deck. You can provide preferences with the command, such as “use the Mac menu-bar app and start at login”. It preserves existing workspaces and settings. If the command is not listed, reload the agent session; from another working folder, ask it to read this checkout's `.agents/skills/setup/SKILL.md` by absolute path.

The syntax follows [Codex skill invocation](https://learn.chatgpt.com/docs/build-skills) and [Claude Code project commands](https://code.claude.com/docs/en/slash-commands). `/setup` is a Claude Code command, not a browser-chat command or a universal MCP feature. Native trust/reload steps may need your interaction. Manual setup follows below.

## Install

You need [Git](https://git-scm.com/) and [uv](https://docs.astral.sh/uv/getting-started/installation/). The repository is private, so your GitHub account needs access. Node.js, GitHub CLI and an AI API key are **not required to run the app**. Your existing agent supplies the AI connection.

```sh
git clone https://github.com/kaiketsu-labs/slide-studio.git
cd slide-studio
uv python install 3.14
uv sync --locked --python 3.14
uv run playwright install chromium --only-shell
```

Already have a checkout? Open its folder in Terminal and run the dependency commands above. This is a source installation: no npm or PyPI release or standalone Mac download is published by this project yet.

Chromium enables the local [visual-review tools](VISUAL-REVIEW.md). It follows [Playwright's browser installation](https://playwright.dev/python/docs/browsers). If you skip that step, the editor still works and capture tools explain how to install it. Captures return images directly through MCP; they do not require the agent to operate a browser tab.

## Choose how to start

Use one startup method for this workspace. Both serve the same browser app and MCP endpoint.

### Mac menu-bar app

Requires macOS 13+ and Apple's Command Line Tools. If needed, install the tools with `xcode-select --install` and wait for that installation to finish. From the Slide Studio checkout, run:

```sh
uv run --no-sync python scripts/install_macos.py --launch-at-login
```

If you already started the server in Terminal, stop it with **Ctrl+C** first. Open `~/Applications/Slide Studio.app` in Finder, then choose **Open Slide Studio** from the slide-stack icon beside the clock. The app starts the server and registers login startup; approve it in System Settings if macOS asks.

A green status dot means the server is responding. Start, Stop and Restart control the server owned by this app. Keep the source checkout and `.venv` in place: this controller is not a self-contained, notarized distribution. [Menu controls, configuration and removal](MACOS.md).

### Terminal

From the Slide Studio checkout:

```sh
uv run --no-sync uvicorn studio.app:app --host 127.0.0.1 --port 8790
```

Keep that Terminal running, then open [Slide Studio](http://127.0.0.1:8790/). Stop the server with **Ctrl+C**. On macOS, `./start.command` is a shortcut after setup; `uv` must be on your PATH.

### Designs and work

The sidebar’s **Designs** opens registered design packs and their examples, with the workspace default marked. They are excluded from **All artifacts** and project counts. Presentations created from a design remain normal artifacts. Classification uses each pack’s `design.json`; files and existing links stay in place.

### Storage locations

By default, presentations, project briefs and owned artifact bundles go in `~/Documents/Presentations`; review state goes in `.state/` inside the app checkout. These locations are created on first launch. To use different locations, set these variables when starting the server:

```sh
SLIDE_STUDIO_COLLECTION="$HOME/Documents/My Slides" \
SLIDE_STUDIO_DATABASE="$HOME/Documents/Slide Studio State/review.sqlite3" \
uv run --no-sync uvicorn studio.app:app --host 127.0.0.1 --port 8790
```

Bind the service to `127.0.0.1`. It is a trusted local workspace, without multi-user authentication.

## Connect your agent

Keep Slide Studio running. In an MCP-compatible agent, add this server:

| Setting | Value |
| --- | --- |
| Transport | Streamable HTTP |
| URL | `http://127.0.0.1:8790/mcp` |

For Codex CLI:

```sh
codex mcp add slide-studio --url http://127.0.0.1:8790/mcp
```

For [Claude Code](https://code.claude.com/docs/en/mcp), run from the project where you will review slides:

```sh
claude mcp add --transport http slide-studio http://127.0.0.1:8790/mcp
```

Use a new agent session if the newly added server is not visible in the current one. From the hub, **… → Presentation tools** opens the existing editor. Its **Menu → Connect an agent** provides a copyable presentation prompt and the server URL. Copying the prompt does not configure an MCP client or start an idle agent.

For general work, ask the agent to call `list_projects` and `list_artifacts`, then `get_working_context(project_id=..., artifact_id=...)` for the selected scope. It receives applicable rules, project/artifact context, source pointers and a delivery receipt. Read source files with `read_artifact_files`; load the generic guide through `get_authoring_guide(topic="artifacts")`.

Ask the connected agent to call `get_working_context(deck_id=...)` when resuming an existing deck, so it receives that deck's design preferences. For a new deck, use `get_working_context(design_id=...)` for the chosen brand, or omit the ID for the default, then call `create_presentation` with a brief. The compact context contains complete workspace rules and preferences, a short quickstart, available designs/presentations, reference libraries, local paths and workspace/MCP URLs. Load specific guides with `get_authoring_guide(topic=...)`. Read the full saved brief and relevant attachments through `get_presentation_outline(deck_id=..., include_context=True)` before drafting. Each tool call uses explicit presentation/slide IDs; there is no hidden active presentation.

In the presentation editor, open **Menu → Instructions** to edit **Workspace rules** with rich text. Expand **Learned preferences** for secondary guidance. Click **Save** to write the collection's `INSTRUCTIONS.md` and `LEARNED-PREFERENCES.md`; conflicts with another editor are reported without replacing either document. Applicable rules guide projects and artifacts, and learned guidance never overrides explicit workspace rules. Presentation-only requirements remain specific to presentations. Both are included in subsequent MCP context and feedback reads. Enabled GitHub auto-sync includes the saved files; reference-only connections do not upload them. Each presentation keeps its audience, client, content and brief in its own README. Advanced Markdown uses a source editor when needed to preserve formatting.

The bundled `$slide-studio-learn` skill can review relevant human transcripts and released feedback weekly. Scheduling belongs to your agent harness; installing Slide Studio does not create a schedule automatically. Explicit rules stay primary, and each learned preference records its evidence and scope. [Invocation and weekly setup](PREFERENCE-LEARNING.md).

## Create and review an artifact project

The library shows all existing artifacts. Use project filters, format tabs and search to find work. Preview cards contain cached rendered images; unavailable captures use a format fallback. Existing decks open in their established slide editor, and direct `/?deck=...` links remain valid.

![Artifact Hub with fictional projects and rendered preview cards.](assets/artifact-hub.png)

Choose **New project** to save a title and optional shared brief. Choose **Import**, select the project, and add files or a folder. **Open first** selects the entrypoint; type detection chooses a viewer. Browser import currently accepts up to 1,000 files and 100 MB at once. Sources can include arbitrary HTML, CSS, JavaScript, SVG, images, fonts, data and native files. Importing never executes build or installation scripts.

An agent can instead author files using its preferred tools, then call `register_artifact` with the project ID, local source path and entrypoint. The hub creates an owned copy and preserves the original. Subsequent `revise_artifact` calls publish changed files with an expected revision/hash and an idempotency request ID.

A card's folder action or **Sources & files → Move to project** changes membership without relocating the canonical source. Related artifacts share the project brief, but retain their own purpose, review state and conversation. Ask for related changes explicitly; grouping is not permission to rewrite every artifact.

### Adaptive viewers

| Format | Available controls |
| --- | --- |
| HTML | Natural page preview, responsive/desktop/tablet/mobile widths, replay and element selection. Authored JavaScript runs in an opaque-origin sandbox with scoped local resources. External services are not automatically available. |
| Markdown | Rendered document or original source. Raw HTML stays literal; owned images use revision-pinned resources. |
| Image/SVG | Original aspect ratio, fit/zoom, and drag a region while Select is active. |
| Other files | Original download, file inventory and artifact-level feedback. A dedicated preview may be unavailable. |

Choose **Select**, then click an element or text block, select document text, or drag an image rectangle. Hold **Cmd, Ctrl or Shift** to add targets. Remove a target with its × or clear the group. Image regions retain the original image dimensions; text and element targets retain quoted content. Dynamic anchors are best-effort and stay attached to their original revision.

![Artifact review with fictional content and multiple selected targets.](assets/artifact-review.png)

Write feedback in the artifact's conversation. **Enter** sends and **Shift+Enter** adds a line. Use the paperclip, drop files or paste an image; a file-only message is supported. Limits are five files, 25 MB each and 50 MB combined. Remove an unsent file with its ×; sent files can be downloaded from the message. Text and target drafts persist in browser storage; unsent file selections remain in browser memory.

**Live** releases a message immediately. **Batch** keeps messages and attachments private until **Send batch**. Automatic host continuation currently supports presentation review only; generic artifact agents read released feedback through `read_artifact_feedback`. The Live availability dot's tooltip reports this limitation. An ordinary MCP connection cannot wake a closed chat.

**Auto-update** is on by default and remembered in this browser. Incoming previews keep your feedback draft and its original target/version context. Slide updates wait for direct element edits, moves and animation playback; HTML previews wait while you interact inside them. Turn Auto-update off to open revisions manually. Generic viewers check periodically, so changes can take a few seconds.

**Updated** marks a new revision until opened. **Approve** records the displayed version, independently of the update marker. The preview and its local dependencies are pinned to the displayed revision. In the slide editor, click objects directly and hold **Cmd, Ctrl or Shift** to add or remove them; the **Objects** picker also supports multiple targets. Individual context chips remove objects without discarding the message. Direct text editing and positioning remain single-object actions.

Open **Sources & files** for downloads, canonical source path and content hash. The **Agent context** inspector shows applicable source paths, scopes, hashes and the latest recorded delivery. Available pointers are not read receipts. Matching retention hashes are useful only while the agent actually retains that guidance.

For visual inspection, agents can call `capture_artifact` with viewport dimensions, a revision, elapsed animation time and optional full-page capture. It returns actual rendered evidence from a fresh offline runtime. Browser inspection is still needed for user-driven interaction states. Use the specialized slide capture tools for existing decks.

## Make your first presentation

An empty workspace on first launch is expected.

1. From the hub, open **… → New presentation**. The existing editor also has **Menu → New presentation**. The hub's separate **New project** action creates a shared artifact project.
2. Click **+** beside **Design library**. Send a PPTX, URL or reference files through the design chat. Then tell your connected agent: **“Read my pending design request in Slide Studio, inspect the reference, and build and register the design.”** If the request stays **Queued**, resume the agent with that prompt; connecting an agent alone does not keep it listening. You can also provide the reference directly in the agent's chat. [Design import details](DESIGN-PACKS.md).
3. Once the design appears, select it. Enter a title and a brief: audience, intended takeaway and key material. The **paperclip beside Brief** attaches reference files, up to five files, 25 MB each and 50 MB total.
4. Click **Create presentation**. This creates one branded draft cover and saves the brief and files for the agent; it does not generate the rest of the presentation automatically.
5. Use the **Menu → Connect an agent → Copy agent prompt**, paste it into your connected agent's chat, and ask it to draft the presentation. Review the slides as the agent updates them.

Designs define a visual language, not locked layouts or a required slide count. Ask the agent to set a default design if you want it selected for future presentations. New installations have no brand-specific default.

## Review and present

The following controls apply to the presentation editor. Its top-right **Menu** groups file actions, workspace settings and the agent connection; **Present** stays beside it. Return through the library to open related non-slide artifacts. Open the menu with **F10** or focus it and press Up/Down. Use arrows, Home/End, Enter or type an action's name; Escape returns focus to the menu button. Appearance opens a submenu with Right; Left returns.

| Action | Mac | Windows/Linux |
| --- | --- | --- |
| New presentation | ⌘N | Ctrl+N |
| Open project | ⌘O | Ctrl+O |
| Save project | ⌘S | Ctrl+S |
| Project files / choose format | ⇧⌘S | Ctrl+Shift+S |
| Present from current slide | ⌘Return | Shift+F5 |
| Present from beginning | ⇧⌘Return or F5 | F5 |

File and presentation bindings follow [PowerPoint's editing](https://support.microsoft.com/en-us/accessibility/powerpoint/use-keyboard-shortcuts-to-create-powerpoint-presentations) and [slideshow shortcuts](https://support.microsoft.com/en-us/accessibility/powerpoint/use-keyboard-shortcuts-to-deliver-powerpoint-presentations). Save downloads a portable project, including recovered drafts; slides already persist locally. Shortcuts leave modal editors and text entry alone, except Save can capture a text draft. Some browsers reserve New/Open or function keys before the page receives them; use the menu in that case.

| Control | What it does |
| --- | --- |
| Top-left selector | Switch presentations. |
| Archive icon beside the selector | Remove the selected entry from the list. Use **Undo**, or open the restore icon below the selector later. Files, notes and review history remain recoverable. |
| Slide thumbnail | Open a slide. With a thumbnail focused, Up/Down selects the previous/next slide; Home/End selects the first/last. Red means unreviewed; green means you approved that exact content and its assets. An orange outline appears live after a revision and clears when you open that slide. Opening does not approve it. |
| Hide / Unhide Slide | Right-click a thumbnail or click its eye icon. Open eye means shown; crossed-out eye means hidden. Hidden thumbnails also dim and show a crossed-out number. Content, notes and approval are retained. |
| Move slides | Drag thumbnails to the insertion line, including between sections. Shift-click selects a range; Cmd/Ctrl-click selects individual slides. Right-click for movement commands, or press Cmd/Ctrl + Up/Down; add Shift for beginning/end. |
| Sections | The section icon starts a section at the active slide, or renames it at an existing boundary. Fold with its chevron; use its menu to rename, move or remove a group. Removing a section keeps its slides. Active new/recovered slides reveal their group. |
| Layout undo / redo | With a thumbnail or section header focused, Cmd/Ctrl+Z undoes slide moves and section changes; Cmd/Ctrl+Shift+Z or Ctrl+Y redoes them. Conflicting changes from another editor are reported without overwriting their layout. |
| New / Duplicate / Delete | The slide-plus icon below the deck selector adds a blank slide after the active one. Right-click thumbnails for New, Duplicate or Delete. With a thumbnail focused: Ctrl+M or Cmd+Shift+N adds, Cmd/Ctrl+D (also with Shift) duplicates, and Delete/Backspace deletes. Browser-reserved shortcuts may require using the menu. Multi-selection applies to Duplicate, Delete and Hide/Unhide. Keep at least one slide. |
| Recover deleted slides | Use the confirmation's Undo or Cmd/Ctrl+Z immediately after deletion. The trash icon below the selector lists deleted groups for recovery after reopening the app or importing a project. Other structural changes can invalidate layout undo; durable deleted-slide recovery remains available. |
| Slide objects | The object icon above the canvas lists visible text, images, diagrams and named groups. Hover or focus highlights an object; select it to use the existing edit/move/comment controls. Arrows, Home/End and Esc work inside the picker. |
| Slide element | Select it to edit text, drag or align to the slide. The check icon sends; Esc cancels. The agent applies the suggestion; the local preview is provisional. Tab/Enter select elements; arrows on the move icon position them, Enter sends. Unfinished edits block switching until sent or cancelled. |
| Slide conversation | Each thumbnail opens its own messages and draft. Messages render Markdown, including lists, code, tables and links. The slide number/title show the context included with every message. Ask for local edits or deck-wide changes here; the agent infers the scope. **Enter** sends; **Shift+Enter** adds a new line. The same shortcuts apply to design chat. Agent questions remain visible; explanations and sources are collapsed. |
| Chat width | On desktop, drag the divider between slide and chat. Focus it and use Left/Right to resize, Home/End for the limits, or Enter to reset. Double-click also resets. The browser remembers the width while keeping room for the slide; narrow screens stack chat below it. |
| Live / Batch | Live releases each submitted comment immediately. Batch holds comments until **Send batch**. Loaded review hooks can continue the connected agent automatically. |
| Notes | Open by default and save automatically after a short pause or when leaving the field/slide. **Saving… / Saved** shows local file persistence. Failures retain a browser draft with Retry; **Review changes** compares conflicting versions. Project save and Present wait for pending notes. |
| Storage and backup | The icon beside Menu shows local saving and GitHub backup status. Open it to copy the working-folder path, see the last backup, or retry an error. GitHub backup is one-way; Save/Open project carries full private workspace state between devices. |
| Present | Start the full-size slideshow directly. Click the slide or press a normal key to advance. Arrows/Page Up/Page Down navigate; Home/End jump to the first/last slide. **Esc** and End presentation return directly to the editor at the current slide. Typing in notes and activating focused controls keep their normal behavior. |
| Fullscreen (bottom right) | Present the selected slide in native fullscreen. The separate Fit slide button resets the editor zoom. |
| Zoom | Ctrl/Cmd + scroll zooms toward the cursor. The slider and +/− zoom around the visible centre, from 10–400%. Scroll across enlarged slides. Fit slide returns to 100%; 50% or below opens the slide overview. Project save/open keeps the zoom level. |
| Appearance | Use **Menu → Appearance** to choose Light, Dark or System. Your choice is saved in this browser; slide designs keep their authored colours. |
| Hidden slides during a show | Normal navigation, progress dots and the next-slide preview skip hidden slides. **See all slides** in the bottom toolbar, a slide right-click, or the private presenter filmstrip lets you select one manually. Starting on a hidden slide skips to a visible slide; an entirely hidden deck starts empty. |

For automatic feedback in **Codex or Claude Code**, open **Menu → Connect an agent → Automatic feedback → Copy setup prompt** and paste it into that agent. It follows the [project-local hook setup](LIVE-REVIEW.md), including the harness's native trust/reload step. Optional user-level hooks are covered in the same guide. Then ask it to call `start_live_review` for your deck and keep the review session open. The Live selector shows **Codex**, **Claude Code** or **Unavailable**; its tooltip identifies the connected review session. **Menu → Connect an agent → End review** stops listening and preserves comments. A green Mac menu-bar dot only reports server health. A closed agent still needs to be resumed. Other MCP clients can use all slide tools; automatic continuation requires a lifecycle adapter for that harness.

Present requests browser fullscreen directly from your click, without opening an intermediate page. Esc or End returns to the editor at the current slide, keeping unsent drafts. If the browser blocks or does not support fullscreen, the slideshow still fills its available viewport.

On one screen, Present shows the audience slide. Where browser support, permissions and hardware allow, a second display can show audience slides while your private presenter view shows current/next slides, editable notes, elapsed time, clock, navigation and display controls. Screen placement requires a real two-display check. [Verification scope](../QA.md).

## Work on several artifacts or decks

Use separate browser tabs and explicit artifact IDs for parallel general work. Each artifact keeps its own draft, conversation and approval. Project context is shared; sources and feedback from related artifacts are loaded only when requested.

Open each deck in its own browser tab and use a separate agent chat with that deck's copied prompt. They share one server and MCP URL. Slides, feedback, notes and approvals stay with their deck; workspace rules, learned preferences, design libraries and the design inbox are shared. Agents use explicit deck IDs, and one live reviewer owns each deck at a time. [Parallel work and conflict handling](MACOS.md#parallel-decks).

## Files, persistence and backups

In the presentation editor, use **Menu → Project files** for **Save project**, **Open project**, **Export HTML** and **Export PDF**. These are presentation formats; complete multi-artifact project archives are not implemented. Project files retain private history and unfinished drafts; HTML/PDF are audience exports without private notes or chat. PDF includes hidden slides. [Formats, restoration and limits](PROJECTS.md).

| Location | Contents |
| --- | --- |
| `<collection>/projects/<project-id>/README.md` | Shared artifact project brief. |
| `<collection>/projects/<project-id>/artifacts/<artifact-id>/files/` | Canonical owned artifact source files. Membership changes preserve this path. |
| The artifact's `revisions/<revision>/` | Immutable source snapshot and manifest for that revision. |
| The artifact's `artifact.json` | Readable manifest; the review database publishes the current revision pointer atomically. |
| `<collection>/YYYY-MM-DD-short-title/index.html` | Canonical presentation, slide HTML, notes, sources and agent context. |
| The same folder's `README.md` | Brief, sources, user preferences and continuation context. |
| `brief-files.json` and `brief-attachments/` | Original brief attachments and their metadata. |
| Design folders in the collection | Brand styling, assets, supplied master and reusable examples. |
| `.state/` by default | Review database, approvals, feedback, removal/default-design settings, design-chat attachments and reference-library state. |

Back up **both the collection and the state folder**. If you override `SLIDE_STUDIO_DATABASE`, back up that database and its containing state folder instead of assuming `.state/`. Browser-local unfinished drafts are separate from saved server data. Removing a presentation from the selector changes local visibility; it does not delete its folder or unregister its design. Older HTML/PPTX files can be used as references, but only compatible Slide Studio decks appear in the editable selector.

## Optional reference libraries and private sync

From **… → Presentation tools**, open **Menu → Reference libraries**. Add a local folder or GitHub repository URL so the agent can inspect earlier decks, original masters and custom assets. Repository contents are reference data, not agent instructions. Connecting a library does not install its designs automatically.

GitHub access requires [GitHub CLI](https://cli.github.com/) with `gh auth login` and `gh auth setup-git`. New connections are **reference only**: agents can use earlier presentations for tone, design and context. Enable **Back up all presentations** beside the repository, or **Back up automatically** when connecting it, to upload saved workspace files to a **private** repository. Changes are grouped after eight quiet seconds while Slide Studio is running; delivery resumes on the next launch. The storage icon beside Menu shows delivery status even with the dialog closed; open it for the destination timestamp and retry control. Existing manual destinations retain their current-deck and collection upload buttons until you change the setting. Local files remain the working copy. This is one-way versioned storage; private review state is excluded. See [reference and sync behavior](REPOSITORY-SYNC.md) for conflicts, exclusions and recovery.

The Artifact Hub does not broaden this established presentation backup scope to new multi-artifact projects.

## Update an existing installation

Save your work and back up the collection and state folder. From a checkout without uncommitted changes:

```sh
git pull --ff-only
uv sync --locked --python 3.14
uv run playwright install chromium --only-shell
```

Restart the server from the Mac menu, or stop the Terminal server with Ctrl+C and start it again. Refresh the browser and resume live review in your agent. Keep the same collection and database paths. Do not delete `.state` or replace it with a new checkout's empty state. Native menu-bar code changes also require [reinstalling the controller](MACOS.md#existing-workspaces-and-updates).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `uv: command not found` | Install uv, open a new terminal and check `uv --version`. |
| Clone fails | Confirm your GitHub account has access to this private repository and Git authentication is configured. |
| Port 8790 is already in use | Open the running workspace, or start on a different local port and use that port in the browser and MCP URL. |
| Mac app says “Running · started elsewhere” | Stop the original Terminal server with Ctrl+C, then choose Start in the Mac menu. The controller cannot stop a server it does not own. |
| Mac app fails to start | Choose Show Logs. Confirm the checkout and `.venv` still exist and dependencies are installed. [Mac setup](MACOS.md). |
| Empty design library | Add a reference through the design `+` and have your connected agent build/register the pack. No corporate assets ship with the app. |
| Only one cover appears | Resume the agent with the copyable prompt and ask it to draft the slides from the saved brief. |
| Presentation Live is unavailable / a request stays queued | Keep the server and agent session open. Check that the configured hooks are loaded/trusted, then ask the agent to call `start_live_review` and read saved feedback. Plain MCP connection alone cannot start an idle chat. |
| Generic artifact feedback does not wake the agent | Live means immediate release. Resume the agent and ask it to read released artifact feedback; generic host hooks are not implemented. |
| Artifact preview is unavailable | Check the entrypoint and owned local dependencies. External services are not automatically enabled; unsupported files can still be downloaded. Capture requires local Chromium. |
| An artifact revision reports a conflict | Reload its metadata and review the intervening changes before submitting a new expected revision/hash. Do not overwrite retained snapshots. |
| Design could not load | Use the retry icon; ask the agent to check local `theme.css`, font and image paths in the design pack. |
| A removed presentation is missing | Use the restore icon below the top-left selector. Keep the same state database across launches. |
| Slides or comments seem to have disappeared | Check the collection and database environment variables before creating new data; saved content may be in your previous locations. |

## Development and agent documentation

```sh
uv sync --locked --python 3.14
uv run pytest
uv run basedpyright
uv run ruff check studio tests
uv run ruff format --check studio tests
node --test tests/*.cjs
```

Node.js is needed only for the optional frontend behavior tests. Tests generate fictional designs; no corporate masters, fonts, logos or personal decks are included. [QA](../QA.md) records fresh-install and browser checks and the remaining display/application limitations.

Agents should start with the [contract map](../agent/README.md) and [artifact authoring guide](../agent/guides/artifacts.md). Presentation work also uses [AGENT-GUIDE.md](AGENT-GUIDE.md) and [DESIGN-PACKS.md](DESIGN-PACKS.md); explicit private presentation uploads use [REPOSITORY-SYNC.md](REPOSITORY-SYNC.md). The canonical MCP bootstrap is [agent/bootstrap.md](../agent/bootstrap.md).
