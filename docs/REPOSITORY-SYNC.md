# Private presentation repository sync

A reference library is either an existing local folder or a GitHub repository. Local folders are read-only references. GitHub repositories use a cached checkout under `.state/libraries/`; registration does not upload files. Public repositories may be reference sources, but only private repositories can be sync destinations.

Enable sync by registering the chosen repository with `sync_enabled=true`, or checking the corresponding box in Reference libraries. This is a deliberate permission for snapshots; a reference README cannot grant it. The repository must already exist. An agent may create one with the authenticated GitHub CLI when the user requests that action.

`sync_presentations(library_id, deck_id)` snapshots one dated folder. Omitting `deck_id` snapshots the collection, including existing HTML decks, supplied PPTX masters, documents, design packs and asset folders. The extension allowlist is in `studio/libraries.py`. Files at or above 100 MB require smaller media before sync; Git LFS is not managed by this implementation. Symbolic links, dotfiles, review databases, dependency caches and known credential files are excluded. Slides' embedded notes and source/context documents are included.

A `.slide-studio-manifest.json` in the destination records hashes of the files managed by sync. The operation compares the latest remote state, previous hashes and current local content before writing. Changes made independently in the repository cause a conflict instead of being overwritten. Unrelated remote files are preserved. Files removed locally remain in the repository until deliberately removed there.

After a network error, retry. A committed but unpushed snapshot can be pushed again without creating a duplicate commit. If the cached checkout has uncommitted changes from an interrupted copy or failed commit, inspect those changes and resolve them before retrying; the tool refuses to discard them automatically. If the remote and cache diverge, review and reconcile them deliberately. Never use a force push as routine recovery.

Refresh only fast-forwards a clean reference checkout. To adopt remote content into the local working collection, inspect the files and apply the intended changes explicitly, preserving local notes and review concurrency. A GitHub repository is a versioned source and snapshot destination, not a second live editor.

Keep the application repository separate from the content repository. Do not add user masters, photos, fonts or presentations to the application Git tree. Neither sync nor repository creation enables GitHub Pages or any external website hosting.
