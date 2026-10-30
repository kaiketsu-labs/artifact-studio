# Visual review through MCP

Slide Studio can return actual slide pixels directly through MCP. This is a local content-rendering feature, independent of a browser tab or agent harness. It does not grant access to a deck or override a host's browser, file or visual-access restrictions. If a policy has denied inspecting a deck, stop and resolve that restriction; do not call this as an alternative route.

Install the renderer once, alongside the normal `uv sync --locked` setup:

```sh
uv run playwright install chromium --only-shell
```

The Python packages are locked with the app. Chromium is a separate official Playwright download. The capture tool reports an actionable setup error if it is unavailable; it never downloads a browser automatically during a capture.

## Agent workflow

Call `get_working_context`, read the workspace rules, then read the deck and released feedback. Use `capture_contact_sheet(deck_id, offset=0, limit=12)` to scan completed slides, including hidden ones. Follow `next_offset` until there are no pages left. Each tile has a slide number/title; full-size frame paths are retained locally.

Use `capture_slide(deck_id, slide_id)` for a 1600 × 900 completed-slide image. Inspect every slide at full size; a contact sheet does not verify chart labels or small text. The tool returns an image block plus metadata, including the slide ID, content hash, rendering-dependency hash, expected revision hash, renderer/runtime hash, capture time and diagnostics. If `stale` is true, capture again before drawing conclusions about the current slide.

`runtime_matches_deck=false` means the deck has a different runtime; the image uses the trusted shared renderer and cannot establish fidelity to that custom runtime. Resolve this mismatch before claiming slideshow equivalence.

For staged content, call `capture_slide` with `stage=0` for the initial view, then each numbered stage. `elapsed_ms` samples time within that stage. Earlier stages are completed before the requested stage starts. For example, sample a 1200 ms bar at `stage=1, elapsed_ms=600`, then at `elapsed_ms=1300`; inspect later delayed results too. `diagnostics.reveal_steps` reports the highest supported stage. Stage values outside this slide's range fail rather than returning a misleading frame.

A completed capture reveals every build and preserves authored final counter text. Reveal captures use the same shared build implementation as the slideshow, with a deterministic clock. Use `reduced_motion=true` to inspect the reduced-motion variant. CSS animations belonging to earlier builds are completed, the current build is sampled at the requested time, and slide-wide animations use the accumulated slide time; infinite animations use their initial frame in completed captures. Video/audio are paused and reported as media requiring separate playback checks.

`possible_overflow` is a geometry hint, not proof of a layout defect: intentional bleed and decorative artwork can extend past the canvas. It does not detect element-to-element overlap or all internal clipping. `failed_images` or `failed_fonts` needs investigation before calling the capture complete. Check charts against their source data as well as the rendered pixels. Review findings with multiple fresh-context agents, verify them, fix confirmed issues and recapture affected slides.

## Scope and storage

Rendering uses an isolated Chromium context with embedded local assets, no network requests and no user browser profile. It never opens the application's localhost origin. Notes, chats, agent explanations, unsent drafts and withheld Batch comments are excluded. Capture preparation is serialized. Individual local assets are limited to 25 MiB, dependencies to 64 MiB per frame, and expanded assets and prepared documents to 96 MiB per page. Repeated asset references count toward the expansion limit; reduce the page limit or resize assets when needed.

Captures do not approve slides, clear revision markers or mutate canonical deck files.

Images and `manifest.json` are saved below the local state directory's `visual-reviews/` folder. They can contain private slide content; do not upload or commit them. Inline MCP images work for clients without local filesystem access. Keep ordinary audience/export and presentation data restrictions in force.

This verifies slide content in Chromium. It does not verify presenter controls, live interaction, keyboard behavior, browser-specific layout, physical screen placement, native PowerPoint or native PDF output. Those require their own permitted application checks. Never describe a capture as a physical-display or end-to-end presentation-mode test.
