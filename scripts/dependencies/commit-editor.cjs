const {readFileSync, readdirSync, lstatSync} = require('node:fs');
const path = require('node:path');
const {BUNDLES, candidate} = require('./policy.cjs');
const {gh, graphql, inputs, inspect, summary} = require('./github.cjs');

function additions(directory) {
  const names = readdirSync(directory).sort();
  if (JSON.stringify(names) !== JSON.stringify([...BUNDLES].sort())) throw Error('Unexpected generated files');
  return BUNDLES.map(name => {
    const file = path.join(directory, name);
    const stat = lstatSync(file);
    if (!stat.isFile() || stat.size < 1 || stat.size > 5 * 1024 * 1024) throw Error('Invalid generated file');
    return {path: `web/vendor/${name}`, contents: readFileSync(file).toString('base64')};
  });
}

function run(directory) {
  const input = inspect(inputs());
  const reason = candidate(input);
  if (reason || !input.files.some(file => file.filename.startsWith('scripts/text-editor/'))) throw Error(reason || 'Not an editor update');
  const files = additions(directory);
  // GitHub creates a single commit atomically on this exact source head. No
  // checked-out PR code or dependency is ever run with this job's write token.
  const result = graphql(`mutation($input:CreateCommitOnBranchInput!) {
    createCommitOnBranch(input:$input) { commit { oid } }
  }`, {input: {
    branch: {repositoryNameWithOwner: input.repository, branchName: input.pr.head.ref},
    expectedHeadOid: input.head,
    message: {headline: 'fix(deps): rebuild the offline editor and licenses'},
    fileChanges: {additions: files},
  }});
  summary(`Generated editor committed at ${result.createCommitOnBranch.commit.oid}.`);
  // GITHUB_TOKEN commits do not trigger pull_request CI. Explicit dispatch
  // records all checks against the new head, including platform journeys.
  gh(['workflow', 'run', 'ci.yml', '--repo', input.repository, '--ref', input.pr.head.ref]);
  summary('Requested CI for the generated commit. A missing or failed check blocks native auto-merge.');
}
if (require.main === module) run(process.argv[2]);
module.exports = {additions, run};
