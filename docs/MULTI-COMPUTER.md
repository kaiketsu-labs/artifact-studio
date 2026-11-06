# Working on two computers

Artifact Studio's GitHub backups do not keep two independent working collections in sync. **Do not enable current-source uploads from two computers to the same repository branch.** A second computer with stale files can replace newer Studio-managed source and remove projects from the latest backup commit. Earlier commits and the first computer's local files remain available for recovery.

This limitation was reproduced against `1494bf9` on 2026-11-05 using two isolated stores, independent checkouts and real disposable Git remotes. It also affects alternating between computers, without simultaneous edits.

## What refreshes automatically

| Operation | Behavior |
| --- | --- |
| Local current-source observation | Checks saved files every 3 seconds while the server runs. Uploads after at least 8 quiet seconds, subject to worker scheduling and network time. |
| Idle computer after another computer pushes | Does not poll the remote or update its working collection. Its last successful backup status can remain visible. |
| Next current-source upload | Fetches and fast-forwards the reference checkout before publishing the local snapshot. This does not merge remote files into the working collection. |
| **Refresh references** | Downloads into the reference cache only. Open projects, notes and the working collection are not updated. |
| Full workspace backup schedule | Uploads changed ZIP recovery snapshots at the configured interval. Does not download or merge another computer's changes. |

“Backed up” confirms delivery of a particular local snapshot at its recorded time. It is not confirmation that the current remote branch still matches that snapshot.

## Simulated active-session results

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

## Why the stale upload is accepted

[`Libraries.sync`](../artifact_studio/libraries.py) pulls the destination, then reads `.artifact-studio-manifest.json` from that freshly updated checkout. The manifest records the most recent Studio-managed hashes in the repository, rather than this computer's last acknowledged hashes.

When A publishes through Studio, both its files and that manifest change. B then pulls them together. B's checks see that the remote files match the manifest and allow replacement with B's stale local snapshot. Automatic deletion uses the same remote manifest, so it can also treat A-only files as files removed locally on B.

Git's normal push protection catches a branch changing after the fetch. It cannot detect this sequential stale replacement, because B has already incorporated A's commit into the checkout history before creating its own commit. Fetching more frequently, retrying or force-pushing does not solve the missing local baseline.

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

## Changes needed for independent computers to sync safely

The first change should prevent stale publishing, before adding automatic downloads:

1. Persist a repository-specific baseline locally for each replica: last confirmed remote commit, managed paths and content hashes. Do not replace it with a newly pulled remote manifest. A new replica connecting to an existing backup needs an explicit adoption step.
2. Before publishing, compare the local baseline, local files and fetched remote files. Stop for review when remote changes would be replaced. Never infer deletion from a file that this replica has never acknowledged. Advance the baseline only after confirmed delivery; handle a lost push acknowledgement by reconciliation.
3. Offer explicit **Compare / Pull / Resolve** actions that preserve local edits, notes and retained history. Keep full archives before applying changes. A remote-only change must be shown or explicitly adopted, rather than silently overwritten or silently merged.
4. Distinguish **Last uploaded**, **Remote changed** and **Conflict** in status. Read-only background remote checks can report staleness without waking an agent or modifying working files.
5. For actual multi-writer synchronization, merge project/navigation metadata and deletion records as well as artifact files. Independent databases can assign the same revision number to different content; content hashes and replica identity must remain part of conflict detection. Private review events and approvals need explicit merge semantics too.

An alternative for independent backups is per-replica source directories and manifests within a shared repository. That isolates snapshots but still does not provide live project synchronization. Human-readable device/workspace labels would also make archive selection safer.

The simulation documents an open runtime defect. It does not implement the baseline guard, remote polling or two-way merge. See [backup controls](REPOSITORY-SYNC.md) and [workspace recovery](WORKSPACE-RECOVERY.md) for the current supported actions.
