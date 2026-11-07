// One always-reported protection check, including applicable platform journeys.
function failures(needs) {
  const required = ['scope', 'quality', 'browser', 'agents', 'audit'];
  const failed = required.filter(name => needs[name]?.result !== 'success');
  for (const name of ['docker', 'macos']) {
    const selected = needs.scope?.outputs?.[name];
    const expected = selected === 'true' ? 'success' : selected === 'false' ? 'skipped' : null;
    if (!expected || needs[name]?.result !== expected) failed.push(name);
  }
  return failed;
}
if (require.main === module) {
  const failed = failures(JSON.parse(process.env.CI_RESULTS || '{}'));
  if (failed.length) {
    console.error(`Required checks did not pass: ${failed.join(', ')}`);
    process.exitCode = 1;
  }
}
module.exports = {failures};
