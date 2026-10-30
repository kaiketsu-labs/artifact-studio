const {test}=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('../web/preview-loader.js');

const slide=(id,hash=id)=>({id,content_hash:hash,html:`<article>${id}</article>`});
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function documentFor(item,{fonts=Promise.resolve(),images=[],styled=true}={}){
  return {body:{dataset:{previewKey:item.content_hash},innerHTML:item.html,offsetWidth:1600},images,fonts:Object.assign(new Set(),{ready:fonts}),querySelector:()=>styled?{sheet:{cssRules:[{}]}}:null,querySelectorAll:()=>[]};
}
function fixture(){
  const timers=new Map(),ready=[],failures=[],starts=[];let serial=0;
  const frame={isConnected:true,dataset:{},style:{},contentDocument:null,onload:null,srcdoc:''};
  const loader=create({documentFor:(item,deck,design)=>`${deck}/${design}/${item.id}`,onStart:(_frame,reused)=>starts.push(reused),onReady:(_frame,doc)=>ready.push(doc.body.dataset.previewKey),onFailure:()=>failures.push(true),schedule:callback=>{timers.set(++serial,callback);return serial;},cancel:id=>timers.delete(id)});
  async function loaded(item,options){frame.contentDocument=documentFor(item,options);await frame.onload();await settle();}
  return {loader,frame,ready,failures,starts,timers,loaded};
}

test('switching slides reuses the loaded design document and unchanged thumbnails stay ready',async()=>{
  const check=fixture(),first=slide('first'),second=slide('second');
  check.loader.load(check.frame,first,'deck','design-v1');await check.loaded(first);
  const doc=check.frame.contentDocument,source=check.frame.srcdoc;
  check.loader.load(check.frame,second,'deck','design-v1');await settle();
  assert.equal(check.frame.contentDocument,doc);assert.equal(check.frame.srcdoc,source);
  assert.equal(doc.body.innerHTML,second.html);assert.deepEqual(check.starts,[false,true]);
  assert.deepEqual(check.ready,['first','second']);assert.equal(check.frame.style.visibility,'visible');
  check.loader.load(check.frame,second,'deck','design-v1');await settle();
  assert.deepEqual(check.ready,['first','second']);assert.equal(check.timers.size,0);
});

test('theme or deck changes create a fresh document and pending requests are not restarted',async()=>{
  const check=fixture(),item=slide('first');
  check.loader.load(check.frame,item,'deck','design-v1');
  check.loader.load(check.frame,item,'deck','design-v1');assert.deepEqual(check.starts,[false]);
  await check.loaded(item);
  check.loader.load(check.frame,item,'deck','design-v2');assert.equal(check.frame.srcdoc,'deck/design-v2/first');
  await check.loaded(item);
  check.loader.load(check.frame,item,'other-deck','design-v2');assert.equal(check.frame.srcdoc,'other-deck/design-v2/first');
  assert.deepEqual(check.starts,[false,false,false]);
});

test('a slow earlier font load cannot reveal or validate the slide selected afterward',async()=>{
  const check=fixture(),first=slide('first'),second=slide('second');let release;
  check.loader.load(check.frame,first,'deck','design');
  const old=check.loaded(first,{fonts:new Promise(resolve=>{release=resolve;})});
  check.loader.load(check.frame,second,'deck','design');
  assert.deepEqual(check.starts,[false,true]);
  release();await old;await settle();
  assert.deepEqual(check.ready,['second']);assert.equal(check.frame.dataset.loadToken,'deck/second/second/design-ready');
});

test('image failure retries once and a late successful decode cannot hide the failure',async()=>{
  const check=fixture(),item=slide('broken');
  check.loader.load(check.frame,item,'deck','design');
  await check.loaded(item,{images:[{decode:()=>Promise.reject(new Error('missing image'))}]});
  assert.equal(check.ready.length,0);
  const retry=[...check.timers.values()][0];retry();
  await check.loaded(item,{images:[{decode:()=>Promise.reject(new Error('missing image'))}]});
  assert.deepEqual(check.failures,[true]);assert.equal(check.frame.style.visibility,'hidden');
});

test('a timed-out reused document cannot become ready through a late font completion',async()=>{
  const check=fixture(),first=slide('first'),second=slide('second');let release;
  check.loader.load(check.frame,first,'deck','design');await check.loaded(first);
  check.frame.contentDocument.fonts.ready=new Promise(resolve=>{release=resolve;});
  check.loader.load(check.frame,second,'deck','design');
  [...check.timers.values()][0]();release();await settle();
  assert.deepEqual(check.ready,['first']);assert.equal(check.frame.style.visibility,'hidden');
});

test('clearing a preview cancels retries and ignores an old font completion',async()=>{
  const check=fixture(),item=slide('first');let release;
  check.loader.load(check.frame,item,'deck','design');
  const pending=check.loaded(item,{fonts:new Promise(resolve=>{release=resolve;})});
  check.loader.cancel(check.frame);
  release();await pending;
  assert.equal(check.timers.size,0);assert.equal(check.frame.onload,null);
  assert.deepEqual(check.ready,[]);assert.deepEqual(check.failures,[]);
});

test('reused slides wait for external SVG images before approval becomes available',async()=>{
  const check=fixture(),first=slide('first'),second=slide('svg');let release;
  check.loader.load(check.frame,first,'deck','design');await check.loaded(first);
  const doc=check.frame.contentDocument;
  doc.querySelectorAll=()=>[{getAttribute:name=>name==='href'?'assets/chart.svg':null}];
  doc.createElement=()=>({decode:()=>new Promise(resolve=>{release=resolve;})});
  check.loader.load(check.frame,second,'deck','design');await settle();
  assert.deepEqual(check.ready,['first']);assert.equal(check.frame.style.visibility,'hidden');
  release();await settle();assert.deepEqual(check.ready,['first','svg']);
});
