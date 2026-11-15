const test=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('../web/presentation/warmup.js');
function fixture({navigator={},supported=true,canWarm=()=>true,fetcher}={}){
  let now=0,next=0;
  const timers=new Map(),idles=new Map(),events=new Map(),requests=[];
  const listen=(name,callback)=>{events.set(name,callback);};
  const doc={visibilityState:'visible',hasFocus:()=>true,activeElement:{matches:()=>false},addEventListener:listen};
  const view={navigator,AbortController,location:{origin:'http://localhost',href:'http://localhost/?deck=test'},addEventListener:listen,
    setTimeout(callback,delay){const id=++next;timers.set(id,{at:now+delay,callback});return id;},clearTimeout:id=>timers.delete(id),
    requestIdleCallback: supported?callback=>{const id=++next;idles.set(id,callback);return id;}:null,cancelIdleCallback:id=>idles.delete(id),
    fetch(url,options){requests.push({url,options});return fetcher?fetcher(url,options):Promise.resolve(response());}};
  const warmup=create({doc,view,canWarm});
  function tick(ms){now+=ms;for(const [id,task] of [...timers])if(task.at<=now){timers.delete(id);task.callback();}}
  function idle(remaining=50){for(const [id,callback] of [...idles]){idles.delete(id);callback({timeRemaining:()=>remaining});}}
  return {doc,view,warmup,requests,timers,idles,tick,idle,event:name=>events.get(name)?.()};
}
function response({size=100,cache='private, no-cache',ok=true,consume=async()=>new ArrayBuffer(0),cancel=async()=>{}}={}){
  return {ok,headers:{get:name=>name==='Content-Length'?String(size):cache},arrayBuffer:consume,body:{cancel}};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
test('wait for quiet and idle, then warm only three small files sequentially',async()=>{
  const f=fixture();f.warmup.update('/decks/test/index.html?present=1#first');
  f.tick(7999);assert.equal(f.idles.size,0);assert.equal(f.requests.length,0);
  f.tick(1);assert.equal(f.requests.length,0);f.idle();await settle();
  f.warmup.update('/decks/test/index.html?present=1#second');
  f.tick(8000);f.idle();await settle();f.tick(8000);f.idle();await settle();
  assert.deepEqual(f.requests.map(r=>r.url),['presentation.js','icons.js','slideshow.css'].map(name=>'http://localhost/decks/test/'+name));
  for(const {options} of f.requests){assert.equal(options.priority,'low');assert.equal(options.credentials,'same-origin');assert.equal(options.cache,undefined);}
  f.warmup.update('/decks/other/index.html');f.event('pointerdown');f.tick(60000);f.idle();assert.equal(f.requests.length,3);assert.equal(f.timers.size,0);
});
test('busy idle deadline and new input postpone speculative work',()=>{
  const f=fixture();f.warmup.update('/decks/test/index.html');f.tick(8000);f.idle(2);
  assert.equal(f.requests.length,0);f.tick(7000);f.event('keydown');f.tick(1000);f.idle();assert.equal(f.requests.length,0);
  f.tick(7000);f.idle();assert.equal(f.requests.length,1);
});
test('input aborts in-flight work; hiding cancels future work and foregrounding resumes',async()=>{
  const f=fixture({fetcher:(_url,{signal})=>new Promise((_,reject)=>signal.addEventListener('abort',()=>reject(Error('aborted'))))});
  f.warmup.update('/decks/test/index.html');f.tick(8000);f.idle();
  const signal=f.requests[0].options.signal;f.event('input');assert.equal(signal.aborted,true);await settle();
  f.doc.visibilityState='hidden';f.event('visibilitychange');assert.equal(f.timers.size,0);
  f.tick(60000);f.idle();assert.equal(f.requests.length,1);
  f.doc.visibilityState='visible';f.event('visibilitychange');f.tick(8000);f.idle();assert.equal(f.requests.length,2);
  f.warmup.cancel();assert.equal(f.requests[1].options.signal.aborted,true);await settle();
});
test('constrained devices, connections, editing and unsupported browsers do no warmup work',()=>{
  for(const navigator of [{connection:{saveData:true}},{connection:{effectiveType:'3g'}},{connection:{effectiveType:'2g'}},{onLine:false},{hardwareConcurrency:2},{deviceMemory:2}]){
    const f=fixture({navigator});f.warmup.update('/decks/test/index.html');assert.equal(f.timers.size,0);
  }
  for(const options of [{supported:false},{canWarm:()=>false}]){const f=fixture(options);f.warmup.update('/decks/test/index.html');assert.equal(f.timers.size,0);}
  const f=fixture();f.doc.activeElement.matches=()=>true;f.warmup.update('/decks/test/index.html');assert.equal(f.timers.size,0);
});
test('oversized, unknown-size and no-store responses are not read into memory',async()=>{
  for(const options of [{size:128*1024+1},{size:0},{cache:'no-store'},{ok:false}]){
    let consumed=false,cancelled=false;
    const f=fixture({fetcher:async()=>response({...options,consume:async()=>{consumed=true;},cancel:async()=>{cancelled=true;}})});
    f.warmup.update('/decks/test/index.html');f.tick(8000);f.idle();await settle();assert.equal(consumed,false);assert.equal(cancelled,true);f.warmup.cancel();
  }
});
test('switching decks cancels stale work and rejects hosted, missing and cross-origin URLs',()=>{
  const f=fixture();f.warmup.update('/decks/old/index.html');f.tick(8000);f.warmup.update('/decks/new/index.html');f.idle();assert.equal(f.requests.length,0);
  f.tick(8000);f.idle();assert.match(f.requests[0].url,/decks\/new\/presentation.js$/);
  for(const href of [null,'/decks/test/presenter','https://example.org/decks/test/index.html']){f.warmup.update(href);assert.equal(f.timers.size,0);}
});
