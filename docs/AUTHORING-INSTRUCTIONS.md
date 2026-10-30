# Workspace rules

- Keep slides concise and human-readable. Use useful icons, diagrams, arrows and charts instead of dense text.
- Treat design examples as a visual language, not fixed layouts.
- Visually verify every slide at presentation size. Visually verify every chart, including axes, scale, units, labels, legends, clipping and plotted series against the underlying source data. Do not claim a visual check without inspecting the rendered artifact.
- Visually review every animation as well as the completed slide: inspect the initial view, each reveal, intermediate motion and the settled result at presentation size. Check order, timing, hidden elements, chart/counter synchronization, clipping and unwanted flashes. Check replay/back navigation and reduced motion where supported. Inspect actual rendered frames; report unavailable application or display checks honestly.
- Use restrained animation when a slide has many elements: reveal meaningful groups in the order of the explanation. Animate a particularly interesting chart element or result when it helps direct attention. Keep other elements stable; avoid decorative motion and excessive clicks. Do not add motion to every slide by default.
- For multiple independent requests, use subagents in parallel with clear ownership and limited context; integrate and verify their results. Keep dependent edits sequential and avoid simultaneous writes to the same slide or file.
- Use multiple fresh-context goldfish reviewers with distinct focus areas; prefer Terra. Verify findings before changing the presentation and recheck affected slides.
- Keep speaker notes brief and limited to context the audience cannot already read. Preserve human notes.
- Keep sources and routine data dates in the context/source panels. Use footnotes only when they are necessary to interpret the slide.
- Keep slide progress visible but unobtrusive. Verify presenter and audience behavior separately; do not expose private notes on the audience display.

## Scope and learning

- Explicit workspace rules are primary and apply to all presentations.
- Each presentation has its own fresh context: audience, client, content, event and intended takeaway. Keep these details with that presentation.
- Learned preferences are secondary. Apply them only when they fit both these workspace rules and the presentation's requirements.
- Learn general preferences only from explicit requests to apply them across presentations, or consistent evidence across different presentations, clients and audiences. Do not generalize a one-off content edit.
- Do not silently change or weaken explicit workspace rules through automated learning. Preserve them and ask if there is a real conflict.

## Free composition

Design examples, helper classes and animation attributes are optional hints, not fixed layouts or a feature catalogue. Compose HTML, CSS, SVG, canvas, media and custom interactions freely. Slide Studio supports self-contained custom JavaScript through `interactive_html` in an offline sandbox. Keep a meaningful static `html` fallback aligned for selection and preview; use the current authoring capability guide rather than legacy design-guide restrictions.
