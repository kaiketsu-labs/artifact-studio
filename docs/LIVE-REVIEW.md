# Automatic feedback with an existing agent

Slide Studio uses the same stateless MCP server for Codex, Claude Code and other MCP clients. Automatic continuation additionally needs a lifecycle adapter in the agent harness. This app includes project-local or optional user-level command hooks for **Codex** and **Claude Code**. Other clients can still read and revise slides through MCP; they need their own lifecycle adapter for automatic continuation.

The hooks keep an explicitly started review open at the end of an agent response. New Live feedback, a released batch or a design request lets the same agent continue. They do not start a closed application, resurrect a terminated chat or bypass host permissions. Keep both the local server and the review session open.

## One-time setup

Connect the MCP server first, using Streamable HTTP at `http://127.0.0.1:8790/mcp`. The MCP server name defaults to `slide-studio`.

From the Slide Studio checkout, prepare the hooks for the **agent's working project**, not a presentation output folder:

```sh
uv --no-cache run --no-sync python -m studio.hook_client configure \
  --harness codex \
  --project /absolute/path/to/agent-project \
  --url http://127.0.0.1:8790 \
  --server-name slide-studio \
  --dry-run
```

Use `--harness claude-code` for Claude Code. Inspect the output, then repeat without `--dry-run` to save it. Replace `--project …` with `--global` to install user-level hooks instead (`~/.claude/settings.json` or `~/.codex/hooks.json`), so a session started in any folder can join live review. The hooks stay inert until that session calls `start_live_review`. Claude Code runs an identical hook defined at both levels once; Codex runs both, so remove a project copy after installing globally. If the MCP connection has a different name, pass that exact name. Only local HTTP URLs are accepted. The generated commands use the checkout's absolute path and uv executable; regenerate them if either moves.

The configurator merges existing settings atomically and rejects malformed settings instead of replacing them. Repeating the same configuration does not duplicate it. It replaces this adapter's older generated handlers while preserving unrelated hooks. The launcher uses `uv --no-cache` and `--no-sync` so it does not need a protected shared uv cache. Rerun configuration after an app update changes the launcher; changed Codex hook definitions require native review/trust again. It writes only:

| Harness | Project file | User-level file (`--global`) | Host action |
| --- | --- | --- | --- |
| Codex | `.codex/hooks.json` | `~/.codex/hooks.json` | Review and trust the exact hook definitions through `/hooks`. Reload/restart the session if necessary. |
| Claude Code | `.claude/settings.local.json` | `~/.claude/settings.json` | Reload the session and review the hooks through `/hooks`; follow the host's settings/trust prompts. |

Hooks run local commands with your host's permissions. The agent must not bypass native trust or managed policy. Project setup writes project settings only; `--global` explicitly selects the user-level file above. Neither installs model credentials or an extra worker. Configuration alone does not prove that a running host has loaded or trusted the hooks.

## Start and end a review

In the connected agent chat, ask:

> Read get_working_context(deck_id=...), then get_authoring_guide(topic="live_review"). Start live review for my presentation with start_live_review(deck_id=...), then check get_live_review_status(deck_id=...). Read get_presentation_outline(deck_id=..., include_context=True), the full brief, relevant brief_files attachments and read_feedback, then address my released feedback. Keep the review open while I use Slide Studio.

Use `start_live_review()` with no deck for the design inbox. The response reports `awaiting_host_hook`: the loaded host hook must register this successful call to opt that session into review. The tool response alone does not prove availability. Ordinary reads do not start or restart it. With loaded hooks, the Live selector shows the connected harness, including between revision passes; otherwise it shows **Unavailable**. The tooltip includes the review session identifier. Merely having MCP configured is not availability.

`start_live_review` reports `status="awaiting_host_hook"`: the call requests a connection, and any returned lease describes the target’s current reviewer. The host's `PostToolUse` hook runs **after** that response. Call `get_live_review_status(deck_id=...)` afterward (omit the ID for the design inbox): it returns `status="connected"` or `"unavailable"`, `review_connected`, `review_harness` and `review_session`, without starting or renewing a lease. Status covers the active reviewer for that exact target, which may be another session; compare `review_session` with your host session before claiming your own connection. An ordinary MCP feedback wait is not a host hook connection. Expired or disconnected leases are unavailable. Working context and review status include `workspace_url` and `mcp_url` derived from the actual HTTP request, including its port; direct Python calls receive relative URLs.

Use **Menu → Connect an agent → End review** for the selected presentation. This releases its hook wait and preserves queued comments. For a design-only session, interrupt/end the agent session. After ending or interrupting, call `start_live_review` again to reconnect. If the app server stops, the hook exits rather than trapping the chat. An abrupt host kill can leave a short availability lease until it expires.

Live submits each comment immediately. Batch remains private until **Send batch**. Neither switches nor hooks silently release a queued batch. A closed or unavailable agent can read saved feedback when resumed.

## Agent behavior

Read `read_feedback(deck_id, after_delivery_id=...)` before revising. Carry `next_cursor` forward. Only successful feedback-feed reads acknowledge delivery; a presentation snapshot and a wake-up notification do not. This avoids skipping a batch released during another read. Read design requests using their separate `next_cursor`, acknowledge work with `reply_to_design_request(status="working")`, and reply when complete.

Use fresh slide/context hashes, preserve human notes, visually verify affected slides and answer in the app's feedback conversation. A host continuation is a request to read the tools, not evidence that a revision is complete. On a quiet renewal, **call the read tools**, then leave the review open. Do not repeatedly announce that nothing changed. Do not bypass the host's continuation limits.

One active hook reviewer owns a presentation at a time; another session is refused while its lease is active. End the old review before transferring it. An already running revision may finish after End review; the button ends further listening rather than cancelling a model's in-flight tool call.

## How it works and limitations

`PostToolUse` reports successful Slide Studio tool calls and delivery cursors to the local `/api/review/hooks` endpoint. No transcript or reference-file content is forwarded by the hook. `Stop` waits in a local command, long-polling the durable queue without a model request for each poll. Feedback returns the host's `decision: block` with a tool-reading instruction, so the existing review turn continues. The hook renews after about nine quiet minutes through one agent continuation; it is not an unlimited, cost-free background worker.

Session end releases the lease; Codex also has an Interrupt hook. The Claude adapter uses the common events supported by the installed 2.1.70 client and omits the newer StopFailure event. After a Claude interruption/API failure or an abrupt process kill, availability can persist until its bounded lease expires; use End review for immediate release. Only another explicit start reconnects an ended review. Local server loss fails open so unrelated agent work can continue. Run only one server against the same state database while testing hooks.

This is a host lifecycle integration, not an MCP protocol feature that can start any idle client. Claude Code's native MCP channels are a separate research-preview mechanism and are not installed by this adapter. Remote/cloud hosts cannot reach this machine's loopback server without a separately designed connection.

Official host contracts: [Codex hooks](https://learn.chatgpt.com/docs/hooks), [Claude Code hooks](https://code.claude.com/docs/en/hooks). Native host loading/trust, model continuation, OS process termination and real display placement require separate application checks; simulated hook tests cannot establish those.
