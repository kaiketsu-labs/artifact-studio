const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function exporter(window){
  vm.runInNewContext(fs.readFileSync(require.resolve('../web/image-export.js'),'utf8'),{window});
  return window.ImageExport;
}

test('clipboard write starts in the click task while the PNG is still rendering',async()=>{
  let release,written;
  const file=new Blob(['png'],{type:'image/png'}),response=new Promise(resolve=>{release=resolve;});
  const api=exporter({fetch:()=>response,ClipboardItem:class{constructor(types){this.types=types;}},navigator:{clipboard:{write:items=>{written=items;return items[0].types['image/png'].then(image=>assert.equal(image,file));}}}});
  const pending=api.copy('/slide/image',{content_hash:'version'});
  assert.equal(written.length,1);
  release({ok:true,blob:async()=>file});
  await pending;
});

test('stale slide errors reach the caller instead of reporting a successful copy',async()=>{
  const api=exporter({fetch:async()=>({ok:false,json:async()=>({detail:'The slide changed.'})}),ClipboardItem:class{constructor(types){this.types=types;}},navigator:{clipboard:{write:items=>items[0].types['image/png']}}});
  await assert.rejects(api.copy('/slide/image',{}),/The slide changed/);
});

test('missing clipboard support does not start a render and denied access is actionable',async()=>{
  let requested=0;
  const window={fetch:()=>{requested++;return Promise.resolve({ok:true,blob:async()=>new Blob(['png'])});},navigator:{}};
  const api=exporter(window);
  await assert.rejects(api.copy('/slide/image',{}),/HTTPS or localhost/);
  assert.equal(requested,0);
  window.ClipboardItem=class{};
  window.navigator.clipboard={write:async()=>{throw Object.assign(new Error(),{name:'NotAllowedError'});}};
  await assert.rejects(api.copy('/slide/image',{}),/Allow clipboard access/);
});
