# Workspace recovery

Use **Restore workspace** to review a backup and restore its files into a new sibling folder. The running workspace and its database stay active. Opening the recovered folder does not switch server configuration. To run the recovered workspace later, explicitly configure its collection and database paths.

Two sources have different recovery guarantees:

| Source | What returns | What cannot return |
| --- | --- | --- |
| Connected backup repository or local snapshot | Projects and briefs, folder hierarchy and sibling order, current artifact files, current artifact registry, legacy presentations and portable memberships/archived visibility/default design | Private comments, approvals, connected sessions, earlier artifact revisions, private attachments excluded from the snapshot |
| Downloaded local full workspace archive | Collection files, retained artifact revisions, database review history and drafts, approvals, navigation, and app-owned attachments both inside and outside the collection | Host credentials, Git configuration, reference-library checkouts, upload opt-ins and connected agent sessions |

A current-source restore keeps the original artifact ID, project membership, physical `storage_project_id`, revision number and content identity. It reconstructs one immutable snapshot for that current revision and clears approval/seen state. It does not create missing earlier revisions. Legacy deck source files are recovered in place within the new collection. Snapshots made before portable recovery metadata existed cannot recover legacy memberships or archived visibility; preview explains that limitation.

Full archives are local files, never a new GitHub payload. They include private review material. Keep them in a suitable local backup location. The HTTP upload path accepts archives below 2 GB; the backend archive format permits up to 20 GB expanded, 100,000 files and 2 GB per individual source file. Export/download streams to disk and does not base64-encode the workspace. SHA-256 objects are deduplicated across working files, revisions and attachments.

## In the app

Open the storage icon, then **Save workspace** for a local full archive or **Restore workspace** to choose an archive or connected backup. Choose a new folder name, review the project/artifact/folder counts and destination, and click **Restore files**. Existing folders are refused. Recovery does not switch the Mac app or stop the running server.

The recovered folder includes its own `.artifact-studio/review.sqlite3`. To run that workspace later, select its collection folder and database through setup, or start a separate instance with `ARTIFACT_STUDIO_COLLECTION` and `ARTIFACT_STUDIO_DATABASE` pointing to those paths. Repository connections and upload permissions must be set up explicitly again.

## Version timeline

The history icon above a slide or artifact opens its retained versions as small visual previews. Click a thumbnail or use ↑/↓ to select a version and see its larger preview. **Content changes** shows the newest save in each consecutive identical-content run. **All saves** also shows internal-title/source-only revisions and repeated restores, labeled **Same content**. No saved version is deleted, and a return to earlier content (A → B → A) remains a separate step. HTML, authored JavaScript, interactions and artifact dependencies count as content; formatting changes are retained because they can affect layout or behavior. Private notes/visibility do not create extra visual revisions. Saves within three minutes are grouped, with only the latest version shown initially. Expand the earlier-versions row to pick another save; arrow keys move between the visible versions. The history explains how many saves with unchanged content are hidden and why source or animation changes may look identical in a still preview. **Restore version** creates a new revision with a concurrency check, clears approval, and preserves current human speaker notes, slide visibility/order and artifact project context.

History pages return metadata only. Thumbnails are requested separately as they approach the visible area, one at a time; only the selected version has a live preview frame. The private `history-thumbnails` cache beside the database retains at most 256 small PNGs, each no larger than 400 × 250 pixels. The dialog retains at most 64 image URLs and releases them on close. These disposable caches do not alter retained source or approvals. Content signatures are cached as small hashes; historical slide source is read once to index older saves, then filtering and pagination use the metadata index.

Bundle revisions retain exact local file bytes. Unchanged files can share verified immutable storage without linking mutable working files. Historical slide sources reuse current shared theme files/assets; the preview explains this fidelity limit. Old saves without timestamps remain labeled earlier saves. No history is fabricated for periods before local retention or for revisions absent from a current-only backup.

## Backend interface

`artifact_studio.workspace_recovery.WorkspaceRecovery` receives the destination `Store` for preview/apply, or the current `Store` for export/portable metadata.

```python
recovery = WorkspaceRecovery(destination_store)
preview = recovery.preview(source_path, mode="current")
result = recovery.restore(source_path, preview.content_hash, mode="current")

preview = recovery.preview(archive_path, mode="archive")
result = recovery.restore(archive_path, preview.content_hash, mode="archive")

summary = WorkspaceRecovery(current_store).export_archive(new_local_zip_path)
```

`RecoveryPreview` exposes `mode`, `source`, `content_hash`, `projects`, `artifacts`, `presentations`, `folders`, `files`, `bytes`, `conflicts`, `warnings` and `can_restore`. `RecoveryResult` adds `restored=True`. `ArchiveSummary` returns `path`, `sha256`, `files`, `objects` and expanded `bytes`.

Preview validates the entire source and binds its token to the source bytes and the destination's database/empty-state identity. Apply repeats validation, stages all files and database changes in temporary local storage, and checks the token again before publication. Destination files are created exclusively; no existing workspace files are overwritten. A destination containing user files, review rows or private attachments is a conflict. The intended destination is a new, unused folder with a fresh initialized `Store`, including when its database is nested inside the collection.

Archive objects are streamed in 1 MB chunks and verified by declared size and SHA-256. Paths are normalized and reject traversal, links, case-insensitive file collisions, file/directory collisions, unknown state destinations and unlisted ZIP entries. Archive databases reject views and unknown triggers. The exact built-in history triggers are allowlisted, removed before sanitization and recreated from trusted application code. SQLite's online backup API captures the database while an immediate transaction serializes application edits. Source files that change during copying abort the export. Direct edits outside the application should be paused during backup; no filesystem snapshot mechanism can make those edits transactional with SQLite.

Restoration rebases registry and attachment path fields from the old collection/state roots to the destination. Authored artifact files retain their exact bytes and content hashes, even when user-authored JSON contains an old path. File modes and nanosecond modification times are retained; legacy slide approval identities depend on asset timestamps. Host sessions, library registrations and upload permissions are reset. Reference-library source data outside the collection remains external and must be reconnected explicitly.

The full archive stores the review database and `feedback-files`/`design-inbox` directories beside it. Artifact feedback stored under the collection's `.state/artifact-feedback` is included as collection data. Other state caches, Git internals, `.ssh`, `.aws`, and conventional credential files are excluded. Owned artifact source files retain their full bundle, including arbitrary file extensions. Archives do not include arbitrary files elsewhere on the host. Generated `node_modules` and `.venv` directories outside owned artifact bundles are omitted; the authoring source and dependency manifests remain available to rebuild them.

## Portable current-source metadata

Before collecting a complete current snapshot, call:

```python
WorkspaceRecovery(current_store).write_portable_metadata()
```

The method synchronizes saved owned-artifact edits and atomically writes `workspace-recovery.json` at the collection root. `portable_metadata()` returns the same JSON as a string for integrations that already manage their own write. This metadata includes only the source root, legacy artifact-to-project memberships, archived deck/design visibility, and the default design. It contains no comments, approvals, review sessions, attachments or repository upload settings.

Continue including `project-tree.json`, `projects/*/project.json`, project `README.md`, `projects/*/artifacts/*/artifact.json` and the complete current `files/` trees. Current snapshots exclude `revisions/` and private `.state` data by policy. A filtered snapshot that omitted a declared dependency cannot pass complete-artifact recovery verification. Missing manifests, changed artifact bytes or unresolved project memberships are reported rather than silently inventing registry data.

## Validation

`tests/test_workspace_recovery.py` uses fictional temporary collections. It covers fresh registry recovery, moved artifact storage, current-only history, navigation order, legacy memberships, archived visibility, old-snapshot warnings, stale preview tokens, existing-workspace refusal, local full-state recovery, private draft attachments inside/outside the collection, complete retained revisions, approval preservation, timestamp preservation, path rebasing, source JSON byte identity, unknown triggers and malformed/tampered archives. Tests never open or modify the real presentation collection.
