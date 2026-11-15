# Offline receipt reuse

Issue [#208](https://github.com/kaiketsu-labs/artifact-studio/issues/208) reproduced
quota exhaustion from repeatedly saving an unchanged slide. A disposable
fictional deck and identity received the same 600 snapshot requests in each run.

| Result | Before | After |
| --- | ---: | ---: |
| Successful snapshot requests | 500 | 600 |
| Quota failures | 100 | 0 |
| First failed request | 501 | None |
| Retained source snapshots | 500 | 1 |
| Retained source bytes | 146,000 | 292 |
| Median request time | 35.024 ms | 12.577 ms |
| p95 request time | 93.214 ms | 48.223 ms |

The retained-source reduction is 99.8% for this repeated-save workload. Timings
are descriptive: one local run per version, no warmup, and baseline quota failures
are included. They do not establish a general latency improvement. No model was
called and no provider token or billing measurement is inferred.

The baseline was `9db1045`; the candidate was an uncommitted patch on `2141f0f`.
The [machine-readable results](offline-receipts.json) preserve those identifiers.
The benchmark script accepts either checkout, creates temporary data and never
contacts GitHub or a live collection:

```sh
uv run python scripts/benchmark_offline_receipts.py --source /path/to/baseline
uv run python scripts/benchmark_offline_receipts.py --source /path/to/candidate
```

Regression tests cover 510 concurrent repeated saves, restart reuse, 500 legacy
aliases consolidated without invalidation, an offline second device's original
edit/comment after another device changes and refreshes the slide, exact scope
and comment-anchor partitioning, atomic quota rejection, and existing revocation
and interrupted-write recovery. Independent Astra fault injection interrupted a
migration after partial copying: the old schema and every alias survived rollback,
and retry passed SQLite's integrity check.

This fixes repeated-save exhaustion and reclaims duplicate source storage. It
does **not** delete distinct historical revisions. Safe automatic cleanup requires
knowing which snapshots each disconnected device or stale browser tab still owns;
that part of #208 remains tracked separately in [offline limitations](../OFFLINE.md).
