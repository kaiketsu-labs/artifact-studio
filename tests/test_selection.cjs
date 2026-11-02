const {test}=require('node:test');
const assert=require('node:assert/strict');
const {groupTargets,targetsOf,selectTargets,isAdditive,validTarget}=require('../web/selection.js');
const {create}=require('../web/drafts.js');

const heading={selector:'#heading',label:'Heading',quote:'Original heading',html:'<h1 id="heading">Original heading</h1>',x:.1,y:.2,width:.6,height:.1};
const chart={selector:'#chart',label:'Chart',quote:'Original data',html:'<svg id="chart"></svg>',x:.1,y:.4,width:.7,height:.5};

test('modifier selection toggles one anchor without replacing existing snapshots',()=>{
  const selected=selectTargets([heading],chart,true);
  assert.equal(selected[0],heading);
  assert.deepEqual(groupTargets(selected),{kind:'group',label:'2 objects',elements:[heading,chart]});
  assert.deepEqual(selectTargets(selected,{...heading,quote:'Changed heading'},true),[chart]);
  assert.deepEqual(selectTargets(selected,chart),[chart]);
  assert.equal(groupTargets([chart]),chart);
  assert.equal(groupTargets([]),null);
  for(const modifier of ['ctrlKey','metaKey','shiftKey'])assert.equal(isAdditive({[modifier]:true}),true);
  assert.equal(isAdditive({altKey:true}),false);
});

test('multi-object drafts retain every snapshot, retry ID and original version after reload',()=>{
  const values=new Map(),storage={keys:()=>[...values.keys()],getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
  const store=create(storage),target=groupTargets([heading,chart]);
  const draft={feedback:'Align these objects',target,slide_id:'cover',context_slide_id:'cover',content_hash:'original-version',request_id:'original-request'};
  store.feedback.set('deck/cover',draft);assert.equal(store.persist(),true);
  const reloaded=create(storage);
  assert.deepEqual(reloaded.feedback.get('deck/cover'),draft);
  assert.deepEqual(targetsOf(reloaded.feedback.get('deck/cover').target),[heading,chart]);
  assert.equal(validTarget({kind:'group',label:'Nested',elements:[target]}),false);
  assert.equal(validTarget({kind:'group',label:'Empty',elements:[]}),false);
  assert.equal(validTarget({kind:'group',label:'Corrupt',selector:'#heading'}),false);
  assert.equal(validTarget({kind:'group',label:'Oversize',elements:Array(101).fill(heading)}),false);
  assert.equal(validTarget(heading),true);
});
