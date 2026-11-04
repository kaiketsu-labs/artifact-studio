# Automatic feedback with an existing agent

Artifact Studio uses the same stateless MCP server for Codex, Claude Code and other MCP clients. Automatic continuation additionally needs a lifecycle adapter in the agent harness. This app includes project-local or optional user-level command hooks for **Codex** and **Claude Code**. Other clients can still read and revise slides through MCP; they need their own lifecycle adapter for automatic continuation.

The hooks automatically attach to selected presentation work and listen for feedback after an agent response. Claude Code listens in the background; Codex keeps a synchronous Stop hook open. New Live feedback, a released batch or a design request lets the same agent continue. They do not start a closed application, resurrect a terminated chat or bypass host permissions. Keep both the local server and the review session open.

## One-time setup

Connect the MCP server first, using Streamable HTTP at `http://127.0.0.1:8790/mcp`. The MCP server name defaults to `artifact-studio`.

From the Artifact Studio checkout, prepare the hooks for the **agent's working project**, not a presentation output folder:

```sh
uv --no-cache run --no-sync python -m artifact_studio.hook_client configure \
  --harness codex \
  --project /absolute/path/to/agent-project \
  --url http://127.0.0.1:8790 \
  --server-name artifact-studio \
  --dry-run
```

Use `--harness claude-code` for Claude Code. Inspect the output, then repeat without `--dry-run` to save it. Replace `--project …` with `--global` to install user-level hooks instead (`~/.claude/settings.json` or `~/.codex/hooks.json`), so a session started in any folder can join live review. Hooks attach when that session selects or works on a presentation; unscoped discovery does not start review. Claude Code runs an identical hook defined at both levels once; Codex runs both, so remove a project copy after installing globally. If the MCP connection has a different name, pass that exact name. Only local HTTP URLs are accepted. The generated commands use the checkout's absolute path and uv executable; regenerate them if either moves.

The configurator merges existing settings atomically and rejects malformed settings instead of replacing them. Repeating the same configuration does not duplicate it. It replaces this adapter's older generated handlers while preserving unrelated hooks. The launcher uses `uv --no-cache` and `--no-sync` so it does not need a protected shared uv cache. Restart the Studio server and rerun configuration after updating this adapter; existing settings still carry the old 600-second timeout. The new Stop timeout is 28,800 seconds. Changed Codex hook definitions require native review/trust again. It writes only:

| Harness | Project file | User-level file (`--global`) | Host action |
| --- | --- | --- | --- |
| Codex | `.codex/hooks.json` | `~/.codex/hooks.json` | Review and trust the exact hook definitions through `/hooks`. Reload/restart the session if necessary. |
| Claude Code | `.claude/settings.local.json` | `~/.claude/settings.json` | Reload the session and review the hooks through `/hooks`; follow the host's settings/trust prompts. |

Hooks run local commands with your host's permissions. The agent must not bypass native trust or managed policy. Project setup writes project settings only; `--global` explicitly selects the user-level file above. Neither installs model credentials or an extra worker. Configuration alone does not prove that a running host has loaded or trusted the hooks.

## Start and end a review

After the one-time hook setup, selecting or working on a presentation automatically attaches the current agent chat. Context reads with a selected deck (including a legacy presentation opened by artifact ID), creation and authoring calls trigger the host hook. No separate request to start review is needed. Unscoped discovery and general artifacts do not start presentation review.

For an existing presentation, the agent should:

> Read get_working_context(deck_id=...), then check get_live_review_status(deck_id=...). Read the outline, brief, relevant files and released feedback; keep review open while the human uses Artifact Studio.

Use `start_live_review()` with no deck for the design inbox, or with a deck to explicitly resume an ended review. The response reports `awaiting_host_hook`; the loaded host hook must run before the connection is established. With loaded hooks, the Live selector shows the connected harness, including between revision passes; otherwise it shows **Unavailable**. The tooltip includes the review session identifier. Merely having MCP configured is not availability.

`start_live_review` reports `status="awaiting_host_hook"`: the call requests a connection, and any returned lease describes the target’s current reviewer. The host's `PostToolUse` hook runs **after** that response. Call `get_live_review_status(deck_id=...)` afterward (omit the ID for the design inbox): it returns `status="connected"` or `"unavailable"`, `review_connected`, `review_harness` and `review_session`, without starting or renewing a lease. Status covers the active reviewer for that exact target, which may be another session; compare `review_session` with your host session before claiming your own connection. An ordinary MCP feedback wait is not a host hook connection. Expired or disconnected leases are unavailable. Working context and review status include `workspace_url` and `mcp_url` derived from the actual HTTP request, including its port; direct Python calls receive relative URLs.

The start response is compact: it points to this topic rather than resending the guide you just read. Set `include_guide=True` to include the full `guide` field, or use `get_authoring_guide(topic="live_review")`.

Use **Menu → Agent connection → End review** for the selected presentation. This releases its hook wait and preserves queued comments. For a design-only session, interrupt/end the agent session. After ending or interrupting, call `start_live_review` again to reconnect. If the app server stops, the hook exits rather than trapping the chat. An abrupt host kill can leave a short availability lease until it expires.

Live submits each comment immediately. Batch remains private until **Send batch**. Neither switches nor hooks silently release a queued batch. A closed or unavailable agent can read saved feedback when resumed.

## Agent behavior

Read `read_feedback(deck_id, after_delivery_id=...)` before revising. Carry `next_cursor` forward. Only successful feedback-feed reads acknowledge delivery; a presentation snapshot and a wake-up notification do not. This avoids skipping a batch released during another read. Read design requests using their separate `next_cursor`, acknowledge work with `reply_to_design_request(status="working")`, and reply when complete.

Use fresh slide/context hashes, preserve human notes, visually verify affected slides and answer in the app's feedback conversation. A host continuation is a request to read the tools, not evidence that a revision is complete. Quiet polling never asks the model to continue or read empty queues. Do not repeatedly announce that nothing changed. Do not bypass the host's continuation limits.

Read this guidance when starting review. Later hook messages contain only the needed read calls and their delivery cursors. A **feedback** message names only the queues with pending work. It contains no comment bodies and does not acknowledge delivery. There are no idle-renewal continuations. These messages are tool instructions, not text to repeat in the chat.

One active hook reviewer owns a presentation at a time; another session is refused while its lease is active. End the old review before transferring it. An already running revision may finish after End review; the button ends further listening rather than cancelling a model's in-flight tool call.

## How it works and limitations

`PostToolUse` reports successful Artifact Studio tool calls and delivery cursors to the local `/api/review/hooks` endpoint. No transcript or reference-file content is forwarded by the hook. `Stop` long-polls the durable queue in a local command; quiet polls do not request model turns.

| Harness | Stop behavior | Real feedback | Quiet expiry |
| --- | --- | --- | --- |
| Claude Code | `asyncRewake: true` lets the turn end while a background command listens. | Exit code **2**, with the read instruction on stderr, wakes the host. Exit-zero JSON `decision: block` would not wake it. | Exit zero, without waking the model. |
| Codex | A synchronous command keeps the Stop hook open. | Exit-zero JSON `decision: block` continues the turn. | Exit-zero `{}`, allowing the turn to end. |

Both generated configurations request an eight-hour timeout. The client stops quietly 60 seconds before that boundary, allowing its final HTTP request to finish before the host timeout. There is no periodic model renewal. When listening ends, the short server lease expires and Live becomes unavailable; the next eligible Studio tool call reconnects review unless it was explicitly ended. Feedback stays queued while unavailable.

Each Stop invocation has a unique waiter ID. A new invocation replaces the previous listener for that harness/session; a successful tool hook or end-review/session event invalidates the parked listener. Superseded listeners exit quietly. Waking never advances a delivery cursor: only the successful feed read acknowledges feedback.

Claude Code's published 2.1.80 schema accepts `asyncRewake` on Stop and an eight-hour timeout; use a current client (2.1.287 also fixes repeated asyncRewake notifications for missing scripts). The generated commands, exit codes, cancellation and feedback delivery are checked with real local subprocesses. A signed-in host's actual idle wake and an uninterrupted eight-hour wait still need native validation; schema acceptance does not prove either. Abrupt host death may leave a background listener until it fails, is superseded, or reaches its time limit.

Codex's upstream asynchronous Stop implementation discards Stop/feedback decisions and only drains background context during a running or later turn. It is not an idle wake mechanism. The adapter therefore does not set `async` or `asyncRewake` for Codex. An event-driven integration through an owned app-server connection would require separate thread ownership and lifecycle support; it cannot be assumed to attach to an arbitrary existing CLI or desktop chat.

Session end releases the lease; Codex also has an Interrupt hook. The Claude adapter uses PostToolUse, Stop and SessionEnd; it does not currently register StopFailure. After a Claude interruption/API failure, a background listener may remain active until the next tool hook or its time limit; use End review for immediate release. Only another explicit start reconnects an ended review. Local server loss fails open so unrelated agent work can continue. Run only one server against the same state database while testing hooks.

This is a host lifecycle integration, not an MCP protocol feature that can start any idle client. Claude Code's native MCP channels are a separate research-preview mechanism and are not installed by this adapter. Remote/cloud hosts cannot reach this machine's loopback server without a separately designed connection.

Official host contracts: [Codex hooks](https://learn.chatgpt.com/docs/hooks), [Claude Code hooks](https://code.claude.com/docs/en/hooks). Native host loading/trust, model continuation, OS process termination and real display placement require separate application checks; simulated hook tests cannot establish those.

Implementation evidence: [Codex background Stop output handling](https://github.com/openai/codex/blob/48524f337a84b570f0c2453ca43ec9a1a29375c0/codex-rs/hooks/src/engine/command_runner.rs#L141-L162), [Codex configurable timeout](https://github.com/openai/codex/blob/48524f337a84b570f0c2453ca43ec9a1a29375c0/codex-rs/hooks/src/engine/discovery.rs#L946-L969), and [Claude Code release history](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md). The inspected upstream Codex revision is not a guarantee for every installed host version.
