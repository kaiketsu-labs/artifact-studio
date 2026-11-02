const test=require('node:test');
const assert=require('node:assert/strict');
const {requestFor,canShare}=require('../web/sharing.js');

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

test('native file sharing checks the exact file payload and keeps unsupported formats local',()=>{
  const file=new File(['audience'], 'deck.html', {type:'text/html'}),data={files:[file],title:'Deck'};
  const navigator={share(){},canShare:payload=>payload===data};
  assert.equal(canShare(data,navigator),true);
  assert.equal(canShare(data,{share(){}}),false);
  assert.equal(canShare(data,{share(){},canShare:()=>false}),false);
  assert.equal(canShare(data,{share(){},canShare(){throw Error('Unsupported');}}),false);
  assert.equal(canShare(data,{}),false);
});

test('text sharing works on older share APIs without requiring file support',()=>{
  assert.equal(canShare({text:'A design message'},{share(){}}),true);
  assert.equal(canShare({text:'A design message'},{share(){},canShare:()=>false}),false);
});
