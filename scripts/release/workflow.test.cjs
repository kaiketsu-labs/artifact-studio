// Execute the publication guard from the workflow with a mocked GitHub CLI.
const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { runInNewContext } = require('node:vm');

const workflow = fs.readFileSync(path.resolve(__dirname, '../../.github/workflows/release-please.yml'), 'utf8');
test('release PR output can be empty when no PR was created', () => {
  assert.match(workflow, /RELEASE_PR: \$\{\{ fromJSON\(steps\.release\.outputs\.pr \|\| '\{\}'\)\.number \}\}/);
  assert.equal(JSON.parse('' || '{}').number, undefined);
  assert.equal(JSON.parse('{"number":48}' || '{}').number, 48);
});
test('only manual publish or successful same-repository main push CI enables publication', () => {
  assert.match(workflow, /workflows: \[CI\]/);
  assert.match(workflow, /default: prepare/);
  const prepare = workflow.match(/  prepare:\n[\s\S]*?    if: (.*)/)[1];
  const publish = workflow.match(/  publish:\n[\s\S]*?    if: >-\n([\s\S]*?)    runs-on:/)[1].trim();
  assert.match(workflow.split('  publish:')[0], /skip-github-release: true/);
  const base = { event: 'push', conclusion: 'success', head_branch: 'main', head_repository: { id: 1 } };
  for (const [event, ref, command, run, expected] of [
    ['push', 'main', undefined, base, [true, false]],
    ['workflow_dispatch', 'main', 'prepare', base, [true, false]],
    ['workflow_dispatch', 'main', 'publish', base, [false, true]],
    ['workflow_dispatch', 'feature', 'publish', base, [false, false]],
    ['workflow_run', 'main', undefined, base, [false, true]],
    ['workflow_run', 'main', undefined, { ...base, event: 'pull_request' }, [false, false]],
    ['workflow_run', 'main', undefined, { ...base, event: 'workflow_dispatch' }, [false, false]],
    ['workflow_run', 'main', undefined, { ...base, conclusion: 'failure' }, [false, false]],
    ['workflow_run', 'main', undefined, { ...base, head_branch: 'feature' }, [false, false]],
    ['workflow_run', 'main', undefined, { ...base, head_repository: { id: 2 } }, [false, false]],
    ['pull_request', 'main', undefined, base, [false, false]],
  ]) {
    const context = { github: { event_name: event, ref: `refs/heads/${ref}`, event: { workflow_run: run, repository: { id: 1 } } }, inputs: { command } };
    assert.deepEqual([prepare, publish].map(expression => Boolean(runInNewContext(expression, context))), expected);
  }
  assert.match(workflow, /if: steps.guard.outputs.publish == 'true'/);
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
  const fs = require('node:fs');
  const calls = Number(fs.readFileSync(process.env.MOCK_CALLS, 'utf8'));
  fs.writeFileSync(process.env.MOCK_CALLS, String(calls + 1));
  console.log(process.env.MOCK_CURRENT.split(',')[Math.min(calls, process.env.MOCK_CURRENT.split(',').length - 1)]);
} else process.exit(2);
`, { mode: 0o755 });

function run({ outcomes = { aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa: 'success', bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb: 'success' }, pending = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n', current = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', automatic = false } = {}) {
  fs.writeFileSync(path.join(temp, 'output'), '');
  fs.writeFileSync(path.join(temp, 'calls'), '0');
  const result = spawnSync('bash', ['-e', '-c', guard], {
    encoding: 'utf8',
    env: { ...process.env, PATH: `${temp}:${process.env.PATH}`, GH_REPO: 'owner/repo', RELEASE_SHA: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      MOCK_OUTCOMES: JSON.stringify(outcomes), MOCK_PENDING: pending, MOCK_CURRENT: current, AUTOMATIC: String(automatic), GITHUB_OUTPUT: path.join(temp, 'output'), MOCK_CALLS: path.join(temp, 'calls') },
  });
  result.outputFile = fs.readFileSync(path.join(temp, 'output'), 'utf8');
  return result;
}

test('allows a previously merged release after both commits pass CI', () => {
  for (const automatic of [false, true]) {
    const result = run({ automatic });
    assert.equal(result.status, 0);
    assert.match(result.outputFile, /publish=true/);
  }
});
test('rejects failed or missing main CI', () => {
  for (const outcome of ['failure', 'missing', 'queued']) {
    const result = run({ outcomes: { aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa: outcome, bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb: 'success' } });
    assert.notEqual(result.status, 0);
    assert.match(result.stdout, /CI to pass on main/);
  }
});
test('rejects failed or missing CI on any pending release commit', () => {
  for (const outcome of ['failure', 'missing']) {
    const result = run({ pending: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\ncccccccccccccccccccccccccccccccccccccccc\n', outcomes: { aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa: 'success', bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb: 'success', cccccccccccccccccccccccccccccccccccccccc: outcome } });
    assert.notEqual(result.status, 0);
    assert.match(result.stdout, /CI to pass on release commit cccccccccccccccccccccccccccccccccccccccc/);
  }
});
test('rejects no pending release or a missing merge commit', () => {
  for (const pending of ['', 'missing\n']) assert.notEqual(run({ pending }).status, 0);
});
test('rejects main moving after the run was requested', () => {
  const result = run({ current: 'dddddddddddddddddddddddddddddddddddddddd' });
  assert.notEqual(result.status, 0);
  assert.match(result.stdout, /Main changed/);
});

test('automatic stale runs and runs without pending releases skip publication', () => {
  for (const options of [{ current: 'dddddddddddddddddddddddddddddddddddddddd' }, { pending: '' }]) {
    const result = run({ ...options, automatic: true });
    assert.equal(result.status, 0);
    assert.equal(result.outputFile, '');
  }
});

test('rechecks main immediately before publication', () => {
  for (const automatic of [false, true]) {
    const result = run({ current: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa,dddddddddddddddddddddddddddddddddddddddd', automatic });
    assert.equal(result.status, automatic ? 0 : 1);
    assert.equal(result.outputFile, '');
    assert.match(result.stdout, /Main changed/);
  }
});
