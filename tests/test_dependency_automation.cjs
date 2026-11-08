const {test, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {candidate, mergeReason, BUNDLES} = require('../scripts/dependencies/policy.cjs');
const {additions} = require('../scripts/dependencies/commit-editor.cjs');
const {scope} = require('../scripts/ci/scope.cjs');
const {failures} = require('../scripts/ci/gate.cjs');

const root = path.resolve(__dirname, '..');
const head = 'a'.repeat(40);
function fixture() {
  return {
    repository: 'example/studio', head,
    pr: {number: 7, state: 'open', draft: false, user: {login: 'dependabot[bot]', type: 'Bot'},
      base: {ref: 'main', repo: {full_name: 'example/studio'}},
      head: {sha: head, ref: 'dependabot/uv/python-compatible', repo: {full_name: 'example/studio'}},
      changed_files: 1, commits: 1, auto_merge: {}},
    files: [{filename: 'uv.lock', status: 'modified'}],
    commits: [{sha: head, author: {login: 'dependabot[bot]'}, commit: {verification: {verified: true}}}],
  };
}
function settings() { return {autoMergeAllowed: true, defaultBranchRef: {name: 'main'}}; }
function rules() { return [{type: 'required_status_checks', parameters: {strict_required_status_checks_policy: true,
  required_status_checks: [{context: 'Dependency safety', integration_id: 15368}]}}]; }
const update = 'version-update:semver-patch';

test('only a complete signed same-repository Dependabot update is a candidate', () => {
  assert.equal(candidate(fixture()), '');
  const changes = [
    f => { f.pr.user.login = 'human'; },
    f => { f.pr.user.type = 'User'; },
    f => { f.pr.state = 'closed'; },
    f => { f.pr.draft = true; },
    f => { f.pr.base.ref = 'feature'; },
    f => { f.pr.head.repo.full_name = 'attacker/studio'; },
    f => { f.pr.base.repo.full_name = 'attacker/studio'; },
    f => { f.pr.head.ref = 'main'; },
    f => { f.pr.head.sha = 'b'.repeat(40); },
    f => { f.pr.changed_files = 2; },
    f => { f.pr.commits = 2; },
    f => { f.commits[0].author.login = 'human'; },
    f => { f.commits[0].commit.verification.verified = false; },
    f => { f.commits[0].sha = 'b'.repeat(40); },
    f => { f.files[0].status = 'renamed'; },
    f => { f.files[0].previous_filename = 'something'; },
    f => { f.files = []; f.pr.changed_files = 0; },
  ];
  for (const change of changes) {
    const input = fixture(); change(input); assert.ok(candidate(input));
  }
});

test('workflow, image and unrelated changes stay outside automatic library merges', () => {
  for (const filename of ['Dockerfile', '.github/workflows/ci.yml', 'web/vendor/codemirror.js', 'artifact_studio/app.py']) {
    const input = fixture(); input.files[0].filename = filename;
    assert.match(candidate(input), /outside/);
  }
});

test('native queue requires a known compatible update and strict Actions-bound safety check', () => {
  for (const type of [update, 'version-update:semver-minor']) assert.equal(mergeReason(fixture(), type, settings(), rules()), '');
  for (const type of ['', 'version-update:semver-major', 'unknown', 'version-update:semver-patch,major']) assert.ok(mergeReason(fixture(), type, settings(), rules()));
  assert.ok(mergeReason(fixture(), update, null, rules()));
  assert.ok(mergeReason(fixture(), update, {autoMergeAllowed: false}, rules()));
  assert.ok(mergeReason(fixture(), update, {autoMergeAllowed: true, defaultBranchRef: {name: 'other'}}, rules()));
  const variants = [[], null, [{type: 'other'}],
    [{type: 'required_status_checks', parameters: {strict_required_status_checks_policy: false, required_status_checks: [{context: 'Dependency safety', integration_id: 15368}]}}],
    [{type: 'required_status_checks', parameters: {strict_required_status_checks_policy: true, required_status_checks: [{context: 'Dependency safety'}]}}],
    [{type: 'required_status_checks', parameters: {strict_required_status_checks_policy: true, required_status_checks: [{context: 'Dependency safety', integration_id: 1}]}}],
    [{type: 'required_status_checks', parameters: {strict_required_status_checks_policy: true, required_status_checks: []}}, {type: 'required_status_checks', parameters: {strict_required_status_checks_policy: false, required_status_checks: [{context: 'Dependency safety', integration_id: 15368}]}}]];
  for (const value of variants) assert.ok(mergeReason(fixture(), update, settings(), value));
});

function results(docker = true, macos = true) {
  return {
    scope: {result: 'success', outputs: {docker: String(docker), macos: String(macos)}},
    quality: {result: 'success'}, browser: {result: 'success'}, agents: {result: 'success'}, audit: {result: 'success'},
    docker: {result: docker ? 'success' : 'skipped'}, macos: {result: macos ? 'success' : 'skipped'},
  };
}
test('gate rejects failed, missing and skipped required suites, including audits', () => {
  assert.deepEqual(failures(results()), []);
  assert.deepEqual(failures(results(false, false)), []);
  for (const job of Object.keys(results())) {
    for (const status of ['failure', 'cancelled', 'skipped', 'pending', undefined]) {
      const value = results(); value[job].result = status;
      assert.ok(failures(value).includes(job));
    }
    const value = results(); delete value[job]; assert.ok(failures(value).includes(job));
  }
  const unknown = results(false, false); unknown.scope.outputs.docker = 'maybe';
  assert.ok(failures(unknown).includes('docker'));
});
test('scope runs platform checks for dependency and gating changes; unknown history is broad', () => {
  for (const files of [null, ['uv.lock'], ['pyproject.toml'], ['.github/workflows/ci.yml'], ['scripts/ci/gate.cjs']]) assert.deepEqual(scope(files), {docker: true, macos: true});
  assert.deepEqual(scope(['scripts/release/package-lock.json']), {docker: false, macos: false});
  assert.deepEqual(scope(['web/vendor/codemirror.js']), {docker: true, macos: false});
  assert.deepEqual(scope(['Dockerfile']), {docker: true, macos: false});
  assert.deepEqual(scope(['macos/ArtifactStudio.swift']), {docker: false, macos: true});
});

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'studio-dependency-checks-'));
after(() => fs.rmSync(temporary, {recursive: true, force: true}));
function generated() {
  const directory = fs.mkdtempSync(path.join(temporary, 'generated-'));
  for (const name of BUNDLES) fs.writeFileSync(path.join(directory, name), `fictional ${name}`);
  return directory;
}
test('generated files are a bounded exact allowlist, never arbitrary paths or links', () => {
  const directory = generated();
  assert.deepEqual(additions(directory).map(file => file.path), BUNDLES.map(name => `web/vendor/${name}`));
  fs.writeFileSync(path.join(directory, 'unexpected'), 'no');
  assert.throws(() => additions(directory), /Unexpected/);
  fs.unlinkSync(path.join(directory, 'unexpected'));
  fs.unlinkSync(path.join(directory, BUNDLES[0]));
  assert.throws(() => additions(directory), /Unexpected/);
  fs.symlinkSync(path.join(directory, BUNDLES[1]), path.join(directory, BUNDLES[0]));
  assert.throws(() => additions(directory), /Invalid/);
  fs.unlinkSync(path.join(directory, BUNDLES[0]));
  fs.writeFileSync(path.join(directory, BUNDLES[0]), '');
  assert.throws(() => additions(directory), /Invalid/);
});

const bin = path.join(temporary, 'bin'); fs.mkdirSync(bin);
fs.writeFileSync(path.join(bin, 'gh'), `#!/usr/bin/env node
const fs = require('node:fs');
const args = process.argv.slice(2);
const mock = JSON.parse(process.env.MOCK_DEPENDENCIES);
let body;
if (args.includes('--input')) body = JSON.parse(fs.readFileSync(0, 'utf8'));
fs.appendFileSync(process.env.MOCK_LOG, JSON.stringify({args, body})+'\\n');
if (args[0] === 'api' && args[1] === 'graphql') {
  if (mock.apiError) process.exit(1);
  if (body.query.startsWith('mutation')) {
    if (mock.headMoved) process.exit(1);
    console.log(JSON.stringify({data: {createCommitOnBranch: {commit: {oid: 'c'.repeat(40)}}}}));
  } else console.log(JSON.stringify({data: {repository: mock.settings}}));
} else if (args[0] === 'api' && args[1].includes('/rules/branches/main?')) {
  if (mock.rulesError) process.exit(1);
  if (!args.includes('--paginate') || !args.includes('--slurp')) process.exit(2);
  console.log(JSON.stringify(mock.pages));
} else if (args[0] === 'api') {
  const value = args[1].includes('/files?') ? mock.files : args[1].includes('/commits?') ? mock.commits : mock.pr;
  console.log(JSON.stringify(value));
} else if (args[0] === 'pr' && args[1] === 'merge') {
  if (!args.includes('--auto') && !args.includes('--disable-auto')) process.exit(2);
} else if (args[0] === 'workflow' && args[1] === 'run') {
  if (args[2] !== 'ci.yml') process.exit(2);
} else process.exit(2);
`, {mode: 0o755});

function execute(script, value = {}, args = []) {
  const log = path.join(temporary, `log-${Math.random()}.jsonl`);
  const output = path.join(temporary, `output-${Math.random()}`);
  const mock = {...fixture(), settings: settings(), pages: [rules()], ...value};
  const result = spawnSync(process.execPath, [path.join(root, 'scripts/dependencies', script), ...args], {
    encoding: 'utf8', env: {...process.env, PATH: `${bin}:${process.env.PATH}`,
      GH_REPO: 'example/studio', PR_NUMBER: '7', PR_HEAD: head, UPDATE_TYPE: update,
      GITHUB_OUTPUT: output, GITHUB_STEP_SUMMARY: '', MOCK_LOG: log, MOCK_DEPENDENCIES: JSON.stringify(mock)},
  });
  const calls = fs.existsSync(log) ? fs.readFileSync(log, 'utf8').trim().split('\n').map(JSON.parse) : [];
  return {...result, calls};
}
test('actual prepare runner revokes an old queue before enabling a guarded native queue', () => {
  const result = execute('prepare.cjs');
  assert.equal(result.status, 0, result.stderr);
  const merges = result.calls.filter(call => call.args[0] === 'pr');
  assert.equal(merges.length, 2);
  assert.ok(merges[0].args.includes('--disable-auto'));
  assert.deepEqual(merges[1].args.slice(-2), ['--match-head-commit', head]);
  assert.ok(merges[1].args.includes('--auto'));
});
test('a failed settings lookup cannot retain an old automatic-merge queue', () => {
  const result = execute('prepare.cjs', {apiError: true});
  assert.notEqual(result.status, 0);
  const merges = result.calls.filter(call => call.args[0] === 'pr');
  assert.equal(merges.length, 1);
  assert.ok(merges[0].args.includes('--disable-auto'));
});
test('active rules read is paginated and fails closed on errors, malformed pages, and unsafe rules', () => {
  const successful = execute('prepare.cjs', {pages: [[], rules()]});
  assert.equal(successful.status, 0, successful.stderr);
  assert.ok(successful.calls.some(call => call.args.includes('--paginate') && call.args.includes('--slurp')));
  assert.doesNotMatch(successful.calls.find(call => call.args[1] === 'graphql').body.query, /branchProtectionRule/);
  for (const input of [{rulesError: true}, {pages: {}}, {pages: []}, {pages: [null]}, {pages: [[null]]},
    {pages: [[{type: 'required_status_checks', parameters: {strict_required_status_checks_policy: true, required_status_checks: [{context: 'Dependency safety'}]}}]]},
    {pages: [[{type: 'required_status_checks', parameters: {strict_required_status_checks_policy: true, required_status_checks: [{context: 'Dependency safety', integration_id: 1}]}}]]},
    {pages: [[{type: 'required_status_checks', parameters: {strict_required_status_checks_policy: false, required_status_checks: [{context: 'Dependency safety', integration_id: 15368}]}}]]}]) {
    const result = execute('prepare.cjs', input);
    const merges = result.calls.filter(call => call.args[0] === 'pr');
    assert.equal(merges.length, 1);
    assert.ok(merges[0].args.includes('--disable-auto'));
  }
});
test('writer commits only generated paths with a head compare-and-swap, then dispatches fresh CI', () => {
  const value = fixture(); value.files[0].filename = 'scripts/text-editor/package-lock.json';
  const result = execute('commit-editor.cjs', value, [generated()]);
  assert.equal(result.status, 0, result.stderr);
  const write = result.calls.find(call => call.body?.query.startsWith('mutation')).body.variables.input;
  assert.equal(write.expectedHeadOid, head);
  assert.equal(write.branch.repositoryNameWithOwner, value.repository);
  assert.deepEqual(write.fileChanges.additions.map(file => file.path), BUNDLES.map(name => `web/vendor/${name}`));
  assert.equal(result.calls.at(-1).args[0], 'workflow');
  assert.deepEqual(result.calls.at(-1).args.slice(-2), ['--ref', value.pr.head.ref]);
});
test('stale source heads and concurrent writes cannot receive generated files or a CI dispatch', () => {
  const value = fixture(); value.files[0].filename = 'scripts/text-editor/package-lock.json';
  const stale = structuredClone(value); stale.pr.head.sha = 'b'.repeat(40);
  for (const input of [stale, {...value, headMoved: true}]) {
    const result = execute('commit-editor.cjs', input, [generated()]);
    assert.notEqual(result.status, 0);
    assert.ok(!result.calls.some(call => call.args[0] === 'workflow'));
    assert.ok(!result.calls.some(call => call.args[0] === 'pr'));
  }
});
