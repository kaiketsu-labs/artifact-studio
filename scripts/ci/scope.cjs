// Compute optional platform coverage; unknown history always runs both suites.
const {execFileSync} = require('node:child_process');
const {appendFileSync} = require('node:fs');

function scope(files) {
  const full = {docker: true, macos: true};
  if (files === null) return full;
  const selected = {docker: false, macos: false};
  for (const file of files) {
    // Only immediate docs/*.md files are included by .dockerignore.
    const docker = ['Dockerfile', '.dockerignore', '.github/workflows/docker.yml',
      'tests/integration/test_docker.py'].includes(file) ||
      /^(compose(\.[^/]+)?\.yaml$|docker\/|artifact_studio\/|web\/|runtime\/|agent\/|docs\/[^/]+\.md$)/.test(file);
    const macos = ['web/brand/mark.png', 'scripts/install_macos.py', 'scripts/test_macos_logs.py',
      '.github/workflows/macos.yml'].includes(file) || /^(macos\/|tests\/native\/)/.test(file);
    if (docker || macos) {
      selected.docker ||= docker;
      selected.macos ||= macos;
      continue;
    }
    const independent = /^(docs\/|\.agents\/skills\/|\.claude\/commands\/|\.github\/ISSUE_TEMPLATE\/)/.test(file) ||
      /^scripts\/(release\/|dependencies\/|(benchmark_|capture_)[^/]+\.py$)/.test(file) ||
      /^tests\/(integration\/)?test_[^/]+\.(py|cjs)$/.test(file) ||
      ['AGENTS.md', 'README.md', 'CHANGELOG.md'].includes(file);
    if (!independent) {
      // Shared fixtures, new top-level inputs and unrecognised paths need both owners.
      return full;
    }
  }
  return selected;
}

if (require.main === module) {
  const base = process.env.BASE_SHA || '';
  let files = null;
  if (/^[a-f0-9]{40}$/.test(base) && !/^0+$/.test(base)) {
    try {
      files = execFileSync('git', ['diff', '--name-only', '--no-renames', '-z', base, 'HEAD'], {encoding: 'utf8'}).split('\0').filter(Boolean);
    } catch { /* A missing base must broaden coverage, never skip it. */ }
  }
  for (const [name, value] of Object.entries(scope(files))) {
    appendFileSync(process.env.GITHUB_OUTPUT, `${name}=${value}\n`);
  }
}
module.exports = {scope};
