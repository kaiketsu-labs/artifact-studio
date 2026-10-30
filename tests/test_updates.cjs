const {test}=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('../web/updates.js');

function fixture({push=true}={}){
  const handlers=new Map(),sources=[],calls=[];
  const document={hidden:false,addEventListener:(name,fn)=>handlers.set(name,fn),removeEventListener:name=>handlers.delete(name)};
  let periodic;
  const updates=create({document,refresh:async()=>calls.push('snapshot'),workflow:async id=>calls.push('workflow:'+id),createSource:push?url=>{
    const events=new Map(),source={url,readyState:0,closed:false,addEventListener:(name,fn)=>events.set(name,fn),close(){this.closed=true;this.readyState=2;},change(){events.get('change')();}};
    sources.push(source);return source;
  }:null,repeat:fn=>{periodic=fn;return 1;},cancelRepeat:()=>{periodic=null;},onError:error=>calls.push(error.message)});
  return {updates,document,sources,calls,visibility:()=>handlers.get('visibilitychange')(),tick:()=>periodic?.()};
}

test('healthy push polls only availability while every actual change still refreshes',async()=>{
  const f=fixture();f.updates.watch('deck-a');f.sources[0].readyState=1;
  for(let i=0;i<17;i++)await f.updates.poll();
  assert.equal(f.calls.length,17);assert.ok(f.calls.every(call=>call==='workflow:deck-a'));
  f.sources[0].change();assert.equal(f.calls.at(-1),'snapshot');
  f.updates.stop();
});

test('connecting, disconnected and unsupported push retain the full-refresh fallback',async()=>{
  const f=fixture();f.updates.watch('deck-a');await f.updates.poll();
  f.sources[0].readyState=2;await f.updates.poll();
  assert.deepEqual(f.calls,['snapshot','snapshot']);f.updates.stop();
  const plain=fixture({push:false});plain.updates.watch('deck');await plain.updates.poll();
  assert.deepEqual(plain.calls,['snapshot']);plain.updates.stop();
});

test('hidden tabs close their stream and do no polling; returning catches up immediately',async()=>{
  const f=fixture();f.updates.watch('deck-a');const old=f.sources[0];
  f.document.hidden=true;f.visibility();assert.ok(old.closed);
  await f.updates.poll();old.change();assert.deepEqual(f.calls,[]);
  f.document.hidden=false;f.visibility();
  assert.equal(f.sources.length,2);assert.deepEqual(f.calls,['snapshot']);
  old.change();assert.deepEqual(f.calls,['snapshot']);f.updates.stop();
});

test('switching decks discards old notifications and stopping removes background work',async()=>{
  const f=fixture();f.updates.watch('deck-a');const old=f.sources[0];
  f.updates.watch('deck-b');old.change();assert.deepEqual(f.calls,[]);
  f.sources[1].readyState=1;await f.updates.poll();assert.deepEqual(f.calls,['workflow:deck-b']);
  f.updates.stop();f.sources[1].change();f.tick();await f.updates.poll();
  assert.deepEqual(f.calls,['workflow:deck-b']);assert.ok(f.sources.every(source=>source.closed));
});

test('slow availability calls do not overlap and failure falls back to a snapshot',async()=>{
  let release,count=0,refreshes=0;
  const document={hidden:false,addEventListener(){},removeEventListener(){}};
  const updates=create({document,refresh:async()=>{refreshes++;},workflow:()=>{count++;return new Promise((resolve,reject)=>{release=()=>reject(new Error('old server or lost connection'));});},createSource:()=>({readyState:1,addEventListener(){},close(){}}),repeat:()=>1,cancelRepeat(){}});
  updates.watch('deck');const first=updates.poll();await updates.poll();assert.equal(count,1);
  release();await first;assert.equal(refreshes,1);updates.stop();
});
