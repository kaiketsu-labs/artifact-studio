const {candidate, mergeReason} = require('./policy.cjs');
const {gh, api, graphql, inputs, inspect, output, summary} = require('./github.cjs');

function run() {
  const identity = inputs();
  const current = api(identity.endpoint);
  // Revoke a previous queue entry before metadata/settings reads that might
  // fail. Human edits and changed protections must not inherit old approval.
  if (current.auto_merge) gh(['pr', 'merge', String(identity.number), '--repo', identity.repository, '--disable-auto']);
  const input = inspect(identity);
  const valid = !candidate(input);
  output('editor', valid && input.files.some(file => file.filename.startsWith('scripts/text-editor/')));
  // Re-check this setting on each event; a previous eligibility result cannot
  // authorize a later human edit, a major update, or changed protection rules.
  const [owner, name] = input.repository.split('/');
  const settings = graphql(`query($owner:String!,$name:String!) {
    repository(owner:$owner,name:$name) { autoMergeAllowed defaultBranchRef {
      name branchProtectionRule { requiresStatusChecks requiresStrictStatusChecks requiredStatusCheckContexts }
    } }
  }`, {owner, name}).repository;
  const reason = mergeReason(input, process.env.UPDATE_TYPE || '', settings);
  if (reason) {
    summary(`Automatic merge not enabled: ${reason}.`);
    return;
  }
  gh(['pr', 'merge', String(input.number), '--repo', input.repository, '--auto', '--squash', '--match-head-commit', input.head]);
  summary('Native auto-merge enabled; GitHub must satisfy the protected Dependency safety check on an up-to-date branch.');
}
if (require.main === module) run();
module.exports = {run};
