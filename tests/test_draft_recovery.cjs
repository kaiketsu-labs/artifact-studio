const {test}=require('node:test');
const assert=require('node:assert/strict');
const {state}=require('../web/draft-recovery.js');
const draft={feedback:'Make the story clearer',content_hash:'old',request_id:'request',ask_agent:true};
const current={content_hash:'new'};
test('stale draft retains original anchors until explicit review and use',()=>{
  const original=structuredClone(draft);
  assert.equal(state(draft,current).stale,true);
  assert.equal(state(draft,current).safe,true);
  assert.equal(state(draft,current).reviewed,false);
  assert.equal(state(draft,current,{hash:'new',requestId:'request'}).reviewed,true);
  assert.deepEqual(draft,original);
});
test('further revision or edited request invalidates review',()=>{
  const review={hash:'new',requestId:'request'};
  assert.equal(state(draft,{content_hash:'newer'},review).reviewed,false);
  assert.equal(state({...draft,request_id:'edited'},current,review).reviewed,false);
});
test('selection, multiobject, proposal and voice anchors cannot be rebased',()=>{
  for(const fields of [{target:{selector:'h1'}},{target:{targets:[{selector:'h1'},{selector:'p'}]}},{design_option_id:'proposal'},{element_option_id:'option'},{edit_intent:{}},{voice_segments:[{content_hash:'old'}]}]){
    assert.equal(state({...draft,...fields},current).safe,false);
  }
  assert.equal(state(draft,current,undefined,[{content_hash:'old'}]).safe,false);
});
test('server conflict can require review before local refresh has caught up',()=>{
  assert.equal(state(draft,{content_hash:'old'},{conflict:true}).stale,true);
  assert.equal(state(draft,{content_hash:'old'}).stale,false);
});
test('attachment-only drafts recover while empty discarded drafts do not block sending',()=>{
  const empty={...draft,feedback:''};
  assert.equal(state(empty,current).stale,false);
  assert.equal(state(empty,current,undefined,[],true).stale,true);
});
