# Hosted deployment simulation

This review covers the application-side acceptance for
[#158](https://github.com/kaiketsu-labs/artifact-studio/issues/158) and
[#186](https://github.com/kaiketsu-labs/artifact-studio/issues/186) using fictional
identities and a disposable loopback HTTPS edge. It does not contact Cloudflare,
change an account, or establish a production deployment's configuration.

## Identity and invitation model

Studio keeps project and artifact grants authoritative. Membership of the
identity provider's group permits sign-in; it does not grant every Studio
resource. The optional invitation adapter maintains a dedicated admission group
from eligible invitations and grants, instead of requiring an operator to add
each guest manually. A manually maintained group remains simpler for a fixed team.

The mock provider checks that the adapter changes only its dedicated group's
email members. It preserves unrelated members and refuses mismatched issuer,
audience, application, group selectors, conditional-write version or missing MFA
requirements. It does not weaken the provider's sign-in policy. The feature flags
remain disabled by default.

Existing regression tests cover verified recipients, wrong recipients, uninvited
identities, overlapping grants, revocation, expired invitations, interrupted
provisioning, duplicate retry after restart, and a new invitation racing removal.
Browser journeys follow actual cross-origin HTTPS redirects and retain the
fragment-only invitation in tab-local storage through sign-in and reload.

The added recovery scenario removes the browser's session cookie before joining,
signs in again as the same verified fictional account, and checks that the
original destination and invitation remain intact. This models session recovery.
It does **not** simulate cryptographic passkey assertions or prove an identity
provider's authenticator recovery policy. A changed subject cannot silently take
over an existing email-bound Studio identity.

## Cookie isolation and preview capabilities

The fictional edge emits a Secure, HttpOnly, SameSite=Lax cookie with `Path=/`
and **no Domain attribute** from the workspace origin. The browser verifies that
it is host-only and inaccessible to JavaScript. Actual HTTPS requests to the
preview sibling are observed with no workspace cookie, including an independent
top-level navigation to the preview origin. The probe uses an invalid synthetic
capability and receives 404; ordinary authorized preview assets also load.

The negative control deliberately installs a parent-domain cookie and confirms
that the browser sends it to the preview sibling. This demonstrates why actual
deployment cookie scope matters; distinct hostnames alone do not establish cookie
isolation. The negative control uses only a disposable fictional cookie.

Existing capability tests check the expected artifact and revision, modified and
missing files, traversal, unsupported methods, expired signatures and invalid
tokens. No full capability URL is included in this evidence or a saved browser
trace.

## Logging evidence and limits

| Stream | Simulation check | Retention established here |
| --- | --- | --- |
| Studio hosted request logger | Contains request ID, coarse kind, status and outcome; excludes cookie value, invitation, capability URL and email | Test-process memory only |
| Actual Uvicorn process started in hosted mode | Synthetic path/query secrets do not appear; request ID and error outcome do | Disposable test log |
| Uvicorn local-mode negative control | The same synthetic URL appears, detecting an incorrect startup mode | Disposable test log |
| Fictional HTTPS edge observations | Host, cookie-present boolean and status only; preview paths are redacted | Test-process memory only |
| Real Cloudflare Tunnel, Access audit, HTTP request analytics and Logpush destinations | Not connected; simulation cannot inspect provider-side streams | Unknown |

Do not describe these results as verified provider-side redaction. On a real
staging account, inspect each enabled logging destination and its configured
retention, then send one synthetic preview capability. Record only whether its
marker was retained, the stream name and the retention setting; do not copy the
capability itself into tickets or screenshots. Repeat the actual host-only cookie
check in browser Network tools after sign-in. Recheck the exact public invitation
landing, protected continuation, provider policy/MFA, least-privilege token scope
and authenticator recovery before enabling either invitation flag in production.

## Reproduction and measurement

```sh
uv run pytest tests/test_invitation_provisioning.py tests/test_cloudflare_admission.py \
  tests/test_hosted_auth.py tests/test_hosted_preview.py tests/test_server_startup.py \
  --durations=5
ARTIFACT_STUDIO_BROWSER_TESTS=1 uv run pytest tests/test_invitation_landing.py --durations=5
```

These commands use temporary collections and fictional identities. The browser
fixture creates a temporary self-signed HTTPS certificate and maps only its test
hostnames to loopback. It is a test transport, never a deployment configuration.

The provider/auth/preview/logging group passed **32 tests in 10.13 seconds** in
the local run. The **16 existing invitation browser cases** passed in the full
run; the added cookie/recovery case passed in **10.83 seconds** after its test
probe was corrected to respect the workspace's cross-origin fetch restriction.
Those are regression-suite execution times, not service latency or throughput
claims. Local browser checks use installed Chromium 155; CI checks the
repository's pinned Chromium version.

The separate deterministic admission workload is reproducible with:

```sh
uv run python scripts/benchmark_hosted_admission.py --ticks 1000
```

At source `2141f0f`, using a disposable collection and `httpx.MockTransport`, its
[recorded results](hosted-admission.json) are:

| Workload | Observation |
| --- | --- |
| Initial eligible invitation | 1 adapter reconciliation |
| 1,000 idle reconciliation ticks | 0 further adapter calls; 1.604 seconds total locally |
| 100 explicit retries of the same mock provider operation | 402 mock HTTP requests; exactly 1 group update |
| Provider policy modifications | 0 |
| Real external requests | 0 |

The provider adapter revalidates its policy and group before mutating; repeat
operations do not repeat the group write. Idle service ticks do not contact the
provider once the recorded state is settled. This is a current-behavior budget,
not a before/after optimization or an estimate of real Cloudflare latency. There
are no model calls or measured provider-token costs. Production Cloudflare
behavior and physical-device MFA remain outside this simulation.
