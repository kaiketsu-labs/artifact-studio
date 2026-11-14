# Working on two computers

Artifact Studio's GitHub backups do not keep two independent working collections in sync. Current-source uploads now compare remote files with a baseline stored in this computer's private database. An upload that would replace newer remote content stops for review; a project never acknowledged by this computer is not treated as a local deletion.

This guide covers the backup workflow. A hosted deployment can separately configure [project GitHub sync](HOSTED.md#hosted-project-github-sync) for selected sources, with explicit owner opt-in and file conflict resolution. That feature does not transfer private notes, chats or full workspace state; enabling backups does not enable it.

**Update every computer that uploads.** Older versions can still replace newer Studio-managed files and remove projects from the latest backup commit. That defect was reproduced against `1494bf9` on 2026-11-05 and guarded against on 2026-11-06. It affects alternating computers as well as simultaneous sessions. Earlier commits and the first computer's local files remain available for recovery.

## What refreshes automatically

| Operation | Behavior |
| --- | --- |
| Local current-source observation | Checks saved files every 3 seconds while the server runs. Uploads after at least 8 quiet seconds, subject to worker scheduling and network time. |
| Idle computer after another computer pushes | Does not poll the remote or update its working collection. Its last successful backup status can remain visible. |
| Next current-source upload | Fetches and fast-forwards the reference checkout before publishing the local snapshot. This does not merge remote files into the working collection. |
| **Refresh references** | Downloads into the reference cache only. Open projects, notes and the working collection are not updated. |
| Full workspace backup schedule | Uploads changed ZIP recovery snapshots at the configured interval. Does not download or merge another computer's changes. |

“Backed up” confirms delivery of a particular local snapshot at its recorded time. It is not confirmation that the current remote branch still matches that snapshot.

## Simulated active-session results before the fix

The following table records the original behavior at `1494bf9`, before local baseline checks were added.

| Scenario | Observed result |
| --- | --- |
| A pushes while B stays open without local changes | B makes no Git calls on subsequent worker ticks, keeps its old working files and reference cache, and retains its last successful status. Ticks through an eight-hour interval used injected time, not eight hours of wall-clock waiting. |
| A and B edit the same presentation; A uploads, then B | Both uploads succeed. B replaces A's latest backup. A can replace it again by saving only notes, because the next whole-workspace upload also includes A's older slide copy. |
| A edits project one; B edits project two | B's whole-workspace upload replaces A's updated project-one files with B's stale copies. Different project IDs do not isolate uploads. |
| Separate stores revise the same owned artifact through the guarded API | Both edits pass their own valid local revision checks. B's later upload replaces A's backup. Local revision guards do not coordinate independent databases. |
| Separate stores revise owned artifacts in different projects | B's later upload reverts A's artifact in the backup while publishing B's own edit. |
| A creates a project absent from B | B's automatic whole-workspace upload removes that project's managed files from the latest backup commit. |
| Two workers push in parallel, on the same or different projects | One push succeeds; Git rejects the other. Both local edits remain. The failed destination pauses; an explicit retry still needs manual reconciliation of the divergent checkout. |
| An external Git edit changes source without updating Studio's manifest | Idle B does not notice. B's next upload detects the source/manifest mismatch and stops with a remote-change error. |
| B explicitly refreshes after A uploads | B sees newer reference files; its working project and last upload status stay unchanged. |

These are backend and real-Git simulations, including parallel worker threads. They do not simulate two physical Macs or authenticated live GitHub push races. GitHub archive discovery, retention and recovery checks used the existing simulated GitHub transport.

## Local backup baselines

The fixed [`Libraries.sync`](../artifact_studio/libraries.py) keeps each destination's acknowledged file hashes and commit in the local `library_sync_checkpoints` table. The checkpoint is bound to this collection and branch. Fetching or refreshing a reference never advances it.

| Situation at upload time | Current behavior |
| --- | --- |
| Remote bytes equal the intended local bytes | Acknowledges those matching files without replacing them. |
| Remote bytes equal this computer's last acknowledged bytes | Allows this computer's saved update or automatic deletion. |
| Remote bytes changed or disappeared since this computer acknowledged them | Stops before writing the new snapshot; preserves local files and remote content. |
| A remote file has never been acknowledged here | Preserves it when absent locally; refuses to replace differing existing content. |
| A manual upload covers only one presentation | Acknowledges only that presentation, retaining earlier ownership of other paths without adopting newly fetched ones. |
| A full automatic backup would remove an A-only project missing on B | Preserves the unowned project files. Another conflicting path can still stop the entire backup. |

The destination manifest remains a managed-file inventory; it grants no permission to overwrite. Automatic deletions require local ownership and a matching remote hash. Git still rejects competing pushes without force-pushing. These guards stop stale publishing; they do not merge remote edits into open projects.

New local paths absent remotely can still be backed up as new files. This guard does not reconstruct deletions that happened before this replica ever acknowledged a path; that requires deletion history and explicit merge semantics.

Before a push, Studio stores the exact prepared commit locally. A lost acknowledgement is reconciled only when that commit is confirmed in remote history. A failed push can retry the exact prepared snapshot from its original remote base, then validate later local changes normally. Unexplained unpublished commits, changed branch history and divergent checkouts require review.

On upgrade, an existing automatic-backup receipt can seed the baseline from its exact last confirmed commit, limited to paths present in the first requested snapshot. Absent legacy paths are preserved because the old global manifest cannot prove deletion ownership. A first deck-scoped migration covers only that deck; other paths may need review later. Legacy manual backups without a confirmed receipt stop on differing existing files rather than guessing ownership. Newly connected computers can acknowledge matching files, but cannot overwrite a different existing backup merely by enabling uploads.

Checkpoints are host state: full workspace archives remove them and omit them from saved-content fingerprints. Restoring or refreshing a backup does not grant upload ownership.

Current-source uploads omit machine-specific locators from the reserved project, current artifact and workspace recovery manifests. Project context, context hashes and counts are rebuilt from the README and registry during recovery instead of being duplicated in the uploaded project view. Local files, immutable revisions, approval hashes and full archives retain their existing data. Authored JSON inside a bundle is never rewritten.

Artifacts also retain their saved MIME declarations when verified or edited on another host. Different operating-system MIME registries do not create synthetic revisions or invalidate unchanged snapshots. File paths, sizes, SHA-256 hashes and the saved manifest identity are still verified; actual tampering is rejected. New files receive their MIME type when first registered.

Existing backups with host-specific paths migrate only when their normalized metadata exactly matches the intended upload. This allows a recovered copy on another computer to acknowledge identical content without an inherited upload checkpoint. Different titles, briefs, navigation, source bytes or unknown metadata fields still require review. Pending uploads retain their exact original commit and hashes until delivery is confirmed.

If **Remote changes need review** appears, save a full archive of local work, preserve the remote backup, and inspect both versions before applying the intended changes. Retry after reconciliation; retries do not bypass the guard. A new backup repository is also available when the workspaces should remain independent.

The regression tests cover stale replicas, unknown and deleted remote files, reference refresh, partial uploads, legacy migration, unpublished commits, lost acknowledgements and parallel pushes: [conflicts](../tests/test_library_sync_conflicts.py), [checkpoint recovery](../tests/test_sync_checkpoint_recovery.py).

## Why the stale upload was accepted

The earlier implementation pulled `.artifact-studio-manifest.json` together with the remote files, then trusted its hashes as permission to replace them. A Studio upload from A changed both its files and that manifest, so stale B saw a matching manifest and replaced them with its own old snapshot. Automatic deletion used the same global inventory.

Git's push protection caught branch changes after a fetch, but not a stale replacement committed after pulling A's changes. The local baseline fixes that missing comparison. More frequent polling alone would not.

## Safe ways to work now

### Both computers need the same live projects

Run one canonical Studio server and collection on one host, and connect both browsers and agents to that server. Use an authenticated SSH or VPN tunnel that preserves the server's loopback binding; the local service should not be exposed directly to the public internet. Keep the host awake while either computer works.

Both clients then share the same working files and database. Existing artifact revision guards reject competing writes against the same baseline. The shared-store concurrent-writer test confirms one accepted write and one conflict. This does not make concurrent direct filesystem edits safe; agents should use the guarded authoring tools. Browser-only unsaved drafts still belong to their browser.

Live feedback has a separate ownership rule: one active reviewer per artifact. A second agent session cannot take over that artifact's live-review lease until the first review ends or expires. Separate artifacts can have separate reviewing chats. [Live-review ownership](LIVE-REVIEW.md).

### Both computers need independent local or offline workspaces

Full workspace ZIP backups can use the same private GitHub repository with separate workspace backup series. Fresh installations get different series IDs; retention filters by that ID. The existing checks confirm another computer can discover the series and restore sources, chats, approvals and retained history without inheriting upload permission.

Use explicit handoffs:

1. Save pending edits and create a full archive on the computer holding the intended version.
2. On the other computer, connect the repository as **reference only**, select the intended archive and preview a restore into a new folder.
3. Open the recovered collection and database, inspect the result, then reconnect agents and backup settings explicitly.
4. If both computers have new work, preserve both full archives and reconcile the intended changes before choosing the next canonical workspace. Restoring an archive does not merge two edited workspaces.

For current-source backups, give independently edited workspaces separate private repositories, or keep uploads confined to one canonical workspace. A simple “one computer at a time” rule is insufficient if switching computers leaves the next computer's working files stale.

Use the supported archive recovery path instead of cloning the private state/cache directories. Copying `workspace-backups.sqlite3` duplicates the backup series identity; both computers would then share the same retention scope. That identity duplication was reproduced locally. The supported archive restore creates a new series identity.

Full archives include saved private review material. Repository access determines who can download it. Keep content backups separate from the application source repository.

## Remaining work for live synchronization

The local baseline guard prevents stale publishing. Independent computers still need additional features for convenient live synchronization:

1. Offer explicit **Compare / Pull / Resolve** actions that preserve local edits, notes and retained history. Keep full archives before applying changes. A remote-only change must be shown or explicitly adopted, rather than silently overwritten or silently merged.
2. Distinguish **Last uploaded**, **Remote changed** and **Conflict** in status. Read-only background remote checks can report staleness without waking an agent or modifying working files.
3. For actual multi-writer synchronization, merge project/navigation metadata and deletion records as well as artifact files. Independent databases can assign the same revision number to different content; content hashes and replica identity must remain part of conflict detection. Private review events and approvals need explicit merge semantics too.

An alternative for independent backups is per-replica source directories and manifests within a shared repository. That isolates snapshots but still does not provide live project synchronization. Human-readable device/workspace labels would also make archive selection safer.

The overwrite/deletion guard is implemented. Background remote polling and two-way merge remain unimplemented. See [backup controls](REPOSITORY-SYNC.md) and [workspace recovery](WORKSPACE-RECOVERY.md) for the current supported actions.
