# Browser CI scheduling

## Earlier split

The successful [integration run 43729454155](https://github.com/kaiketsu-labs/artifact-studio/actions/runs/43729454155)
at `3df2ca4` spent 8m12s in `editor` and 8m36s in `runtime-1`, versus
4m34s in `runtime-2` and 5m05s in `runtime-3`.

The six-job matrix introduced at that point divided the first two suites across two jobs each.
It retained all 94 file selections, two workers per job and failure diagnostics.
At the time, Firefox ran after Chromium in `runtime-1`.

Browser jobs share the locked Python dependency cache. Playwright caches are
separate for Chromium and Chromium/Firefox, keyed by operating system,
architecture and the pinned browser manifest. Exact-version legacy cache
fallbacks ease migration; a fallback still runs the pinned-browser installer.

That split traded two additional runner setups for a shorter parallel critical path;
it was not a claim of lower total runner minutes. Compare job durations in the
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

### Verification

Pytest collection found all 640 prior Chromium cases plus the five additional
cases, with no duplicate node IDs in the Chromium matrix. Firefox still runs
the presentation display module separately. On a six-case renderer module,
sharing the browser process reduced a local invocation from 11.06 to 8.11
seconds. A separate isolation probe left a page and cookie in an open context
and verified that the next test saw neither.

The platform selection job only needs file names. A full-history sparse checkout
of its script and a regular clone returned the same 36 changed paths at
`4c5d16d`. Local Git object storage was 884 KiB versus 58,024 KiB. Both sides
of renames participate in platform selection. Replaying the selector on the
previous 100 first-parent changes broadened platform coverage on seven and
removed no previously selected platform in that sample. A scratch Docker build
confirmed that nested documentation is excluded from the shipped build context.

The protected check is **Dependency safety**. Manual dispatch exercises every
platform and can repeat the benchmark. The [CI audit PR](https://github.com/kaiketsu-labs/artifact-studio/pull/245)
records the hosted control, candidate results and any failed trials.
