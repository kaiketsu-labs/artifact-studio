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
test('full workspace backup status is visible when artifact sync is off',()=>{
  const libraries=[repo('one',false)],full=[{library_id:'one',settings:{enabled:true},phase:'scheduled',last_backup_at:''}];
  const summary=()=>backupSummary(libraries,[],'saved',false,full);
  assert.equal(summary().phase,'pending');
  full[0].phase='backing_up';assert.equal(summary().phase,'syncing');
  full[0].phase='backed_up';full[0].last_backup_at='2026-11-05T10:30:00Z';assert.equal(summary().phase,'synced');
  full[0].phase='error';assert.equal(summary().phase,'error');
  full[0].settings.enabled=false;assert.equal(summary().phase,'local');
});
test('a full backup cannot hide an artifact sync failure or local unsaved notes',()=>{
  const libraries=[repo('one')],full=[{library_id:'one',settings:{enabled:true},phase:'backed_up',last_backup_at:'2026-11-05T10:30:00Z'}];
  assert.equal(backupSummary(libraries,[{library_id:'one',phase:'error'}],'saved',false,full).phase,'error');
  assert.equal(backupSummary(libraries,[{library_id:'one',phase:'synced'}],'pending',false,full).phase,'pending');
});
test('an unchanged workspace reports success only after a verified full backup exists',()=>{
  const libraries=[repo('one',false)],full=[{library_id:'one',settings:{enabled:true},phase:'unchanged',last_backup_at:''}];
  const summary=()=>backupSummary(libraries,[],'saved',false,full);
  assert.equal(summary().phase,'pending');
  assert.doesNotMatch(summary().text,/succeeded/);
  full[0].last_backup_at='2026-11-05T10:30:00Z';
  assert.equal(summary().phase,'synced');
  assert.match(summary().text,/Last GitHub backup succeeded/);
  assert.equal(backupSummary(libraries,[],'pending',false,full).phase,'pending');
  assert.equal(backupSummary(libraries,[],'saved',true,full).phase,'error');
});
