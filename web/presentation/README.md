# Presentation controls

- `launcher.js` owns entering/exiting the embedded audience view and native
  fullscreen. `create()` binds the editor controls once; `start()` opens a show,
  and `isOpen()` reports the current lifecycle. Return handling restores editor
  focus and the audience's final slide.
- `warmup.js` owns bounded passive runtime prefetch. `update(href)` selects the
  current deck and `cancel()` aborts background work. Its existing page lifecycle
  listeners pause work on input, focus/visibility loss and navigation.
- `library-actions.js` owns presentation archive and restore controls through
  the existing `SlidePresentations.create()` API.

These are classic scripts with the same browser globals and Node exports as
before. `web/index.html` loads them in the original order. Historical flat URLs
resolve to the same bytes through `artifact_studio.web_assets`; do not add copied
implementations or symlinks. Fullscreen/loading styles remain in `web/style.css`.
The standalone, exportable viewer remains under `runtime/`.
