# Local saving, reference libraries and GitHub backup

The **storage icon in the library or beside Menu in the slide editor** opens **Storage and backup**; **Menu → Reference libraries** opens the same dialog. It shows the actual working-folder path, a copy-path control, local save status, connected references and GitHub backup outcomes. Connections default to **reference only**. Agents can inspect earlier decks, brand masters, assets and writing tone without uploading your workspace. Public repositories may be references; uploads require a private repository. Local reference folders remain read-only and their latest files are read directly.

## Local saving and handoff

Slides live in the working folder. Speaker notes save automatically after about 700 ms without typing, on leaving the field/slide, and before project save or presentation startup. **Saved** confirms the canonical file write; **Saving…** means it is pending. Failed requests retain a browser draft. Retry after reconnecting; a restored draft also resumes saving when its baseline is still valid. A concurrent edit opens **Review changes**, with both texts and an explicit choice. Revision checks protect older requests, including when text was changed and then reverted. Notes do not change slide approval.

The storage icon remains visible while the dialog is closed. A pending or failed note save takes priority over an earlier successful backup. GitHub's last successful backup and timestamp describe confirmed delivery; subsequent remote edits are checked at the next delivery, rather than continuously merged into local files.

To continue on another laptop or Codex environment, **Save project** and **Open project** there. The archive carries notes, chats, approvals and browser drafts. GitHub backups contain current artifact/project files, saved presentation files and references, while the review database and browser drafts stay on the original device. Refreshing a GitHub reference updates its cache; applying remote content to the working collection remains an explicit action. This feature does not provide bidirectional workspace sync.

## Automatic sync

Check **Back up automatically** when connecting a GitHub repository, or **Back up all artifacts** beside an existing connection. This explicitly includes **all saved artifacts in this workspace**, including the initial saved collection, notes and design references. Existing connections are never opted in automatically. The repository must already exist, and GitHub CLI must be authenticated with `gh auth login` and `gh auth setup-git`.

While the local server runs, it checks saved files and groups changes after eight quiet seconds. Closing a browser tab does not stop delivery; stopping Artifact Studio does. The next launch resumes from durable local checkpoints. Unsaved editor text is not a file change. Turning the setting off returns the repository to reference only. If a library operation is already running, wait for it to finish before changing the setting; orderly shutdown waits for that operation too.

The destination shows waiting, backing up, backed up or an actionable error. The header icon also reports pending delivery and failures. A delivery failure pauses that destination until you use **Retry GitHub backup**. Further edits do not silently dismiss the error. Normal file replacements/removals are re-observed and retried after the quiet interval. Reverted edits clear a pending marker when the saved tree matches the confirmed backup. There is no browser-tab or active-agent requirement.

Automatic sync also removes files previously managed by Artifact Studio when they are removed locally, but only if their remote hash still matches the last delivered version. Git history retains the earlier versions. Independently changed remote files cause a conflict; unrelated repository files are preserved. This is one-way sync, never a remote overwrite of local working files.

## Included files

Snapshots include current owned artifact bundles of any file type, project briefs, current artifact/project manifests, existing HTML decks, supplied PPTX masters, design packs, documents and asset folders. Legacy collection references use the extension allowlist in `artifact_studio/libraries.py`. Current owned bundle files are not restricted to those extensions. Files at or above 100 MB require smaller media; Git LFS is not managed here. Symbolic links, dotfiles, review databases, dependency/reference caches and known credential files are excluded. Slides’ embedded notes and source/context documents are included. Review chats, approval state, private delivery cursors and immutable artifact revision directories are not uploaded. Previously delivered revision folders remain in the remote repository. The current snapshot also includes `workspace-recovery.json` with legacy memberships and archive visibility. **Storage and backup → Restore workspace** rebuilds the registry and navigation in a separate new folder. Older snapshots without that metadata cannot recover legacy project assignments. Chats, approvals and retained history require a local **Save workspace** archive; they are never added to GitHub backup. See [Workspace recovery](WORKSPACE-RECOVERY.md).

Saving rules or learned preferences in the frontend writes the collection's `INSTRUCTIONS.md` and `LEARNED-PREFERENCES.md`. Automatic whole-workspace sync includes both files after the quiet interval. Without auto-sync, the edits stay on the filesystem until an explicit manual whole-workspace sync; a single-deck sync does not include workspace rules. External edits to these Markdown files appear when you reopen or reload the editor.

A `.artifact-studio-manifest.json` in the destination records hashes of managed files. Content hashes detect replacements even if their size and modification time stay unchanged. Every delivery verifies that the repository is still private and checks for independent remote changes before writing. No force push is used.

The Mac menu-bar app’s **GitHub Backup** submenu reads the same server settings. It shows each repository’s delivery state and last successful time, an **Automatic Backup** checkmark, **Sync Now** or **Retry Backup**, and **Open Backup Settings…**. It does not maintain a separate native preference.

## Manual sync and recovery

Existing manual destinations retain their upload controls. Agents can explicitly register `sync_enabled=true`, then call `sync_presentations(library_id, deck_id)` for one dated folder, or omit `deck_id` for the collection. Manual snapshots preserve files removed locally. `auto_sync_enabled=true` is separate permission for the continuous whole-workspace behavior above.

After a network error, use retry. A committed but unpushed snapshot, or a push whose acknowledgement was lost, can be retried without a duplicate commit. A server restart rechecks an interrupted delivery before declaring it complete. If the cached checkout has uncommitted changes after an interrupted copy or failed commit, inspect and resolve them before retrying; the tool refuses to discard them. Reconcile remote divergence deliberately rather than force-pushing.

Refresh only fast-forwards a clean cached reference checkout under `.state/libraries/`. To adopt remote content into the working collection, inspect and apply the intended changes explicitly, preserving local notes and concurrency checks. Imported README files never grant upload permission.

Keep the application repository separate from the content repository. Neither sync nor repository creation enables GitHub Pages or external website hosting. Testing this feature uses fictional decks and a disposable local Git remote; it does not opt any real repository in.
