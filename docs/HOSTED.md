# Hosted shared workspace

Hosted mode is an implementation draft and has not been validated against a live Cloudflare account. Local mode remains the default. A hosted server is a separate workspace: open its HTTPS URL in a browser on a Mac, iPhone or other device. The Mac controller continues to manage the local installation. No mode switch moves files between workspaces.

![Hosted agent connection in the library, using a fictional workspace.](assets/hosted-connection.png)

<details>
<summary>Phone view</summary>

<img src="assets/hosted-connection-mobile.png" width="390" alt="Hosted agent connection on a phone in dark appearance, with the server URL and credential setup guidance.">

</details>

All configured human members share all content in this workspace, including projects, artifacts, notes and review history. Agents have a constrained MCP surface only; they do not receive the human browser session or local host administration. Browser upload, authoring, previews and review remain available. Local backup controls, host hooks, automatic feedback setup and the local Copilot setup are unavailable in hosted mode.

## Configure the server

Create a private JSON configuration based on [the fictional example](../examples/hosted-config.json). Set `public_url` and `preview_url` to distinct canonical HTTPS origins, `auth_provider` to `cloudflare`, and supply your Access team domain, application audience, exact human member emails and exact service-token Client IDs. Set `ARTIFACT_STUDIO_MODE=hosted` and `ARTIFACT_STUDIO_HOSTED_CONFIG=/run/artifact-studio/hosted.json`, mounting the JSON read-only at that path. Persist the server's workspace volume and protect backups independently. Do not commit real identities or credentials.

Route both hostnames through a Cloudflare Tunnel to the same private service. Do not expose the origin port publicly. Create one Access application for the workspace hostname with two policies: an **Allow** policy for exact human emails requiring MFA, and a **Service Auth** policy for the explicitly selected agent service tokens. Studio restricts those service identities to `/mcp`, even though they authenticate at the same Access application. Configure their exact Client IDs in `agent_subjects`. The origin verifies the signed Access JWT on each request and enforces its own membership and route policy.

Enable [Access Independent MFA](https://developers.cloudflare.com/cloudflare-one/access-controls/access-settings/independent-mfa/) and require it on the human Allow policy. Enroll a passkey/security key or TOTP and a recovery method before enforcing the policy. Test both permitted users and an unlisted identity in a fresh browser session. Service tokens are machine credentials; they do not perform a human MFA challenge on each tool call.

The separate preview hostname must have **no Access application**: sandboxed previews need to load without browser credentials. The server serves only short-lived, revision-scoped preview capabilities there, never workspace APIs. A preview capability is not a permanent sharing URL or workspace login. Keep the preview hostname distinct from the browser hostname.

Connect an agent using the hosted `https://studio.example.com/mcp` URL and Streamable HTTP. Configure `CF-Access-Client-Id` and `CF-Access-Client-Secret` as request headers from the agent's environment or secret manager; do not paste credentials into Studio. This first version uses service credentials, not native agent OAuth. Host lifecycle hooks are unsupported. Rotate or revoke the service token in Cloudflare, remove the Client ID from configuration, restart the server, and verify that the old credentials no longer work.

Before production use, validate the actual account's MFA policy, tunnel routing, origin isolation, service-token authentication and revocation, preview isolation, and backup and restore procedure. Application tests cannot establish those account-specific properties.

## Docker and Tunnel example

Make the configuration readable by the container's UID 1000, then run from the checkout:

```sh
export ARTIFACT_STUDIO_HOSTED_CONFIG_FILE=/absolute/path/to/hosted.json
docker compose -f compose.yaml -f compose.hosted.yaml up -d --build --wait
docker compose -f compose.yaml -f compose.hosted.yaml logs --tail=100 studio
```

The override mounts that file read-only, retains the base Compose sandbox and loopback-only port, disables proxy-header trust, and disables application access logs. Run `cloudflared` on the host and route both public hostnames to `http://127.0.0.1:8790`. Preserve each incoming Host header: do not set `originRequest.httpHostHeader`. The browser hostname is protected by Access; the preview hostname is not. Keep the browser Access cookie host-only. The local container health check still uses loopback.

Copy the single Access application's audience into `auth_options.audience`. A separate Access application for `/mcp` can issue a different audience and is not compatible with this single-audience configuration. For [Cloudflare service tokens](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/application-token/), Studio matches the verified JWT's `common_name` to the exact service token **Client ID** in `agent_subjects`; the service token JWT may have an empty `sub`. The Client Secret belongs only in the agent's secret store.

Preview capability URLs contain sensitive bearer tokens and expire after one hour or a server restart. Reopen or refresh a preview to renew its resource URLs; a long-running authored page that loads new resources after expiry needs a refresh. Disable or redact URL logging in Tunnel, proxies and any other intermediaries; do not share or retain capability URLs. Container access logs are disabled by the override. Run one application process per workspace; capability signing keys are process-local.

For Codex, a Streamable HTTP server can map environment variables to headers in `~/.codex/config.toml` (set the environment variables securely before starting Codex):

```toml
[mcp_servers.artifact_studio]
url = "https://studio.example.com/mcp"
env_http_headers = { "CF-Access-Client-Id" = "CF_ACCESS_CLIENT_ID", "CF-Access-Client-Secret" = "CF_ACCESS_CLIENT_SECRET" }
```

For Claude Code, a project `.mcp.json` can use environment interpolation; set the variables in the agent environment and avoid committing real secrets:

```json
{"mcpServers":{"artifact-studio":{"type":"http","url":"https://studio.example.com/mcp","headers":{"CF-Access-Client-Id":"${CF_ACCESS_CLIENT_ID}","CF-Access-Client-Secret":"${CF_ACCESS_CLIENT_SECRET}"}}}}
```

These examples use service-token headers. External OAuth and native agent OAuth are not implemented. Check the installed client version's configuration support and verify a real tool call.

## What runs where

| Workflow | Local | Hosted draft |
| --- | --- | --- |
| Library, uploads, slide editing, notes, chat and review | Available | Available to every configured human member |
| Agent edits and visual checks | Full local MCP | Constrained remote MCP; discover its schemas after connecting |
| New projects and presentations | Browser or local agent | Create through the browser; agents can add/revise slides and create/revise general artifacts |
| Theme, design-pack and host-file management by an agent | Full local tools | Local administrative tools are not exposed remotely |
| Automatic agent wake-up and native lifecycle hooks | Supported with setup | Not included; agent reads released feedback through MCP |
| Mac menu-bar server controls | Manage the local service | Open the hosted URL in your browser |
| Authentication and MFA | Loopback access | Provider login/MFA plus origin JWT and membership checks |
| Workspace and backup administration | Local UI | Server operator; remote restore and credential controls are disabled |

Hosted mode does not add simultaneous text merging, private member projects, personal
draft isolation or multi-tenant billing. Revision guards still reject stale edits.
Keep all persistent `/data` together. Use the existing verified workspace archive
mechanism for application-consistent backups, or stop the server before backing up
the complete volume; do not copy a live SQLite database alone. Restore into a
separate volume and verify it before replacing the running workspace. See
[workspace recovery](WORKSPACE-RECOVERY.md).
