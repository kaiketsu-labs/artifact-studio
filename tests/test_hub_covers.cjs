const {test}=require('node:test');
const assert=require('node:assert/strict');
const {create,coverURL}=require('../web/hub-covers.js');

const url=(name='one',version='1')=>`/api/hub/previews/${name}?v=${version}`;
const flush=async()=>{for(let index=0;index<4;index++)await new Promise(resolve=>setImmediate(resolve));};
const png=(size=8)=>new Response(new Uint8Array(size),{headers:{'Content-Type':'image/png'}});
const result=(status,code,seconds='1')=>new Response(JSON.stringify({code}),{status,headers:{'Content-Type':'application/json','Retry-After':seconds}});

function card(key,{top=0,fallback='',cached=false,renderer=true}={}){
  const retry={hidden:true,disabled:false},status={hidden:true,textContent:''};
  const open={attributes:{},focused:false,setAttribute(name,value){this.attributes[name]=value;},removeAttribute(name){delete this.attributes[name];},focus(){this.focused=true;}};
  const node={dataset:{},querySelector:selector=>selector==='[data-cover-retry]'?retry:selector==='[data-cover-status]'?status:selector==='.hub-card-open'?open:null};
  retry.dataset={coverRetry:key};retry.closest=()=>node;
  const cover={top,getBoundingClientRect(){return {top:this.top,bottom:this.top+150,left:0,right:240,width:240,height:150};}};
  const image={dataset:{coverUrl:key,coverFallback:fallback,coverCached:String(cached),coverRenderer:String(renderer)},isConnected:true,hidden:true,src:'',
    closest:selector=>selector==='.hub-card'?node:cover,removeAttribute(name){if(name==='src')this.src='';},getBoundingClientRect:()=>({width:0,height:0})};
  return {image,cover,node,retry,status,open};
}

function fixture(options={}){
  let clock=0,timerId=0,objectId=0,maxActive=0;
  const timers=new Map(),requests=[],created=[],revoked=[],observers=[],listeners=new Map(),decodes=[];
  const document={hidden:false,addEventListener(name,listener){listeners.set(name,listener);},removeEventListener(name){listeners.delete(name);}};
  const view={document,innerHeight:600,innerWidth:1000,Blob,AbortController,
    setTimeout(callback,delay){const id=++timerId;timers.set(id,{callback,at:clock+delay});return id;},clearTimeout:id=>timers.delete(id),
    addEventListener(name,listener){listeners.set(name,listener);},removeEventListener:name=>listeners.delete(name),
    URL:{createObjectURL(blob){const entry={url:`blob:cover-${++objectId}`,size:blob.size};created.push(entry);return entry.url;},revokeObjectURL:value=>revoked.push(value)},
    IntersectionObserver:class {
      constructor(callback,settings){this.callback=callback;this.settings=settings;this.targets=[];observers.push(this);}
      observe(target){this.targets.push(target);}disconnect(){this.targets=[];}
    },
    fetch(key,{signal}){return new Promise((resolve,reject)=>{
      const request={key,signal,settled:false,answer(response){this.settled=true;resolve(response);}};
      requests.push(request);maxActive=Math.max(maxActive,requests.filter(item=>!item.settled).length);
      signal.addEventListener('abort',()=>{if(!options.ignoreAbort){request.settled=true;reject(new Error('Aborted'));}},{once:true});
    });},
  };
  if(options.controlDecode)view.Image=class {decode(){return new Promise((resolve,reject)=>decodes.push({image:this,resolve,reject}));}};
  const loader=create({view,document,now:()=>clock,...options});
  const mount=items=>loader.mount({querySelectorAll:()=>items.map(item=>item.image),contains:image=>items.some(item=>item.image===image)});
  const intersection=(item,visible)=>{item.cover.top=visible?0:2000;observers.at(-1).callback([{target:item.cover,isIntersecting:visible}]);};
  const advance=async milliseconds=>{
    const target=clock+milliseconds;
    while(true){const next=[...timers.entries()].sort((a,b)=>a[1].at-b[1].at).find(([,timer])=>timer.at<=target);if(!next)break;
      const [id,timer]=next;clock=timer.at;timers.delete(id);timer.callback();await flush();
    }
    clock=target;await flush();
  };
  return {loader,mount,intersection,advance,requests,created,revoked,observers,document,listeners,decodes,maxActive:()=>maxActive};
}

test('only exact versioned local cover URLs are accepted',()=>{
  assert.equal(coverURL(url()),url());
  for(const value of [null,'/api/artifacts/one/files/large.png','https://example.test'+url(),'//example.test'+url(),url()+'#x','/api/hub/previews/one?v=','/api/hub/previews/one'])assert.equal(coverURL(value),'');
});

test('viewport covers take priority, deduplicate and cap requests even before observer delivery',async()=>{
  const f=fixture(),near=card(url('near'),{top:700}),first=card(url('first')),duplicate=card(url('first')),second=card(url('second')),far=card(url('far'),{top:2000});
  f.mount([near,first,duplicate,second,far]);
  assert.deepEqual(f.requests.map(item=>item.key),[url('first'),url('second')]);
  assert.equal(f.observers[0].settings.rootMargin,'120px 0px');
  assert.ok(f.observers[0].targets.includes(first.cover),'Observe the non-hidden cover, not its initially hidden image');
  f.requests[0].answer(png());await flush();
  assert.equal(first.image.src,duplicate.image.src);assert.equal(first.image.hidden,false);
  assert.deepEqual(f.requests.map(item=>item.key),[url('first'),url('second'),url('near')]);
  f.requests[1].answer(png());f.requests[2].answer(png());await flush();
  assert.equal(f.maxActive(),2);assert.equal(far.image.src,'');
  f.loader.destroy();
});

test('search and sorting reuse completed and in-flight URLs while discarded navigation aborts',async()=>{
  const f=fixture(),one=card(url()),two=card(url('two'));
  f.mount([one,two]);f.requests[0].answer(png());await flush();
  const reordered=[card(url('two')),card(url())];f.mount(reordered);
  assert.equal(f.requests.length,2);assert.equal(f.requests[1].signal.aborted,false);
  assert.equal(reordered[1].image.src,'blob:cover-1');
  f.loader.clear();assert.equal(f.requests[1].signal.aborted,true);await flush();
  f.mount([card(url())]);assert.equal(f.requests.length,2,'Returning to the library retains the decoded cover cache');
  f.loader.destroy();assert.deepEqual(f.revoked,['blob:cover-1']);
});

test('leaving the viewport aborts pending work; late responses cannot overwrite navigation',async()=>{
  const f=fixture({ignoreAbort:true}),one=card(url()),two=card(url('two'),{top:2000});
  f.mount([one,two]);f.intersection(one,false);
  assert.equal(f.requests[0].signal.aborted,true);f.loader.clear();
  f.requests[0].answer(png());await flush();
  assert.equal(one.image.src,'');assert.equal(f.created.length,0);
  f.mount([two]);f.intersection(two,true);assert.equal(f.requests.at(-1).key,url('two'));
  f.requests.at(-1).answer(png());await flush();f.intersection(two,false);
  assert.equal(two.image.src,'');f.intersection(two,true);
  assert.equal(two.image.src,'blob:cover-1');assert.equal(f.requests.length,2);
  f.loader.destroy();
});

test('pending saved covers honor Retry-After and settle without repeated grid requests',async()=>{
  const f=fixture(),one=card(url());f.mount([one]);
  f.requests[0].answer(result(202,'cover_pending','3'));await flush();
  assert.equal(one.node.dataset.previewState,'loading');assert.equal(one.retry.hidden,true);
  f.mount([one]);await f.advance(2999);assert.equal(f.requests.length,1);
  await f.advance(1);assert.equal(f.requests.length,2);
  f.requests[1].answer(png());await flush();assert.equal(one.node.dataset.previewState,'ready');
  await f.advance(60000);assert.equal(f.requests.length,2);
  f.loader.destroy();
});

test('reference previews use their visible container without a hub card and stop retries on dismissal',async()=>{
  const f=fixture(),reference=card(url('reference'));
  reference.image.closest=selector=>selector==='[data-cover-container],.hub-cover'?reference.cover:null;
  f.mount([reference]);
  assert.equal(f.requests.length,1,'The hidden image must use its visible reference container');
  assert.deepEqual(f.observers[0].targets,[reference.cover]);
  f.requests[0].answer(result(202,'cover_pending'));await flush();
  f.loader.clear();await f.advance(5000);assert.equal(f.requests.length,1);
  f.mount([reference]);assert.equal(f.requests.length,2);
  f.requests[1].answer(png());await flush();
  assert.equal(reference.image.hidden,false);assert.equal(reference.image.src,'blob:cover-1');
  f.loader.clear();f.mount([reference]);
  assert.equal(f.requests.length,2,'Reopening a reference reuses its decoded cover');
  f.loader.destroy();
});

test('admission retries are bounded and terminal failures survive grid rebuilding',async()=>{
  const f=fixture({retryLimit:2}),one=card(url());f.mount([one]);
  f.requests[0].answer(result(202,'cover_busy'));await flush();await f.advance(1000);
  f.requests[1].answer(result(202,'cover_busy'));await flush();await f.advance(2000);
  f.requests[2].answer(result(202,'cover_busy'));await flush();
  assert.equal(one.retry.hidden,false);assert.equal(one.retry.disabled,false);
  f.mount([card(url())]);await f.advance(60000);assert.equal(f.requests.length,3);
  f.loader.retry(url());assert.equal(f.requests.length,4);f.requests[3].answer(result(413,'cover_unavailable'));await flush();
  f.mount([card(url())]);assert.equal(f.requests.length,4);
  f.mount([card(url('one','changed'))]);assert.equal(f.requests.length,5,'A changed source is a new cover, independent of the failed version');
  f.loader.destroy();await flush();
});

test('renderer outages suppress cold requests and manual storms but keep saved previews available',async()=>{
  const f=fixture({concurrency:1}),first=card(url('first')),cold=card(url('cold')),saved=card(url('saved'),{cached:true});
  f.mount([first,cold,saved]);f.requests[0].answer(result(503,'renderer_unavailable','30'));await flush();
  assert.equal(f.requests.length,2);assert.equal(f.requests[1].key,url('saved'));
  assert.equal(first.retry.disabled,true);assert.equal(cold.retry.disabled,true);
  f.loader.retry(url('first'));f.mount([first,cold,saved]);assert.equal(f.requests.length,2);
  f.requests[1].answer(png());await flush();assert.equal(saved.node.dataset.previewState,'ready');
  await f.advance(30000);assert.equal(f.requests.at(-1).key,url('cold'));
  f.requests.at(-1).answer(png());await flush();
  assert.equal(first.retry.disabled,false);assert.equal(first.retry.hidden,false);
  f.loader.retry(url('first'));assert.equal(f.requests.at(-1).key,url('first'));
  f.loader.destroy();await flush();
});

test('a browser renderer outage still loads and retries uncached raster covers',async()=>{
  const f=fixture({concurrency:1}),html=card(url('html')),raster=card(url('raster'),{renderer:false}),other=card(url('other'));
  f.mount([html,raster,other]);f.requests[0].answer(result(503,'renderer_unavailable','30'));await flush();
  assert.equal(f.requests.at(-1).key,url('raster'));assert.equal(raster.retry.hidden,true);assert.equal(other.retry.disabled,true);
  f.requests[1].answer(result(404,'cover_unavailable'));await flush();
  assert.equal(raster.retry.hidden,false);assert.equal(raster.retry.disabled,false);
  f.loader.retry(url('raster'));assert.equal(f.requests.at(-1).key,url('raster'));
  f.requests[2].answer(png());await flush();assert.equal(raster.node.dataset.previewState,'ready');
  assert.equal(f.requests.length,3,'Uncached HTML still honors the browser renderer cooldown');
  f.loader.destroy();
});

test('previous saved cover stays painted until replacement has decoded and survives replacement failure',async()=>{
  const f=fixture({controlDecode:true}),one=card(url('one','new'),{fallback:url('one','old')});
  f.mount([one]);assert.deepEqual(f.requests.map(item=>item.key),[url('one','old'),url('one','new')]);
  f.requests[0].answer(png());await flush();f.decodes[0].resolve();await flush();
  const previous=one.image.src;assert.ok(previous);assert.equal(one.status.textContent,'Updating preview');
  f.requests[1].answer(png());await flush();assert.equal(one.image.src,previous,'Keep the saved preview during image decode');
  f.decodes[1].resolve();await flush();assert.notEqual(one.image.src,previous);assert.equal(one.status.hidden,true);
  const updated=card(url('one','next'),{fallback:url('one','new')});f.mount([updated]);
  assert.equal(updated.image.src,one.image.src || 'blob:cover-2');
  f.requests.at(-1).answer(result(503,'cover_unavailable','30'));await flush();
  assert.equal(updated.image.src,'blob:cover-2');assert.equal(updated.image.hidden,false);
  assert.equal(updated.status.textContent,'Earlier preview');assert.equal(updated.retry.hidden,false);
  assert.match(updated.open.attributes['aria-description'],/earlier saved preview/);
  f.loader.destroy();
});

test('keyboard retry keeps focus on the artifact when its transient control disappears',async()=>{
  const f=fixture(),one=card(url());f.mount([one]);f.requests[0].answer(result(404,'cover_unavailable'));await flush();
  assert.equal(one.retry.hidden,false);f.document.activeElement=one.retry;f.loader.retry(url());
  assert.equal(one.retry.hidden,true);assert.equal(one.open.focused,true);assert.equal(f.requests.length,2);
  f.requests[1].answer(png());await flush();assert.equal(one.open.attributes['aria-description'],undefined);
  f.loader.destroy();
});

test('a stale cover requests one registry refresh, never a refresh loop from old fallback links',async()=>{
  let refreshed=0;const f=fixture({onChanged:()=>{refreshed++;}}),one=card(url(),{fallback:url('one','old')});
  f.mount([one]);f.requests[0].answer(result(409,'cover_changed'));f.requests[1].answer(result(409,'cover_changed'));await flush();
  assert.equal(refreshed,1);f.mount([card(url())]);f.loader.retry(url());
  f.requests.at(-1).answer(result(409,'cover_changed'));await flush();assert.equal(refreshed,1);
  f.loader.destroy();
});

test('cache count and bytes are bounded, evicted visible frames stay painted, and destroy releases blobs',async()=>{
  const f=fixture({maxEntries:2,maxBytes:12}),one=card(url('one')),two=card(url('two')),three=card(url('three'));
  f.mount([one,two,three]);f.requests[0].answer(png(4));f.requests[1].answer(png(4));await flush();
  f.requests[2].answer(png(8));await flush();
  assert.equal(f.revoked.length,1);assert.equal(one.image.src,'blob:cover-1','Eviction must not flash an already painted frame');
  assert.equal(f.created.filter(item=>!f.revoked.includes(item.url)).reduce((sum,item)=>sum+item.size,0),12);
  f.intersection(one,false);f.intersection(one,true);assert.equal(f.requests.length,4,'Returning to an evicted cover loads it on demand');
  f.loader.destroy();await flush();assert.equal(f.revoked.length,f.created.length);
});

test('oversized payloads cannot enter the blob cache or repeatedly reload',async()=>{
  const f=fixture({maxItemBytes:4}),one=card(url());f.mount([one]);f.requests[0].answer(png(5));await flush();
  assert.equal(f.created.length,0);assert.equal(one.node.dataset.previewState,'unavailable');
  f.mount([card(url())]);await f.advance(60000);assert.equal(f.requests.length,1);
  f.loader.destroy();
});

test('hidden pages stop demand and only resume for the current viewport',async()=>{
  const f=fixture(),one=card(url());f.mount([one]);f.document.hidden=true;f.listeners.get('visibilitychange')();
  assert.equal(f.requests[0].signal.aborted,true);await flush();await f.advance(60000);assert.equal(f.requests.length,1);
  f.document.hidden=false;f.listeners.get('visibilitychange')();assert.equal(f.requests.length,2);
  f.listeners.get('pagehide')();assert.equal(f.requests[1].signal.aborted,true);await flush();
  f.loader.destroy();assert.equal(f.listeners.size,0);
});
