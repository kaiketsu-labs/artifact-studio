// Execute the publication guard from the workflow with a mocked GitHub CLI.
const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { runInNewContext } = require('node:vm');

const workflow = fs.readFileSync(path.resolve(__dirname, '../../.github/workflows/release-please.yml'), 'utf8');
test('only an explicit manual publish on main enables publication', () => {
  assert.match(workflow, /default: prepare/);
  const prepare = workflow.match(/  prepare:\n[\s\S]*?    if: (.*)/)[1];
  const publish = workflow.match(/  publish:\n[\s\S]*?    if: (.*)/)[1];
  assert.match(workflow.split('  publish:')[0], /skip-github-release: true/);
  for (const [event, ref, command, expected] of [
    ['push', 'main', undefined, [true, false]],
    ['workflow_dispatch', 'main', 'prepare', [true, false]],
    ['workflow_dispatch', 'main', 'publish', [false, true]],
    ['workflow_dispatch', 'feature', 'publish', [false, false]],
    ['pull_request', 'feature', undefined, [false, false]],
  ]) {
    const context = { github: { event_name: event, ref: `refs/heads/${ref}` }, inputs: { command } };
    assert.deepEqual([prepare, publish].map(expression => Boolean(runInNewContext(expression, context))), expected);
  }
});
const match = workflow.match(/      - name: Require successful CI for main and pending releases\n[\s\S]*?        run: \|\n((?:          .*\n|\n)+?)(?=      - uses:)/);
assert.ok(match, 'publication guard must be present in the workflow');
const guard = match[1].split('\n').map(line => line.startsWith('          ') ? line.slice(10) : line).join('\n');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'release-guard-'));
after(() => fs.rmSync(temp, { recursive: true, force: true }));
fs.writeFileSync(path.join(temp, 'gh'), `#!/usr/bin/env node
const args = process.argv.slice(2);
const outcomes = JSON.parse(process.env.MOCK_OUTCOMES);
if (args[0] === 'run' && args[1] === 'list') {
  for (const [flag, expected] of [['--workflow', 'ci.yml'], ['--branch', 'main'], ['--event', 'push'], ['--limit', '1']]) {
    if (args[args.indexOf(flag) + 1] !== expected) process.exit(2);
  }
  console.log(outcomes[args[args.indexOf('--commit') + 1]] || 'missing');
} else if (args[0] === 'pr' && args[1] === 'list') {
  for (const [flag, expected] of [['--state', 'merged'], ['--base', 'main'], ['--label', 'autorelease: pending'], ['--limit', '200']]) {
    if (args[args.indexOf(flag) + 1] !== expected) process.exit(2);
  }
  process.stdout.write(process.env.MOCK_PENDING);
} else if (args[0] === 'api' && args[1] === 'repos/owner/repo/commits/main') {
  console.log(process.env.MOCK_CURRENT);
} else process.exit(2);
`, { mode: 0o755 });

function run({ outcomes = { mainsha: 'success', releasesha: 'success' }, pending = 'releasesha\n', current = 'mainsha' } = {}) {
  return spawnSync('bash', ['-e', '-c', guard], {
    encoding: 'utf8',
    env: { ...process.env, PATH: `${temp}:${process.env.PATH}`, GH_REPO: 'owner/repo', RELEASE_SHA: 'mainsha',
      MOCK_OUTCOMES: JSON.stringify(outcomes), MOCK_PENDING: pending, MOCK_CURRENT: current },
  });
}

test('allows a previously merged release after both commits pass CI', () => {
  assert.equal(run().status, 0);
});
test('rejects failed or missing main CI', () => {
  for (const outcome of ['failure', 'missing']) {
    const result = run({ outcomes: { mainsha: outcome, releasesha: 'success' } });
    assert.notEqual(result.status, 0);
    assert.match(result.stdout, /CI to pass on main/);
  }
});
test('rejects failed or missing CI on any pending release commit', () => {
  for (const outcome of ['failure', 'missing']) {
    const result = run({ pending: 'releasesha\notherrelease\n', outcomes: { mainsha: 'success', releasesha: 'success', otherrelease: outcome } });
    assert.notEqual(result.status, 0);
    assert.match(result.stdout, /CI to pass on release commit otherrelease/);
  }
});
test('rejects no pending release or a missing merge commit', () => {
  for (const pending of ['', 'missing\n']) assert.notEqual(run({ pending }).status, 0);
});
test('rejects main moving after the run was requested', () => {
  const result = run({ current: 'newmain' });
  assert.notEqual(result.status, 0);
  assert.match(result.stdout, /Main changed/);
});
