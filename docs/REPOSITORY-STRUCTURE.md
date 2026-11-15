# Repository structure and migration plan

This is a map of implemented feature folders and the remaining migration plan.
The complete target tree below is still a plan; only the folders described in
**Implemented feature folders** have moved.

## Implemented feature folders

- [`artifact_studio/designs/`](../artifact_studio/designs/): reusable libraries,
  references, authoring, transfer, previews/thumbnails, whole-slide proposals,
  element alternatives, and their HTTP adapters. Production imports use the
  feature package. Historical `design_*`, `slide_design*` and
  `element_alternative*` modules are small compatibility aliases to the same live
  module objects, preserving existing imports and monkeypatches. Keep those
  aliases until a separately announced compatibility change; add new behavior in
  the feature package, never in the adapters.
- [`web/presentation/`](../web/presentation/): audience launch/fullscreen,
  bounded idle warmup, and presentation archive/restore controls. The editor
  loads canonical paths. Exact historical JavaScript URLs are served from the
  same files through [`web_assets.py`](../artifact_studio/web_assets.py), without
  redirects or symlinks. Presentation styles remain in the editor's existing
  stylesheet to preserve cached-page behavior and avoid another CSS request.

`PROJECT_ROOT` in `artifact_studio/__init__.py` identifies shipped repository
resources for moved modules; it is independent of collection and state paths.
The standalone `runtime/`, public ASGI/process entrypoints, copied presentation
runtime, agent contract, test locations and CI selection stay unchanged.
The Docker context explicitly allows Python files in `artifact_studio/designs/`;
when introducing another package, extend that allowlist rather than admitting
arbitrary workspace content to the image build.

## What needs to improve

At the v0.8.0 release commit (`e987622`), `artifact_studio/` has 96 files directly
under it, `web/` has 121, and `tests/` has 177. The flat names have useful feature
prefixes, but finding all parts of a change requires searching several directories.

The larger maintenance problem is mixed responsibilities:

- [app.py](../artifact_studio/app.py) is roughly 2,300 lines and combines process
  setup, service instances, MCP tools and HTTP routes. Its 48 local-module imports
  are reasonable for a composition root; owning all the handlers in that same file
  makes isolated changes and tests harder.
- [web/app.js](../web/app.js) and [web/hub.js](../web/hub.js) each exceed 1,400 lines.
  They coordinate navigation, persistence, previews, chat and many feature panels.
- [storage.py](../artifact_studio/storage.py) mixes presentation serialization,
  database state, revisions and file operations. Many features depend on it and
  [models.py](../artifact_studio/models.py), so moving those files first would
  relocate the coupling rather than resolve it.
- [projects.py](../artifact_studio/projects.py) includes project archives, portable
  drafts, asset embedding and audience exports. These are different change paths.

Organize by feature with a few explicit technical boundaries. Keep related
behavior together and use the same names in Python, browser code and tests.

## Target layout

```text
artifact_studio/
  app.py                   # stable ASGI entrypoint; delegates assembly
  server.py                # stable process entrypoint
  bootstrap/               # local/hosted assembly and service lifetimes
  presentations/           # deck models, edits, sections, questions, proposals
  artifacts/               # artifact models, bundles, projects and revisions
  designs/                 # design library, references and alternatives
  collaboration/           # grants, conversations, presence and offline delivery
  rendering/               # preview/capture workers, asset embedding and exports
  agents/                  # MCP registration, contracts and live-review adapters
  sync/                    # Git/source synchronization, backups and recovery
  platform/                # config, auth adapters, paths, diagnostics and updates

web/
  app.js                   # editor composition and navigation
  hub.js                   # library composition and navigation
  presentation/            # launch, warmup and display integration
  editor/                  # slide rail, sections, objects and editing panels
  library/                 # projects, navigation, covers and design browsing
  chat/                    # composer, timeline, references, voice and drafts
  collaboration/           # invitations, presence, sharing and offline UI
  history/                 # revision browser and restore interactions
  settings/                # storage, backup, appearance and installed version
  ui/                      # shared icons, menus and small controls
  vendor/                  # generated dependencies, not application source

runtime/                   # standalone/exportable presentation viewer
macos/                     # native controller
agent/                     # shipped agent-facing guides and tool contract
tests/
  unit/                    # feature groups matching Python and browser ownership
  integration/             # real browser, server, agent and multiplayer journeys
  support/                 # fictional fixtures and reusable test transports
  native/                  # platform-specific verification
scripts/
  benchmarks/
  screenshots/
  ci/                      # existing CI selection and gates
  dependencies/            # existing dependency automation
  release/                 # existing release tooling
docs/
  ARCHITECTURE.md           # implemented product architecture
  REPOSITORY-STRUCTURE.md   # this source layout and migration plan
  performance/             # measurements and reproducible methods
```

Use shallow folders. A feature can start with `models.py`, a focused service and
its transport adapters; it does not need a mandatory stack of controller,
repository, service and manager wrappers. Avoid a growing `utils/` or `common/`
folder. Add a subpackage when it has a cohesive responsibility and several files.

Keep the standalone viewer in `runtime/`: exported HTML and audience windows must
not depend on the editor shell, chat, authenticated workspace state or its UI
initialization. Grouping it under the browser editor would obscure that boundary.

## Ownership and dependency rules

| Area | Owns | Current starting points |
| --- | --- | --- |
| Presentations | Slide content, ordering, visibility, sections and edit contracts | `models.py`, `slide_actions.py`, `sections.py`, `questions.py`, `structure_proposals.py` |
| Artifacts | Non-slide artifacts, project membership and immutable revision bundles | `artifact_models.py`, `artifacts.py`, `project_tree.py`, `version_history.py` |
| Designs | Reusable design context and reviewable design alternatives | `designs/library.py`, `designs/references.py`, `designs/slides.py`, `designs/elements.py`; `designs/*_api.py` for HTTP binding |
| Collaboration | Membership, grants, conversations, proposal responses and presence | `multiplayer_access.py`, `multiplayer_chat.py`, `deck_presence.py`, `offline_delivery.py` |
| Rendering | Isolated execution, captures, previews, public exports and resource budgets | `visuals.py`, `renderer_health.py`, `interactive.py`, embedding/export portions of `projects.py` |
| Agents | Tool registration and transport-specific responses | registration in `app.py`, `artifact_tools.py`, `multiplayer_tools.py`, `review.py`, `agent_io.py` |
| Sync | Source synchronization, archives, restore and multi-computer conflict rules | `autosync.py`, `project_sync.py`, `presentation_sync.py`, `github_backups.py`, `workspace_recovery.py` |
| Platform | Process configuration, identity providers, diagnostics and updates | `deployment.py`, `authentication.py`, `request_diagnostics.py`, `updater.py` |

HTTP and MCP adapters call feature operations; feature operations do not import
the application singleton or register routes as an import side effect. Share the
operation between transports rather than duplicating validation in each adapter.

Bootstrap creates stores, render budgets and background tasks once, gives explicit
dependencies to feature registration functions, and owns shutdown. Existing
`*_routes(...)` and `register_*_tools(...)` functions provide a starting pattern;
no dependency-injection framework is required.

Models belong to the feature whose rules they express. Extract only
shared primitives such as identifiers and base validation before moving domain
models. Keep authoritative grant/revocation checks and renderer isolation at
their execution boundaries. Folder membership is not an authorization check.

Browser features receive narrow callbacks and state accessors rather than reading
and mutating unrelated feature globals. Feature entrypoints should document their
start/reset/dispose lifecycle. Observers, request cancellation and background
timers belong to that lifecycle, preserving the existing resource limits.

## Migration order

1. **Make discovery reliable.** Add this map and feature ownership guidance.
   Before moving tests or scripts, update discovery and verify that the same test
   inventory still runs. Current JavaScript tests use a top-level `tests/*.cjs`
   glob and browser CI lists specific files: moving files alone can silently omit
   coverage. Keep fixture imports and duplicate pytest module names unambiguous.
2. **Extract registration from the entry files.** Start with one cohesive group
   of MCP tools and its HTTP adapters. Pass the existing store/services explicitly;
   keep public tool names, schemas, URLs and initialization behavior unchanged.
   Keep the existing `artifact_studio.app:app` entrypoint working throughout.
3. **Move one complete feature at a time.** Presentation launch/display helpers
   are a small frontend candidate. Move matching JS/CSS, update HTML/dynamic
   references, tests and docs in the same change, and review the real flow. Then
   group backend features as their ownership becomes clear. Keep move-only changes
   separate from behavioral fixes so reviewers can inspect both.
4. **Break up shared persistence and exports.** Separate pure serialization and
   asset/export logic from workspace transactions and service orchestration only
   after callers have explicit interfaces. Preserve transaction/lock boundaries,
   revision anchors, retries and pending human work. Use concurrency and restore
   simulations to establish equivalence.
5. **Group development tools and tests.** Move benchmarks and capture scripts
   into their own packages once path discovery is ready. Update documented module
   commands and relative resource paths. Keep intentional compatibility launchers
   for commands used by installations or integrations, with a removal policy.

Design services and presentation helpers now provide the first feature-folder
examples. Registration extraction from `app.py` and the more coupled storage,
sync and collaboration domains remain separate follow-up work. Do not interpret
the target tree as authorization for a bulk rename of every remaining module.

## Compatibility checks for every move

- **Entrypoints:** preserve ASGI/process imports and command-line updater calls.
  The hook client and local HTTP helper are distributed as standalone files by
  [hook_bundle.py](../artifact_studio/hook_bundle.py) and support Python 3.10;
  moving them into a Python 3.14 application package must not break that contract.
- **Resources:** audit `__file__`/`parents[...]` paths. Centralize paths to shipped
  assets before changing package depth. User collection/database roots remain
  explicit configuration, separate from the source tree.
- **Browser URLs:** update static references, dynamic loads, CSS-relative URLs,
  offline caches and hosted asset policy together. Preserve URLs embedded in old
  presentation files, or provide narrow, tested compatibility routes.
- **Distribution:** exercise the source checkout, Docker `COPY` layout and Mac
  controller. Do not combine this migration with a `src/` packaging conversion,
  framework rewrite or new bundler.
- **Contracts and tests:** compare the generated agent contract, run strict types
  and affected tests, and verify browser/agent/CI discovery. Test patches of old
  module globals can stop reaching the implementation after a re-export; update
  the injection seam rather than keeping two mutable sources of truth.
- **Evidence:** preserve fictional browser journeys, renderer/privacy checks,
  multi-user conflict simulations and performance budgets for affected behavior.

Success means a feature change has an obvious home, reaches fewer unrelated
modules, and retains its tests and public contracts. Directory depth alone is not
the measure.
