const test=require('node:test');
const assert=require('node:assert/strict');
const {requestFor}=require('../web/sharing.js');

test('a presentation cannot share before an explicit audience format is chosen',()=>{
  const target={scope:'artifact',id:'fictional',presentation:true,expected_hash:'retained-hash'};
  assert.throws(()=>requestFor(target,null),/Choose HTML or PDF/);
  assert.throws(()=>requestFor(target,'pptx'),/Choose HTML or PDF/);
  assert.deepEqual(requestFor(target,'pdf'),{scope:'artifact',id:'fictional',format:'pdf',expected_hash:'retained-hash'});
});

test('project format covers its presentations while arbitrary artifacts retain source format',()=>{
  assert.throws(()=>requestFor({scope:'project',id:'project',presentation:true}),/Choose/);
  assert.deepEqual(requestFor({scope:'project',id:'project'},null),{scope:'project',id:'project',format:null});
});
