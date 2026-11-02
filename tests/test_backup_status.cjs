const {test}=require('node:test');
const assert=require('node:assert/strict');
const {backupSummary}=require('../web/libraries.js');
const repo=(id,auto=true)=>({id,kind:'github',auto_sync_enabled:auto});

test('local files and references never imply that GitHub backup is enabled',()=>{
  const state=backupSummary([{id:'collection',kind:'local'},repo('reference',false)],[]);
  assert.equal(state.phase,'local');assert.match(state.text,/backup is off/);
  assert.equal(backupSummary(null,[]).phase,'checking');
});
test('all automatic destinations must succeed before the summary reports success',()=>{
  const libraries=[repo('one'),repo('two')],states=[{library_id:'one',phase:'synced'}];
  assert.equal(backupSummary(libraries,states).phase,'pending');
  states.push({library_id:'two',phase:'error'});assert.equal(backupSummary(libraries,states).phase,'error');
  states[1].phase='syncing';assert.equal(backupSummary(libraries,states).phase,'syncing');
  states[1].phase='synced';assert.equal(backupSummary(libraries,states).phase,'synced');
});
test('local note failures and unverified status cannot be hidden by an earlier successful backup',()=>{
  const libraries=[repo('one')],states=[{library_id:'one',phase:'synced'}];
  assert.equal(backupSummary(libraries,states,'pending').phase,'pending');
  assert.equal(backupSummary(libraries,states,'error').phase,'error');
  assert.equal(backupSummary(libraries,states,'saved',true).phase,'error');
  assert.doesNotMatch(backupSummary(libraries,states,'saved',true).text,/succeeded/);
});
