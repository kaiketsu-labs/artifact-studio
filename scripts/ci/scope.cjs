// Compute optional platform coverage; unknown history always runs both suites.
const {execFileSync} = require('node:child_process');
const {appendFileSync} = require('node:fs');

function scope(files) {
  if (files === null) return {docker: true, macos: true};
  const shared = files.some(file => /^(pyproject\.toml|uv\.lock|\.github\/workflows\/ci\.yml|scripts\/ci\/)/.test(file));
  return {
    docker: shared || files.some(file => /^(Dockerfile$|\.dockerignore$|compose\.yaml$|docker\/|artifact_studio\/|web\/|runtime\/|agent\/|docs\/.*\.md$|tests\/integration\/test_docker\.py$|\.github\/workflows\/docker\.yml$)/.test(file)),
    macos: shared || files.some(file => /^(macos\/|tests\/native\/|scripts\/(install_macos|test_macos_logs)\.py$|\.github\/workflows\/macos\.yml$)/.test(file)),
  };
}

if (require.main === module) {
  const base = process.env.BASE_SHA || '';
  let files = null;
  if (/^[a-f0-9]{40}$/.test(base) && !/^0+$/.test(base)) {
    try {
      files = execFileSync('git', ['diff', '--name-only', '-z', base, 'HEAD'], {encoding: 'utf8'}).split('\0').filter(Boolean);
    } catch { /* A missing base must broaden coverage, never skip it. */ }
  }
  for (const [name, value] of Object.entries(scope(files))) {
    appendFileSync(process.env.GITHUB_OUTPUT, `${name}=${value}\n`);
  }
}
module.exports = {scope};
