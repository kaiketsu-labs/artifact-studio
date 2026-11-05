# Versions and releases

Artifact Studio uses [Release Please](https://github.com/googleapis/release-please)
to prepare version bumps, `CHANGELOG.md` and GitHub release notes. Stable releases
use **vMAJOR.MINOR.PATCH** tags.

`pyproject.toml` is the application's version source. The health endpoint,
server metadata and Mac app bundle read it. Release Please updates that file
and only Artifact Studio's entry in `uv.lock`; dependency versions stay intact.
`.release-please-manifest.json` tracks the version managed by Release Please.
Version files can advance when a release PR is merged, before its tag is published;
their contents alone do not establish that a release exists. Let Release Please
update the manifest rather than changing it to simulate publication.

## Prepare and publish

1. Enable **Settings → Actions → General → Workflow permissions → Allow GitHub
   Actions to create and approve pull requests**. The workflow uses the built-in
   `GITHUB_TOKEN`; a personal token is unnecessary. Organization policy may
   control this setting. Approval and auto-merge are not performed by the workflow.
2. Pushes to `main` prepare or update a release PR. You can also choose **Actions →
   Release Please → Run workflow**, select `main` and leave `command` as `prepare`.
   Inspect the proposed version and changelog. The workflow explicitly dispatches
   CI for the release branch because PRs created with `GITHUB_TOKEN` do not trigger
   normal `pull_request` runs.
3. When ready, review and merge the release PR. Wait for CI on the resulting
   `main` commit to pass. **Merging the release PR does not publish a release.**
4. To publish, choose **Actions → Release Please → Run workflow**, select `main`
   and set `command` to **publish**. This manual action creates the version tag
   and GitHub release for the merged release PR. It fails if CI for the selected
   `main` commit or the release PR's merge commit has not passed, or if `main` has
   changed since the run was requested. A newer passing commit cannot substitute
   for passing CI on the commit being tagged.

Publishing is never run on a push, and the default manual action only prepares.
No package registry upload, binary installer or automatic merge is configured.
The generated changelog is the release notes source; review the first entry for
older commits that did not use Conventional Commits.

## Choose the next version

Use Conventional Commit titles for squash-merged PRs:

| Title | Next version after 0.1.0 |
| --- | --- |
| `fix: correct an issue` | 0.1.1 |
| `feat: add a capability` | 0.2.0 |
| `feat!: change an incompatible API` | 0.2.0 while below 1.0 |
| `docs: clarify a guide` or `chore: update tooling` | Does not trigger a release alone |

The initial version stays `0.1.0` regardless of earlier feature or breaking-change
commits. Subsequent versions follow the configured pre-1.0 policy. For a deliberate
version override, use Release Please's documented `Release-As: x.y.z` commit
footer; avoid a permanent `release-as` setting that would pin future releases.

## Installation and verification

GitHub releases identify source snapshots. The supported installation remains a
source checkout with `uv sync --locked`. The Mac **Update** command follows the
highest stable `vMAJOR.MINOR.PATCH` tag published on `origin`, ordered numerically;
it ignores prerelease/non-version tags and untagged `main` commits. Publishing the
stable Git tag is the update channel's release boundary, so do not push a stable
version tag for a draft or an unverified build. The release workflow above creates
that tag only during publication.

The updater fetches release refs separately from local tags, validates ancestry
against `origin/main`, and installs the selected commit by fast-forward. It refuses
an absent or changed release, and never automatically downgrades a newer checkout.
It uses the existing authenticated Git connection rather than a GitHub API token.
A checkout between releases still reports its checked-in project version; that
version alone does not mean the commit was published.

Existing installations with the old main-following updater need to receive this
updater change once (through their current Update command or a manual source update
and controller rebuild). Subsequent update checks follow stable release tags.

CI runs offline simulations with the same Release Please version bundled in the
pinned action. They exercise the first and subsequent releases and check the real
version-file updates without creating PRs, tags or releases:

```sh
npm ci --prefix scripts/release --ignore-scripts --no-audit --no-fund
node --test scripts/release/*.cjs
```

Update the action pin and simulation dependency together. App health/version tests
also exercise a future version and the generated Mac bundle metadata. Native Mac
compilation still requires macOS.
