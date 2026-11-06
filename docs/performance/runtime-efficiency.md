# Runtime resource review

This review follows the [persistent library-cover fix](library-preview-results.md). It examines CPU, memory, retained browser documents and disk work using isolated fictional workspaces. Changes remove repeated work while preserving authored slide documents, image quality, immutable snapshot verification, history, notes, feedback delivery and explicit sync permissions.

## Measured improvements

| Workload | Before | After | Change |
| --- | ---: | ---: | --- |
| 40-slide editor rail, live thumbnails at normal desktop size | 40 | 7 | Mount nearby frames; release offscreen/collapsed frames |
| Same editor, browser JS heap after GC | 16.46 MiB | 5.04 MiB | Authored content remains unchanged |
| 40 favorite slides, live previews when open / after close | 40 / 40 | 5 / 0 | Visible previews, two concurrent loads, full close cleanup |
| Favorites search then clear, additional preview requests | 40 | 0 | Reuse surviving cards and frames |
| Favorites closed, browser JS heap after GC | 12.26 MiB | 2.20 MiB | Remove documents and disconnect preview observers |
| Empty feedback poll after 10,000 retained legacy messages | 162 ms / 48.1 MiB | 4.1 ms / 0.14 MiB | Filter delivery cursor in SQL; index pending counts |
| Same poll for an owned artifact | 86 ms / 39.7 MiB | 7.0 ms / 1.2 MiB | Preserve late Batch releases and delivery order |
| One small project's listing beside three 32 MiB artifacts | 169 ms; 192 MiB read | 3.3 ms; about 1 KiB read | Filter project membership before source verification |
| Read 28 bytes from a 32 MiB owned bundle | 32.1 MiB allocation | 2.2 MiB allocation | Stream full verification, retain requested bytes only |
| Capture preparation for a 16 MiB bundle | Four full passes; 67 ms | One verified read; 18 ms | Reuse the same verified bytes through document preparation |
| Paused/error sync with a 256 MiB workspace | 204 ms CPU per tick; 256 MiB hashed | 0.9 ms; no hashing | Inspect only destinations eligible to sync |
| Temporary rollback copies after 20 edits to a 16 MiB artifact | 320 MiB | 0 MiB | Remove the operation's backup after a successful commit |
| Unchanged full-backup scan of that fixture | 672 MiB; 549 ms | 352 MiB; 324 ms | Eliminate leaked temporary copies, retain all revisions |
| Navigation marker lookup in a 32 MiB presentation | 64.1 MiB allocation | 0.25 MiB allocation | Stream until the marker, including split/late markers |
| Three concurrent PDF requests, simulated browser lifecycle | Three simultaneous browsers | One | Share the existing render queue, cooldown and cleanup |

Python memory values are peak `tracemalloc` allocations, not total process RSS. Browser heap values are CDP measurements after explicit GC. Browser frame counts depend on viewport: a compact sorter showing all slides can legitimately keep all visible thumbnails mounted. CPU values are process time, not a claim about a particular laptop's battery life.

Backend timing rows use three local repetitions, navigation five. The capture/share and frontend profiles are single instrumented runs with deterministic fictional inputs; their timing differences are directional. [Raw evidence](runtime-efficiency-evidence.json) retains the detailed counters and methods. Source for the baseline is `2ccc1b3` / `7644722` (identical application code); the revised source is this change.

A separate [17-phase browser/server session](runtime-session-results.md) exercises ordinary idle, library, owned HTML, editor, history, chat and design-dialog flows. The 20-slide editor used 22 → 10 browser frames and 1.11 → 0.73 seconds of browser CPU during loading. Ordinary idle remained similar. That report includes all phase results, limitations and a reproducible benchmark script.

The New presentation dialog also skips hidden-page polling, prevents overlapping refreshes and removes design previews on close. An epoch guard prevents a late response from recreating closed previews. Draft text, attachments and design-reference flows remain intact. Visibility guards were exercised by a browser test override; physical backgrounding and native macOS energy usage were not measured.

## Coverage and behavior checks

The review covered library and design discovery, editor/rail/sorter, favorites, chat and feedback polling, notes, history, alternatives and selection editors, attachments and voice, agent contexts and selective file reads, capture/render/OCR, PDF and archive sharing, imports, sync/backup/recovery, updater and Mac-controller lifetimes.

Three fresh Terra reviewers used the modified app with fictional data. They clicked desktop/mobile light/dark journeys: scrolling first/last slides, exact authored previews after remount, questions, hidden/star controls, slide/section keyboard moves, sorter navigation, favorite descriptions/search, five references across filters, close/reopen, design attachments and delayed responses. The found test locators were corrected. [Favorites](screenshots/runtime-favorites-dark.png) · [Mobile sorter](screenshots/runtime-rail-mobile.png) · [Design dialog](screenshots/runtime-design-dialog.png).

Regression coverage includes stale/tampered snapshots, same-size file changes, membership and external edits, late Batch delivery, pending counts, rollback after failure, sync retry/crash recovery and mixed eligible/paused destinations. No source resolution, retained history, integrity check or authored feature was removed. Existing orphan folders are not deleted automatically; the fix prevents new successful edits from leaking them.

Healthy existing behavior includes ordinary library/editor visibility guards, history dialog frame/object-URL cleanup, alternative-comparison disposal, draft attachment URL release, editor observer teardown, bounded OCR timeouts and the capture queue. Normal idle CPU is relatively low in the representative small workspace, so active work and retained frames were prioritized over reducing low-cost heartbeat frequency.

## Remaining opportunities

| Area | Evidence and next step |
| --- | --- |
| Project ZIP sharing | Two 4 MiB bundles cause 16 MiB of compression input through ZIP/unzip/re-ZIP and about 30 MiB peak Python allocation. Stream one archive with an early size/concurrency budget, preserving exact paths and exclusions. |
| Contact sheets | Twelve slides sharing a 2 MiB image retain about 33 MiB of data-URL HTML and peak near 106 MiB before Chromium. Share verified request-local dependencies and prepare frames incrementally; preserve reveal states, viewport and stale-source checks. |
| Independent immutable asset URLs | Twenty tiny URLs in a 32 MiB bundle still stream 640 MiB for whole-snapshot verification. A reusable integrity mechanism needs an explicit invalidation contract; a timestamp-only or TTL cache would weaken current tamper detection. |
| History/design thumbnails | Some warm paths prepare source before finding their cache; reference thumbnails decode again. Apply content-keyed saved previews while preserving EXIF orientation, formats and same-size external-edit detection. |
| Large uploads/imports | Input chunks, joined archives and expanded files can coexist in memory. Spool/stream with early limits while preserving supported formats and atomic import failure. |
| Saved agent captures | Returned image/OCR paths may be used later. Add an explicit retention contract before automatically deleting old captures. |
| Native controller and update | Source review: health every 2 s; navigation every 10 s with faster retries after errors; updates preserve a whole-environment rollback copy. Native CPU/energy and physical-device testing remain required before tuning these behaviors. |

These are documented follow-up work, not claimed improvements. Active sync still hashes content to detect changes even when size and timestamps are preserved. No changes were made to billing, live port 8790, the signed Mac app or any user collection.
