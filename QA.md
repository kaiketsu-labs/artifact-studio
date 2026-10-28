# Verification

The automated suite covers slide creation with a brief, free composition, concurrency, idempotency, exact-content approvals, notes, durable comments, live/batch delivery, design imports and stateless MCP calls.

Library checks use real disposable Git repositories to exercise initial upload, repeat sync without duplicate commits, one-deck updates, remote conflicts, private-only destinations, reference-only permissions, original asset discovery, download boundaries and concurrent-operation locking. Test transport substitutes local repositories for GitHub; a separate real private-repository smoke test is recorded after publication.

Strict basedpyright, Ruff and JavaScript syntax checks are run before publication. Test designs are generated fictional fixtures rather than corporate templates.

Earlier browser checks covered creating a deck with context, preserving the selected design and retrying a failed stylesheet load. A saved browser-origin permission currently prevents a fresh visual check of the latest library dialog and current presentation. No workaround is used. Actual two-display placement also remains unverified without available hardware. Automated checks do not replace these visual/application checks.

## Repository release check · 28 October 2026

26 Python behavioral tests and 4 Node frontend/runtime tests passed; strict basedpyright and Ruff passed. JavaScript syntax checks passed. The live stateless MCP endpoint registered an authenticated private GitHub library, discovered the source master and all 158 extracted media files, and uploaded 498 collection files. A second sync returned the same remote commit with zero changed files. The interrupted-source-copy case is now covered by a regression test and leaves a clean checkout.

The application tree was checked separately for corporate template directories, PowerPoint files, brand font assets and personal deck source. The app ships without them. Fresh installation and saved per-workspace default-design behavior are covered by tests.
