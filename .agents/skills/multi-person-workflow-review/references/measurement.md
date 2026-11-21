# Measurement and report

Read existing methods before inventing new instrumentation:

- [Original multi-role study](../../../../docs/performance/presentation-workflow-study.md)
- [Fixes and matched rerun](../../../../docs/performance/workflow-iteration.md)
- [Token-efficient reading](../../../../docs/TOKEN-EFFICIENCY.md)
- [Repeatable policy benchmark](../../../../docs/performance/PRESENTATION-WORKFLOW.md)

## Instrument before starting

Record source commit, dirty diff fingerprint, fixture seed/content fingerprint,
browser/platform, models, connected local/hosted mode, workload and acceptance
checks. Preserve the baseline before fixes. Identify each run and each participant
so calls and delegated usage can be attributed without double counting.

At the real MCP boundary, record method/tool, phase, duration, request bytes,
response text bytes, serialized response bytes, image payload sizes, transport
status, JSON-RPC error and tool `isError`. Count failed attempts, retries,
initializations and schema reads. HTTP 200 alone is not tool success. Keep setup,
discovery, authoring, review, recovery and verification phases identifiable.

Record user-task wall time separately from summed tool durations; parallel agent
durations cannot be added to produce elapsed workflow time. Record idle waiting
separately. Save screenshots as image files and return paths or image blocks;
never print binary/base64 payloads into agent text logs.

Use harness/provider usage when it is available through supported interfaces:
input, output, cache and reasoning-token counts, plus provider/model and scope.
Record whether totals include the coordinator and delegated agents, and whether
cached/reasoning counts are subsets of other fields. Do not double-count them.
Aggregate reasoning-token counts are metrics, not access to private reasoning.
Unavailable fields are `null` with a reason, never zero or invented estimates.

A named tokenizer may estimate text usage if legitimately available. Label its
name/version and input scope; do not call the estimate billed usage. Do not turn
UTF-8 byte counts into token counts or infer model cost from transport savings.

For a per-skill analysis, separate the skill's metadata/body, the references
actually opened, tool catalogues, source/result text, images and delegated work.
Measure each skill file's bytes or named-tokenizer estimate as static load cost;
it is not the cost of executing the skill. List unexecuted skills explicitly.
Attribute each MCP record to a participant and phase. Keep coordinator preflight
and synthetic benchmarks out of the autonomous editor's totals, and do not infer
an agent's document reads from links that were merely present in its prompt.
Text and complete serialized MCP result sizes overlap; do not add them. Likewise,
image base64 is already included in serialized JSON. Price comparisons require
actual usage categories and dated prices for the exact provider/model.

Keep full traces local under the ignored run directory. Redact credentials,
cookies, private source paths/content, notes and personal messages before sharing.
Publish only fictional or explicitly authorized sanitized evidence. Do not scrape
hidden harness stores to obtain unavailable usage or reasoning.

## First-delivery design quality

Save the first version actually delivered to the owner. Record whether the owner accepted its visual direction at first delivery, requested a new direction, or left it unrated. Count subsequent human direction changes separately from factual corrections, legibility fixes and the agent’s own revisions before delivery. Record elapsed time to first delivery as well. When comparing multiple decks, give the counts and denominator for rated and unrated decks. An agent acting as the owner is a persona; human first-delivery acceptance requires a human rating.

## Compare equivalent work

Separate these three experiments:

1. **Autonomous usability run:** independent agents performing real tasks; useful
   for observed friction and quality, not a controlled causal speed claim.
2. **Source comparison:** the same fixture, workload, script, correctness checks
   and measurement boundaries against identified baseline and candidate code.
3. **Policy comparison:** different read/cache policies against the same code.
   Label this explicitly; it does not establish a source-level improvement.

Keep browser/cache conditions and concurrency schedules comparable. For timing
claims, use a warmup and at least three paired trials, alternate execution order,
and report all observations, median and range. Do not choose the fastest run or
claim a reliable percentile from a tiny sample. Report single runs as observations.
Reset disposable fixtures between trials; verify the same final content, notes,
hidden-slide state, revision behavior and access boundaries on both sides.

Studio already has a [file-input CLI](../../../../docs/FILE-CLIENT.md). Test it
before proposing another authoring interface: prepared JSON can contain arbitrary
HTML/CSS/SVG/JavaScript and uses the same tools and guards. It saves repeated
source transmission only when files stay outside model-visible output; initial
authoring and actual visual inspection still cost context. Batch independent,
already-reviewed writes and stop on errors. Do not combine dependent writes or
refresh stale hashes to manufacture a smaller or successful trace. The local
client accepts loopback HTTP only; a hosted simulation must use its authorized
remote MCP connection. Keep that limitation explicit rather than bypass it.

The existing scripted policy benchmark can supplement the autonomous run:

```sh
UV_CACHE_DIR=/tmp/artifact-uv-cache uv run --no-sync python -m scripts.benchmark_presentation_workflow \
  --warmups 1 --repeats 3 --output test-results/workflow-review/policy-benchmark.json
```

Run from the repository root with its locked dependencies. Follow the linked
benchmark's limitations; it does not execute the three personas or measure model
inference. Choose a unique output path per run. Do not modify production browser
isolation, disable revision/access guards or bypass environment restrictions to
get a passing measurement. Label test-only adapters and fault injection.

## Required report

Write a local `report.md` and `metrics.json`, plus referenced image files. Use
explicit units and retain raw aggregate trial results; no mandatory report needs
to contain private transcript text.

The report should include:

- **Scope:** task, models/personas, independent sessions, versions, environment,
  fixture, acceptance criteria and omitted coverage.
- **Findings:** severity, reproduction, expected/observed behavior, evidence,
  responsible layer, fix commit and rerun outcome. Separate confirmed defects
  from hypotheses, agent mistakes and infrastructure limits.
- **Visible decisions/workarounds:** action and concise explanation, unnecessary
  reads/restarts, schema guesses, repeated source echo, manual reloads, unsafe
  shortcuts attempted, and whether a fix removed the need. No chain-of-thought.
- **Visual evidence:** comparable before/after screenshots with viewport, theme,
  revision and described state. Screenshots support actual interaction checks.
- **Measurements:** table by run/phase/role, units, actual usage versus estimates
  versus bytes, unavailable values, sample count and methodological limits.
- **Outcome:** content and safety invariants, original-persona and fresh-reviewer
  reruns, relevant tests, remaining blockers and the next highest-value improvement.

Suggested top-level `metrics.json` keys: `run_id`, `source`, `environment`,
`scenario`, `participants`, `measurement_scope`, `trials`, `provider_usage`,
`token_estimates`, `transport`, `correctness`, `limitations`, `evidence`.
Use explicit field names such as `response_text_utf8_bytes` and `elapsed_ms`.
List successful and failed assertions individually; an unavailable check is not a
pass. Do not declare token or performance gains when correctness became weaker.
