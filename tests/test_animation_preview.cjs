const {test}=require('node:test');
const assert=require('node:assert/strict');
const {Playback,CSSPlayback,hasAnimations,freeze}=require('../web/animation-preview.js');

/** Advance the preview clock without real timers or a presentation file. */
function scene(steps=2){
  let now=0,number=0,finished=0,disposed=0;const frames=new Map(),seen=[];
  const motion={parts:[],update(step){seen.push(step);this.parts=step===1?[{active:true,done:false,duration:2000,deadline:now+500}]:[];},deadline:part=>part.deadline,dispose(){disposed++;}};
  const player=new Playback(motion,steps,{now:()=>now,requestFrame:callback=>{frames.set(++number,callback);return number;},cancelFrame:id=>frames.delete(id),onFinish:()=>finished++});
  return {player,seen,frames,get finished(){return finished;},get disposed(){return disposed;},advance(time){now+=time;const queued=[...frames.values()];frames.clear();for(const callback of queued)callback();}};
}

test('one play previews every reveal, waits for delayed chart motion and then restores editing',()=>{
  const run=scene();run.player.start();assert.deepEqual(run.seen,[0]);
  run.advance(900);assert.deepEqual(run.seen,[0,1]);
  run.advance(2500);assert.deepEqual(run.seen,[0,1]);
  run.advance(350);assert.deepEqual(run.seen,[0,1,2]);
  run.advance(900);assert.equal(run.finished,1);assert.equal(run.disposed,1);assert.equal(run.frames.size,0);
});

test('stopping playback cancels later reveals and does not report natural completion',()=>{
  const run=scene();run.player.start();run.advance(900);run.player.stop();run.advance(10000);
  assert.deepEqual(run.seen,[0,1]);assert.equal(run.finished,0);assert.equal(run.frames.size,0);
});

test('future CSS entrances wait for their visible reveal and start at zero only once',()=>{
  let hidden=true,starts=0,pauses=0;
  const animation={currentTime:400,effect:{target:{closest:()=>hidden?{}:null},getComputedTiming:()=>({endTime:1000})},pause(){pauses++;},play(){starts++;}};
  const css=new CSSPlayback({getAnimations:()=>[animation]});
  assert.equal(css.sync(0),0);assert.equal(animation.currentTime,0);assert.equal(starts,0);assert.equal(pauses,1);
  css.sync(2000);assert.equal(starts,0);
  hidden=false;assert.equal(css.sync(3000),4350);assert.equal(starts,1);
  animation.currentTime=500;assert.equal(css.sync(3500),4350);assert.equal(starts,1);assert.equal(animation.currentTime,500);
});

test('infinite CSS previews have a finite playback window and static slides stay disabled',()=>{
  const animation={currentTime:0,effect:{getComputedTiming:()=>({endTime:Infinity})},pause(){},play(){}};
  const css=new CSSPlayback({getAnimations:()=>[animation]});
  assert.equal(css.sync(500),6850);assert.equal(css.sync(2000),6850);
  const slide={querySelector:()=>null,querySelectorAll:()=>[]};
  const doc={querySelector:()=>slide,getAnimations:()=>[],defaultView:{getComputedStyle:()=>({animationName:'none'})}};
  assert.equal(hasAnimations(doc),false);
  doc.defaultView.getComputedStyle=()=>({animationName:'fade-in'});assert.equal(hasAnimations(doc),true);
});

test('ordinary previews hold final CSS values without losing animations needed for replay',()=>{
  let pauses=0;const finite={currentTime:0,effect:{getComputedTiming:()=>({endTime:150})},pause(){pauses++;}},infinite={...finite,effect:{getComputedTiming:()=>({endTime:Infinity})}};
  const doc={getAnimations:()=>[finite,infinite]};freeze(doc);
  assert.equal(finite.currentTime,150);assert.equal(infinite.currentTime,0);assert.equal(pauses,2);assert.equal(doc.getAnimations().length,2);
});

test('automatic reveal groups enable playback without click builds or counters',()=>{
  const slide={querySelector:selector=>selector.includes('[data-auto-reveal]')?{}:null,querySelectorAll:()=>[]};
  assert.equal(hasAnimations({querySelector:()=>slide,getAnimations:()=>[]}),true);
});

test('CSS inside automatic reveal cards waits for its delayed group',()=>{
  let hidden=true,starts=0;
  const animation={currentTime:400,effect:{target:{closest:selector=>hidden&&selector.includes('[data-auto-reveal].unrevealed')?{}:null},getComputedTiming:()=>({endTime:1000})},pause(){},play(){starts++;}};
  const css=new CSSPlayback({getAnimations:()=>[animation]});
  css.sync(0);css.sync(800);assert.equal(starts,0);
  hidden=false;assert.equal(css.sync(850),2200);assert.equal(starts,1);
});
