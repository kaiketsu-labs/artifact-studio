const {test}=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('../web/drafts.js');
const {PresentationClock,nextState,duration}=require('../runtime/presentation.js');

const storage=()=>{const values=new Map();return {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};};

test('a reload restores targeted feedback with the original slide and revision',()=>{
  const local=storage(),first=create(local);
  first.feedback.set('deck-a',{feedback:'Make this shorter',slide_id:'cover',context_slide_id:'cover',content_hash:'original',request_id:'retry-same-request',target:{selector:'h1',label:'Heading'}});
  first.feedback.set('deck-b',{feedback:'Add a conclusion',target:null});
  first.notes.set('deck-a/cover',{notes:'A brief off-slide example',expected_notes:'Original notes'});
  assert.equal(first.persist(),true);
  const restored=create(local);
  assert.equal(restored.feedback.get('deck-a').content_hash,'original');
  assert.equal(restored.feedback.get('deck-a').request_id,'retry-same-request');
  assert.equal(restored.feedback.get('deck-b').feedback,'Add a conclusion');
  assert.equal(restored.notes.get('deck-a/cover').expected_notes,'Original notes');
  restored.feedback.clear();restored.notes.clear();restored.persist();
  assert.equal(create(local).feedback.size,0);
});

test('corrupt or unavailable storage cannot prevent the workspace from opening',()=>{
  const local=storage();local.setItem('slide-studio:drafts:v1','invalid JSON');
  assert.equal(create(local).feedback.size,0);
  local.setItem('slide-studio:drafts:v1',JSON.stringify({version:1,notes:[['x',{notes:4}]],feedback:[['x',{feedback:7}]]}));
  assert.equal(create(local).notes.size,0);assert.equal(create(local).feedback.size,0);
  const unavailable=create({getItem(){throw new Error('Denied');},setItem(){throw new Error('Denied');},removeItem(){throw new Error('Denied');}});
  assert.equal(unavailable.persist(),false);
});

test('presentation time pauses, resumes, resets and passes an hour correctly',()=>{
  let now=0;const clock=new PresentationClock(()=>now);clock.start();now=65000;
  assert.equal(duration(clock.elapsed()),'01:05');clock.pause();now=95000;
  assert.equal(duration(clock.elapsed()),'01:05');clock.start();now=100000;
  assert.equal(duration(clock.elapsed()),'01:10');clock.reset();assert.equal(clock.elapsed(),0);
  now=3701000;assert.equal(duration(clock.elapsed()),'1:00:01');
});

test('navigation reveals before advancing and keeps both ends stable',()=>{
  assert.deepEqual(nextState(0,false,-1,true,3),{current:0,revealed:false});
  assert.deepEqual(nextState(0,false,1,true,3),{current:0,revealed:true});
  assert.deepEqual(nextState(0,true,1,true,3),{current:1,revealed:false});
  assert.deepEqual(nextState(1,false,-1,false,3),{current:0,revealed:true});
  assert.deepEqual(nextState(0,true,-1,true,3),{current:0,revealed:false});
  assert.deepEqual(nextState(2,true,1,true,3),{current:2,revealed:true});
});
