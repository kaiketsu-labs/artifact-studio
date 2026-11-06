# Library preview performance measurements

The revised failure path used less memory and returned repeat/cache requests faster in this controlled workload. The median highest process-tree RSS across each run fell from **337.7 MiB to 219.8 MiB (35%)**. Initial metadata listing also improved, but remains expensive. No out-of-memory crash occurred in either version, so these results do not establish the cause of the reported crash.

All data is fictional. Measurements exercise the actual HTTP handlers and production renderer policy on the managed Linux host. They do not reproduce the user's machine.

## Workload and method

- Baseline: `21269d523b0b529a169af7bb45761a021ef816e4` in a detached checkout. Revised: the implementation worktree based on that commit. Three fresh server processes and fresh fixtures per version, run sequentially. Every recorded run had stable source hashes; the revised runs used identical backend hashes. Full code hashes and raw measurements are in [library-preview-results.json](library-preview-results.json).
- Fifteen cards: three owned HTML artifacts and twelve legacy presentations. Two legacy presentations have 49 slides and approximately 8/18 MiB `index.html` documents, with 51 valid padded SVG assets of 2.4 MiB each. Each cover references one asset. The collection is 288,656,792 bytes; no real collection, review database or credentials are used.
- Fixture generation is excluded from measurements. The app binds only to a newly allocated loopback port, uses an isolated collection/database and has update checking disabled. No backup destination is registered.
- RSS is sampled about every 25 ms across the server's descendants using Linux `/proc/*/stat` PPIDs, including children spawned from HTTP worker threads. A separate 64 MiB child-allocation check verified this traversal. Boundary snapshots cover phases shorter than the sampling interval. Shared pages can be counted more than once, and very short peaks can fall between samples.
- A watchdog kills only the benchmark's own process group above 1,536 MiB or after 180 seconds. No limit was reached in the recorded comparison. A separate run with a deliberately lower 256 MiB limit stopped the isolated process group at 289.37 MiB sampled RSS; polling permits overshoot. This is a benchmark safeguard, not a production memory ceiling.
- HTTP phases request metadata once, burst fifteen cold covers concurrently, repeat after the initial requests settle, then request fifteen seeded warm covers. Revised requests follow the server's exact `preview_url`. Initial HTTP 202 replies are polled until terminal outcomes before the cold phase ends, so the memory measurement includes background renderer work. Initial response latency and terminal completion time remain separate.
- Each phase starts a concurrent `/api/health` probe, with another worker probing approximately every 150 ms. Request p95 values describe only fifteen cover requests per run. Health sample counts are small, so the table reports observed maxima, not a stable percentile. The host is shared and these three repetitions are not statistically controlled capacity estimates.
- **The production Chromium sandbox cannot start on this host.** All cold/repeat covers fail closed. This measures failure-path work, not successful rendering. The warm phase seeds valid fixture PNGs to isolate metadata/cache-serving cost; those PNGs are not rendered artifact covers.

## Results

Values are medians across three runs. Parentheses give the minimum and maximum sampled process-tree peak across those runs. Request latency is the single metadata request or the p95 of fifteen initial cover responses.

| Phase | Baseline peak MiB (range) | Revised peak MiB (range) | Baseline request ms | Revised request ms |
| --- | ---: | ---: | ---: | ---: |
| Metadata listing | 199.4 (199.3–199.5) | 181.1 (181.1–190.6) | 1,518 | 1,386 |
| Cold cover burst | 265.2 (248.0–265.3) | 219.8 (216.3–220.6) | 1,885 | 75 |
| Immediate repeat | 322.7 (320.7–350.2) | 114.1 (113.8–115.2) | 1,735 | 51 |
| Seeded warm cache | 337.7 (292.5–345.2) | 115.5 (115.2–116.0) | 686 | 75 |

Baseline cold and repeated bursts returned HTTP 400 for all fifteen requests. Revised cold bursts initially returned HTTP 202, then all fifteen settled to HTTP 503 because the sandbox was unavailable. The revised **75 ms cold value is acknowledgement latency**, not render completion. Median elapsed time through all terminal cold outcomes was 0.781 seconds revised versus 1.982 seconds baseline. Revised immediate repeats returned HTTP 503 without another browser launch. All seeded warm requests returned HTTP 200 in both versions.

Every observed health request in the measured phases returned HTTP 200:

| Phase | Baseline health maximum ms (samples) | Revised health maximum ms (samples) |
| --- | ---: | ---: |
| Metadata listing | 339 (24) | 243 (26) |
| Cold cover burst | 252 (31) | 94 (18) |
| Immediate repeat | 551 (25) | 31 (9) |
| Seeded warm cache | 291 (18) | 72 (12) |

Request-scoped reuse of legacy presentation summaries avoids duplicate discovery/parsing. Initial metadata listing was approximately 9% faster and used 9% less peak RSS in the revised sample. Legacy cover identities stream dependency bytes to detect externally replaced assets, including replacements that preserve size and modification time. This retains cache correctness but leaves substantial metadata work: approximately 1.4 seconds and 181 MiB sampled peak here. Warm cover requests avoid those source reads. Further metadata optimization needs its own invalidation and performance verification.

## Operational behavior

- Covers are disposable derived files in `artifact-previews/` beside the workspace database, separate from the synced source artifacts. Successful images survive server restarts. The cache retains at most 256 PNGs and 128 MiB, pruning least recently used entries; an individual generated cover is limited to 8 MiB.
- One background worker handles capture, with capacity for eight queued identities. Duplicate requests share pending work. HTTP 202 returns promptly while the browser polls; known failures observe a 30-second cooldown. Cached covers remain available during a renderer outage.
- URLs identify an exact visual version. When content changes, the last successful image remains visible until its replacement is ready. Dependency bytes are streamed to detect external edits without retaining entire assets in memory.
- Capture geometry remains 1280 × 800 for HTML/Markdown/SVG artifacts and the full 1600 × 900 slide viewport for presentations. Raster images use Pillow with a maximum dimension of 1280. Authored HTML/CSS/JavaScript and source documents retain their existing behavior and quality.
- The benchmark starts only isolated loopback servers. These measurements do not deploy the change or restart the live application.

## Quality and persistence evidence

This benchmark cannot prove successful-render fidelity on this host. The sandbox-capable renderer lane runs [test_hub_cover_rendering.py](../../tests/test_hub_cover_rendering.py), which checks actual full-resolution pixels, authored JavaScript/local assets and saved-cover reuse. [test_hub_previews.py](../../tests/test_hub_previews.py) checks version identities, retained last-good covers, failure cooldown and worker restart behavior. Browser scheduling and interaction acceptance are tested separately; synthetic warm entries in this benchmark must not be cited as rendered-preview evidence.

## Browser review

Three fresh Terra reviewers exercised desktop and mobile browsing, search/sort, scrolling, slow requests, keyboard retry, and saved-preview recovery. They found and rechecked fixes for overlapping Share/Retry controls and a renderer outage incorrectly delaying raster covers. After rebasing onto Favorites, a reviewer also clicked Favorite, Share, Retry and Open in light/dark views and checked a 250 px card for overlapping controls.

The real raster backend was exercised through creation, a source revision, a mixed-library renderer outage, and a server restart. Browser failure states used fictional PNG responses; these screenshots show the implemented UI, not proof of rendered HTML fidelity. The automated sandboxed-renderer tests cover that separate requirement.

[Mobile saved preview](screenshots/library-mobile.png) · [Desktop dark saved preview](screenshots/library-desktop-dark.png)

## Reproduce

Run from the development checkout with its locked dependencies installed:

```sh
uv run --no-sync python scripts/benchmark_library_previews.py \
  --source /path/to/checkout \
  --output /tmp/library-preview-run.json
```

Repeat three times with fresh fixture directories for each version. Use `--fixture-root /tmp/new-directory` to retain fictional fixtures, or add `--seed-only` to prepare them for a browser journey. The target fixture directory must not already exist. `--source` controls application imports and the server working directory, allowing the same harness/environment to measure separate baseline and revised checkouts. Cold covers use the normal production sandbox; this script has no sandbox-disable switch.
