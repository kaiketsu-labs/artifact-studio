// Privileged jobs inspect GitHub metadata only; they never execute PR source.
const DEPENDABOT = 'dependabot[bot]';
const SAFETY_CHECK = 'Dependency safety';
const MANIFESTS = new Set([
  'pyproject.toml', 'uv.lock',
  'scripts/release/package.json', 'scripts/release/package-lock.json',
  'scripts/text-editor/package.json', 'scripts/text-editor/package-lock.json',
]);
const BUNDLES = ['codemirror.js', 'codemirror.js.LEGAL.txt', 'codemirror-LICENSE.txt'];

function candidate({pr, files, commits, repository, head}) {
  if (pr?.user?.login !== DEPENDABOT || pr.user.type !== 'Bot') return 'Not a Dependabot PR';
  if (pr.state !== 'open' || pr.draft || pr.base?.ref !== 'main') return 'PR is not ready for main';
  if (pr.head?.repo?.full_name !== repository || pr.base?.repo?.full_name !== repository) return 'Foreign repository';
  if (!/^[a-f0-9]{40}$/.test(head || '') || pr.head.sha !== head) return 'Head changed';
  if (!/^dependabot\/[a-zA-Z0-9._/-]+$/.test(pr.head.ref || '')) return 'Unexpected branch';
  if (!Array.isArray(files) || !files.length || files.length !== pr.changed_files || files.length >= 100) return 'Incomplete file inventory';
  if (files.some(file => !MANIFESTS.has(file.filename) || file.status !== 'modified' || file.previous_filename)) return 'Includes changes outside dependency manifests';
  if (!Array.isArray(commits) || !commits.length || commits.length !== pr.commits || commits.length >= 100) return 'Incomplete commit inventory';
  if (commits.at(-1).sha !== head || commits.some(commit => commit.author?.login !== DEPENDABOT || !commit.commit?.verification?.verified)) return 'Contains unverified or non-Dependabot commits';
  return '';
}

function mergeReason(input, metadata, settings) {
  const reason = candidate(input);
  if (reason) return reason;
  if (!['version-update:semver-patch', 'version-update:semver-minor'].includes(metadata)) return 'Major or unknown update needs review';
  if (!settings?.autoMergeAllowed) return 'Repository auto-merge is disabled';
  const branch = settings.defaultBranchRef;
  const rule = branch?.branchProtectionRule;
  if (branch?.name !== 'main' || !rule?.requiresStatusChecks || !rule.requiresStrictStatusChecks || !rule.requiredStatusCheckContexts?.includes(SAFETY_CHECK)) return 'Require up-to-date branches and the Dependency safety check on main';
  return '';
}

module.exports = {BUNDLES, SAFETY_CHECK, candidate, mergeReason};
