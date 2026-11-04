// Exercise the actual Release Please manifest and updaters without GitHub access.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { Manifest } = require('release-please/build/src/manifest');

const root = path.resolve(__dirname, '../..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const quiet = { debug() {}, info() {}, warn() {}, error() {} };

function github(messages, released) {
  const api = {
    repository: { owner: 'example', repo: 'artifact-studio' },
    async getFileJson(file) {
      return file === '.release-please-manifest.json'
        ? (released ? { '.': '0.1.0' } : {}) : JSON.parse(read(file));
    },
    async getFileContentsOnBranch(file) { return { parsedContent: read(file) }; },
    async findFilesByFilenameAndRef() { return []; },
    async *releaseIterator() {
      if (released) yield { tagName: 'v0.1.0', name: 'v0.1.0', sha: 'old', notes: '' };
    },
    async *tagIterator() {},
    async *mergeCommitIterator() {
      for (const [i, message] of messages.entries()) {
        yield { sha: `commit${i}`, message, files: ['artifact_studio/app.py'] };
      }
      if (released) yield { sha: 'old', message: 'chore: release 0.1.0', files: [] };
    },
  };
  return new Proxy(api, { get(target, property) {
    if (!(property in target)) throw new Error(`Unexpected GitHub call: ${String(property)}`);
    return target[property];
  } });
}

async function candidate(messages, released = false) {
  const manifest = await Manifest.fromManifest(github(messages, released), 'main', undefined, undefined, { logger: quiet });
  const prs = await manifest.buildPullRequests();
  assert.equal(prs.length, 1);
  const pr = prs[0];
  const strategy = (await manifest.getStrategiesByPath())['.'];
  const release = await strategy.buildRelease({ sha: 'merge', title: pr.title.toString(), headBranchName: pr.headRefName, body: pr.body.toString() });
  assert.ok(release);
  assert.equal(release.tag.toString(), `v${pr.version}`);
  return pr;
}

function updated(pr, file, input = read(file)) {
  const update = pr.updates.find(item => item.path === file);
  assert.ok(update, `missing update for ${file}`);
  return update.updater.updateContent(input);
}

test('initial release and later version bumps use actual repository configuration', async () => {
  for (const [messages, released, expected] of [
    [['feat!: first feature\n\nBREAKING CHANGE: changed behavior', 'fix: repair'], false, '0.1.0'],
    [['fix: repair'], true, '0.1.1'],
    [['feat: add feature'], true, '0.2.0'],
    [['feat!: change behavior\n\nBREAKING CHANGE: changed behavior'], true, '0.2.0'],
  ]) {
    const pr = await candidate(messages, released);
    assert.equal(pr.version.toString(), expected);
    const project = updated(pr, 'pyproject.toml');
    assert.match(project, new RegExp(`version = "${expected.replaceAll('.', '\\.') }"`));
    const lock = read('uv.lock');
    const changedLock = updated(pr, 'uv.lock', lock);
    const oldEntry = lock.match(/name = "artifact-studio"\nversion = "[^"]+"/)[0];
    assert.ok(lock.includes(oldEntry));
    assert.equal(changedLock, lock.replace(oldEntry, `name = "artifact-studio"\nversion = "${expected}"`));
    const manifest = JSON.parse(updated(pr, '.release-please-manifest.json', released ? '{".": "0.1.0"}\n' : '{}\n'));
    assert.equal(manifest['.'], expected);
    const changelog = updated(pr, 'CHANGELOG.md', '');
    assert.ok(changelog.includes(expected));
  }
});
