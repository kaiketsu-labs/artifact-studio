const {test}=require('node:test');
const assert=require('node:assert/strict');
const {create,validSegments}=require('../web/voice-feedback.js');
const {create:createDrafts}=require('../web/drafts.js');

const heading={selector:'#heading',label:'Heading',quote:'Original heading',html:'<h1>Original heading</h1>',x:.1,y:.2,width:.6,height:.1};
const chart={selector:'#chart',label:'Chart',quote:'Original data',html:'<svg></svg>',x:.1,y:.4,width:.7,height:.5};
const context=target=>({slide_id:'cover',context_slide_id:'cover',content_hash:'original-version',target});

function fixture(){
  let activeKey='deck/cover',selection=context(heading),number=0;
  const drafts=new Map(),changed=[],persisted=[];
  const queue=create({drafts,key:()=>activeKey,context:()=>selection,persist:()=>persisted.push(structuredClone([...drafts])),onChange:key=>changed.push(key),uuid:()=>`voice-request-${++number}`});
  return {queue,drafts,changed,persisted,setKey:key=>activeKey=key,setContext:value=>selection=value};
}

test('capture freezes group context and late finals route to their original slide draft',()=>{
  const state=fixture(),group={kind:'group',label:'2 objects',elements:[structuredClone(heading),structuredClone(chart)]};
  state.setContext(context(group));
  const first=state.queue.capture();
  assert.ok(first);assert.equal(state.drafts.size,0);
  group.elements[0].quote='Changed selection';
  state.setKey('deck/next');state.setContext({...context(null),slide_id:'next',context_slide_id:'next',content_hash:'next-version'});
  assert.equal(state.queue.insert('Make these clearer',first),true);
  assert.equal(state.queue.insert('and larger.',first),true);
  assert.equal(state.drafts.has('deck/next'),false);
  assert.equal(state.queue.segments('deck/cover')[0].message,'Make these clearer and larger.');
  assert.equal(state.queue.segments('deck/cover')[0].target.elements[0].quote,'Original heading');
  assert.deepEqual(state.changed,['deck/cover','deck/cover']);
  assert.equal(state.persisted.length,2);
});

test('selection boundaries retain independent comments and preserve existing typed feedback',()=>{
  const state=fixture(),typed={feedback:'Typed instruction',...context(heading),request_id:'typed-operation'};
  state.drafts.set('deck/cover',typed);
  const first=state.queue.capture();state.queue.insert('First instruction',first);
  state.setContext({...context(chart),content_hash:'later-version'});
  const second=state.queue.capture();state.queue.insert('Second instruction',second);
  state.queue.insert('please.',first);
  const values=state.queue.segments();
  assert.deepEqual(values.map(item=>item.message),['First instruction please.','Second instruction']);
  assert.deepEqual(values.map(item=>item.target.selector),['#heading','#chart']);
  assert.deepEqual(values.map(item=>item.content_hash),['original-version','later-version']);
  assert.equal(state.drafts.get('deck/cover').feedback,typed.feedback);
  assert.equal(state.drafts.get('deck/cover').request_id,typed.request_id);
  values[0].target.quote='External mutation';
  assert.equal(state.queue.segments()[0].target.quote,'Original heading');
});

test('discarded, acknowledged and edited captures cannot be resurrected by a delayed result',()=>{
  for(const action of ['remove','acknowledge','edit','clear']){
    const state=fixture(),capture=state.queue.capture();state.queue.insert('Original',capture);
    if(action==='clear')state.queue.clear();else if(action==='edit'){
      const updated=state.queue.edit(capture.request_id,'Human correction');
      assert.ok(updated&&updated!==capture.request_id);
      assert.equal(state.queue.segments()[0].message,'Human correction');
      assert.deepEqual(state.queue.segments()[0].target,heading);
    }else state.queue[action](capture.request_id,'deck/cover');
    const snapshot=structuredClone([...state.drafts]);
    assert.equal(state.queue.available(capture),false);
    assert.equal(state.queue.insert('Late words',capture),false);
    assert.deepEqual([...state.drafts],snapshot);
  }
});

test('unchanged edit preserves retry identity and acknowledgements preserve remaining comments',()=>{
  const state=fixture(),first=state.queue.capture();state.queue.insert('First',first);
  const second=state.queue.capture();state.queue.insert('Second',second);
  assert.equal(state.queue.edit(first.request_id,'First'),first.request_id);
  assert.equal(state.queue.acknowledge(first.request_id,'deck/cover'),true);
  assert.deepEqual(state.queue.segments().map(item=>item.request_id),[second.request_id]);
  assert.equal(state.queue.hasDrafts(),true);
  state.queue.acknowledge(second.request_id,'deck/cover');assert.equal(state.queue.hasDrafts(),false);
});

test('limits reject complete additions without truncation, eviction or empty persisted captures',()=>{
  const state=fixture(),capture=state.queue.capture();
  assert.equal(state.queue.insert('   ',capture),true);assert.equal(state.drafts.size,0);
  assert.equal(state.queue.insert('x'.repeat(10000),capture),true);
  const snapshot=structuredClone([...state.drafts]);
  assert.equal(state.queue.insert('extra',capture),false);
  assert.equal(state.queue.edit(capture.request_id,'x'.repeat(10001)),false);
  assert.deepEqual([...state.drafts],snapshot);
  for(let index=1;index<100;index++)assert.equal(state.queue.insert(`Comment ${index}`,state.queue.capture()),true);
  assert.equal(state.queue.insert('Comment 101',state.queue.capture()),false);
  assert.equal(state.queue.segments().length,100);
});

test('voice-only drafts survive reload with original request IDs, hashes and group snapshots',()=>{
  const values=new Map(),storage={keys:()=>[...values.keys()],getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
  let draftStore=createDrafts(storage),number=0;
  const queue=create({drafts:draftStore.feedback,key:()=> 'deck/cover',context:()=>context(null),persist:draftStore.persist,uuid:()=>`persisted-request-${++number}`});
  const capture=queue.capture();queue.insert('Slide-level voice note',capture);
  assert.equal(draftStore.feedback.get('deck/cover').feedback,'');
  draftStore=createDrafts(storage);
  assert.equal(draftStore.feedback.get('deck/cover').voice_segments[0].request_id,capture.request_id);
  assert.equal(draftStore.feedback.get('deck/cover').voice_segments[0].message,'Slide-level voice note');
  assert.equal(draftStore.feedback.get('deck/cover').voice_segments[0].target,null);
});

test('stored segment validation rejects oversized, ambiguous and malformed contexts',()=>{
  const state=fixture(),capture=state.queue.capture();state.queue.insert('Original',capture);
  const segment=state.queue.segments()[0];
  assert.equal(validSegments([segment]),true);
  for(const bad of [
    {...segment,message:'x'.repeat(10001)},
    {...segment,unknown:'extra'},
    {...segment,request_id:'short'},
    {...segment,target:{...heading,width:Infinity}},
    {...segment,target:{...heading,html:'x'.repeat(15001)}},
    {...segment,target:{kind:'group',label:'Nested',elements:[{kind:'group',label:'Empty',elements:[]}]}},
  ])assert.equal(!!validSegments([bad]),false);
  assert.equal(validSegments([segment,segment]),false);
  assert.equal(validSegments(Array(101).fill(segment)),false);
  state.setKey('deck/wrong-slide');assert.equal(state.queue.capture(),null);
  assert.equal(state.queue.insert('Misrouted',{...capture,key:'deck/wrong-slide'}),false);
});

test('late voice results retain the proposal identity across design switches and reload',()=>{
  const values=new Map(),storage={keys:()=>[...values.keys()],getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
  const draftStore=createDrafts(storage);
  let selection={...context(null),design_option_id:'a'.repeat(32),design_option_name:'Editorial'},number=0;
  const queue=create({drafts:draftStore.feedback,key:()=> 'deck/cover',context:()=>selection,persist:draftStore.persist,uuid:()=>`proposal-voice-${++number}`});
  const editorial=queue.capture();
  selection={...context(null),design_option_id:'b'.repeat(32),design_option_name:'Bold'};
  const bold=queue.capture();
  queue.insert('Make Bold more spacious',bold);
  queue.insert('Keep this typography',editorial);
  const restored=createDrafts(storage).feedback.get('deck/cover').voice_segments;
  assert.deepEqual(restored.map(item=>item.design_option_name),['Bold','Editorial']);
  assert.deepEqual(restored.map(item=>item.design_option_id),['b'.repeat(32),'a'.repeat(32)]);
  assert.equal(restored[1].request_id,editorial.request_id);
  assert.ok(validSegments(restored));
  assert.equal(!!validSegments([{...restored[0],target:heading}]),false);
});

test('element voice feedback retains its fragment and full-slide context across later previews',()=>{
  const values=new Map(),storage={keys:()=>[...values.keys()],getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
  const draftStore=createDrafts(storage);let number=0;
  let selection={...context(null),element_option_id:'c'.repeat(32),element_option_name:'Clearer promise',element_context_hash:'a'.repeat(64)};
  const queue=create({drafts:draftStore.feedback,key:()=> 'deck/cover',context:()=>selection,persist:draftStore.persist,uuid:()=>`element-voice-${++number}`});
  const original=queue.capture();assert.ok(original);
  selection={...selection,element_context_hash:'b'.repeat(64)};
  const recomposed=queue.capture();assert.ok(recomposed);
  assert.equal(queue.insert('Keep the earlier composition',original),true);
  assert.equal(queue.insert('Make this version clearer',recomposed),true);
  const restored=createDrafts(storage).feedback.get('deck/cover').voice_segments;
  assert.deepEqual(restored.map(item=>item.element_context_hash),['a'.repeat(64),'b'.repeat(64)]);
  assert.ok(restored.every(item=>item.element_option_id==='c'.repeat(32)&&item.target===null));
  assert.ok(validSegments(restored));
  for(const change of [{target:heading},{design_option_id:'d'.repeat(32),design_option_name:'Bold'},{element_context_hash:'bad'},{element_option_id:undefined}])assert.equal(!!validSegments([{...restored[0],...change}]),false);
});
