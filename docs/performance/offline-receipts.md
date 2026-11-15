# Offline receipt reuse

Issue [#208](https://github.com/kaiketsu-labs/artifact-studio/issues/208) reproduced
quota exhaustion from repeatedly saving an unchanged slide. A disposable
fictional deck and identity received the same 600 snapshot requests in each run.

| Result | Before | Normalized legacy receipts | New signed receipts |
| --- | ---: | ---: | ---: |
| Successful snapshot requests | 500 | 600 | 600 |
| Quota failures | 100 | 0 | 0 |
| First failed request | 501 | None | None |
| Retained source snapshots | 500 | 1 | 0 |
| Retained source bytes | 146,000 | 292 | 0 |
| Median request time | 35.024 ms | 12.577 ms | 21.448 ms |
| p95 request time | 93.214 ms | 48.223 ms | 63.505 ms |

The retained-source reduction is 99.8% for this repeated-save workload. Timings
are descriptive: one local run per version, no warmup, and baseline quota failures
are included. They do not establish a general latency improvement. No model was
called and no provider token or billing measurement is inferred.

The baseline was `9db1045`; the normalized candidate was an uncommitted patch on
`2141f0f`. Signed receipts were measured on the combined `1860c54` tree with the
benchmark option uncommitted. These were sequential local runs under shared load.
The [machine-readable results](offline-receipts.json) preserve those identifiers.
The benchmark script accepts either checkout, creates temporary data and never
contacts GitHub or a live collection:

```sh
uv run python scripts/benchmark_offline_receipts.py --source /path/to/baseline
uv run python scripts/benchmark_offline_receipts.py --source /path/to/candidate
uv run python scripts/benchmark_offline_receipts.py --source /path/to/candidate --receipt-version 2
```

Regression tests cover 510 concurrent repeated saves, restart reuse, 500 legacy
aliases consolidated without invalidation, an offline second device's original
edit/comment after another device changes and refreshes the slide, exact scope
and comment-anchor partitioning, atomic quota rejection, and existing revocation
and interrupted-write recovery. Independent Astra fault injection interrupted a
migration after partial copying: the old schema and every alias survived rollback,
and retry passed SQLite's integrity check.

The combined fix uses signed receipts for new browser saves, avoiding retained
server source snapshots entirely. Each delivery still verifies current access and
the signed original HTML digest. Another device's refresh cannot delete that
anchor. Edit delivery includes the original HTML as well as the draft (each is
bounded to 150,000 characters); a worst-case 80-character slide ID produces a
794-byte receipt. This is a storage/reliability tradeoff, not a reduction in every
request payload. Completed edit results remain stored for idempotent retry while
their recovery source is reclaimed; total server storage is not zero.

Legacy duplicate blobs are consolidated, but distinct legacy revisions remain
retained because their disconnected owners are unknown. A regression deliberately
fails signing-key validation after migration and verifies the original legacy
table and all 500 aliases survive rollback. See [offline limitations](../OFFLINE.md).
