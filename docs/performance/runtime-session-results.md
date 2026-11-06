# Runtime efficiency: representative before/after session

The same 17-phase fictional browser session ran against baseline `76447227dbf45ff1a0ef0fec99005f4b21b5ac4b` and a revised dirty worktree based on `2ccc1b375f94d9f45bb322f079e41a3cfbf1b044`. The revised tree included uncommitted runtime changes; its recorded `commit` identifies the base commit, not the measured implementation. All 154 recorded application source hashes remained unchanged throughout the run and match the implementation subsequently committed as `5feb0f27e77c95e5bfa475df6d20355198023dbe`. Baseline source was an isolated clean detached checkout.

[runtime-session-results.json](runtime-session-results.json) retains both complete raw runs, provenance and limitations; [benchmark_runtime_session.py](../../scripts/benchmark_runtime_session.py) reproduces the session. The script accepts `--source` for either target checkout and `--output` for its raw JSON result; use the same `--seconds` value for both runs (15 by default).

The 20-slide editor changed from **22 to 10 live frames** during initial load. Its sampled browser process-tree peak changed from **538.1 to 517.9 MiB**; browser CPU during that load changed from **1.11 to 0.73 seconds**. Main-target JavaScript heap after visible editor idle changed from **12.38 to 8.22 MiB**; the diagnostic forced-GC checkpoint at the end changed from **11.90 to 7.41 MiB**. These are measured session values, not a claim about every deck or the whole application.

The New presentation visibility guard probe changed from **15 to 0 requests over 10 seconds**. Closing that dialog now leaves **10 frames**, compared with 23 before the fix; this should be read relative to each version's different rail frame count. History closes its selected frame in both versions. Chat sent through the real composer, and all 17 phases completed without a JavaScript exception or watchdog stop.

## Paired measurements

Each cell is baseline → revised. CPU is percent of one core summed across the process tree, RSS is sampled aggregate memory in MiB, and browser frame counts include the top document. Idle phases last 15 seconds, dialog phases 10 seconds; load windows include navigation plus 2 seconds of settling. There is no invented overall total or extrapolated speedup. Small CPU differences on a shared host should not be read as regressions without repetition. Short history/chat phase request counts also vary with the independent 3-second backup-status timer: the chat action itself still made one feedback POST, two deck reads and one hub read in both versions.

| Phase | Server CPU % | Browser CPU % | Server peak MiB | Browser peak MiB | Requests | Frames |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| idle_server | 1.07 → 0.73 | 0.00 → 0.00 | 104.26 → 104.08 | 0.00 → 0.00 | — → — | — → — |
| idle_blank_browser | 0.27 → 0.27 | 0.13 → 0.07 | 104.26 → 104.08 | 372.07 → 372.65 | 0 → 0 | 1 → 1 |
| library_load | 21.15 → 29.97 | 15.29 → 18.81 | 211.60 → 218.85 | 443.49 → 443.18 | 59 → 59 | 1 → 1 |
| library_visible_idle | 0.86 → 0.93 | 0.80 → 0.93 | 113.23 → 112.98 | 452.77 → 452.26 | 18 → 18 | 1 → 1 |
| library_simulated_hidden | 0.33 → 0.40 | 0.33 → 0.33 | 113.23 → 112.98 | 446.89 → 446.60 | 0 → 0 | 1 → 1 |
| owned_artifact_load | 6.13 → 14.64 | 12.26 → 11.71 | 194.09 → 205.89 | 481.96 → 479.37 | 63 → 63 | 2 → 2 |
| owned_artifact_visible_idle | 0.87 → 1.13 | 0.73 → 0.80 | 114.98 → 114.62 | 475.66 → 474.41 | 19 → 19 | 2 → 2 |
| slide_editor_load | 11.66 → 9.45 | 34.99 → 24.63 | 116.23 → 115.49 | 538.14 → 517.89 | 134 → 111 | 22 → 10 |
| slide_editor_visible_idle | 1.06 → 1.06 | 2.26 → 1.86 | 116.35 → 115.74 | 519.40 → 498.26 | 20 → 20 | 22 → 10 |
| slide_editor_simulated_hidden | 0.27 → 0.27 | 0.20 → 0.13 | 116.35 → 115.74 | 488.30 → 470.66 | 0 → 0 | 22 → 10 |
| history_open_expanded | 6.77 → 7.58 | 12.63 → 12.63 | 226.22 → 224.14 | 548.43 → 544.32 | 14 → 14 | 23 → 11 |
| history_closed | 0.97 → 0.64 | 2.91 → 2.89 | 117.98 → 116.99 | 524.74 → 504.68 | 7 → 3 | 22 → 10 |
| chat_send_and_refresh | 2.89 → 2.23 | 10.27 → 8.28 | 118.10 → 116.99 | 523.41 → 506.99 | 7 → 11 | 22 → 10 |
| new_presentation_dialog_visible | 1.96 → 1.66 | 4.90 → 4.69 | 118.10 → 116.99 | 525.86 → 511.31 | 36 → 36 | 23 → 11 |
| new_presentation_dialog_simulated_hidden | 0.70 → 0.50 | 1.80 → 1.19 | 118.10 → 116.99 | 521.48 → 509.01 | 15 → 0 | 23 → 11 |
| new_presentation_dialog_closed | 1.39 → 1.59 | 2.39 → 2.78 | 118.23 → 117.24 | 516.58 → 505.52 | 9 → 8 | 23 → 10 |

## Coverage inventory

| Area | Evidence in this session or companion profile | Remaining scope |
| --- | --- | --- |
| Server and ordinary idle browser | Real isolated server, blank browser, visible/hidden-guard library and editor; process-tree CPU/RSS and network counts | Native Mac health/navigation cadence and real hidden-tab OS throttling were not measured |
| Main library and owned HTML | Four fictional decks and two artifacts; actual navigation, metadata, raster cover and owned HTML iframe | Production Chromium sandbox unavailable here; successful library rendering requires the existing sandbox-capable CI lane |
| Slide editor | Actual 20-slide document, rail/active previews, 30 seeded chat messages, UI chat send and workflow polling | Large animated decks, high-DPI physical displays and long-duration memory stability need dedicated workloads |
| History | Nine revisions, expanded list, selected live preview and close cleanup | This profile did not restore a version; functional restore tests remain a separate check |
| Design creation | Actual menu/dialog flow; visible polling, hidden guard, close cleanup | Hidden state was explicitly simulated through document properties/events; slow-response races are covered by focused lifecycle tests, not this timing run |
| Favorites | Separate frontend profile: 40 → 5 preview iframes open, 40 → 0 closed; search preview requests 40 → 0 | See [runtime-efficiency-evidence.json](runtime-efficiency-evidence.json); these are not numbers from this session |
| Feedback delivery | Separate caught-up 10,000-message legacy profile: median 162.06 → 4.06 ms, traced allocation peak 48.14 → 0.14 MiB | See [runtime-efficiency-evidence.json](runtime-efficiency-evidence.json). Large feeds are synthetic scaling fixtures; external Claude/Codex inference latency was not measured |
| Sync and backups | Separate local error-destination profile: 256 → 0 MiB hashed per tick, CPU 204.1 → 0.9 ms; repeated-revision test removes 320 MiB of leaked rollback copies | No external GitHub upload/download, remote quota, sign-in, or cross-machine conflict workflow measured |
| Rendering/export | Capture agent audited shared admission and snapshot preparation separately; original viewport settings remain required | Successful browser rendering and concurrent real PDF export cannot be established on this host's unavailable production sandbox |

## Interpretation and limitations

- The source fixture is intentionally representative and small: four decks, a 20-slide main deck, two owned artifacts, 30 chat messages, nine cover revisions and approximately 596 KiB of collection files. Companion profiles cover large feedback/media cases separately.
- Ordinary idle server/browser CPU was already low. The highest-value changes remove work that scales with deck size, feedback history or stalled backup destinations. Visible backup-status polling remains a possible smaller optimization if progress/recovery responsiveness is preserved.
- RSS includes browser descendants and may count shared pages more than once. Heap snapshots refer to the main CDP target. CPU/RSS sampling every 100 ms can miss short-lived child CPU and short memory peaks. The blank-browser baseline is substantial and must not be attributed entirely to application code.
- Visibility probes override `document.hidden`/`visibilityState` and dispatch `visibilitychange` because native hidden state is unavailable in this headless browser. This measures application guards, not browser/OS background throttling.
- Phase metrics are frozen at boundaries; transition work is tagged separately. Request counts include actual browser request events; completed response bytes exclude unfinished SSE streams. No collection data, live application on port 8790, external agent service or remote GitHub destination was used.
- One paired session does not establish statistical significance, production memory capacity, or the cause of the original crash. Successful sandboxed capture, native Mac behavior, actual remote synchronization and long-duration stability remain explicit verification boundaries.
