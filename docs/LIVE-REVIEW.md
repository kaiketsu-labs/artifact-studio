# Automatic feedback with an existing agent

Artifact Studio uses the same stateless MCP server for Codex, Claude Code and other MCP clients. Automatic continuation additionally needs a lifecycle adapter in the agent harness. This app includes project-local or optional user-level command hooks for **Codex** and **Claude Code**. Other clients can still read and revise slides through MCP; they need their own lifecycle adapter for automatic continuation.

The hooks automatically attach to selected artifacts of every file type, including presentations, HTML, Markdown, images and other files. They keep listening at the end of an agent response. Sent feedback, explicitly released saved messages or a design request lets the same agent continue. They do not start a closed application, resurrect a terminated chat or bypass host permissions. Keep both the local server and the review session open.

For a remote hosted workspace, use the [client-only hosted setup](HOSTED-AGENT-HOOKS.md). The instructions below cover a local server.

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

Use `--harness claude-code` for Claude Code. Inspect the output, then repeat without `--dry-run` to save it. Replace `--project …` with `--global` to install user-level hooks instead (`~/.claude/settings.json` or `~/.codex/hooks.json`), so a session started in any folder can join live review. Hooks attach when that session selects or works on an artifact; unscoped discovery does not start review. Claude Code runs an identical hook defined at both levels once; Codex runs both, so remove a project copy after installing globally. If the MCP connection has a different name, pass that exact name. Only local HTTP URLs are accepted. The generated commands use the checkout's absolute path and uv executable; regenerate them if either moves.

The configurator merges existing settings atomically and rejects malformed settings instead of replacing them. Repeating the same configuration does not duplicate it. It replaces this adapter's older generated handlers while preserving unrelated hooks. The launcher uses `uv --no-cache` and `--no-sync` so it does not need a protected shared uv cache. Rerun configuration after an app update changes the launcher; changed Codex hook definitions require native review/trust again. It writes only:

| Harness | Project file | User-level file (`--global`) | Host action |
| --- | --- | --- | --- |
| Codex | `.codex/hooks.json` | `~/.codex/hooks.json` | Review and trust the exact hook definitions through `/hooks`. Reload/restart the session if necessary. |
| Claude Code | `.claude/settings.local.json` | `~/.claude/settings.json` | Reload the session and review the hooks through `/hooks`; follow the host's settings/trust prompts. |

Hooks run local commands with your host's permissions. The agent must not bypass native trust or managed policy. Project setup writes project settings only; `--global` explicitly selects the user-level file above. Neither installs model credentials or an extra worker. Configuration alone does not prove that a running host has loaded or trusted the hooks.

## Start and end a review

After the one-time hook setup, selecting or working on an artifact automatically attaches the current agent chat. Scoped working context, registration and artifact authoring/feedback calls trigger the host hook. Presentation context and authoring calls keep their existing behavior, including legacy decks opened by artifact ID. No separate start request is needed. Unscoped discovery and project-only context do not attach to unrelated artifacts.

For a general artifact, read `get_working_context(artifact_id=...)`, then check `get_live_review_status(artifact_id=...)`. Read `read_artifact_feedback(artifact_id=..., after_delivery_id=...)` and carry its `next_cursor` forward. Use `start_live_review(artifact_id=...)` to resume an ended review. Select either `artifact_id` or `deck_id`, not both. One host session follows one selected artifact at a time; use separate chats for parallel artifacts.

For an existing presentation, the agent should:

> Read get_working_context(deck_id=...), then check get_live_review_status(deck_id=...). Read the outline, brief, relevant files and released feedback; keep review open while the human uses Artifact Studio.

Use `start_live_review()` with no deck for the design inbox, or with a deck to explicitly resume an ended review. The response reports `awaiting_host_hook`; the loaded host hook must run before the connection is established. With loaded hooks, the agent status shows the connected harness, including between revision passes; otherwise it shows **Unavailable**. The tooltip includes the review session identifier. Merely having MCP configured is not availability.

`start_live_review` reports `status="awaiting_host_hook"`: the call requests a connection, and any returned lease describes the target’s current reviewer. The host's `PostToolUse` hook runs **after** that response. Call `get_live_review_status(deck_id=...)` afterward (omit the ID for the design inbox): it returns `status="connected"` or `"unavailable"`, `review_connected`, `review_harness` and `review_session`, without starting or renewing a lease. Status covers the active reviewer for that exact target, which may be another session; compare `review_session` with your host session before claiming your own connection. An ordinary MCP feedback wait is not a host hook connection. Expired or disconnected leases are unavailable. Working context and review status include `workspace_url` and `mcp_url` derived from the actual HTTP request, including its port; direct Python calls receive relative URLs.

The start response is compact: it points to this topic rather than resending the guide you just read. Set `include_guide=True` to include the full `guide` field, or use `get_authoring_guide(topic="live_review")`.

Use **End review** in the artifact conversation header, or **Menu → Agent connection → End review** for a presentation. This releases its hook wait and preserves queued comments. For a design-only session, interrupt/end the agent session. After ending or interrupting, call `start_live_review` with the selected target to reconnect. If the app server stops, the hook exits rather than trapping the chat. An abrupt host kill can leave a short availability lease until it expires.

**Send** submits each new comment immediately for every artifact type. **Copy conversation** copies visible conversation text without sending it. Earlier saved messages remain private until **Send saved messages**; hooks never silently release them. A closed or unavailable agent can read saved feedback when resumed.

## Agent behavior

Read `read_feedback(deck_id, after_delivery_id=...)` for presentations or `read_artifact_feedback(artifact_id=..., after_delivery_id=...)` for general artifacts before revising. Carry `next_cursor` forward. Only successful feedback-feed reads acknowledge delivery; a presentation snapshot and a wake-up notification do not. This avoids skipping a batch released during another read. Read design requests using their separate `next_cursor`, acknowledge work with `reply_to_design_request(status="working")`, and reply when complete.

Use fresh slide/context hashes, preserve human notes, visually verify affected slides and answer in the app's feedback conversation. A host continuation is a request to read the tools, not evidence that a revision is complete. On a quiet renewal, **call the read tools**, then leave the review open. Do not repeatedly announce that nothing changed. Do not bypass the host's continuation limits.

Read this guidance when starting review. Later hook messages contain only the needed read calls and their delivery cursors. A **feedback** message names the queues with pending work; an **idle renewal** message checks both queues (only the design inbox for a design-only session). Neither message includes comment content or acknowledges delivery. These messages are tool instructions, not text to repeat in the chat.

One active hook reviewer owns an artifact at a time; another session is refused while its lease is active. End the old review before transferring it. An already running revision may finish after End review; the button ends further listening rather than cancelling a model's in-flight tool call.

## How it works and limitations

`PostToolUse` reports successful Artifact Studio tool calls and delivery cursors to the local `/api/review/hooks` endpoint. No transcript or reference-file content is forwarded by the hook. `Stop` polls the durable queue in a local command without a model request for each poll.

On macOS and Linux, Claude Code uses `asyncRewake`: the turn ends normally while a background hook keeps the lease alive for up to six hours. Feedback makes the hook exit with code 2 and a tool-reading instruction to wake Claude. An idle lifetime ends quietly; the next relevant agent tool call re-arms listening. This removes periodic idle model turns during that lifetime. Reconfigure older installations to load the current hook settings.

Codex and Claude Code on Windows use a synchronous Stop hook. Feedback returns `decision: block` with a tool-reading instruction; about nine quiet minutes also trigger a continuation to renew listening. Those idle renewals can still cost model tokens. Actual billing depends on the host and its cache; hook polls themselves are not model calls. See [token measurements and limits](TOKEN-EFFICIENCY.md).

Session end releases the lease; Codex also has an Interrupt hook. The Claude adapter uses the common events supported by the pinned Claude Code client and omits the newer StopFailure event. After a Claude interruption/API failure or an abrupt process kill, availability can persist until its bounded lease expires; use End review for immediate release. Only another explicit start reconnects an ended review. Local server loss fails open so unrelated agent work can continue. Run only one server against the same state database while testing hooks.

This is a host lifecycle integration, not an MCP protocol feature that can start any idle client. Claude Code's native MCP channels are a separate research-preview mechanism and are not installed by this adapter. Remote/cloud hosts cannot reach this machine's loopback server without a separately designed connection.

Official host contracts: [Codex hooks](https://learn.chatgpt.com/docs/hooks), [Claude Code hooks](https://code.claude.com/docs/en/hooks). Native host loading/trust, model continuation, OS process termination and real display placement require separate application checks; simulated hook tests cannot establish those.
