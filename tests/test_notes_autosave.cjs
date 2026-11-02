const {test}=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('../web/notes-autosave.js');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};

function fixture(write=async()=>{},options={}){
  let next=0;const timers=new Map(),changes=[];
  const notes=create({write,...options,change:(key,state)=>changes.push({key,state}),
    setTimer:fn=>{timers.set(++next,fn);return next;},clearTimer:id=>timers.delete(id)});
  return {notes,changes,async idle(){const due=[...timers.values()];timers.clear();for(const fn of due)fn();await settle();}};
}

test('typing coalesces and a completed save advances the next concurrency baseline',async()=>{
  const writes=[],check=fixture(async(key,body)=>writes.push({key,...body}));
  check.notes.observe('deck/one','Original');
  check.notes.edit('deck/one','A draft');check.notes.edit('deck/one','The final wording');
  assert.equal(writes.length,0);await check.idle();
  assert.deepEqual(writes,[{key:'deck/one',notes:'The final wording',expected_notes:'Original'}]);
  assert.equal(check.notes.state('deck/one').phase,'saved');
  assert.equal(check.notes.state('deck/one').draft,null);
  check.notes.edit('deck/one','One more change');await check.idle();
  assert.equal(writes[1].expected_notes,'The final wording');
});

test('background refreshes do not restart the typing pause',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const writes=[],notes=create({write:async(key,body)=>writes.push(body)});
  notes.observe('deck/one','Original');notes.edit('deck/one','New wording');
  t.mock.timers.tick(400);notes.observe('deck/one','Original');
  t.mock.timers.tick(301);await settle();
  assert.deepEqual(writes,[{notes:'New wording',expected_notes:'Original'}]);
  assert.equal(notes.state('deck/one').phase,'saved');
});

test('in-flight saves serialize later edits and retain their original slide IDs',async()=>{
  const first=deferred(),writes=[];
  const {notes}=fixture(async(key,body)=>{writes.push({key,...body});if(writes.length===1)await first.promise;});
  notes.observe('deck/one','Original');notes.edit('deck/one','First edit');
  const pending=notes.flush('deck/one');await settle();
  notes.edit('deck/one','Latest edit');const latest=notes.flush('deck/one');
  notes.observe('deck/two','Other slide');notes.edit('deck/two','Other notes');await notes.flush('deck/two');
  assert.equal(writes.filter(item=>item.key==='deck/one').length,1);
  first.resolve();await pending;await latest;
  assert.deepEqual(writes.filter(item=>item.key==='deck/one'),[
    {key:'deck/one',notes:'First edit',expected_notes:'Original'},
    {key:'deck/one',notes:'Latest edit',expected_notes:'First edit'}
  ]);
  assert.equal(notes.state('deck/two').expected,'Other notes');
});

test('reversing an edit during a request remains a recoverable draft until the reversal saves',async()=>{
  const first=deferred(),writes=[];
  const {notes}=fixture(async(key,body)=>{writes.push({...body});if(writes.length===1)await first.promise;});
  notes.observe('deck/one','Original');notes.edit('deck/one','Changed');
  const pending=notes.flush('deck/one');await settle();notes.edit('deck/one','Original');
  assert.deepEqual(notes.state('deck/one').draft,{notes:'Original',expected_notes:'Original',pending_notes:'Changed'});
  const reverted=notes.flush('deck/one');first.resolve();await pending;await reverted;
  assert.deepEqual(writes[1],{notes:'Original',expected_notes:'Changed'});
  assert.equal(notes.state('deck/one').draft,null);
});

test('conflicts stop automatic writes until the human reviews a new baseline',async()=>{
  const writes=[];let conflict=true;
  const {notes}=fixture(async(key,body)=>{writes.push(body);if(conflict)throw Object.assign(new Error('Changed elsewhere'),{status:409});});
  notes.observe('deck/one','Original');notes.edit('deck/one','My wording');
  assert.equal(await notes.flush('deck/one'),false);
  notes.observe('deck/one','Their wording');notes.edit('deck/one','My refined wording');
  assert.equal(await notes.flushAll('',{retry:true}),false);assert.equal(writes.length,1);
  assert.equal(notes.state('deck/one').value,'My refined wording');
  conflict=false;assert.equal(await notes.resolve('deck/one','Their wording',true),true);
  assert.deepEqual(writes[1],{notes:'My refined wording',expected_notes:'Their wording'});
});

test('failed requests keep drafts and retry only after an edit or explicit retry',async()=>{
  let offline=true,attempts=0;
  const check=fixture(async()=>{attempts++;if(offline)throw Error('Offline');});
  check.notes.observe('deck/one','Original');check.notes.edit('deck/one','Keep this');await check.idle();
  assert.equal(check.notes.state('deck/one').phase,'error');assert.equal(check.notes.status(),'error');
  assert.equal(check.notes.state('deck/one').draft.notes,'Keep this');
  await check.idle();await check.notes.flushAll();assert.equal(attempts,1);
  offline=false;assert.equal(await check.notes.flushAll('',{retry:true}),true);
  assert.equal(attempts,2);assert.equal(check.notes.status(),'saved');
});

test('restored drafts detect external changes and accepting saved notes does not write',async()=>{
  const writes=[],check=fixture(async(key,body)=>writes.push(body));
  check.notes.observe('deck/one','Saved elsewhere',{notes:'My draft',expected_notes:'Earlier'});
  await check.idle();assert.equal(writes.length,0);assert.equal(check.notes.firstFailed(),'deck/one');
  assert.equal(await check.notes.resolve('deck/one','Saved elsewhere',false),true);
  assert.equal(writes.length,0);assert.equal(check.notes.state('deck/one').value,'Saved elsewhere');
  check.notes.observe('deck/two','Already delivered',{notes:'Already delivered',expected_notes:'Earlier'});
  assert.equal(check.notes.state('deck/two').draft,null);
});

test('a restored reversal fences a request left in flight by the previous page',async()=>{
  const writes=[];
  const {notes}=fixture(async(key,body)=>{writes.push(body);return {notes_revision:body.expected_revision+1};});
  notes.observe('deck/one','Original',{
    notes:'Original',expected_notes:'Original',expected_revision:4,pending_notes:'Changed'
  },4);
  await notes.flushAll();
  assert.deepEqual(writes,[{notes:'Original',expected_notes:'Original',expected_revision:4}]);
  assert.equal(notes.state('deck/one').draft,null);
});

test('an acknowledged predecessor restores the right baseline for a later draft',async()=>{
  const writes=[];
  const {notes}=fixture(async(key,body)=>{writes.push(body);return {notes_revision:6};});
  notes.observe('deck/one','First edit',{
    notes:'Latest edit',expected_notes:'Original',expected_revision:4,pending_notes:'First edit'
  },5);
  await notes.flushAll();
  assert.deepEqual(writes,[{notes:'Latest edit',expected_notes:'First edit',expected_revision:5}]);
});

test('reverting after a lost acknowledgement reconciles its own predecessor before retrying',async()=>{
  const writes=[];
  const check=fixture(async(key,body)=>{
    writes.push(body);
    if(writes.length===1)throw Error('Acknowledgement lost');
    if(writes.length===2)throw Object.assign(Error('Earlier write delivered'),{status:409});
    return {notes_revision:3};
  },{read:async()=>({notes:'Changed',notes_revision:2})});
  check.notes.observe('deck/one','Original',null,1);check.notes.edit('deck/one','Changed');
  await check.idle();assert.equal(check.notes.state('deck/one').phase,'error');
  check.notes.edit('deck/one','Original');await check.notes.flushAll();
  assert.deepEqual(writes.at(-1),{notes:'Original',expected_notes:'Changed',expected_revision:2});
  assert.equal(check.notes.state('deck/one').phase,'saved');
  assert.equal(check.notes.state('deck/one').draft,null);
});
