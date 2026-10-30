# Reference libraries and private GitHub sync

Connect a GitHub repository or an existing local folder through the folder icon in Slide Studio. Connections default to **reference only**. Agents can inspect earlier decks, brand masters, assets and writing tone without uploading your workspace. Public repositories may be references; uploads require a private repository. Local folders remain read-only references.

## Automatic sync

Check **Automatic sync** when connecting a GitHub repository, or **Auto-sync all presentations** beside an existing connection. This explicitly includes **all presentations in this workspace**, including the initial saved collection. Existing connections are never opted in automatically. The repository must already exist, and GitHub CLI must be authenticated with `gh auth login` and `gh auth setup-git`.

While the local server runs, it checks saved files and groups changes after eight quiet seconds. Closing a browser tab does not stop delivery; stopping Slide Studio does. The next launch resumes from durable local checkpoints. Unsaved editor text is not a file change. Turning the setting off returns the repository to reference only. If a library operation is already running, wait for it to finish before changing the setting; orderly shutdown waits for that operation too.

The destination shows pending, syncing, synced or an actionable error. A failure pauses that destination until you use its upload/retry icon. Further edits do not silently dismiss the error. There is no browser-tab or active-agent requirement.

Automatic sync also removes files previously managed by Slide Studio when they are removed locally, but only if their remote hash still matches the last delivered version. Git history retains the earlier versions. Independently changed remote files cause a conflict; unrelated repository files are preserved. This is one-way sync, never a remote overwrite of local working files.

## Included files

Snapshots include existing HTML decks, supplied PPTX masters, design packs, documents and asset folders. The extension allowlist is in `studio/libraries.py`. Files at or above 100 MB require smaller media; Git LFS is not managed here. Symbolic links, dotfiles, review databases, dependency/reference caches and known credential files are excluded. Slides’ embedded notes and source/context documents are included. Review chats, approval state and private delivery cursors are not uploaded.

Saving rules or learned preferences in the frontend writes the collection's `INSTRUCTIONS.md` and `LEARNED-PREFERENCES.md`. Automatic whole-workspace sync includes both files after the quiet interval. Without auto-sync, the edits stay on the filesystem until an explicit manual whole-workspace sync; a single-deck sync does not include workspace rules. External edits to these Markdown files appear when you reopen or reload the editor.

A `.slide-studio-manifest.json` in the destination records hashes of managed files. Content hashes detect replacements even if their size and modification time stay unchanged. Every delivery verifies that the repository is still private and checks for independent remote changes before writing. No force push is used.

## Manual sync and recovery

Existing manual destinations retain their upload controls. Agents can explicitly register `sync_enabled=true`, then call `sync_presentations(library_id, deck_id)` for one dated folder, or omit `deck_id` for the collection. Manual snapshots preserve files removed locally. `auto_sync_enabled=true` is separate permission for the continuous whole-workspace behavior above.

After a network error, use retry. A committed but unpushed snapshot can be pushed again without a duplicate commit. If the cached checkout has uncommitted changes after an interrupted copy or failed commit, inspect and resolve them before retrying; the tool refuses to discard them. Reconcile remote divergence deliberately rather than force-pushing.

Refresh only fast-forwards a clean cached reference checkout under `.state/libraries/`. To adopt remote content into the working collection, inspect and apply the intended changes explicitly, preserving local notes and concurrency checks. Imported README files never grant upload permission.

Keep the application repository separate from the content repository. Neither sync nor repository creation enables GitHub Pages or external website hosting. Testing this feature uses fictional decks and a disposable local Git remote; it does not opt any real repository in.
