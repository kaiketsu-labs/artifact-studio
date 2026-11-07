const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {parse} = require('yaml');
const {RepositoryFileCache} = require('@google-automations/git-file-utils');
const root = path.resolve(__dirname, '../..');
const read = name => parse(fs.readFileSync(path.join(root, name), 'utf8'));

test('Dependabot covers real manifests and transitive Python updates without hiding major security fixes', () => {
  const config = read('.github/dependabot.yml');
  assert.equal(config.version, 2);
  const entries = Object.fromEntries(config.updates.map(item => [item['package-ecosystem'], item]));
  assert.deepEqual(Object.keys(entries).sort(), ['docker', 'github-actions', 'npm', 'uv']);
  assert.deepEqual(entries.uv.allow, [{'dependency-type': 'all'}]);
  assert.deepEqual(entries.npm.directories.sort(), ['/scripts/release', '/scripts/text-editor']);
  for (const directory of entries.npm.directories) {
    assert.ok(fs.existsSync(path.join(root, directory, 'package-lock.json')));
  }
  for (const name of ['uv', 'npm']) {
    assert.equal(entries[name].schedule.interval, 'daily');
    assert.ok(Object.values(entries[name].groups).some(group => group['applies-to'] === 'security-updates'));
  }
  assert.ok(!config.updates.some(item => item.ignore || item['target-branch']));
});

test('one protected gate includes audits and the applicable reusable platform checks', () => {
  const workflow = read('.github/workflows/ci.yml');
  assert.equal(workflow.jobs.safety.name, 'Dependency safety');
  assert.equal(workflow.jobs.safety.if, 'always()');
  assert.deepEqual(workflow.jobs.safety.needs.sort(), ['agents', 'audit', 'browser', 'docker', 'macos', 'quality', 'scope']);
  for (const name of ['audit', 'docker', 'macos']) assert.ok(workflow.jobs[name].uses.startsWith('./.github/workflows/'));
  for (const name of ['docker', 'macos']) {
    const platform = read(`.github/workflows/${name}.yml`);
    assert.ok(Object.hasOwn(platform.on, 'workflow_call'));
    assert.ok(!Object.hasOwn(platform.on, 'pull_request'));
  }
  const audit = read('.github/workflows/dependency-audit.yml');
  assert.ok(audit.on.schedule.length);
  const commands = audit.jobs.audit.steps.filter(step => step.run).map(step => step.run);
  for (const platform of ['linux', 'macos', 'windows']) {
    assert.ok(commands.includes(`uv audit --locked --preview-features audit-command --python-platform ${platform}`));
  }
  assert.ok(commands.includes('npm audit --prefix scripts/release --package-lock-only'));
  assert.ok(commands.includes('npm audit --prefix scripts/text-editor --package-lock-only'));
  assert.ok(!audit.jobs.audit.steps.some(step => step['continue-on-error']));
});

test('untrusted npm code has no write token and privileged jobs only check out base source', () => {
  const workflow = read('.github/workflows/dependabot.yml');
  const jobs = workflow.jobs;
  for (const event of ['synchronize', 'edited', 'converted_to_draft']) {
    assert.ok(workflow.on.pull_request_target.types.includes(event));
  }
  assert.match(jobs.inspect.steps[0].run, /--disable-auto/);
  assert.equal(jobs['build-editor'].permissions.contents, 'read');
  assert.deepEqual(Object.keys(jobs['build-editor'].permissions), ['contents']);
  for (const name of ['inspect', 'commit-editor']) {
    const checkout = jobs[name].steps.find(step => step.uses?.startsWith('actions/checkout@'));
    assert.equal(checkout.with.ref, '${{ github.event.pull_request.base.sha }}');
    assert.equal(checkout.with['persist-credentials'], false);
    assert.ok(!jobs[name].steps.some(step => /npm (ci|install|run)/.test(step.run || '')));
  }
  assert.ok(jobs['build-editor'].steps.some(step => /npm ci .*--ignore-scripts/.test(step.run || '')));
  for (const job of Object.values(jobs)) {
    for (const step of job.steps) if (step.uses) assert.match(step.uses, /^[\w/-]+@[a-f0-9]{40}$/);
  }
});

test('security-fixed nested minimatch remains compatible with release tooling file discovery', async () => {
  const files = ['pyproject.toml', 'uv.lock', 'docs/guide.md', 'scripts/release/package.json', 'scripts/text-editor/package.json'];
  const octokit = {git: {getTree: async () => ({data: {tree: files.map(file => ({path: file, type: 'blob'})), truncated: false}})}};
  const cache = new RepositoryFileCache(octokit, {owner: 'fictional', repo: 'fixture'});
  assert.deepEqual(await cache.findFilesByGlob('scripts/**/package.json', 'main'), files.slice(3));
  assert.deepEqual(await cache.findFilesByGlob('**/*.{md,toml}', 'main'), ['pyproject.toml', 'docs/guide.md']);
});
