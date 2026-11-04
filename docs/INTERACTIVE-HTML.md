# Free HTML, CSS and JavaScript

Artifact Studio is a free canvas. Layout examples, design classes, build attributes and the snippets below are suggestions, not a catalogue of permitted compositions. Invent the HTML, SVG, canvas, charts, media, animation and interactions the presentation needs. Preserve the brand language and the human's instructions.

`add_slide` and `revise_slide` accept two complementary fields:

| Field | Purpose |
| --- | --- |
| `html` | A meaningful static `<article class="slide">…</article>` representation. The editor can select elements, collect feedback and show thumbnails and next-slide previews from this version. |
| `interactive_html` | Optional self-contained HTML, including styles, scripts, event handlers and controls. Play and the current audience/presenter slide run this document in isolation. Set it to `""` to return to ordinary slide rendering. |

The presentation canvas is fixed at 16:9; its composition is free. Use `interactive_html` for a full document or fragment with arbitrary `<style>`, `<script>`, event handlers, controls, video/audio, SVG animation, `<foreignObject>` or embedded iframe `srcdoc`. These features work in presentations, not just general HTML artifacts. There is no widget, framework or layout enum. Responsive `%`, viewport units, SVG viewBox and canvas resize handlers work naturally. The static fallback and interactive document are separate representations: keep their content aligned when implementing feedback. A drag or text proposal targets the fallback and is an instruction for the agent to update both where relevant.

Ordinary `html` supports inline CSS, per-slide `<style>` selectors and `background-image:url(assets/...)` directly. Studio scopes style blocks to their slide roots; use `.slide` or `:scope` to style the root. Conditional `@media`, `@supports` and `@container` rules work. Global rules such as `@font-face`, `@import` and `@keyframes` belong in the shared theme or unrestricted `interactive_html`. The editor's static DOM remains selectable and script-free; its restrictions do not define the capabilities of the executable slide document.

## Assets and authoring

Custom documents run offline. Inline code and data-URL images/fonts/media are supported. They do not inherit the editor's DOM or CSS, cannot access workspace APIs or private notes, cannot load remote resources, and cannot navigate to another website. They run in nested `sandbox="allow-scripts"` frames with opaque origins, no same-origin permission, and restrictive resource/navigation policies. These are execution boundaries, not restrictions on visual compositions. Bundle external libraries and data locally before using them; iframe embeds that require their own remote server cannot run offline.

Prefer sending ordinary deck-local references in `interactive_html` with `embed_assets=True` to `add_slide` or `revise_slide`. The server inlines images, SVG, stylesheets, fonts and classic/bundled JavaScript while saving, and returns a compact receipt rather than echoing base64 through the model. Retrying the same revision request returns its saved result even if the original dependency has since changed or disappeared. `prepare_interactive_html(deck_id, html, include_theme=True)` remains available when you need to inspect the prepared document itself. Preparation reads dependencies only inside that deck folder and executes nothing. Stylesheet-relative paths are resolved correctly. It can include the deck's theme automatically; author styles can override it. Remote dependencies must first be downloaded as local source material. Keep module imports bundled into one inline script; runtime imports/fetches are blocked. The final document is limited to 2 MB of source, so resize large images/media or use ordinary deck assets in the static representation.

Use `list_presentation_assets` to get the actual deck inventory and its `import_directory`. Download or extract new font/image/CSS/JS/media files into that staging directory with file tools, then call `import_presentation_asset(deck_id, source_path, asset_path="assets/...")`. Selected-deck brief/assets, selected-design assets and registered reference files can also be sources; arbitrary private paths and other decks are rejected. The import returns a path/hash/size receipt, never the binary. Files may be up to 50 MB each, with a 300 MB / 5,000-file deck asset limit. `get_design_assets` finds reusable brand sources. To replace a file, pass its current `sha256` as `expected_sha256`. Copies stay specific to that deck; MCP does not fetch remote URLs.

Use `get_presentation_theme` to read author CSS and its `expected_hash`, then `update_presentation_theme(deck_id, css, expected_hash)` to save it. Studio retains `viewer.css` automatically; send the author CSS only. Theme and asset changes invalidate approvals through the shared design hash and do not alter the reusable design or other decks. Previously prepared interactive documents contain embedded snapshots: re-prepare their original local source to adopt later theme/asset changes. Keep that original source locally; avoid editing embedded base64 by hand.

Iframe `srcdoc` dependencies are prepared recursively, including its local scripts/styles/images. Nested documents share the size budget, with a maximum of six iframe levels. Use self-contained `srcdoc` for embedded content; a remote website iframe needs a running external service and is outside the offline runtime. Authored frames cannot grant themselves workspace access by adding sandbox permissions.

Read `get_slide` immediately before `revise_slide`, use its current `expected_hash` and a new request ID. Both fields are versioned; changing interactive content invalidates approval and marks the slide revised. Preserve human notes and visibility.

## Playback and review

The editor remains a still, selectable representation. Its Play button opens the actual custom document; Stop or Escape returns to editing. Current slideshow and presenter views run custom content. Thumbnails, next-slide preview and all-slides overview use the static fallback. Standalone HTML preserves the custom document and executes it in the same sandbox. PDF uses the custom document's final state, with no live interaction after export.

The optional hooks below let arbitrary custom code expose its readiness and reproducible review states. They do not impose a visual design or animation system:

```js
// Optional promise for local asynchronous preparation.
window.artifactStudioReady = Promise.resolve();

// Called for capture_slide/capture_contact_sheet and PDF rendering.
window.artifactStudioCapture = async ({mode, elapsed_ms, final, reduced_motion}) => {
  // Set the requested animation position, or a settled final state.
};
```

For example, an author could map review time to a metric animation like this; the values and element are illustrative, not a required widget:

```js
window.artifactStudioCapture = ({elapsed_ms, final}) => {
  const progress = final ? 1 : Math.min(1, elapsed_ms / 1500);
  document.getElementById('metric').textContent = Math.round(35 + 60 * progress) + '%';
};
```

This produces 35% at 0 ms, 55% at 500 ms and 95% in the final capture. Time alone does not replay arbitrary clicks, input or choices. For those interactions, implement the hook to set a meaningful deterministic review state (or map selected times to scenarios), then also test the actual controls manually.

A capture with `stage=null` requests `final=true`; otherwise `elapsed_ms` identifies an author-defined time. Custom content does not use the built-in reveal-step count. If the custom animation needs deterministic inspection, implement the capture hook rather than waiting for arbitrary timers. PDF requests `mode="print"`, `final=true`, `reduced_motion=true`. Without a hook, captures show the document once its fonts, images and optional readiness promise settle; this does not guarantee every custom animation has finished. Captures have a 15-second readiness limit. Inspect the actual returned image: automatic DOM overflow/font/image diagnostics cannot cross the sandbox, and the result explicitly reports this limitation.

Unhandled keys and blank clicks retain slideshow navigation. Text inputs and controls keep their normal behavior; custom key/click handlers can call `preventDefault()` to consume an action. Escape always returns from preview or exits presentation mode. Host messages are accepted only from the exact opaque frame and its per-mount token.

## Optional presenter/audience state

Custom instances have separate DOMs. To mirror interactive changes between the private current-slide view and the audience, implement a state setter and emit small JSON state when it changes:

```js
window.artifactStudioSetState = state => {
  // Apply state without emitting it again.
};
function userChangedSomething(state) {
  // Update this instance, then share only public presentation state.
  window.artifactStudio.emitState(state);
}
```

Artifact Studio forwards JSON state (up to 64 KiB) to the other current-slide instances and linked audience window, restores it when revisiting the slide in that presentation session, and passes it to newly opened audience windows. Do not emit from `artifactStudioSetState`, which would create an echo. This state is temporary presentation state, not persisted content or an automatic DOM mirror. Without these hooks, custom interactions remain local to each instance. Private notes and conversations are never sent into custom documents.

Always review initial, intermediate and final views at presentation size, including meaningful interactions and reduced-motion behavior. Test author-defined state synchronization when using it. Physical display placement remains a separate hardware check.
