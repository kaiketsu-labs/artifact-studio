# Feature-folder migration verification

The first implemented feature folders group eleven Python design services and
adapters under `artifact_studio/designs/` and three presentation scripts under
`web/presentation/`. This is a source organization change, not a design or tool
behavior change. Storage, collaboration, sync, general editor code and most test
locations remain in their existing layout.

## Reproducible comparison

Use an unchanged baseline checkout and the candidate's locked Python environment:

```sh
uv run --no-sync python scripts/benchmark_feature_layout.py --baseline /path/to/baseline
```

The [recorded comparison](feature-folder-migration.json) identifies both commits
and hashes their Python/JavaScript/HTML source trees. It warms filesystem caches
once, then alternates five baseline/candidate pairs using a fresh Python process
for each import sample. The workload imports design authoring and element
alternatives, using the respective canonical paths.

| Check | Baseline | Candidate |
| --- | ---: | ---: |
| Presentation JavaScript bytes | 14,029 | 14,029 |
| Presentation JavaScript requests | 3 | 3 |
| Tool catalogue | 66 tools | Byte-identical |
| Existing pytest cases discovered | 1,708 | 1,708 |
| Additional compatibility cases | — | 15 |
| Median fresh-process feature import | 864.5 ms | 964.6 ms |

The import timings were collected on a shared, busy host: baseline samples range
from 517.9–939.9 ms, and candidate samples from 870.0–1,798.2 ms. These samples do
not establish either a startup speedup or a reliable regression bound. They are
not browser launch latency. The payload/request counts and catalogue equality
are exact comparisons; this refactor adds no browser asset requests or agent
tool-schema bytes. No provider billing or token reduction is inferred.

## Compatibility evidence

Historical Python imports resolve to the same live module objects as their
canonical replacements. A legacy monkeypatch to the preview size budget changes
the actual canonical preview behavior. This avoids duplicate classes or detached
module globals. Shipped resources resolve from the package-level `PROJECT_ROOT`,
and a Git source archive contains the moved modules, scripts and viewer styles.

Historical browser URLs return the canonical script bytes directly, with the
same ETag and conditional-GET behavior. The compatibility mapping covers only
three exact names; normal static path validation still rejects traversal. It adds
no redirects, symlinks, script loaders or duplicated implementations. Hosted
workspace asset handling retains its existing authentication and path checks.

All 1,708 preexisting pytest test IDs remain discoverable, with 15 new compatibility
cases. Existing Node tests still run through the unchanged `tests/*.cjs` selection.
The source distribution's recursive Docker copies include the feature folders.
No native macOS build or physical display behavior is inferred from these checks.
