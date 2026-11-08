// Publish only the Release Please candidates whose merge commits passed CI.
const { GitHub } = require('release-please/build/src/github');
const { Manifest } = require('release-please/build/src/manifest');

async function readTag(github, tag) {
  const { owner, repo } = github.repository;
  let object;
  try {
    ({ data: { object } } = await github.octokit.git.getRef({ owner, repo, ref: `tags/${tag}` }));
  } catch (error) {
    if (error.status === 404) return null;
    throw error;
  }
  const seen = new Set();
  while (object.type === 'tag') {
    if (seen.has(object.sha)) throw new Error(`Cyclic tag: ${tag}`);
    seen.add(object.sha);
    ({ data: { object } } = await github.octokit.git.getTag({ owner, repo, tag_sha: object.sha }));
  }
  if (object.type !== 'commit') throw new Error(`Tag does not point to a commit: ${tag}`);
  return object.sha;
}

async function ensureTag(github, candidate, existing) {
  const tag = candidate.tag.toString();
  if (existing !== null) return;
  const { owner, repo } = github.repository;
  try {
    await github.octokit.git.createRef({ owner, repo, ref: `refs/tags/${tag}`, sha: candidate.sha });
  } catch (error) {
    if (error.status !== 422 || await readTag(github, tag) !== candidate.sha) throw error;
  }
}

async function publishVerified(manifest, verifiedShas) {
  const allowed = new Set(verifiedShas);
  if (!allowed.size || [...allowed].some(sha => !/^[0-9a-f]{40}$/.test(sha))) {
    throw new Error('Verified release commit list is empty or invalid');
  }
  const candidates = await manifest.buildReleases();
  if (!candidates.length) throw new Error('No release candidates found');
  for (const candidate of candidates) {
    const tag = candidate.tag.toString();
    if (!allowed.has(candidate.sha) || candidate.sha !== candidate.pullRequest.sha ||
        candidate.path !== '.' || candidate.forceTag || candidate.prerelease || candidate.draft ||
        !/^v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$/.test(tag)) {
      throw new Error(`Unverified or unsafe release candidate: ${tag}`);
    }
  }
  // Check every existing tag before any write, including tag creation.
  const existing = await Promise.all(candidates.map(candidate => readTag(manifest.github, candidate.tag.toString())));
  for (const [index, candidate] of candidates.entries()) {
    if (existing[index] !== null && existing[index] !== candidate.sha) {
      throw new Error(`Existing tag points to a different commit: ${candidate.tag}`);
    }
  }
  for (const [index, candidate] of candidates.entries()) {
    await ensureTag(manifest.github, candidate, existing[index]);
  }
  // createReleases must consume this exact, already validated plan.
  const frozen = Object.freeze(candidates.map(candidate => Object.freeze(candidate)));
  manifest.buildReleases = async () => frozen;
  return manifest.createReleases();
}

async function main() {
  const [owner, repo, extra] = (process.env.GH_REPO || '').split('/');
  if (!owner || !repo || extra || !process.env.GH_TOKEN) throw new Error('Missing repository or token');
  const github = await GitHub.create({ owner, repo, token: process.env.GH_TOKEN, defaultBranch: 'main' });
  const manifest = await Manifest.fromManifest(github, 'main');
  await publishVerified(manifest, (process.env.VERIFIED_SHAS || '').split('\n').filter(Boolean));
}

if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { publishVerified };
