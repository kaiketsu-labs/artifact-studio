const { test } = require('node:test');
const assert = require('node:assert/strict');
const { Manifest } = require('release-please/build/src/manifest');
const { publishVerified } = require('./publish.js');

const sha = 'a'.repeat(40);
const other = 'b'.repeat(40);
const tagSha = 'c'.repeat(40);
const candidate = (overrides = {}) => ({
  sha, pullRequest: { sha, number: 1 }, tag: { toString: () => 'v1.2.3' }, path: '.', ...overrides,
});
const notFound = () => Object.assign(new Error('not found'), { status: 404 });

function fixture({ plan = [candidate()], refs = {}, tags = {}, collision } = {}) {
  const calls = [];
  let builds = 0;
  const github = {
    repository: { owner: 'owner', repo: 'repo' },
    octokit: { git: {
      async getRef({ ref }) { calls.push(['get', ref]); if (!refs[ref]) throw notFound(); return { data: { object: refs[ref] } }; },
      async getTag({ tag_sha }) { calls.push(['peel', tag_sha]); return { data: { object: tags[tag_sha] } }; },
      async createRef({ ref, sha: target }) {
        calls.push(['create', ref, target]);
        if (collision) {
          refs[ref.slice(5)] = collision;
          throw Object.assign(new Error('already exists'), { status: 422 });
        }
        refs[ref.slice(5)] = { type: 'commit', sha: target };
      },
    } },
  };
  const manifest = {
    github, sequentialCalls: true,
    async buildReleases() { builds++; return builds === 1 ? plan : [candidate({ sha: other })]; },
    async createReleasesForPullRequest(releases) { calls.push(['publish', releases]); return releases; },
    createReleases: Manifest.prototype.createReleases,
  };
  return { manifest, calls, builds: () => builds };
}

test('real Manifest.createReleases consumes the verified frozen plan', async () => {
  const f = fixture();
  const result = await publishVerified(f.manifest, [sha]);
  assert.equal(f.builds(), 1);
  assert.equal(result[0].sha, sha);
  assert.ok(Object.isFrozen(result[0]));
  assert.deepEqual(f.calls.map(call => call[0]), ['get', 'create', 'publish']);
  assert.equal(f.calls[1][2], sha);
});

test('rejects every unsafe plan before publication', async () => {
  const bad = [
    [], [candidate({ sha: other })], [candidate({ pullRequest: { sha: other } })],
    [candidate({ forceTag: true })], [candidate({ prerelease: true })], [candidate({ draft: true })],
    [candidate({ tag: { toString: () => 'v1.2.3-rc.1' } })],
    [candidate({ path: 'other' })], [candidate(), candidate({ sha: other })],
  ];
  for (const plan of bad) {
    const f = fixture({ plan });
    await assert.rejects(publishVerified(f.manifest, [sha]));
    assert.deepEqual(f.calls, []);
  }
  await assert.rejects(publishVerified(fixture().manifest, ['bad']));
});

test('accepts existing lightweight or annotated tags at the verified commit', async () => {
  for (const annotated of [false, true]) {
    const f = fixture({ refs: { 'tags/v1.2.3': annotated ? { type: 'tag', sha: tagSha } : { type: 'commit', sha } },
      tags: { [tagSha]: { type: 'commit', sha } } });
    await publishVerified(f.manifest, [sha]);
    assert.equal(f.calls.some(call => call[0] === 'create'), false);
    assert.equal(f.calls.at(-1)[0], 'publish');
  }
});

test('preflights all tags and rejects incorrect targets before writes', async () => {
  const second = candidate({ tag: { toString: () => 'v2.0.0' } });
  for (const object of [{ type: 'commit', sha: other }, { type: 'tag', sha: tagSha }]) {
    const f = fixture({ plan: [candidate(), second], refs: { 'tags/v2.0.0': object }, tags: { [tagSha]: { type: 'commit', sha: other } } });
    await assert.rejects(publishVerified(f.manifest, [sha]), /different commit/);
    assert.equal(f.calls.some(call => ['create', 'publish'].includes(call[0])), false);
  }
});

test('concurrent tag creation is accepted only at the verified commit', async () => {
  for (const target of [sha, other]) {
    const f = fixture({ collision: { type: 'commit', sha: target } });
    if (target === sha) await publishVerified(f.manifest, [sha]);
    else await assert.rejects(publishVerified(f.manifest, [sha]));
    assert.equal(f.calls.some(call => call[0] === 'publish'), target === sha);
    assert.equal(f.calls.filter(call => call[0] === 'get').length, 2);
  }
});
