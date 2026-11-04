# Continuous integration

GitHub Actions runs `.github/workflows/ci.yml` on pull requests, pushes to `main`
and manual dispatch. It uses the locked Python 3.14 dependencies, Node 24, pinned
action revisions and read-only repository permissions. Every application process
uses a temporary fictional collection and database; personal presentations,
corporate packs and model credentials are not required.

## Checks

| Check | What it verifies |
| --- | --- |
| Ruff, types and unit tests | Ruff lint and formatting, strict basedpyright, the Python suite, frontend behavior tests and JavaScript syntax. |
| Release version simulations | The pinned Release Please engine prepares 0.1.0 and subsequent bumps offline, keeping project and lockfile versions aligned without publishing. |
| Playwright user journeys and rendering (editor-review) | Slides and thumbnails, UI-to-MCP feedback, notes/approval state, keyboard navigation and drafts, chat, sorter/sections, presentation/audience behavior, slide/object menus, rich-text/presenter voice input and whole-slide design alternatives (publication, preview, comparison, decisions, conflicts and Undo). |
| Playwright user journeys and rendering (artifacts-runtime) | Artifact/design workspaces, object/property controls, sharing, history/recovery, library menus, general/hub voice input, sandboxed renderer, interactive-content and DOM checks. |
| Agent integration (Codex / Claude Code) | Official SDK Streamable HTTP negotiation and tool discovery; stateless guarded edits, retries and conflicts; each generated hook command executed as a real subprocess against the server; Batch withholding/release, wake-up, acknowledgement and session end. Pinned native CLIs are checked in separate matrix jobs. |

The two browser suites run in parallel with `fail-fast: false` and independent
15-minute timeouts. Their test-file sets are disjoint and together preserve all previously covered paths and add the audit regressions. Collection on 4 November 2026
found 46 disjoint paths: 127 cases in `editor-review` and 172 in `artifacts-runtime`; voice checks run
in both suites. These counts guide the split, not a measured runtime guarantee.
Each suite installs the same locked dependencies and Chromium with OS support.

The native Codex CLI must accept the HTTP MCP configuration using its invocation
override. The native Claude Code CLI must register its project-local connection
in an isolated configuration directory and report a successful server health
check. Model API tokens are removed from native CLI subprocess environments.
The Codex check inspects invocation-local MCP overrides while retaining the
caller's normal configuration location; it does not provide a hermetic user
profile or perform an authentication operation. Claude's configuration uses a
temporary directory. Unknown `ARTIFACT_STUDIO_NATIVE_CLIENT` values fail rather than
skipping every native check.

Hook tests exercise each adapter's result envelope and generated command,
including the durable acknowledged feedback cursor;
they do not simulate host trust or claim an authenticated model session has run.

Model-driven changes, host hook approval/loading, automatic continuation inside a
signed-in desktop/CLI session, physical displays and the macOS controller still
need their own application checks. CI does not bypass native trust or start paid
model requests. The tested client versions are recorded in the artifacts; update
the matrix versions deliberately when supporting a new client release.

Presentation window journeys run in Chromium and Firefox. They check explicit audience-window opening, denied or unavailable screen access, blocked pop-ups, synchronized navigation, private notes, audience fullscreen and returning to editing. Granted-screen cases use simulated display metadata; physical monitor placement still requires a hardware check.

## Run locally

Use an isolated default collection/database before the unit suite imports the app:

```sh
export ARTIFACT_STUDIO_COLLECTION="$(mktemp -d)/presentations"
export ARTIFACT_STUDIO_DATABASE="$(mktemp -d)/review.sqlite3"
uv sync --locked --python 3.14
uv run ruff check artifact_studio tests
uv run ruff format --check artifact_studio tests
uv run basedpyright
uv run pytest
node --test tests/*.cjs
```

Enable the browser and agent suites explicitly:

```sh
uv run playwright install chromium --only-shell
ARTIFACT_STUDIO_BROWSER_TESTS=1 uv run pytest tests/integration -m browser
uv run playwright install firefox
ARTIFACT_STUDIO_BROWSER_TESTS=1 ARTIFACT_STUDIO_TEST_BROWSER=firefox uv run pytest tests/integration/test_presentation_displays.py
ARTIFACT_STUDIO_AGENT_TESTS=1 uv run pytest tests/integration/test_agents.py
ARTIFACT_STUDIO_RENDER_TESTS=1 uv run pytest tests/test_visuals.py tests/test_interactive.py tests/test_element_rotation_dom.py
```

The renderer requires an OS that supports Chromium's sandbox. The browser CI jobs
use Ubuntu runners and install Chromium's OS dependencies; renderer failures
are reported as failures rather than skipped or retried without the sandbox.
When a managed environment blocks sandbox-required rendering, report that check
as unavailable locally; keep the sandbox enabled in CI.

For the native checks, install the two exact package versions from the workflow,
then run `ARTIFACT_STUDIO_NATIVE_CLIENT_TESTS=1 uv run pytest
tests/integration/test_native_clients.py`. Set `ARTIFACT_STUDIO_NATIVE_CLIENT` to
`codex` or `claude-code` to select one client, as CI does. Missing executables fail
an enabled native check; they are not silently skipped.

Set `ARTIFACT_STUDIO_ARTIFACTS=test-results/integration` to retain per-test server logs,
browser screenshots, Playwright traces and native client versions. CI uploads
these and JUnit reports even after a failure, with seven-day retention. Browser
artifacts are named `browser-results-editor-review` and
`browser-results-artifacts-runtime`; their JUnit files are
`browser-editor-review.xml` and `browser-artifacts-runtime.xml`. View a
trace with `uv run playwright show-trace path/to/trace.zip`.

## Before committing

Optional local hooks match CI's Ruff checks and the version in `uv.lock`:

```sh
uvx pre-commit install
uvx pre-commit run --all-files
```

Adding a workflow does not change GitHub branch protection. To require these
checks before merge, select the six expanded CI job names in the repository's branch
rules after their first successful run.

The separate [Release Please workflow](RELEASING.md) prepares release PRs on pushes
to `main` and explicitly dispatches CI for those bot-created PRs. Publication is a
separate manual action after the release PR is merged and `main` CI passes.
