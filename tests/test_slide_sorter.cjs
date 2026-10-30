const {test}=require('node:test');
const assert=require('node:assert/strict');
const {fitZoom,nextIndex}=require('../web/slide-sorter.js');
const {dropPosition,insertSlides}=require('../web/slide-order.js');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

/** Drive real zoom handlers with a deterministic display clock and preparation gate. */
function zoomFixture({source=path.resolve(__dirname,'../web/slide-sorter.js'),prepare=()=>true,blocked=()=>false}={}){
  const frames=new Map(),counts={prepare:0,changes:0,icons:0,geometry:0,previewWrites:0,railWrites:0},events={};let sequence=0,controller;
  const doc={activeElement:null,addEventListener(){},querySelector:()=>null};
  class Control{
    constructor(){this.events={};this.value='';this.classList={toggle(){}};this.style={setProperty(){}};}
    addEventListener(type,callback){this.events[type]=callback;}
    fire(type,event={}){this.events[type]?.(event);}
    setAttribute(){}
    focus(){doc.activeElement=this;}
    getBoundingClientRect(){return {width:1200,height:600};}
  }
  const workspace=new Control(),rail=new Control(),preview=new Control(),selected=new Control();rail.querySelector=()=>selected;
  preview.getBoundingClientRect=()=>{counts.geometry++;return {width:700*(controller?.snapshot().editor_zoom||100)/100,height:400};};
  preview.style.setProperty=()=>counts.previewWrites++;rail.style.setProperty=()=>counts.railWrites++;
  const controls=Object.fromEntries(['editButton','gridButton','minus','plus','range','value','fit','status'].map(name=>[name,new Control()]));doc.activeElement=controls.range;
  const requestFrame=callback=>{const id=++sequence;frames.set(id,callback);return id;},cancelFrame=id=>frames.delete(id);
  const root={SlideIcons:{button(){counts.icons++;}},addEventListener(type,callback){events[type]=callback;},requestAnimationFrame:requestFrame,cancelAnimationFrame:cancelFrame};
  const context={window:root,document:doc,module:{exports:{}}};vm.runInNewContext(fs.readFileSync(source,'utf8'),context,{filename:source});
  controller=context.module.exports.create({workspace,rail,preview,controls,read:()=>({count:18,index:0}),prepare:()=>{counts.prepare++;return prepare();},onChange:()=>counts.changes++,edit:()=>{},blocked,requestFrame,cancelFrame});
  const step=async()=>{const callbacks=[...frames.values()];frames.clear();for(const callback of callbacks)callback();await new Promise(resolve=>setImmediate(resolve));};
  const input=value=>{controls.range.value=String(value);controls.range.fire('input');};
  return {controller,controls,counts,frames,doc,selected,step,input,events};
}

test('slider bursts paint only the latest target per frame without refreshing the workspace',async()=>{
  const check=zoomFixture(),icons=check.counts.icons;
  for(let index=0;index<149;index++)check.input(55+index%9*5);check.input(65);
  assert.equal(check.frames.size,1);assert.equal(check.controller.snapshot().view_zoom,100);
  await check.step();
  assert.equal(check.controller.snapshot().view_zoom,65);assert.equal(check.counts.prepare,1);
  assert.equal(check.counts.changes,0);assert.equal(check.counts.geometry,0);assert.equal(check.counts.icons,icons);
  assert.equal(check.counts.previewWrites,2);assert.equal(check.counts.railWrites,0);
});
test('crossing the sorter boundary refreshes once and keeps focus on the dragged slider',async()=>{
  const check=zoomFixture();check.input(45);await check.step();
  check.input(30);await check.step();check.input(20);await check.step();
  assert.equal(check.counts.changes,1);assert.equal(check.counts.geometry,1);assert.equal(check.doc.activeElement,check.controls.range);
  check.input(80);await check.step();assert.equal(check.counts.changes,2);assert.equal(check.controller.snapshot().editor_zoom,80);
});
test('a delayed edit check applies the newest target without replaying intermediate positions',async()=>{
  let release;const check=zoomFixture({prepare:()=>new Promise(resolve=>{release=resolve;})});
  check.input(90);await check.step();check.input(60);check.input(65);release(true);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(check.controller.snapshot().view_zoom,65);assert.equal(check.counts.prepare,1);assert.equal(check.frames.size,0);
});
test('unchanged and blocked zooms do no preparation and restore the displayed slider value',async()=>{
  let blocked=false;const check=zoomFixture({blocked:()=>blocked});check.input(100);await check.step();
  blocked=true;check.input(80);await check.step();assert.equal(check.counts.prepare,0);assert.equal(check.controls.range.value,'100');
  const unfinished=zoomFixture({prepare:()=>false});unfinished.input(80);await unfinished.step();assert.equal(unfinished.controls.range.value,'100');
});
test('reset invalidates an old preparation without dropping new requests for the new deck',async()=>{
  let release,first=true;const check=zoomFixture({prepare:()=>{if(!first)return true;first=false;return new Promise(resolve=>{release=resolve;});}});
  check.input(45);await check.step();check.controller.reset();assert.equal(check.controls.range.value,'100');
  check.input(80);release(false);await new Promise(resolve=>setImmediate(resolve));await check.step();
  assert.equal(check.controller.snapshot().view_zoom,80);assert.equal(check.counts.changes,0);
});
test('queued button and wheel increments accumulate instead of losing input between frames',async()=>{
  const check=zoomFixture();for(let index=0;index<3;index++)check.controls.minus.fire('click');await check.step();
  assert.equal(check.controller.snapshot().view_zoom,70);
  for(let index=0;index<3;index++)check.controller.zoomWheel({ctrlKey:true,deltaY:15,preventDefault(){}});
  await check.step();assert.equal(check.controller.snapshot().view_zoom,55);
});
test('zoom supports 400%, keeps the enlarged editor zoom through overview, and fits back to 100%',async()=>{
  const check=zoomFixture();assert.equal(check.controls.plus.disabled,false);
  check.input(225);await check.step();assert.equal(check.controller.snapshot().editor_zoom,225);
  check.controls.gridButton.fire('click');await check.step();assert.equal(check.controller.isOpen(),true);
  check.controls.editButton.fire('click');await check.step();assert.equal(check.controller.snapshot().view_zoom,225);
  check.input(900);await check.step();assert.equal(check.controller.snapshot().view_zoom,400);assert.equal(check.controls.plus.disabled,true);
  check.controls.fit.fire('click');await check.step();assert.equal(check.controller.snapshot().view_zoom,100);assert.equal(check.controls.plus.disabled,false);
  const restored=check.controller.restore({view_zoom:300,editor_zoom:300});await check.step();assert.equal(await restored,true);assert.equal(check.controller.snapshot().view_zoom,300);
});

test('fitting a deck reduces card size for longer decks and stays in overview mode',()=>{
  assert.equal(fitZoom(3,1200,500),50);
  assert.ok(fitZoom(29,1200,500)<fitZoom(3,1200,500));
  assert.ok(fitZoom(29,600,500)<=fitZoom(29,1200,500));
  for(const count of [1,18,29,100]){
    const zoom=fitZoom(count,1200,500);
    assert.ok(zoom>=10&&zoom<=50);assert.equal(zoom%5,0);
  }
});

test('grid arrows follow rows and columns without losing the last incomplete row',()=>{
  assert.equal(nextIndex(1,'ArrowDown',18,6),7);
  assert.equal(nextIndex(7,'ArrowUp',18,6),1);
  assert.equal(nextIndex(7,'ArrowRight',18,6),8);
  assert.equal(nextIndex(7,'ArrowLeft',18,6),6);
  assert.equal(nextIndex(16,'ArrowDown',18,6),17);
  assert.equal(nextIndex(0,'ArrowLeft',18,6),0);
});

test('drag insertion follows horizontal rows and preserves multi-slide order',()=>{
  const rectangles=Array.from({length:6},(_,index)=>({left:index%3*120,right:index%3*120+100,top:Math.floor(index/3)*100,bottom:Math.floor(index/3)*100+80}));
  assert.equal(dropPosition(rectangles,5,30,true),0);
  assert.equal(dropPosition(rectangles,110,30,true),1);
  assert.equal(dropPosition(rectangles,350,30,true),3);
  assert.equal(dropPosition(rectangles,5,130,true),3);
  assert.equal(dropPosition(rectangles,350,130,true),6);
  assert.equal(dropPosition(rectangles,250,90,true),2);
  assert.equal(dropPosition([],250,90,true),0);
  assert.deepEqual(insertSlides(['a','b','c','d','e'],['b','d'],2),['a','c','b','d','e']);
  assert.equal(dropPosition(rectangles,350,30),0);
});
