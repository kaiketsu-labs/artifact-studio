---
name: setup
description: Set up or repair Artifact Studio, connect an agent through MCP and start an artifact project. Use for Artifact Studio onboarding or its setup command, not unrelated project setup.
---

# Set up Artifact Studio

Guide the user to a working workspace and connected agent. If the user selects an existing hosted HTTPS workspace, configure its remote MCP connection without installing a local server. For optional automatic continuation, follow [client-only hosted hooks](../../../docs/HOSTED-AGENT-HOOKS.md); ask only for missing credentials/scope and follow native hook trust. Use the local installation instructions below only for local mode. Reuse existing configuration; rerunning setup must not reset data or create duplicate apps, connections, hooks or decks.

Resolve the checkout from this file (`../../..`). Read its [getting-started guide](../../../docs/GETTING-STARTED.md) for maintained commands. If MCP is available, start with `get_working_context` and reuse its returned paths and this connection’s verified URL. Otherwise inspect only relevant local configuration and check the chosen loopback server's `/api/health`; do not infer ownership from an occupied port. Preserve collection and database paths, workspace rules, notes and review history.

Reuse the existing startup method. For a new Mac setup, default to the menu-bar app and offer login startup. When the user chooses Docker, follow the [Docker guide](../../../docs/DOCKER.md). Use the [Terminal instructions](../../../docs/GETTING-STARTED.md#run-from-terminal) for native development, troubleshooting or native setup without the Mac app. Check the chosen setup's prerequisites and install only what is missing. Ask only for choices that affect the requested setup, such as custom storage or automatic agent review.

For Mac setup, read [MACOS.md](../../../docs/MACOS.md). Reuse an installed controller; never overwrite it or kill a server it does not own. A Terminal-to-controller handover requires stopping the identified Terminal server first. Keep the checkout and `.venv` in place.

Configure this harness's MCP endpoint only if missing, preserving other settings. Follow native reload and trust requirements. Verify a real `get_working_context` call after activation; HTTP health alone does not prove the agent is connected. If a reload or human action is required, give that one next action and resume verification afterward.

For automatic presentation review, load the [live-review guide](../../../docs/LIVE-REVIEW.md). Reuse installed hooks; ask project-local versus user-level scope only if missing. Preview configuration, preserve unrelated hooks and follow host trust/reload. Then read the selected presentation's working context: trusted hooks attach automatically. Check get_live_review_status without asking the user to start review. Use start_live_review to resume after End review or explicitly select the design inbox. Hooks cannot wake a closed chat.

Once connected, continue an existing artifact or start a project from the user's brief and intended output. Pages, Markdown, images and bundles need no design pack. For a presentation, obtain its design reference, audience, takeaway and material/files; do not invent a brand or populate a demo unasked. A design library may be empty. Follow the guide's import workflow. Keep explanations short, rules shared and audience/content context scoped to its project. Do not enable GitHub sync, publish content or schedule learning as a side effect of setup.

Finish with the working workspace link, connection status and any single remaining action. Distinguish verified server health, MCP access and Live review; report incomplete checks plainly.
