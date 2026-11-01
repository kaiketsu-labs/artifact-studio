---
name: setup
description: Set up or repair a local Slide Studio installation, MCP connection and first-deck workflow. Use for Slide Studio onboarding or its setup command, not unrelated project setup.
---

# Set up Slide Studio

Guide the user to a working local workspace and connected agent. Reuse existing configuration; rerunning setup must not reset data or create duplicate apps, connections, hooks or decks.

Resolve the checkout from this file (`../../..`). Read its [getting-started guide](../../../docs/GETTING-STARTED.md) for maintained commands. If MCP is available, start with `get_working_context` and reuse its returned paths and this connection’s verified URL. Otherwise inspect only relevant local configuration and check the chosen loopback server's `/api/health`; do not infer ownership from an occupied port. Preserve collection and database paths, workspace rules, notes and review history.

Ask only for missing choices, together: startup method, non-default storage, and whether automatic agent review is wanted. On a new Mac, offer the menu-bar app with optional login startup; otherwise use Terminal. Honour existing choices. Check Git, uv, the locked Python environment and the review browser; install only missing prerequisites through the guide. Do not invent npm/PyPI installation commands.

For Mac setup, read [MACOS.md](../../../docs/MACOS.md). Reuse an installed controller; never overwrite it or kill a server it does not own. A Terminal-to-controller handover requires stopping the identified Terminal server first. Keep the checkout and `.venv` in place.

Configure this harness's MCP endpoint only if missing, preserving other settings. Follow native reload and trust requirements. Verify a real `get_working_context` call after activation; HTTP health alone does not prove the agent is connected. If a reload or human action is required, give that one next action and resume verification afterward.

For requested Live review, load only the [live-review guide](../../../docs/LIVE-REVIEW.md), or its MCP authoring-guide topic. Ask project-local versus user-level scope if not already chosen; default to project-local when no broader scope is requested. Preview configuration, preserve unrelated hooks, follow host trust/reload, then explicitly start review for the intended deck or design inbox. Do not claim Live availability without checking it; hooks cannot wake a closed chat.

Once connected, offer to continue an existing deck or create the first one. For a new deck, obtain its design reference and brief (audience, takeaway and key material/files); do not invent a brand or populate a demo unasked. A design library may be empty. Follow the guide's import workflow and use the copied deck prompt to continue authoring. Keep explanations short, rules shared and audience/content context scoped to that deck. Do not enable GitHub sync, publish content or schedule learning as a side effect of setup.

Finish with the working workspace link, connection status and any single remaining action. Distinguish verified server health, MCP access and Live review; report incomplete checks plainly.
