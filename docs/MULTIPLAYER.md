# Multiplayer through your computer

## Full editor for the workspace owner

To use the regular Artifact Studio library and editor when you sign in as the
owner, start the unified server instead of the standalone collaboration server.
It uses the same collection, review database, multiplayer state, and Cloudflare
policies. Keep only the owner in `member_emails` and leave `agent_subjects` empty.
Invited users sign in through Cloudflare Access and see the scoped collaboration
view. They need a Studio invitation to see specific items.

```sh
uv run uvicorn artifact_studio.unified:create_app --factory \
  --host 127.0.0.1 --port 8790 --no-proxy-headers --no-access-log
```

Set the same environment variables documented below and stop any other Studio
process using the workspace before starting. The owner opens the usual URL for
editing, importing, exporting, and presenting. **Collaborate** opens the selected
artifact in the sharing and conversation view. **Back to Studio** returns the
owner to that artifact in the full editor. Additional isolated workspaces are
managed in the collaboration view. Existing local review messages stay separate
from Shared and My agent chats. Use **Agent connection** to connect a scoped agent
to those chats. Host administration stays on the server.

The standalone collaboration server remains available for existing deployments.

## Standalone collaboration

Multiplayer is an opt-in server for working on the same artifacts with other people
and their agents. Your computer stores the work and must stay awake and connected.
The local app remains the default. This collaboration UI provides workspace/project
navigation, previews, invitations, agent connections and chat. Agents edit the
existing source through guarded MCP tools. The local app's full manual editor,
import, export and backup controls are not yet available in this UI.

![Fictional presentation in the multiplayer workspace, with sharing controls and Shared/My agent chats.](assets/multiplayer-workspace.png)

## Share and collaborate

1. Select **Share** on an artifact, **Share** beside a project, or **Manage access**
   under the workspace selector. Enter an email and choose **Can view**, **Can
   comment**, or **Can edit**. Send the generated link to that person. Studio does
   not send email. They sign in with that exact email and select **Join**.
2. Each person selects **Connect agent**, chooses a scope, and creates their own
   connection. Copy its private token once and set `ARTIFACT_STUDIO_AGENT_TOKEN`
   in the agent environment. Follow the displayed command and ask the agent to
   read `get_working_context`.
3. **Shared** is visible to collaborators. **Discuss** sends a message without
   waking an agent; explicitly choose an agent to request a change. **My agent**
   is private to you and your agents with access to this artifact. Replacing an
   agent retains your conversation. Artifact edits remain shared.
4. **Batch** saves messages visible only to their author. **Send batch** releases
   only your drafts in the selected chat. Text drafts are separate for each person,
   workspace, artifact, slide and chat. `Ctrl/Cmd+Enter` sends a message.

Projects inherit workspace grants; artifacts inherit workspace and project grants.
Broader permissions win: direct View cannot reduce inherited Edit. **Manage access**
names each source and lets the owner manage the parent. Removing direct access
leaves inherited access intact. Invitations expire after seven days and can be used
once. New invitations replace older links for the same person/scope; revocation
also cancels outstanding invitations for that grant.

**Shared** indicates direct or inherited access. **Contains shared items** means only
descendants have grants. Artifact-only guests cannot see siblings or the private
parent project's title and brief. Sharing includes the artifact source, brief and
speaker notes. Existing local review history and unreleased feedback stay in the
local app; multiplayer starts new conversations.

The server owner creates isolated workspaces with **＋** beside the selector. Invited
people can join several; this version reserves workspace creation and access
management to the server/workspace owner. Switching the browser workspace never
retargets an existing agent connection.

## Configure the server and authenticated tunnel

Use two DNS names you control, for example `studio.example.com` and
`preview.example.com`. Both tunnel to the same loopback server. Do not port-forward
the origin. Create a private configuration outside the collection using
[the example](../examples/hosted-config.json) and [provider guide](AUTH-PROVIDERS.md).
Set the two URLs, Cloudflare team domain and browser application audience, put the
owner's email in `member_emails`, and use an empty `agent_subjects` list. This mode
uses its own per-agent tokens. Protect the file with mode `600` or owner-controlled
group-readable `640`.

```sh
export ARTIFACT_STUDIO_MODE=hosted
export ARTIFACT_STUDIO_HOSTED_CONFIG=/absolute/private/hosted.json
export ARTIFACT_STUDIO_MULTIPLAYER_OWNER=owner@example.com
export ARTIFACT_STUDIO_COLLECTION=/absolute/path/to/collection
export ARTIFACT_STUDIO_DATABASE=/absolute/private/review.sqlite3
export ARTIFACT_STUDIO_MULTIPLAYER_STATE=/absolute/private/multiplayer
uv run uvicorn artifact_studio.multiplayer:create_app --factory \
  --host 127.0.0.1 --port 8790 --no-proxy-headers
```

Stop another Studio server using this collection first. The default workspace uses
the selected collection/database in place. Additional workspaces live under the
multiplayer state directory. Preserve the original collection, original database
**and the entire multiplayer state directory** in private backups. The local GitHub
artifact sync does not back up this registry or credentials. Server operators and
backup readers can read private chats: this is application privacy, not end-to-end
encryption.

Cloudflare configuration differs from the older [whole-workspace hosted mode](HOSTED.md):

| Application/path | Cloudflare policy | Studio enforcement |
| --- | --- | --- |
| `studio.example.com` | Allow intended human emails/group; require MFA | Signed identity, invitations and per-object roles |
| `studio.example.com/mcp` and its subpaths | Specific **Bypass** application | Per-agent bearer token and current scoped permissions |
| `studio.example.com/api/review/hooks` | Specific **Bypass** application | The same per-agent token and scope |
| `preview.example.com` | No Access login | Expiring revision capabilities, checked against current recipient access |

Only these agent paths bypass browser login. They still require Studio authentication.
Do not bypass the whole workspace or `/api/*`. Confirm the specific applications
take precedence; otherwise agents receive a Cloudflare login page instead of MCP.
The human policy must allow invitees to authenticate; an invitation cannot change
Cloudflare policy. Human login alone grants no artifact access.

Route both hostnames through a named Cloudflare Tunnel to `http://127.0.0.1:8790`,
with a final `http_status:404` ingress rule. A temporary public URL without these
identity and path policies is not equivalent. Keep the tunnel and computer running.
When offline, collaborators cannot connect; their unsent text drafts remain in their
own browser.

## Agent setup and delivery

Set the token using your environment or secret manager. Never put its value in chat,
source files, screenshots or shared configuration.

```sh
codex mcp add artifact-studio --url https://studio.example.com/mcp \
  --bearer-token-env-var ARTIFACT_STUDIO_AGENT_TOKEN

claude mcp add --transport http artifact-studio https://studio.example.com/mcp \
  --header 'Authorization: Bearer ${ARTIFACT_STUDIO_AGENT_TOKEN}'
```

Single quotes retain the variable reference for Claude's environment interpolation,
avoiding a literal secret in configuration. Choose a unique MCP server name when
connecting several workspaces. Tokens expire after 30 days, are stored hashed and
can be disconnected independently. Revoking a person's access removes their agent's
effective permissions on subsequent operations.

For simultaneous workspaces, use separate agent processes with their own token
environment. The supplied hook client reads one token per process; switching a
browser workspace or reusing a server name does not change that token's scope.

Agents can poll `read_feedback` or install `get_agent_hook_client` with normal native
hook approval. Read `get_authoring_guide(topic="live_review")` for setup. The client
accepts the multiplayer token and retains Cloudflare service-header support for old
hosted installations. **Connected** means recent MCP activity; **Listening** means a
live host-hook lease. `start_live_review` alone returns `awaiting_host_hook`.

Multiple agents can listen to one artifact. Only the assigned agent receives a task.
It calls `claim_feedback` with a unique stable session ID, then uses hash/revision
guards for edits. Claims last ten minutes and can be renewed. `reply_to_feedback`
with the claim replies in the original channel and acknowledges the request.
Lost responses can be retried with the same request ID; unacknowledged work remains
pending. This is retryable delivery, not exactly-once network delivery. Ending one
native session does not stop another. A stopped session requires explicit resume.

Stale concurrent edits are rejected; agents must re-read and reconcile. There is no
silent source merge. Access checks, writes and revocation share a cross-process lock.
A write committed before revocation remains in history; subsequent writes are denied.
Previously downloaded content cannot be recalled from another computer.

## Validation and first-version limits

Tests use real loopback HTTP/MCP, native hook-client requests and independent
Chromium contexts with fictional identities. Separate Terra reviewers exercise owner,
collaborator, keyboard and mobile journeys, and fixes receive regression tests.
The fixture simulates TLS routing and identity. It does **not** validate a live
Cloudflare account, MFA, tunnel uptime, native host trust prompts or backup restore.
Check those with two real accounts before sharing real work: wrong-email invitations
fail, uninvited users see no artifacts, missing/expired tokens fail, preview resources
cannot reach the origin, and revocation blocks both browser and agent access.

This first collaboration UI has no attachment upload or private-history export.
Remote tools exclude host files and server administration. Request bodies are bounded
at 2 MiB. Browser, MCP, hook and preview capacity is separate so idle agent long polls
do not consume browser capacity. Initial agent context contains the selected scope
and artifact; detailed authoring guides load on demand.
