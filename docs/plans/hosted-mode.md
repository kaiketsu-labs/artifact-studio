# Local and hosted workspaces

Status: implementation draft; no live deployment has been performed.

## Decisions after design review

Local mode remains the default: loopback, no login, existing Mac controller and
full local MCP tools. Hosted mode is an explicit server configuration, not a
switch that silently moves the user's files. Open a hosted HTTPS URL in a browser;
the Mac controller continues to manage the local installation.

The first hosted version is one shared workspace. Every configured human member
can read and edit its content. It is not a multi-tenant service or private per-user
storage. Agent identities can use only the constrained remote MCP tool surface.
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
