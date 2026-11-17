# Browser CI scheduling

## Earlier split

The successful [integration run 43729454155](https://github.com/kaiketsu-labs/artifact-studio/actions/runs/43729454155)
at `3df2ca4` spent 8m12s in `editor` and 8m36s in `runtime-1`, versus
4m34s in `runtime-2` and 5m05s in `runtime-3`.

The six-job matrix introduced at that point divided the first two suites across two jobs each.
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

## November 17 audit

Across the six most recent successful main pushes before the audit, allocated
runner time ranged from 33.5 to 38.9 minutes. In all six, `runtime-1` was
the longest browser job (423–533 seconds), while `runtime-4` took 64–87 seconds.
The median Chromium test step in the latter took 36.5 seconds. Each job also
checks out the repo, restores Python and Playwright, and installs OS dependencies.

The five-job layout absorbs that short batch into a Chromium runtime job,
moves hosted browser journeys to lighter jobs, and puts Firefox on the editor
runner that also runs the Chromium presentation compatibility tests. The matrix
adds five previously unscheduled browser/renderer cases. It keeps both workers,
the browser isolation settings, the agent checks and the required aggregate gate.
Every PR still runs the full core matrix. Shared HTML, runtime-loaded scripts
and HTTP consumers make import-only test selection unreliable here.

To repeat the baseline using Python 3.11+ and an authenticated GitHub CLI, run

```sh
python3 scripts/ci/measure-run.py 43900121347 43825570026 43824493787 43823181256 43821994544 43812044301
```

Compare first attempts with the same event, runner labels and platform checks.
Feedback is elapsed time from workflow creation until the required gate finishes,
including queues. Compute is the sum of allocated job seconds by runner label.
These measure different things. Minute rounding, repository allowances and billing
rates would be needed to turn compute into a charge. Failed or cancelled attempts
also consume compute. The historical sample above includes successes only.

Vincent Koc's [CI audit](https://vincent.build/openclaw-ci-cost-reduction/)
describes separate OpenClaw changes that [packed short jobs](https://github.com/openclaw/openclaw/pull/95649)
and [added jobs to balance a long tail](https://github.com/openclaw/openclaw/pull/104472).
The appropriate job count here depends on this project's hosted timings.
