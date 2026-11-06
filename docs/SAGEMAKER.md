# SageMaker Studio Code Editor

Run Artifact Studio from its own checkout and Python environment, with one server
worker and persistent collection/state paths. The application still supports its
normal previews, captures, exports, history, feedback and enabled backups.

This guide concerns the app running inside a SageMaker Studio **Code Editor
space**. AWS documents one EC2 instance and one EBS volume per space, the mounted
home directory at `/home/sagemaker-user`, and optional access to an EFS file system
([Code Editor documentation][aws-code-editor]). EBS data persists when the
application is stopped; local NVMe storage is ephemeral
([Code Editor storage documentation][aws-code-editor-use]). These facts do not
establish this app's required instance size or the cause of a memory failure.

## Install in the project environment

Keep the checkout under `/home/sagemaker-user`, or another deliberately chosen
persistent location. From that checkout, follow the normal locked installation:

```sh
uv python install 3.14
uv sync --locked --python 3.14
uv run --no-sync playwright install chromium --only-shell
```

`uv sync` uses the project's `.venv`; install app dependencies there instead of
adding them to the Code Editor base environment or a notebook kernel. A runtime
checkout that does not run the development checks can use
`uv sync --locked --python 3.14 --no-dev`. This omits test, lint and type-checking
packages, reducing installed dependencies and disk use; it does not establish a
lower memory requirement for the running app. Restore development dependencies
with the normal sync command when working on source or running checks.

The repository's `.vscode/settings.json` excludes its generated `.state`, Python
environment/cache and test-result directories from Code Editor file watching and
search. Application source remains discoverable. These project-local settings
avoid indexing generated files; their effect on Code Editor memory has not been
measured. Keep the exclusions scoped to generated data when customizing them.

The Chromium headless shell supports the app's existing Playwright calls. Keep
the production Chromium sandbox enabled. If the renderer reports
`sandbox_unavailable`, investigate the Code Editor image and host sandbox support;
do not add `--no-sandbox` or change `chromium_sandbox=True`. The agent tool
`get_capture_status` reports the current renderer observation and queue; its
explicit `probe_renderer=True` option attempts a sandboxed launch. Installing the
browser alone does not prove that the host can launch it.

## Start one server

For a terminal launch from the configured checkout:

```sh
uv run --no-sync uvicorn artifact_studio.app:app \
  --host 127.0.0.1 --port 8790 --workers 1
```

Use this for the intended server instance; do not start another copy of an
already running workspace. Keep the existing collection, database and any other
environment settings. An explicit worker count avoids inheriting a larger
`WEB_CONCURRENCY` value. Each additional worker imports the app and creates its own
process-local state, background tasks and rendering coordination. The development
`--reload` watcher adds work and is unnecessary for ordinary use.

The app binds to loopback and expects `localhost` or `127.0.0.1`. Use the existing
authenticated access/forwarding arrangement for the space. Changing the bind
address alone does not configure a supported public endpoint.

Do not apply an arbitrary `RLIMIT_AS`/`ulimit -v` ceiling to the server: Chromium
and Python reserve virtual address space that is different from resident memory.
Measure the running workload and effective container limits first. This launch
does not reduce rendering dimensions, image quality, retained revisions or
application features.

## Keep workspace data on persistent storage

Existing `ARTIFACT_STUDIO_COLLECTION` and `ARTIFACT_STUDIO_DATABASE` values remain
authoritative. For a **new** workspace, these are example persistent locations:

| Data | Example path | Configuration |
| --- | --- | --- |
| Collection: authored files, assets and revisions | `/home/sagemaker-user/Documents/Artifacts` | `ARTIFACT_STUDIO_COLLECTION`; also the default for that home directory |
| Review database and adjacent state | `/home/sagemaker-user/artifact-studio-state/review.sqlite3` | `ARTIFACT_STUDIO_DATABASE` |
| Checkout and project environment | `/home/sagemaker-user/artifact-studio` | The checkout's `.venv` is managed by `uv` |

The default database is `.state/review.sqlite3` inside the checkout. That default
is persistent when the checkout itself is on persistent storage. Choose explicit
paths when keeping state separate from source updates; setting a new path does
not move an existing workspace. Preserve and back up **both** the collection and
the full database directory. See [workspace recovery](WORKSPACE-RECOVERY.md)
before migrating existing data.

The database directory also holds feedback attachments, reference repositories,
saved captures and derived previews. `artifact-previews/` reuses library covers
across server restarts and is bounded to 256 PNGs and 128 MiB;
`history-thumbnails/` is bounded to 256 previews. Keeping these derived previews
avoids repeated rendering after a restart. Saved agent outputs in
`visual-reviews/`, `design-captures/` and `element-captures/` may be referenced by
later work; they are not covered by the library-cover retention limit. Measure
their size before choosing a retention policy. Do not treat the whole state
directory as a disposable cache.

An optional EFS mount is a separate storage choice, not the space's EBS home
directory. Do not infer that a path is persistent merely because it is available
to the editor. For a new single-space installation, the EBS home directory keeps
the collection, SQLite database and local preview caches together without adding
a network filesystem dependency. Existing EFS-based workspaces need a deliberate
storage and recovery plan before relocation. Keep the only copy of workspace
data off ephemeral local NVMe storage.

## Measure the actual app process

From the checkout, inspect the diagnostic options:

```sh
uv run --no-sync python scripts/diagnose_runtime.py --help
```

Supply the actual app PID from its Uvicorn startup log or the process manager that
launched it. Substitute your configured state directory and the cache directories
you want to measure; these example paths describe the new-workspace layout above:

```sh
uv run --no-sync python scripts/diagnose_runtime.py \
  --pid 12345 --seconds 10 \
  --state-path /home/sagemaker-user/artifact-studio-state \
  --cache-path /home/sagemaker-user/artifact-studio-state/artifact-previews \
  --cache-path /home/sagemaker-user/artifact-studio-state/history-thumbnails
```

The standard-library-only tool prints JSON to stdout. It takes two Linux process
tree snapshots separated by `--seconds` (0.1–60 seconds, default 2), reads host
memory and visible cgroup v1/v2 memory/CPU limits, and reports available space for
the explicitly supplied state path. It reports RSS at each endpoint and CPU time
for process identities present at both. Repeat `--cache-path` up to eight times
to total file metadata sizes without reading contents. Each cache scan stops at
50,000 entries or a two-second work budget and marks incomplete results; a blocked
filesystem call can exceed that time budget. The tool does not launch, stop or
reconfigure the app, read process command lines or environment values, or call
AWS services or instance metadata.

Capture a quiet window, then compare endpoint snapshots taken while the operation
that causes pressure is active, such as opening a large presentation, generating
a contact sheet or sharing a project. Work that starts and finishes between the
two snapshots can be missed completely; the project performance benchmarks sample
more frequently to estimate peaks in their supported fictional workloads. Compare
the app tree against cgroup memory usage and headroom:
the same Code Editor environment may also run the editor, terminals, agents or
notebooks. Host free memory alone can conceal a stricter container limit.

Interpret the report with these limits:

- RSS summed across descendants may count shared pages more than once. It is not
  Python heap usage or peak memory. Processes created or removed between
  snapshots are excluded from the CPU calculation.
- The app tree includes its rendering descendants. A browser on your own computer
  is outside that tree, so its JavaScript/iframe memory is not measured here.
- Cgroup usage can include processes outside the selected app tree. Visible
  cgroup limits and headroom are observations, not a guaranteed allocation or a
  prediction that a particular export will succeed. Inaccessible or unresolved
  limits remain unknown; host memory is reported separately and cannot establish
  the app's effective container capacity.
- CPU is measured over the selected window; a short idle sample does not predict
  export, import or rendering load. Disk availability and file-size totals are
  different quantities, particularly for sparse files or shared storage.
- Reports contain process IDs and explicitly supplied filesystem paths. Review
  a report before sharing it outside your workspace.

## Project work that can still reduce pressure

The [runtime resource review](performance/runtime-efficiency.md) and
[representative session](performance/runtime-session-results.md) record existing
improvements and their measurement limits. Their shared-host measurements are
not SageMaker capacity tests. Relevant remaining opportunities in the app are:

| Observed workload | Follow-up that preserves behavior |
| --- | --- |
| Project ZIPs now compress members once, but the final ZIP and one verified artifact snapshot remain in memory; imports retain multiple buffers | Further spooling needs early size checks while preserving archive paths, supported formats and atomic failure behavior. |
| Contact sheets repeat shared asset data across prepared slides | Reuse verified request-local dependencies and prepare frames incrementally; preserve reveal states and capture geometry. |
| Independent immutable asset requests repeatedly verify the same bundle | Design an invalidation contract before caching verification; retain detection of same-size, same-timestamp source changes. |
| History/design previews do preparation or decoding on warm requests | Add content-keyed reuse with the existing orientation, format and integrity checks. |
| Saved capture outputs and retained revisions grow over time | Measure each category and define explicit retention/recovery behavior before adding cleanup. |

These are development opportunities, not flags that are safe to toggle off.
Polling, backup checks, capture limits and cache sizes should be changed only
with workload evidence and behavior checks. The current capture queue and saved
previews already limit repeated work; a persistent cache and one server process
let that coordination work as intended.

[aws-code-editor]: https://docs.aws.amazon.com/sagemaker/latest/dg/code-editor.html
[aws-code-editor-use]: https://docs.aws.amazon.com/sagemaker/latest/dg/code-editor-use-studio.html
