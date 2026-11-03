const {test}=require('node:test');
const assert=require('node:assert/strict');
const {appendVoiceSegment,validVoiceSegment,feedbackTarget,groupTargets}=require('../web/hub.js');
const hash='a'.repeat(64);
const capture=(id,target=null)=>({artifact_id:'page',id,request_id:id,revision:1,content_hash:hash,target});

test('dictation snapshots preserve group membership independently of later selection changes',()=>{
  const targets=[{kind:'element',selector:'#title',label:'Title'},{kind:'text',quote:'Evidence',block_index:1}];
  const selected=feedbackTarget(groupTargets(targets)),draft={message:'Typed feedback',targets,voice_segments:[]};
  const first=capture('first-capture',selected);
  assert.equal(appendVoiceSegment(draft,first,'Align these'),true);
  targets.pop();selected.elements[0].label='Changed';
  assert.equal(appendVoiceSegment(draft,first,'and make them larger.'),true);
  assert.equal(draft.message,'Typed feedback');
  assert.equal(draft.voice_segments[0].message,'Align these and make them larger.');
  assert.equal(draft.voice_segments[0].target.elements.length,2);
  assert.equal(draft.voice_segments[0].target.elements[0].label,'Title');
  assert.ok(!('block_index' in draft.voice_segments[0].target.elements[1]));
});

test('multiple recognition sessions retain separate version and target envelopes',()=>{
  const draft={voice_segments:[]};
  appendVoiceSegment(draft,capture('first-session',{kind:'element',selector:'#title'}),'Earlier title');
  const later={...capture('next-session',{kind:'element',selector:'#body'}),revision:2,content_hash:'b'.repeat(64)};
  appendVoiceSegment(draft,later,'New body');
  assert.deepEqual(draft.voice_segments.map(segment=>[segment.revision,segment.content_hash,segment.target.selector]),[[1,hash,'#title'],[2,'b'.repeat(64),'#body']]);
  // A retry token can change on a deliberate edit while the immutable recording identity remains.
  draft.voice_segments[0].request_id='edited-retry-id';
  appendVoiceSegment(draft,capture('first-session'),'continued');
  assert.equal(draft.voice_segments.length,2);
  assert.equal(draft.voice_segments[0].request_id,'edited-retry-id');
  assert.equal(draft.voice_segments[0].message,'Earlier title continued');
});

test('limits reject overflowing final text without losing already accepted dictation',()=>{
  const draft={voice_segments:[]};
  appendVoiceSegment(draft,capture('long-capture'),'x'.repeat(10000));
  assert.equal(appendVoiceSegment(draft,capture('long-capture'),'overflow'),false);
  assert.equal(draft.voice_segments[0].message.length,10000);
  draft.voice_segments=Array.from({length:100},(_,index)=>({...capture('capture-'+index),message:'Kept'}));
  assert.equal(appendVoiceSegment(draft,capture('new-capture'),'No room'),false);
  assert.equal(draft.voice_segments.length,100);
  assert.equal(appendVoiceSegment(draft,capture('capture-0'),'more'),true);
});

test('browser recovery requires bounded final text and original revision/hash/retry identity',()=>{
  const valid={...capture('saved-capture'),message:'Saved'};
  assert.equal(validVoiceSegment(valid),true);
  for(const patch of [{message:12},{message:'x'.repeat(10001)},{request_id:'short'},{revision:0},{content_hash:'unknown'},{content_hash:[hash]},{target:{kind:'script'}},{target:{kind:'group'}},{target:{kind:'group',elements:[null]}},{target:{kind:'group',elements:[{kind:'group',elements:[]}]}},{target:{kind:'text',quote:123}}])assert.equal(!!validVoiceSegment({...valid,...patch}),false);
});
