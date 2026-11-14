# Microsoft 365 Copilot

Use a **Copilot Studio agent** with Artifact Studio's separate, authenticated MCP gateway. The ordinary editor stays at `http://127.0.0.1:8790`; never expose that port or its unrestricted local MCP endpoint through a tunnel.

You can chat with the configured agent in the Microsoft 365 Copilot desktop app, including on Mac. Copilot Studio is the setup surface: add the MCP connection there, test it, then make the agent available to yourself through the Microsoft 365 channel. Signing into the desktop app does not necessarily sign into Copilot Studio in a separate browser.

The gateway supports stateless Streamable HTTP with JSON responses. Microsoft receives the context, source and tool results requested within the configured scope. Files remain on this Mac. This is an explicit cloud connection, not part of the default local setup.

## Choose the connection

**One project** is recommended: related artifacts share a brief and can be reviewed together. **Whole workspace** is an explicit alternative for a trusted personal connection. The key grants authoring access within that scope; anyone holding it has the same access. Keep the agent and connection private to the intended user. A shared or multi-user deployment should use an organization-managed identity gateway and per-user authorization rather than sharing this personal key.

Copilot Studio is the supported setup route. A directly packaged Microsoft 365 plugin is another option but requires separate packaging and tenant deployment; this repository does not install one automatically. Your Microsoft account needs access to a Copilot Studio environment and permission to use the connector. Do not enable trials, subscriptions or organization-wide sharing as part of ordinary setup.

## Prepare locally

Use the existing checkout and locked environment. Keep the collection and database paths identical to the local workspace when enabling real work. Start the first connection test with a separate collection containing only fictional artifacts.

Create a private key outside the collection:

```sh
mkdir -p .state/copilot
uv run python -m artifact_studio.copilot init-key --key-file .state/copilot/key
```

The key is generated with 256 bits of randomness, saved with owner-only permissions and never printed. Existing keys are not overwritten. Do not commit the key, paste it into agent chats, or put it in a URL.

Start the gateway with explicit paths and the selected project ID:

```sh
uv run python -m artifact_studio.copilot serve \
  --collection /absolute/path/to/collection \
  --database /absolute/path/to/review.sqlite3 \
  --key-file .state/copilot/key \
  --public-host your-gateway.example.com \
  --project-id project-id \
  --port 8798
```

Replace the example values. Obtain the real project ID from `list_projects` or the local project API. Use `--whole-workspace` **instead of** `--project-id` only when that broader scope is intended. The gateway binds to loopback. Only `/mcp` is exposed; the browser editor and its APIs are not mounted. Missing/wrong keys, browser-origin requests and unexpected Host headers are rejected.

## HTTPS endpoint

Copilot Studio runs in Microsoft's cloud and cannot call this Mac's `127.0.0.1`. Use an explicitly approved HTTPS reverse proxy or tunnel forwarding **only port 8798**, preserving the configured public Host. The Mac and gateway must remain running. A stable authenticated endpoint is needed for ongoing use.

For a short fictional-data test, an existing `cloudflared` installation can provide a temporary URL:

```sh
cloudflared tunnel --url http://127.0.0.1:8798
```

Use the returned hostname as `--public-host` and its `/mcp` URL in Copilot Studio. The API key remains mandatory. The URL changes when a Quick Tunnel restarts; stop the tunnel after testing. Cloudflare relays the requests and results, so this is a separate disclosure choice. Do not start a tunnel or switch a gateway from fictional to personal data without the user's explicit scope decision. A temporary tunnel is not a production deployment.

## Connect in Copilot Studio

Open [Copilot Studio](https://copilotstudio.microsoft.com/) and use the intended work account/environment. Create a private agent or select the agent intended for this connection. Add the short [agent instructions](../agent/copilot.md); the same bootstrap is supplied by MCP initialization.

In **Tools → Add a tool → New tool → Model Context Protocol**, enter:

| Field | Value |
| --- | --- |
| Name | Artifact Studio |
| Description | Create and revise the artifacts in the connected Artifact Studio scope; read released feedback and visually review changes. |
| Server URL | `https://your-gateway.example.com/mcp` |
| Authentication | API key |
| Key type | Header |
| Header name | `X-Artifact-Studio-Key` |

Create the connection and enter the private key in Microsoft's credential field. It is a credential, not an agent instruction. Check the discovered tools. Do not treat a saved URL or a successful HTTP health check as proof that Copilot can call them.

Test in the agent's preview before publishing to **Teams and Microsoft 365 Copilot**. Keep distribution restricted to yourself unless broader sharing was explicitly requested. Tenant policies or environment permissions can prevent adding or publishing the agent.

## Verify the full round trip

### Available tools

The gateway exposes a smaller tool set than the local agent connection:

| Work | Available remotely |
| --- | --- |
| Context | Shared project brief, workspace rules, scoped artifact catalog and released feedback |
| Chat references | Read saved attachments from released messages in the shared project, including slides chosen from other presentations |
| Files | Create and revise owned HTML, CSS, SVG, JavaScript, Markdown and media from inline file contents |
| Presentations | Read, revise, add and reorder existing slides with current revision checks |
| Visual review | Owned artifact screenshots; audience-only slide captures at the completed state or a selected reveal step |

There is no shell, arbitrary host-file import, approval control, workspace-settings access or automatic local review hook. File requests are limited to 2 MiB; read files in smaller groups when needed. New artifact creation needs an existing shared project.

Use `read_feedback_attachment(artifact_id, event_id, file_index)` for a released chat attachment. Pass the destination presentation ID as `artifact_id` for presentation chat. File indices start at zero. The reader returns up to 16 KiB of text by default, with a byte `next_offset` for another page; `null` means complete. A slide reference's first page includes `section_offsets`: jump to `section_offsets.slide` to skip embedded fonts and read its markup. These are navigation hints from the saved source; ambiguous markers are omitted. Set `encoding="base64"` for binary files, and request at most 64 KiB per call. The reader verifies the saved size and hash. Previously saved unsent messages remain private. A saved slide reference preserves the selected description, theme and assets without granting access to the source presentation's other content.

Slide captures use the trusted viewer with `theme.css` and regular files under `assets/`. Private notes, review history and README files are excluded. Custom viewer changes or assets outside that convention require a local check. Capture metadata states that limitation; a screenshot does not verify physical screen placement or presenter controls.

Use fictional content and a concrete request, for example:

> Read the connected Artifact Studio context and find the Copilot connection test artifact. Change its headline to “Hello from Microsoft 365”, keeping the rest intact. Read back the saved version and visually inspect it. Reply briefly inside Artifact Studio.

Check the actual Copilot activity trace, MCP tool result, saved artifact revision and Artifact Studio preview. Then submit a comment in Artifact Studio and ask Copilot to read and address the new released feedback. A withheld Batch comment must stay unavailable until released. Confirm that unrelated projects and unauthenticated requests are rejected.

Record server, transport, Copilot invocation, source readback and visual checks separately. If sign-in, licensing, connector policy or capture support prevents a check, report that specific gap; local tests do not establish Microsoft acceptance.

## Live feedback and shutdown

Live comments become available immediately, but this integration does **not** wake an idle Microsoft 365 conversation. Ask the agent to review new comments. Codex/Claude lifecycle hooks do not apply to Copilot Studio. Continuous processing needs a separately configured Microsoft-side event flow and is not enabled by this gateway.

Stop the gateway and tunnel to end access. Remove the connection/agent in Copilot Studio when no longer needed. Rotate a key by creating a new private file, restarting the gateway with it, and updating Microsoft's connection; the previous key then stops working. Changes to scope require restarting with the intended scope and reviewing who can use the connection.

Official references: [MCP setup and API-key authentication](https://learn.microsoft.com/en-us/microsoft-copilot-studio/mcp-add-existing-server-to-agent), [Microsoft 365 publishing](https://learn.microsoft.com/en-us/microsoft-copilot-studio/publication-add-bot-to-microsoft-teams), [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).
