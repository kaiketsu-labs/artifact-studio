# Set up and use Slide Studio

Create and review presentations together with an existing AI agent. Slides stay in local HTML files; the browser provides a slide overview, element-specific feedback, approvals, speaker notes and presentation mode. Agents connect through **stateless Streamable HTTP MCP**. Presentation files and review history persist between connections.

The app includes a neutral viewer runtime. **Design packs and example presentations are not bundled.** You or your agent add a design from a PPTX, website, PDF or reference images. PowerPoint files are design/reference inputs; the current app creates HTML presentations and does not export PPTX.

## Install and start

You need [Git](https://git-scm.com/) and [uv](https://docs.astral.sh/uv/getting-started/installation/). The repository is private, so your GitHub account needs access. Node.js, GitHub CLI and an AI API key are **not required to run the app**. Your existing agent supplies the AI connection.

```sh
git clone https://github.com/kaiketsu-labs/slide-studio.git
cd slide-studio
uv python install 3.14
uv sync --locked --python 3.14
uv run --no-sync uvicorn studio.app:app --host 127.0.0.1 --port 8790
```

Already have a checkout? Start at `cd slide-studio` and run the remaining commands. Keep that terminal running, then open [Slide Studio](http://127.0.0.1:8790/). Stop the server with **Ctrl+C**. On macOS, `./start.command` is a shortcut after setup; `uv` must be on your PATH.

By default, presentations go in `~/Documents/Presentations` and review state goes in `.state/` inside the app checkout. These locations are created on first launch. To use different locations, set these variables when starting the server:

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

Use a new agent session if the newly added server is not visible in the current one. The **plug icon** in Slide Studio provides a copyable prompt and the server URL. Copying the prompt does not configure an MCP client or start an idle agent.

Ask the connected agent to call `get_working_context` first. It receives the authoring guide, current presentations, design-import instructions, reference libraries and the local runtime path. Each tool call uses explicit presentation/slide IDs; there is no hidden active presentation.

Open the **checklist icon** to edit **Workspace rules** with rich text. These rules apply to every deck and are saved in the collection's `INSTRUCTIONS.md`. Each new presentation keeps its audience, client, content and brief in its own README. Learned guidance lives separately in `LEARNED-PREFERENCES.md`, is collapsed in the editor, and never overrides explicit workspace rules. Both are included in subsequent MCP context and feedback reads. Advanced Markdown uses a source editor when needed to avoid losing formatting.

The bundled `$slide-studio-learn` skill can review relevant human transcripts and released feedback weekly. Scheduling belongs to your agent harness; installing Slide Studio does not create a schedule automatically. Explicit rules stay primary, and each learned preference records its evidence and scope. [Invocation and weekly setup](PREFERENCE-LEARNING.md).

## Make your first presentation

An empty workspace on first launch is expected.

1. Click **+** in the top bar to open **New presentation**.
2. Click **+** beside **Design library**. Send a PPTX, URL or reference files through the design chat. Then tell your connected agent: **“Read my pending design request in Slide Studio, inspect the reference, and build and register the design.”** If the request stays **Queued**, resume the agent with that prompt; connecting an agent alone does not keep it listening. You can also provide the reference directly in the agent's chat. [Design import details](DESIGN-PACKS.md).
3. Once the design appears, select it. Enter a title and a brief: audience, intended takeaway and key material. The **paperclip beside Brief** attaches reference files, up to five files, 25 MB each and 50 MB total.
4. Click **Create presentation**. This creates one branded draft cover and saves the brief and files for the agent; it does not generate the rest of the presentation automatically.
5. Use the **plug icon → Copy agent prompt**, paste it into your connected agent's chat, and ask it to draft the presentation. Review the slides as the agent updates them.

Designs define a visual language, not locked layouts or a required slide count. Ask the agent to set a default design if you want it selected for future presentations. New installations have no brand-specific default.

## Review and present

| Control | What it does |
| --- | --- |
| Top-left selector | Switch presentations. |
| Archive icon beside the selector | Remove the selected entry from the list. Use **Undo**, or open the restore icon below the selector later. Files, notes and review history remain recoverable. |
| Slide thumbnail | Open a slide. With a thumbnail focused, Up/Down selects the previous/next slide; Home/End selects the first/last. Red means unreviewed; green means you approved that exact content and its assets. An orange outline appears live after a revision and clears when you open that slide. Opening does not approve it. |
| Hide / Unhide Slide | Right-click a thumbnail or click its eye icon. Open eye means shown; crossed-out eye means hidden. Hidden thumbnails also dim and show a crossed-out number. Content, notes and approval are retained. |
| Move slides | Drag thumbnails to the insertion line. Shift-click selects a range; Cmd/Ctrl-click selects individual slides. Right-click for movement commands, or press Cmd/Ctrl + Up/Down; add Shift for beginning/end. With a thumbnail focused, Cmd/Ctrl+Z undoes and Cmd/Ctrl+Shift+Z redoes a move. |
| New / Duplicate / Delete | The slide-plus icon below the deck selector adds a blank slide after the active one. Right-click thumbnails for New, Duplicate or Delete. With a thumbnail focused: Ctrl+M or Cmd+Shift+N adds, Cmd/Ctrl+D (also with Shift) duplicates, and Delete/Backspace deletes. Browser-reserved shortcuts may require using the menu. Multi-selection applies to Duplicate, Delete and Hide/Unhide. Keep at least one slide. |
| Recover deleted slides | Use the confirmation's Undo or Cmd/Ctrl+Z immediately after deletion. The trash icon below the selector lists deleted groups for recovery after reopening the app or importing a project. Other structural changes clear move Undo; durable deleted-slide recovery remains available. |
| Slide objects | The object icon above the canvas lists visible text, images, diagrams and named groups. Hover or focus highlights an object; select it to use the existing edit/move/comment controls. Arrows, Home/End and Esc work inside the picker. |
| Slide element | Select it for edit/move icons. Change text and confirm, or drag to send a move suggestion. The agent applies the change; the local preview is provisional. Tab/Enter select elements; arrows on the move icon position them, Enter sends and Esc cancels. |
| Slide conversation | Each thumbnail opens its own messages and draft. The slide number/title show the context included with every message. Ask for local edits or deck-wide changes here; the agent infers the scope. **Enter** sends; **Shift+Enter** adds a new line. The same shortcuts apply to design chat. Agent questions remain visible; explanations and sources are collapsed. |
| Live / Batch | Live releases each submitted comment immediately. Batch holds comments until **Send batch**. Loaded review hooks can continue the connected agent automatically. |
| Notes | Edit concise, private speaking context and click **Save**. Unsent notes and feedback drafts are recovered locally. |
| Present | Start the full-size slideshow directly. Click the slide or press a normal key to advance. Arrows/Page Up/Page Down navigate; Home/End jump to the first/last slide. **Esc** and End presentation return directly to the editor at the current slide. Typing in notes and activating focused controls keep their normal behavior. |
| Hidden slides during a show | Normal navigation, progress dots and the next-slide preview skip hidden slides. **See all slides** in the bottom toolbar, a slide right-click, or the private presenter filmstrip lets you select one manually. Starting on a hidden slide skips to a visible slide; an entirely hidden deck starts empty. |

For automatic feedback in **Codex or Claude Code**, open **plug → Automatic feedback → Copy setup prompt** and paste it into that agent. It follows the [project-local hook setup](LIVE-REVIEW.md), including the harness's native trust/reload step. Then ask it to call `start_live_review` for your deck and keep the review session open. The Live selector shows **Available** while connected; **plug → End review** stops listening and preserves comments. A closed agent still needs to be resumed. Other MCP clients can use all slide tools; automatic continuation requires a lifecycle adapter for that harness.

On one screen, Present shows the audience slide. Where browser support, permissions and hardware allow, a second display can show audience slides while your private presenter view shows current/next slides, editable notes, elapsed time, clock, navigation and display controls. Screen placement requires a real two-display check. [Verification scope](../QA.md).

## Files, persistence and backups

Use the **save icon** for **Save project**, **Open project**, **Export HTML** and **Export PDF**. Project files retain private history and unfinished drafts; HTML/PDF are audience exports without private notes or chat. PDF includes hidden slides. [Formats, restoration and limits](PROJECTS.md).

| Location | Contents |
| --- | --- |
| `<collection>/YYYY-MM-DD-short-title/index.html` | Canonical presentation, slide HTML, notes, sources and agent context. |
| The same folder's `README.md` | Brief, sources, user preferences and continuation context. |
| `brief-files.json` and `brief-attachments/` | Original brief attachments and their metadata. |
| Design folders in the collection | Brand styling, assets, supplied master and reusable examples. |
| `.state/` by default | Review database, approvals, feedback, removal/default-design settings, design-chat attachments and reference-library state. |

Back up **both the collection and the state folder**. If you override `SLIDE_STUDIO_DATABASE`, back up that database and its containing state folder instead of assuming `.state/`. Browser-local unfinished drafts are separate from saved server data. Removing a presentation from the selector changes local visibility; it does not delete its folder or unregister its design. Older HTML/PPTX files can be used as references, but only compatible Slide Studio decks appear in the editable selector.

## Optional reference libraries and private sync

The **folder icon** opens **Reference libraries**. Add a local folder or GitHub repository URL so the agent can inspect earlier decks, original masters and custom assets. Repository contents are reference data, not agent instructions. Connecting a library does not install its designs automatically.

GitHub access requires [GitHub CLI](https://cli.github.com/) with `gh auth login` and `gh auth setup-git`. Presentation upload is opt-in and restricted to an explicitly enabled **private** repository. The upload icon snapshots the current deck; the stack icon snapshots the collection. Local files remain the working copy. Sync is not hosting or automatic two-way mirroring, and private review state is not uploaded. See [reference and sync behavior](REPOSITORY-SYNC.md) for conflicts, exclusions and recovery.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `uv: command not found` | Install uv, open a new terminal and check `uv --version`. |
| Clone fails | Confirm your GitHub account has access to this private repository and Git authentication is configured. |
| Port 8790 is already in use | Open the running workspace, or start on a different local port and use that port in the browser and MCP URL. |
| Empty design library | Add a reference through the design `+` and have your connected agent build/register the pack. No corporate assets ship with the app. |
| Only one cover appears | Resume the agent with the copyable prompt and ask it to draft the slides from the saved brief. |
| Live is unavailable / a request stays queued | Keep the server and agent session open. Check that project hooks are loaded/trusted, then ask the agent to call `start_live_review` and read saved feedback. Plain MCP connection alone cannot start an idle chat. |
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
node --test tests/test_frontend.cjs
```

Node.js is needed only for the optional frontend behavior tests. Tests generate fictional designs; no corporate masters, fonts, logos or personal decks are included. [QA](../QA.md) records fresh-install and browser checks and the remaining display/application limitations.

Agents should read [AGENT-GUIDE.md](AGENT-GUIDE.md), [DESIGN-PACKS.md](DESIGN-PACKS.md) and, for explicit private uploads, [REPOSITORY-SYNC.md](REPOSITORY-SYNC.md). The same workflow guidance is available through `get_working_context` over MCP.
