# Private GitHub source-sync acceptance

Issue [#163](https://github.com/kaiketsu-labs/artifact-studio/issues/163) requires a
dedicated private GitHub round trip. Local bare-Git tests establish application
merge/recovery behavior; they do not establish GitHub credentials or deployed
network access.

Run `python -m scripts.verify_github_sync --help` for the opt-in harness. It uses
the normal `ProjectGitHubSync`, `GitTree`, sharing guards and presentation journal,
creates a new fictional three-slide workspace, and records 18 timed checkpoints:
initial upload, unchanged state, independent merge, slide-content/order/delete/asset
conflicts and explicit choices, interruption after a successful push, restart
recovery, and a competing editor's rejected stale push. It verifies that private
notes, source-panel context, agent context and unreleased review stay local. A
non-owner cannot read, configure or run sync. It does not test live provider login,
browser UX or a separate GitHub credential denied by the repository.

## Prepare a disposable destination

Use a **new private** repository named
`YOUR_ACCOUNT/artifact-studio-sync-acceptance-YYYYMMDD-RUN`. The CLI refuses other
names, a branch outside `acceptance/<run-id>`, a public/non-writable repository,
an existing target presentation folder or a mismatched fixture marker. Never
point it at a real project or backup repository.

1. Create the private repository with a README and a unique run ID. Repository
   creation needs a GitHub credential authorized for that operation; repository
   access alone may be insufficient.
2. Commit `acceptance-marker/fixture.json` with these exact UTF-8 bytes, including
   the trailing newline:

   ```json
   {"purpose":"artifact-studio-sync-acceptance","fictional_only":true}
   ```

3. Create an `acceptance/<run-id>` branch from that commit. Leave
   `fictional/presentation` absent. The marker and all unrelated paths must remain
   intact throughout the test.
4. Supply GitHub authentication at runtime through `GH_TOKEN` or the normal
   private `gh` configuration. Do not put credentials in an image, source file,
   shell command argument, report or Git commit. A token restricted to this
   dedicated repository is preferable.

The harness does not create repositories or delete branches/history. Keep its
repository until its report is reviewed, then remove only that identified
disposable fixture through your normal repository cleanup procedure.

## Run with the production staging boundary

The scratch path must be a dedicated Linux tmpfs, owned by the running user,
mode `0700`, with `noexec,nodev,nosuid`, at most 512 MiB and 32,768 inodes. The
production check is intentionally not disabled by the CLI. An ordinary directory
or unrestricted `/dev/shm` is not an acceptable substitute.

Use the application's current container image, built from the same source commit
as the harness, and mount `scripts/` read-only if it is not in that image. Provide
an empty writable output directory and this additional tmpfs:

```text
/run/artifact-studio-project-sync:rw,noexec,nosuid,nodev,size=512m,nr_inodes=32768,mode=0700,uid=1000,gid=1000
```

Run as the image's non-root user (UID 1000), with a read-only root filesystem,
dropped capabilities and `no-new-privileges`. Adjust the output directory's owner
to that user. The command inside the container is:

```sh
python -m scripts.verify_github_sync \
  --repository YOUR_ACCOUNT/artifact-studio-sync-acceptance-YYYYMMDD-RUN \
  --branch acceptance/YYYYMMDD-RUN \
  --scratch /run/artifact-studio-project-sync \
  --workspace /acceptance/new-fictional-workspace \
  --output /acceptance/result.json \
  --allow-private-fixture-writes
```

The workspace path must not exist. Review `result.json` alongside the private
branch history. Its durations describe one fictional workload and include Git
network calls; they are not a production throughput or agent-token benchmark.
Do not publish a private repository URL or credentials with the evidence. The
harness deliberately retains the fictional local workspace for failure analysis.

## Evidence recorded on 2026-11-15

- The complete 18-checkpoint harness passed against a disposable local bare Git
  repository in 14.82 seconds. That test substitutes GitHub transport, the GitHub
  privacy lookup and mount verification. The combined acceptance, project-sync
  and presentation-sync tests passed: 33 tests in 40.25 seconds, with one browser
  test deselected. These are local test durations, not live GitHub latency.
- A separate non-root Docker preflight passed the unchanged production mount
  check with 536,870,912 bytes and 32,768 inodes. The image used the repository's
  pinned Python/uv bases and locked runtime dependencies. A host-trusted CA bundle
  was supplied as a build secret; TLS verification stayed enabled.
- **Live private GitHub acceptance passed all 18 checkpoints** using a new empty
  fixture repository supplied by the maintainer. Source commit `3dc5743` ran in
  the non-root container with the same production mount check, runtime-only
  credentials and TLS verification. The container needed its configured proxy
  hostname mapped to the host-resolved proxy address; proxy routing was retained.
- Private notes/context and unreleased review survived every checkpoint.
  Non-owner status, configuration and sync calls were denied. Real GitHub writes
  verified independent merges, all four conflict classes, recovery after a
  successful interrupted push, and rejection of a second editor's stale push.
- The [redacted per-checkpoint results](performance/github-sync-acceptance.json)
  record 33.737 seconds across the timed sync calls. This sum excludes container
  startup, preflight, peer edits and separate remote preservation checks; it is
  not the total harness duration or a production throughput measurement.

This completes the dedicated private-repository acceptance for this fixture.
It does not establish live identity-provider/browser behavior, permissions for a
separate denied GitHub credential, or physical microphone support (#159).
