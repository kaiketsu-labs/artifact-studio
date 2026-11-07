const {execFileSync} = require('node:child_process');
const {appendFileSync} = require('node:fs');

function gh(args, input) {
  return execFileSync('gh', args, {encoding: 'utf8', input, maxBuffer: 20 * 1024 * 1024});
}
function api(endpoint) { return JSON.parse(gh(['api', endpoint])); }
function graphql(query, variables) {
  const response = JSON.parse(gh(['api', 'graphql', '--input', '-'], JSON.stringify({query, variables})));
  if (response.errors) throw Error(JSON.stringify(response.errors));
  return response.data;
}
function inputs() {
  const repository = process.env.GH_REPO || '';
  const number = Number(process.env.PR_NUMBER);
  if (!/^[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+$/.test(repository) || !Number.isSafeInteger(number) || number < 1) throw Error('Invalid repository or PR number');
  const endpoint = `repos/${repository}/pulls/${number}`;
  return {repository, number, head: process.env.PR_HEAD, endpoint};
}
function inspect(input) {
  return {
    ...input,
    pr: api(input.endpoint),
    files: api(`${input.endpoint}/files?per_page=100`),
    commits: api(`${input.endpoint}/commits?per_page=100`),
  };
}
function output(name, value) {
  appendFileSync(process.env.GITHUB_OUTPUT, `${name}=${value}\n`);
}
function summary(message) {
  console.log(message);
  if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${message}\n`);
}
module.exports = {gh, api, graphql, inputs, inspect, output, summary};
