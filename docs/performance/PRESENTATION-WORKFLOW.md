# Repeatable presentation workflow benchmark

Run from the repository root:

```sh
UV_CACHE_DIR=/tmp/artifact-uv-cache uv run python -m scripts.benchmark_presentation_workflow \
  --warmups 1 --repeats 3 --output /tmp/presentation-workflow.json
```

The checked-in [raw results](presentation-workflow.json) record the Python, MCP and Playwright versions and operating system. Each trial launches the real application and negotiates real MCP against a new temporary collection/database. Server startup, fixture creation and MCP initialization are outside the measured workflow. One warmup per policy is discarded; three measured pairs alternate baseline/efficient ordering. No personal collections or external model providers are accessed. The script extends the real-server measurement approach in `scripts/measure_agent_io.py`; it does not mock the MCP or storage boundaries.

The fixture contains twelve fictional slides, including ten evidence slides with authored 24-row HTML tables, twenty unrelated decks and twelve released review comments. Both policies load the brief and complete editable source, inspect the same feedback, encounter the same concurrent human notes update, reject a stale revision, reread and reconcile, save the same title/HTML patch, then validate final source and outline. Assertions verify the stale response describes a conflict, no stale HTML/title is written, notes survive reconciliation, all distinct feedback is received, the efficient cursor poll returns no duplicate comments, and every untouched slide is byte-identical. The final edited title, HTML and notes must agree across policies and repeated trials.

The **baseline is an intentionally redundant policy on the same current application**, not an earlier release: it requests discovery, repeats rules/guides and feedback, fetches the entire presentation before the same source read, and requests a full mutation response. The efficient policy retains exact hashes, uses a feedback cursor, scopes reads and accepts a mutation receipt. Both execute the same edit, concurrency recovery and final verification; neither skips source, brief or correctness checks. These timings measure scripted local tool operations, not autonomous agent quality or model inference. This fixture complements the separate multi-role workflow study; it is not a transcript of independent human behavior.

Counts are UTF-8 bytes of compact JSON request parameters, text blocks, and complete serialized MCP results, respectively. Text and serialized result counts overlap and must never be added: MCP may carry both a text and structured representation. Counts exclude HTTP headers/framing, initialization, prompts, host formatting and provider caches. The catalog is measured once per trial, separately from workflow totals; hosts may select/cache schemas differently. Aggregate workflow elapsed time includes local processing between calls and the separate catalog read; individual call elapsed times are also recorded. Server startup is excluded. Millisecond differences on this shared runner are descriptive, not a latency guarantee.

Optional named estimates are supported with `--tokenizer o200k_base` and an installed `tiktoken` package. For example, `uv run --no-sync --with tiktoken==0.12.0 python -m scripts.benchmark_presentation_workflow --tokenizer o200k_base`. They count request JSON and response text independently; catalog JSON estimates remain separate. These would be tokenizer estimates, not billed tokens. In this environment the package downloaded, but its official encoding resource at `https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken` was denied by the network proxy with HTTP 403. The recorded run therefore has no token estimate; no substitute encoding or bytes-to-token conversion was used.

Provider input/output, cache usage, reasoning and image tokens, model inference latency, and rendering/capture metrics are unavailable in this benchmark. It does not send image base64 into a text tokenizer. The separate actual editing/browser study supplies visual observations; no visual correctness claim follows from these byte savings.

Run the opt-in regression with `STUDIO_BENCHMARK_TEST=1 UV_CACHE_DIR=/tmp/artifact-uv-cache uv run pytest tests/test_workflow_benchmark.py`. It executes one real paired workflow; normal unit suites skip local server startup. The existing CI agent job also runs this test with `ARTIFACT_STUDIO_AGENT_TESTS=1`; repeated timing trials remain manual. The private-note marker in raw results is a fixed fictional integration-test sentinel, never a user's notes. Source fingerprints in the result identify the benchmark and loaded guide documents.

Recorded server source was commit `0de54b953608f661262d541c030b4a6d7b07a015`; product Python and `uv.lock` were unchanged. Benchmark, tests and documentation were uncommitted working-tree additions/edits, identified by the recorded hashes.

| Measured workflow | Redundant baseline | Efficient |
| --- | ---: | ---: |
| Request parameter UTF-8 bytes | 2,274 | 2,895 |
| Response text UTF-8 bytes | 166,296 | 57,442 |
| Serialized MCP result UTF-8 bytes | 324,527 | 113,253 |
| Median local workflow elapsed (ms) | 510.422 | 514.699 |

The separate tool catalog was 147,956 serialized JSON bytes per trial. Response payloads decreased, while this shared-runner trial did not demonstrate a latency improvement. Request parameters increased because exact retention hashes are sent explicitly. Raw per-call/trial timings remain in the report for inspection.

Workflow ranges in the final three measured trials: baseline 405.134–553.732 ms; individual calls 9.822–94.088 ms; efficient 380.524–791.417 ms; individual calls 10.594–81.593 ms. No p95 is claimed from three trials.
