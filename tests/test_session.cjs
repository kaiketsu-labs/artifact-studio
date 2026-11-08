const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/session.js', 'utf8');
function setup(fetch) {
  let events = 0;
  const window = {fetch, dispatchEvent: () => events++};
  vm.runInNewContext(source, {window, Event: class {}});
  return {window, events: () => events};
}
const ok = () => ({status: 200, ok: true, json: async () => ({mode: 'hosted'})});
(async () => {
  let calls = 0;
  const shared = setup(async () => {calls++; return ok();});
  await Promise.all([shared.window.ArtifactSession.get(), shared.window.ArtifactSession.get()]);
  assert.equal(calls, 1);
  await shared.window.ArtifactSession.get({refresh: true});
  assert.equal(calls, 2);
  assert.doesNotThrow(() => shared.window.ArtifactSession.assertActive());

  let resolve;
  const race = setup(() => new Promise(done => {resolve = done;}));
  const pending = race.window.ArtifactSession.get();
  race.window.ArtifactSession.observe({status: 401});
  resolve(ok());
  await assert.rejects(pending, error => error.expired);
  await assert.rejects(race.window.ArtifactSession.get({refresh: true}), error => error.expired);
  race.window.ArtifactSession.observe({status: 401});
  assert.equal(race.events(), 1);
  assert.equal(race.window.artifactStudioSession, null);
  assert.throws(() => race.window.ArtifactSession.assertActive(), error => error.expired && error.status === 401);

  const redirect = setup(async () => ({status: 200, ok: true, redirected: true, headers: {get: () => 'text/html; charset=utf-8'}}));
  await assert.rejects(redirect.window.ArtifactSession.get(), error => error.expired);
  assert.equal(redirect.events(), 1);
  assert.throws(() => redirect.window.ArtifactSession.assertActive(), error => error.expired);
  console.log('Session behavior checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
