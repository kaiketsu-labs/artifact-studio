# Browser CI scheduling

The successful [integration run 43729454155](https://github.com/kaiketsu-labs/artifact-studio/actions/runs/43729454155)
at `3df2ca4` spent 8m12s in `editor` and 8m36s in `runtime-1`, versus
4m34s in `runtime-2` and 5m05s in `runtime-3`.

The browser matrix now divides the first two suites across two jobs each.
All 94 test-file selections present after adding reply-thread coverage are
preserved exactly once. Existing suite names remain, and the Firefox checks
still run in `runtime-1`. Every job retains two pytest workers, failure
diagnostics and the existing aggregate safety gate.

Browser jobs share the locked Python dependency cache. Playwright caches are
separate for Chromium and Chromium/Firefox, keyed by operating system,
architecture and the pinned browser manifest. Exact-version legacy cache
fallbacks ease migration; a fallback still runs the pinned-browser installer.

This trades two additional runner setups for a shorter parallel critical path;
it is not a claim of lower total runner minutes. Compare job durations in the
final integration and release CI runs to measure the result. Those runs include
new reply-thread tests, so the earlier timings are an operational baseline,
not a controlled benchmark of identical application code.
