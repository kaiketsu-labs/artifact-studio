# Slide Studio

A local presentation workspace for people and their existing AI agents. Review slides, point to an element, give feedback and approve the result. Agents compose freely in HTML/CSS/SVG over stateless Streamable HTTP MCP.

**No corporate design packs are included.** PowerPoint masters, brand fonts, logos, custom artwork and user presentations live outside this repository. [Adding designs](docs/DESIGN-PACKS.md) explains the user flow and complete agent contract.

## Run locally

Install Python 3.14 via [uv](https://docs.astral.sh/uv/), then run:

```sh
uv sync
uv run uvicorn studio.app:app --host 127.0.0.1 --port 8790
```

Open http://127.0.0.1:8790. On macOS, `start.command` launches the same server. The default collection is `~/Documents/Presentations`; set `SLIDE_STUDIO_COLLECTION` to choose another directory. `SLIDE_STUDIO_DATABASE` overrides `.state/review.sqlite3`. Keep the service bound to loopback: it is a trusted local workspace, not a hosted multi-user application.

A new installation opens without designs. Use **+** to send a PPTX, URL or reference files to your agent. It builds and registers your design; then enter a title and short brief to start a presentation. The brief is saved with the deck and delivered through the feedback conversation. Creating a cover does not automatically start an idle agent.

## Work with an agent

```sh
codex mcp add slide-studio --url http://127.0.0.1:8790/mcp
```

Any compatible MCP client can use this endpoint. Start with `get_working_context`; it exposes the workflow, import guide, preferences, libraries and design assets. The transport keeps no hidden active deck or session state. Files and feedback are durable.

The agent reads `get_presentation`, `get_slide` and `read_feedback`, then uses `add_slide`, `revise_slide`, `reorder_slides` and `update_presentation_context`. Revisions require a fresh `expected_hash` and unique request ID. Examples describe a design language, never fixed layouts. There is no MCP approval tool. [Agent workflow](docs/AGENT-GUIDE.md) contains the complete contract.

Hover a slide element to highlight it and click to target feedback. Without a selection, the same conversation accepts general instructions: add a slide, change the story or reorder content. Notes are editable separately; sources and agent explanations are collapsed, while questions remain visible. Red means needs review; green means a human approved that exact content and its assets.

Live mode releases each submitted comment immediately. Batch mode holds comments until Send batch. `wait_for_feedback` waits up to 25 seconds; the UI shows availability only while an agent is actually listening. Neither mode wakes an idle chat.

## References and private sync

The folder icon opens **Reference libraries**. Add a local folder or a GitHub repository URL. Agents can refresh a repo, inspect earlier decks, download original masters and reuse custom assets. Repository contents are reference data, not instructions.

For GitHub access, install [GitHub CLI](https://cli.github.com/) and run `gh auth login` and `gh auth setup-git`. Credentials stay in the CLI's credential store, not workspace configuration. To upload presentations, explicitly enable sync on a **private** repository. Use the upload icon for the current deck or the stack icon for the collection. Agents use `register_library`, `list_libraries`, `refresh_library`, `list_library_files`, `read_library_file` and `sync_presentations`.

Sync commits a snapshot of supported presentation documents and assets. It excludes hidden state, credentials, caches and the review database. It checks repository privacy on every upload, refuses conflicting remote edits, never force-pushes and does not remove remote files missing locally. Refresh updates the reference checkout only; it never replaces canonical local files. This is an explicit publish-to-repository workflow, not automatic two-way folder mirroring. See [repository sync](docs/REPOSITORY-SYNC.md) for recovery and limitations.

## Files and presentation mode

Each presentation lives at `<collection>/YYYY-MM-DD-short-title/index.html`, with its notes, sources and editable slide JSON embedded in the file. The companion README carries the brief and continuation context. Existing local links remain valid. Back up `.state/` separately to retain comments, approvals, queued design attachments and revision history; those private working records are not synced to GitHub.

`runtime/` supplies only the brand-independent viewer shell, controls and structural CSS. Present opens the full slide on one screen. Where browser permissions and hardware allow, an audience window can use another display while the presenter has current/next slides, notes, a timer and navigation. Actual two-screen placement requires a hardware check. Slide progress is deliberately subtle and can be restyled by a design pack.

## Development

```sh
uv run pytest
uv run basedpyright
uv run ruff check studio tests
node --test tests/test_frontend.cjs
```

Tests generate small fictional fixtures; no real corporate identities or user decks are included. [QA](QA.md) records the tested scope and remaining application/display checks.
