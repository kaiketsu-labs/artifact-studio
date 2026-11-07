# Continuous integration

GitHub Actions runs `.github/workflows/ci.yml` on pull requests, pushes to `main`
and manual dispatch. It uses the locked Python 3.14 dependencies, Node 24, pinned
action revisions and read-only repository permissions. Every application process
uses a temporary fictional collection and database; personal presentations,
corporate packs and model credentials are not required.

## Checks

| Check | What it verifies |
| --- | --- |
| Ruff, types, unit tests & release simulation | Ruff lint and formatting, strict basedpyright, the Python suite, frontend behavior tests, JavaScript syntax and offline Release Please simulations. |
| Playwright user journeys and rendering (editor) | Slides and thumbnails, UI-to-MCP feedback, notes/approval state, keyboard navigation, drafts, chat, sorter/sections, presentation/audience behavior, slide/object menus, rich-text/presenter voice input, structural undo, element commands and whole-slide design alternatives. |
| Playwright user journeys and rendering (runtime) | Artifact/design workspaces, object/property controls, sharing, history/recovery, library menus and cover loading, chat path references and pending/cached hover previews, voice input, sandboxed renderer, interactive-content, DOM checks, authoring recovery, and Firefox presentation compatibility. |
| Agent integration | Official SDK Streamable HTTP negotiation and tool discovery; stateless guarded edits, retries, conflicts, batch withholding/release, authoring recovery, slide insertion, and native Codex and Claude Code client and hook integration. |
| Docker | Builds the image and exercises the shipped Compose setup: HTTP/MCP, sandboxed captures and OCR, host-file import, and deck/feedback/settings/capture persistence across container replacement. |

The separate `docker.yml` workflow runs when container or runtime inputs change.
It builds without publishing an image, uses a random Compose project with its own
named volume and loopback port, and removes only those disposable test resources.
No host collection or credentials are mounted. Linux amd64 is exercised in CI;
other Docker platforms need their own host check.

The separate `macos.yml` workflow runs for native-source, native-test and build-dependency
changes. It exercises real diagnostic-file rotation and live subprocess output with
Swift 6, then builds/signs a disposable native controller and runs native icon rendering.
Unrelated changes do not start an additional macOS runner.

The two browser suites run in parallel with `fail-fast: false` and independent
25-minute timeouts. Each suite and the Python unit step use two pytest-xdist workers
with work stealing, so a long test file cannot hold up an otherwise idle worker.
Every selected case still runs once; worker crashes fail the check instead of
silently restarting workers. The agent integration step remains sequential.

`tests/conftest.py` isolates each worker's default collection, database and explicit
artifact directory by run ID and worker ID before application imports. Existing
per-test temporary stores, servers and browser contexts remain isolated too.
Do not increase the worker count without checking runner CPU and memory limits.

Browser cache keys follow the installed Playwright browser manifest, architecture
and suite, so an unrelated Python lockfile update does not download browsers again.
OS dependencies are still installed on every runner. The runtime suite prepares
Chromium and Firefox together, avoiding a second package-index refresh, and matching
caches skip browser downloads. Chromium renderer sandboxing remains enabled.
Only the runtime suite installs Tesseract, where the real OCR checks run.
Completed browser installs are cached before tests, so a later test failure does
not force the next attempt to download the browsers again.
Isolated integration servers disable automatic upstream release checks; the version
journeys simulate release metadata at the network boundary while exercising the real UI.

Pytest prints the 15 slowest setup/call/teardown durations in each test step's log
to help distinguish test time from installation and cache time in subsequent runs.
Test results, screenshots, and Playwright traces are uploaded only upon failure (`if: failure()`)
with a 1-day retention period to conserve organization storage quotas.

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
signed-in desktop/CLI session, physical displays and macOS menu/alert interaction still
need their own application checks. CI does not bypass native trust or start paid
model requests. The tested client versions are recorded in failure artifacts; update
the pinned versions deliberately when supporting a new client release.

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
uv run pytest -n 2 --dist worksteal --max-worker-restart=0
node --test tests/*.cjs
```

Enable the browser and agent suites explicitly:

```sh
uv run playwright install chromium --only-shell
ARTIFACT_STUDIO_BROWSER_TESTS=1 uv run pytest -n 2 --dist worksteal --max-worker-restart=0 tests/integration -m browser
uv run playwright install firefox
ARTIFACT_STUDIO_BROWSER_TESTS=1 ARTIFACT_STUDIO_TEST_BROWSER=firefox uv run pytest -n 2 --dist worksteal --max-worker-restart=0 tests/integration/test_presentation_displays.py
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
`codex` or `claude-code` to select one client; CI runs both. Missing executables fail
an enabled native check; they are not silently skipped.

Set `ARTIFACT_STUDIO_ARTIFACTS=test-results/integration` to retain per-test server logs,
browser screenshots, Playwright traces and client logs. In CI, failed runs upload
these diagnostics with 1-day retention. Browser
artifacts are named `browser-results-<suite>` for `editor` and `runtime`; their JUnit files are
`browser-<suite>.xml`, with `browser-firefox.xml` for Firefox. View a
trace with `uv run playwright show-trace path/to/trace.zip`.

## Before committing

Optional local hooks match CI's Ruff checks and the version in `uv.lock`:

```sh
uvx pre-commit install
uvx pre-commit run --all-files
```

Adding a workflow does not change GitHub branch protection. To require these
checks before merge, select the four expanded CI job names in the repository's branch
rules after their first successful run.

The separate [Release Please workflow](RELEASING.md) prepares release PRs on pushes
to `main` and explicitly dispatches CI for those bot-created PRs. Publication is a
separate manual action after the release PR is merged and `main` CI passes.
