# Visual review through MCP

Artifact Studio can return actual slide pixels directly through MCP. This is a local content-rendering feature, independent of a browser tab or agent harness. It does not grant access to a deck or override a host's browser, file or visual-access restrictions. If a policy has denied inspecting a deck, stop and resolve that restriction; do not call this as an alternative route.

Install the renderer once, alongside the normal `uv sync --locked` setup:

```sh
uv run playwright install chromium --only-shell
```

The Python packages are locked with the app. Chromium is a separate official Playwright download. The capture tool reports an actionable setup error if it is unavailable; it never downloads a browser automatically during a capture.

## Agent workflow

Before a capture batch, `get_capture_status(probe_renderer=True)` tests the sandboxed renderer once. The default status read is cheap and returns the last observed readiness. Launch failures are cached briefly; an explicit probe retries after setup is repaired. No renderer setting is weakened.

Call `get_working_context`, read the workspace rules, then read the deck and released feedback. Use `capture_contact_sheet(deck_id)` to scan up to 24 completed slides, including hidden ones. Follow `next_offset` until there are no pages left, or pass `slide_ids=[...]` to capture only affected slides in deck order. Offsets apply to that selected subset. Each tile has a slide number/title and a local frame path. The default `resolution="draft"` produces 800 × 450 frames; `resolution="full"` produces 1600 × 900 frames. Both preserve the same 1600 × 900 layout.

Use `capture_slide(deck_id, slide_id)` for a 1600 × 900 completed-slide image. Inspect every slide at full size; a contact sheet does not verify chart labels or small text. The tool returns an image block plus metadata, including the slide ID, content hash, rendering-dependency hash, expected revision hash, renderer/runtime hash, capture time and diagnostics. If `stale` is true, capture again before drawing conclusions about the current slide.

Single-slide captures also accept `resolution="draft"` for iteration. Parallel requests wait in a bounded first-in, first-out queue instead of failing immediately while another capture runs. `get_capture_status()` reports running/queued counts and capacity. Waiting times out after 30 seconds; a full queue or timeout returns an actionable error. Capture failures release the slot. Inspect `queue_wait_ms`, `resolution`, frame dimensions and selected-slide count in the returned manifest.

`runtime_matches_deck=false` means the deck has a different runtime; the image uses the trusted shared renderer and cannot establish fidelity to that custom runtime. Resolve this mismatch before claiming slideshow equivalence.

For staged content, call `capture_slide` with `stage=0` for the initial view, then each numbered stage. `elapsed_ms` samples time within that stage. Earlier stages are completed before the requested stage starts. For example, sample a 1200 ms bar at `stage=1, elapsed_ms=600`, then at `elapsed_ms=1300`; inspect later delayed results too. `diagnostics.reveal_steps` reports the highest supported stage. Stage values outside this slide's range fail rather than returning a misleading frame.

Review animation behavior against an explicit expectation, not merely a list of screenshots:

- Read the slide markup and source data. Identify what should appear or change at each advance and which elements should stay hidden, stable or deliberately blurred.
- Capture the initial view, every reveal at time zero, an intermediate point during motion, just before/after a material delay, and the settled result. Inspect the actual returned images at full size. Confirm reveal order, hidden content, bar/counter synchronization, target emphasis, legibility and lack of clipping or accidental flashes in sampled frames.
- Compare the settled frame with the completed capture and verify exact final values. Capture the reduced-motion variant too. Fix confirmed defects and repeat affected frames.
- Use permitted live playback for transition continuity, slide entry, replay/back navigation and interaction. Deterministic sampled frames cannot prove the absence of a transient flash between samples. The editor's Play button is a quick human preview; it is disabled when the slide has no supported animation.

Use multiple fresh-context reviewers with distinct aspects when reviewing a deck, and parallelize independent requests with clear ownership. Keep private notes outside review images. If a renderer/runtime mismatch, unsupported media or an application-access limitation prevents a check, report that specific gap rather than declaring the animation verified.

For a separately permitted normal-browser audience review, use `audience_url` from selected-deck working context or a compact creation receipt. It opens an inline sanitized audience document with notes, agent context and conversations excluded. Do not inspect the review workspace to consume feedback: it can show withheld Batch comments and unsent drafts. `/decks/<id>/index.html` is the canonical source and embeds private notes even with `?present=1`; it is not a sanitized audience surface. The HTML export offers the same sanitized document as a download. Browser audience review is distinct from production sandbox capture and is never a way around a policy denial. Neither establishes physical-display behavior.

A completed capture reveals every build and preserves authored final counter text. Reveal captures use the same shared build implementation as the slideshow, with a deterministic clock. Use `reduced_motion=true` to inspect the reduced-motion variant. CSS animations belonging to earlier builds are completed, the current build is sampled at the requested time, and slide-wide animations use the accumulated slide time; infinite animations use their initial frame in completed captures. Video/audio are paused and reported as media requiring separate playback checks.

`possible_overflow` is a geometry hint, not proof of a layout defect: intentional bleed and decorative artwork can extend past the canvas. It does not detect element-to-element overlap or all internal clipping. `failed_images` or `failed_fonts` needs investigation before calling the capture complete. Check charts against their source data as well as the rendered pixels. Review findings with multiple fresh-context agents, verify them, fix confirmed issues and recapture affected slides.

## Scope and storage

Rendering uses an isolated Chromium context with embedded local assets, no network requests and no user browser profile. It never opens the application's localhost origin. Notes, chats, agent explanations, unsent drafts and withheld Batch comments are excluded. Capture preparation is serialized. Individual local assets are limited to 25 MiB, dependencies to 64 MiB per frame, and expanded assets and prepared documents to 96 MiB per page. Repeated asset references count toward the expansion limit; reduce the page limit or resize assets when needed.

Captures do not approve slides, clear revision markers or mutate canonical deck files.

Images and `manifest.json` are saved below the local state directory's `visual-reviews/` folder. They can contain private slide content; do not upload or commit them. Inline MCP images work for clients without local filesystem access. Keep ordinary audience/export and presentation data restrictions in force.

This verifies slide content in Chromium. It does not verify presenter controls, live interaction, keyboard behavior, browser-specific layout, physical screen placement, native PowerPoint or native PDF output. Those require their own permitted application checks. Never describe a capture as a physical-display or end-to-end presentation-mode test.

## Custom interactive documents

Slides with `interactive_html` are captured from their actual isolated custom document, not their static fallback. Provide `window.artifactStudioCapture` for reproducible time/final states and `window.artifactStudioReady` when local preparation is asynchronous. The diagnostics explicitly indicate that sandbox DOM inspection is unavailable. Inspect the image itself. See [custom HTML and interaction hooks](INTERACTIVE-HTML.md).

## Optional text fidelity review

After a full-size capture, `check_slide_text` compares its pixels with caller-supplied expected visible text using optional local Tesseract OCR. The capture and image hashes anchor the result; stale or modified captures require recapture. Mismatches and confidence need visual interpretation, and even a match does not prove glyph correctness. This catches candidates beyond resource-loading checks, without disabling ligatures or approving slides. Read `get_authoring_guide(topic="text_review")` or [TEXT-REVIEW.md](TEXT-REVIEW.md).

Scoped cloud and hosted agents can also call `capture_contact_sheet`. It returns one draft image for up to eight completed slides per page, with optional `slide_ids` and `next_offset`, through the same audience-only clone as `capture_slide`. Paths and private notes are excluded. Use `capture_slide` to inspect full-size detail and reveal states. Captures share the bounded renderer queue; smaller pages can help when the remote image limit is exceeded.
