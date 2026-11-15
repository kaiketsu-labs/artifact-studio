# Presentation startup and background work

Present mounts the currently visible slide. The presenter panes and thumbnail
strip are created when presenter view opens; the overview grid is created when
you open All slides. Hidden panes do not keep another interactive slide running.
Interactive state is retained when switching between views.

## Idle runtime warmup

The local editor can warm the HTTP cache for `presentation.js`, `icons.js` and
`slideshow.css` after eight seconds without interaction and a browser idle
callback. This fetches static bytes without opening a presentation, executing
its scripts, mounting slides or loading their images.

- At most three requests per editor tab, sequentially and at low fetch priority.
- At most 128 KiB per file; unknown-size, oversized and `no-store` responses are
  not consumed. The current runtime files total roughly 72 KiB.
- Input, scrolling, a hidden tab, losing focus or starting Present cancels pending
  work. Focused text fields and interactive frames, busy editing/recording, offline
  mode, Save-Data, slow mobile connections, and devices reporting two or fewer
  cores or 2 GiB memory suppress it.
- Browsers without idle-callback support and hosted sessions skip warmup. Hosted
  `no-store` responses and private presentation documents remain uncached.
- Runtime files still revalidate on launch. An unchanged installed file returns
  HTTP 304; an update returns new bytes. Serving these public runtime files checks
  that the deck exists without reading and parsing the presentation source.

This is optional preparation, not a guarantee that Present starts instantly.
Saving pending notes, generating hosted documents, rendering the visible content,
and loading its assets can still take time. Fetch priority is a browser hint;
the request count, size, cancellation and idle scheduling provide the other limits.

## Reproduce the browser comparison

```sh
git fetch origin tag v0.8.0
git show v0.8.0:runtime/presentation.js > /tmp/presentation-v0.8.0.js
uv run python -m scripts.benchmark_presentation_start \
  --baseline-runtime /tmp/presentation-v0.8.0.js --runs 5
```

The benchmark starts an isolated real server with a fictional 40-slide deck,
including 38 local SVG images and substantial slide markup. Each sample uses a
fresh Chromium context at 1440 × 900. Both runtime scripts are supplied through
the same Playwright interception; this isolates rendering work but disables HTTP
caching, so these timing measurements do not measure idle warmup. It records the
time from clicking Present to a visible first heading, viewer DOM nodes, and
requests before opening the overview, then checks Enter and the 40-slide overview.

For an explicitly selected system Chromium, pass
`--browser-executable /usr/bin/chromium`. That test-only option uses `--no-sandbox`;
the production renderer configuration is unchanged. Native app and physical
display timing are outside this benchmark.

The [recorded five-run comparison](presentation-startup.json) used Chromium
155.0.8059.79 on the cloud review machine, with baseline commit `e987622`
(the v0.8.0 release commit). Exact runtime hashes and every sample are in the JSON.

| Measurement | v0.8.0 runtime | Updated runtime |
| --- | ---: | ---: |
| Median click to visible first slide | 459.4 ms | 307.4 ms |
| Viewer DOM descendants after startup | 11,632 | 190 |
| Initial requests | 44 | 6 |
| Offscreen fixture image requests | 38 | 0 |

This synthetic fixture showed roughly 33% shorter median startup, with noisy
timings. It is not a prediction for other decks, machines or hosted connections.
The request and DOM reductions were consistent in all five samples. A separate
unintercepted browser check confirmed that warmed runtime JavaScript revalidated
with HTTP 304 and did not retransmit its response bodies. No presentation iframe
or interactive content was started by the idle warmup.

## Other verified reductions

Version history aborts an active thumbnail fetch when its dialog closes or the
tab becomes hidden, and resumes visible thumbnails on return. Cancellation does
not become a cached failure. Existing revision and permission checks still apply.

Selecting another slide preserves unchanged section title/count text nodes.
A two-section fixture previously produced eight redundant mutations per selection;
the regression now records zero. Section names, counts, questions and selection
still update when their values change.

The library cover loader already limits concurrency, observes visible cards,
cancels unwanted work, and pauses hidden tabs; those safeguards remain in place.
