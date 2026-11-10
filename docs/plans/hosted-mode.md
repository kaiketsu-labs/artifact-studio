# Local and hosted workspaces

Status: implementation draft; no live deployment has been performed.

The opt-in [multiplayer implementation](../MULTIPLAYER.md) now provides the scoped
sharing, isolated workspaces, Shared/My agent conversations and multiple-agent
routing described below. The original `artifact_studio.app` hosted server retains
its whole-workspace membership model; start `artifact_studio.multiplayer:create_app`
for the new collaboration flows. Live Cloudflare deployment remains unverified.

## Decisions after design review

Local mode remains the default: loopback, no login, existing Mac controller and
full local MCP tools. Hosted mode is an explicit server configuration, not a
switch that silently moves the user's files. Open a hosted HTTPS URL in a browser;
the Mac controller continues to manage the local installation.

The first hosted version is one shared workspace. Every configured human member
can read and edit its content. It is not a multi-tenant service or private per-user
storage. Agent identities can use the constrained remote MCP tool surface and the service-only normalized lifecycle-hook endpoint.
Authentication is an installed provider implementing a typed contract; core host,
origin, membership and route authorization rules cannot be bypassed by choosing
a different provider. Cloudflare Access signed JWT validation is the first adapter.
The provider handles login/MFA; Studio verifies each request at the origin.

Hosted startup fails without canonical HTTPS workspace and preview URLs, a
provider and explicit membership. No unverified identity-header adapter, local
fallback, arbitrary discovery/JWKS URL from a token, or public admin routes.

Two fresh Terra reviewers found that a JWT wrapper alone would expose executable
collection files, host-management APIs and local MCP tools. They also identified
opaque iframe module requests losing cookies, proxy-origin confusion, healthcheck
compatibility, and misleading remote import/Mac/hook instructions.

## Implementation boundaries

* Protect all workspace routes, downloads and events; use a central explicit
  hosted route policy. Keep a minimal health response for container liveness.
* Keep agent operations on the existing constrained remote tools. No remote
  arbitrary host filesystem imports, credentials, updates or backup restoration.
* Serve authored artifact resources through short-lived, revision-scoped preview
  capabilities on a separate HTTPS hostname. That host serves only preview
  capabilities, never workspace APIs. Keep opaque iframe sandbox/CSP. A capability
  authorizes only one immutable artifact revision and required trusted runtime
  scripts; it is not a workspace login or a permanent sharing URL.
* Restrict direct collection files and sandbox document responses. Presentation
  editing and browser upload remain available; no scripts run in editable slides.
* Show the active mode, workspace URL and authentication provider in connection
  settings. Explain server-side storage, shared membership and agent configuration.
  Hide local-only administration where it is unavailable.
* Document provider contract, Cloudflare MFA and Tunnel setup, local/hosted Compose
  choices, backups, token revocation and explicit remaining deployment checks.

## Acceptance and rollout

Test invalid/missing configuration, malformed and forged JWTs, claim checks,
membership, key rotation/outage, duplicate headers, host/proxy/origin attacks,
all route families, direct authored document navigation, capability expiry and
cross-artifact reads, real browser modules/assets and authenticated writes, remote
MCP tool inventory and writes, plus existing local regressions. Fresh Terra agents
must click through the changed UI and report issues; recheck fixes.

The PR stays draft until reviewed. Mock-provider tests prove the application
boundary, not a live Cloudflare account, MFA enrollment or native agent OAuth.
No Hetzner deployment, firewall/account changes, signed Mac rebuild or live-app
restart is included. Production readiness also requires testing the chosen
provider, tunnel, client credentials, revocation and restore in the real account.

## Implementation and review result

The draft implements the two modes, Cloudflare verifier, provider entry points,
hosted route policy, constrained MCP transport, preview capabilities, Compose
override and connection UI. Three fresh Terra user reviewers exercised desktop,
390px phone light/dark, and real MCP authoring with fictional data. They completed
project creation, upload, Markdown/HTML preview, chat persistence, presentation
creation, URL/prompt copying and guarded artifact/slide revisions. Stale edits were
rejected. Their discoverability finding led to a visible workspace mode and a
direct library connection dialog. Local-only backup polling was then suppressed
and checked by a browser regression test.

Validation: 906 Python tests and 275 frontend behavior tests passed; the focused
hosted/browser and existing About journeys passed (13 browser cases). Additional
protocol tests exercise the complete hosted app with a fixture provider; signed
RSA tests exercise the real Cloudflare verifier, expiry/claims, key rotation,
outage/backoff and the four-request MCP budget. Ruff and strict types are required
before pushing. Existing renderer-specific opt-in tests remain CI checks.

Review also checked the embedded CSP retained by proposal `srcdoc` previews;
their existing metadata policies already restrict network access. Hosted audience
responses now add an explicit offline policy, authored file downloads are
attachments, and direct top-level artifact-preview navigation is rejected.

External MFA enrollment, Tunnel isolation and real Codex/Claude service-token
authentication remain deployment acceptance work. Hosted capabilities and local
administration differences are listed explicitly in `docs/HOSTED.md`.

## Safety and usability hardening

A second independent review found unbounded browser request parsing and remote
tool work continuing after cancellation without retaining a concurrency slot.
Hosted writes now validate complete, bounded bodies before invoking handlers;
uploads have a separate one-request budget. Remote tool execution retains its
four-worker budget until the underlying work finishes, even after a timeout.

Configuration validation now rejects unsafe file permissions, symlinks, duplicate
keys, oversized files, invalid origins, and a hosted configuration accidentally
loaded in local mode. A redacted CLI report and browser Server checks distinguish
configured protections from warnings and external settings the app cannot verify.
The interface never marks MFA, origin isolation or recovery as proven by JSON.

Fresh Terra reviewers exercised desktop checks and sign-in expiry, phone
light/dark layouts at 320px, 375px and 390px, and an independent configuration/CLI review.
Their findings led to cleared and disabled connection controls after expiry,
removal of stale copy confirmation, deliberate sign-in with existing unsaved-work
protection, and compact editor controls on phones. Tests also cover redirected
login HTML and in-flight session responses arriving after expiry.
Direct static HTML entrypoints now receive the same anti-framing policy as the
workspace root; authored preview endpoints retain their separate sandbox policy.
Full CI exposed two additional narrow-header regressions. The final layout keeps
Home and chat reachable at every tested width; the local mode indicator remains
in About on small phones, while Hosted stays visible. A reproduced saved-preview
iframe reload on focus was fixed by avoiding an unnecessary DOM reparent.

Current local verification: 927 Python tests and 276 frontend behavior tests
passed, plus 44 hosted, About, contrast, accessibility, editor-layout and reference
preview browser cases. Ruff and strict basedpyright passed. The fixture provider
exercises app boundaries, not a
live identity provider. Current-head GitHub CI remains the release gate. No
deployment, account changes or live server restart are part of this update.

## Astra security review and regression fixes

Three independent Astra reviewers examined authentication and membership,
authored-content isolation, and deployment/resource boundaries. The authentication
review found no confirmed bypass across existing tests and additional signed-JWT,
header, path and membership probes. These fixture tests do not verify a live
Cloudflare account.

The review reproduced and fixed the following issues using fictional content:

* Alternate workspace HTML entrypoints lacked the parent frame policy that blocks
  authored pages from navigating to an external collector. All workspace shells
  now share this policy. Direct capability documents download, and authenticated
  preview documents require frame Fetch Metadata. Internal bundle links still work.
* Hosted selection messages used the preview origin instead of the workspace
  origin. Explicit trusted parent metadata restores editing and anchored feedback.
  A blocked-navigation notice offers a deliberate preview reload.
* Hosted Present targeted a deliberately blocked raw document. A trusted presenter
  route restores presentation controls, notes and audience synchronization without
  exposing authored scripts on the workspace origin. Audience responses omit
  private notes and agent context. Phone presenter controls now stack and scroll.
* Authored capture scripts could await indefinitely. Driver deadlines now cover
  promise execution and result serialization; regression cases verify renderer
  recovery after a timeout. The production Chromium sandbox remains enabled.
* Every preview asset request rehashed its whole bundle. Requests now verify only
  the requested immutable file, reject special files and stream the exact verified
  bytes in small chunks. Four response slots remain held through slow sends; a
  bounded queue preserves normal asset bursts. HEAD and media seeking still work.
* Full CI exposed a second saved-preview lifecycle problem: an incoming slide
  update rebuilt an unchanged attachment list and detached its open preview.
  The composer now preserves unchanged markup. A regression and independent
  browser review confirm that the same frozen preview survives a live update,
  while adding/removing references and sending feedback continue to work.
  Rapid returns from an artifact iframe also renew the hover request identifier,
  preventing a delayed pointer message from closing the reopened preview.

Independent Astra rechecks covered tampering, paths, ranges, slow clients,
cancellation, queue overflow and recovery. A real HTTPS Chromium probe confirmed
the navigation policy using browser-generated Fetch Metadata. Fresh Terra user
reviewers exercised selection, feedback, internal links, reload, presentation,
private notes, and desktop/375px/390px layouts. Updated Server checks screenshots
include the unverified OS/container resource limits requirement.

Final local test counts and current-head CI results are recorded in the PR. Live
MFA, Tunnel/header/cookie configuration, revocation, backup recovery and suitable
resource limits remain deployment acceptance work. The PR remains a draft; this
review does not deploy or restart a service.


## Hosted automatic review

The agent machine can fetch two fixed standard-library client files through
`get_agent_hook_client` over its existing MCP connection, or download the same
bundle through the authenticated browser. It does not install the server or its
Python/browser dependencies. Client source remains compatible with Python 3.10+
through per-file lint/format targets; independent checks used Python 3.12 with
site packages disabled.

The hosted hook endpoint accepts verified configured service principals only,
rejects browser Origin requests, hashes principal/harness/native session into an
opaque ownership namespace, and has a separate eight-request/64-KiB/35-second
budget. Shared review contracts preserve Live/Batch, cursor acknowledgement,
lease expiry, End review and explicit resume. Hosted hooks exclude the local
design inbox and do not widen host administration. The ordinary Copilot gateway
keeps its existing tool inventory and cannot install hooks.

Three fresh Terra reviewers exercised the browser lifecycle and mobile download,
service isolation and expiry, and client-only configuration/TLS/redirect behavior.
Their findings fixed Python-3.14-only syntax in the downloaded helper and late
session-response/reopen races that restored controls after expiry. Automated
regressions cover those cases. Real installed-host trust and model continuation,
and real provider credential revocation, remain deployment acceptance checks.
Hooks require a supported running host and cannot reopen closed chats.

## Multiplayer expansion: multiple workspaces and inherited sharing

These requirements extend the hosted foundation above. They are planned, not
implemented by the current draft. A hosted shared workspace alone does not meet
the multiplayer requirement: the current review lease rejects a second active
reviewer for the same artifact, and configured members share the whole collection.

### Workspaces and access

The hierarchy is **workspace → project → artifact**. Presentations are artifacts;
slides remain part of their presentation. A person can own or join several
workspaces. Each workspace owns its content, rules, designs, conversations,
history and access grants. A workspace switcher identifies the active workspace
and whether its server is local or remote. Switching the browser must not silently
retarget an already-connected agent.

| Share target | Access granted |
| --- | --- |
| One artifact | That artifact and its required resources; no access to sibling artifacts or the rest of its project. |
| One project | The project and its current and future artifacts. |
| Whole workspace | All current and future projects and artifacts in that workspace. |

Use named invitations and explicit **View**, **Comment** and **Edit** roles, with
ownership/access management kept separate. Agent permissions are limited by both
the inviting person's effective access and the connection's selected scope.
The server must enforce this for browser requests, MCP, events, previews, exports
and referenced content; a filtered library or an agent prompt is insufficient.

Access inherited from a parent remains visible on children. A narrower direct
grant does not silently reduce broader inherited access. Removing a direct share
must explain any access that remains through the project or workspace. Moves
between projects must show changes to inherited access before confirmation.
Moving between workspaces is an explicit transfer, not an implicit side effect of
selecting another workspace.

An artifact-only guest should open the shared item directly and see only
authorized navigation. Search, recents, favorites, reference pickers and previews
must not reveal other workspaces or sibling content. Per-workspace storage,
caches, event streams and browser draft keys require the same isolation. Existing
single-workspace data should migrate to a named default workspace without copying
or losing history.

### Sharing experience

Use one **Share** entry on workspace, project and artifact menus. It offers live
collaboration invitations and access management alongside the existing explicit
file-copy/export action. A copied file remains a snapshot; the collaboration link
opens the canonical shared content.

The dialog always names the selected scope and role, lists **People with access**,
and offers **Copy invitation link**, role changes and revocation. Invitations
must establish authenticated, scoped access; they are not the existing temporary
preview capability URLs. Each collaborator connects their own agent identity,
without receiving the owner's service credentials. Revocation must be checked on
subsequent requests and writes, including already-running agent operations.

Shared items have a visible shared badge. Parent containers distinguish **Shared**
from **Contains shared items**, so sharing one presentation does not make its
whole project appear shared. **Manage access** shows the person, effective role
and source, for example “Edit · inherited from Project launch”. The active
workspace and current collaborators remain visible in the editor.

### Multiple agents on one artifact

The existing single-reviewer rule prevents two listening agents from treating
every feedback message as their task. Keep stale-write protection, but replace
artifact-wide exclusive review ownership with identified participants, separate
agent sessions and explicit feedback routing.

By default a person's request goes to their selected agent. A shared request can
be assigned to another connected agent; claiming/acknowledging it must prevent
duplicate work. Each participant has an independent delivery cursor. Ending one
agent session must not disconnect everyone. Unsent drafts and withheld Batch
feedback remain isolated until their author deliberately submits/releases them.
Agents may work on different slides concurrently. Concurrent changes to the same
source retain the current revision/hash checks: a stale write is rejected and
must be reread and reconciled. This does not promise automatic character-level
merging or Google Docs-style simultaneous typing.

### How another computer connects

All participants connect to one reachable authoritative Studio server using the
browser URL and an authenticated MCP connection. Their agents run on their own
computers; they do not need their own Studio server or synchronized Git checkout.
The server stores the canonical workspaces. A server can host multiple isolated
workspaces; a workspace is not synonymous with a server or a hostname.

For regular collaboration, prefer an always-on server running the Docker setup
behind authenticated HTTPS. It can be a VPS, NAS or another suitable machine.
For temporary collaboration, the owner's computer can host Studio behind an
authenticated tunnel; it must remain awake and connected. A shared LAN or VPN is
another route when all participants can reach it. A localhost URL and a GitHub
backup do not provide this live connection. Exposing the existing unauthenticated
local mode is not a hosting solution.

The existing Cloudflare adapter is a reusable transport/login foundation, but its
static workspace-wide membership and service credentials must evolve to support
the scoped invitation and delegation model. Adding an invitation button alone
would not establish those permissions. Multi-workspace implementation must also
replace the current process-global store selection with explicit request-bound
workspace routing or isolated workspace services behind a common gateway.

### Multiplayer acceptance journeys

Use two human sessions and two independently authenticated agent connections with
fictional data in two workspaces. Exercise every share level and role, direct and
inherited access, invitation acceptance/expiry/revocation, moves that change access,
and identical artifact IDs in different workspaces. Verify negative access through
all APIs, MCP, event streams, previews, references and downloads.

Both agents must remain connected to the same presentation, receive only their
assigned released work, preserve private drafts/Batch feedback, survive one agent
disconnecting, and handle competing edits without silent loss. Browser reviews
must verify the workspace switcher, scoped guest navigation, shared/inherited
badges and Manage access on desktop and narrow layouts. Application simulations
remain separate from validating real hosting, identity-provider and native-agent
connections.
