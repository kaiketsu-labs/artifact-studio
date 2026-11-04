# Working on Artifact Studio

- Read the project structure and relevant implementation before editing. Reuse existing controllers and API contracts; keep changes small and typed.
- Use `uv run` for Python tooling. Run Ruff, strict basedpyright, relevant pytest journeys and frontend behavior tests. See [README.md](README.md) for commands.
- Review changed UI in the rendered app, using fictional fixtures. Exercise the actual mouse and keyboard flow, multi-selection, light/dark appearance, narrow windows and menu placement where relevant. Verify reviewer findings before fixing them. Screenshots and unit tests alone do not establish interaction behavior; state unavailable native-app or physical-display checks honestly.
- After UI changes, use several fresh-context `gpt-6.1-terra` reviewers as real users, each clicking through a distinct journey. Verify findings, fix confirmed issues and repeat affected reviews. Do not use Luna for review passes.
- Refresh affected documentation and screenshots when behavior changes. Keep agent bootstrap text short; put detail in the linked, on-demand guides. [agent/README.md](agent/README.md) maps the agent-facing contract; registered tool schemas remain authoritative.
- Preserve free HTML/CSS/SVG/JavaScript composition, original revision anchors, human notes and unreleased feedback. UI editing helpers are conveniences, not an authoring catalogue.
- Keep the source branch current with GitHub. Extend an existing relevant PR, resolve conflicts, verify the result, then commit and push completed source changes. Preserve unrelated local work and check the remote before pushing. This does not authorize publishing collection data, private state, transcripts or backup archives; documentation fixtures must be fictional.

Workspace and presentation instructions belong to the selected collection and retain their own authority. This file governs development of the tool.
