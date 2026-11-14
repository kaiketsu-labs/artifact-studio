const {test}=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('../web/native-conversation.js');

test('a lost acknowledgement retries the exact saved payload after revision and routing changes',async()=>{
  const stored=new Map(),attempts=[];
  let loseResponse=true;
  const chat=create({api:async(path,body)=>{
    attempts.push(structuredClone(body));
    const prior=stored.get(body.request_id);
    if(prior){assert.deepEqual(body,prior.body);return prior.receipt;}
    const receipt={id:stored.size+1};
    stored.set(body.request_id,{body:structuredClone(body),receipt});
    if(loseResponse){loseResponse=false;throw new Error('Response lost after persistence');}
    return receipt;
  }});
  const files=[{name:'brief.txt',data:'ZmljdGlvbmFs'}];
  const target={kind:'element',selector:'#heading',label:'Original heading'};
  const draft={message:'Clarify this heading',requestId:'original-request',contentHash:'artifact-v1',
    slideContentHash:'slide-v1',slideId:'cover',target,files,channel:'shared',assigned:'original-agent',
    forAgent:true,includeSharedContext:true,collaboratorRequestGeneration:4};
  await assert.rejects(chat.send('deck',draft),/Response lost/);
  assert.equal(chat.hasPendingRequest('deck',draft.requestId),true);
  assert.equal(chat.hasPendingRequest('another-deck',draft.requestId),false);
  files[0].data='changed';target.selector='#another-heading';
  const receipt=await chat.send('deck',{...draft,contentHash:'artifact-v2',slideContentHash:'slide-v2',
    assigned:'another-agent',includeSharedContext:false,collaboratorRequestGeneration:5});
  assert.equal(receipt.id,1);
  assert.equal(stored.size,1);
  assert.deepEqual(attempts[1],attempts[0]);
  assert.equal(attempts[0].collaborator_request_generation,4);
  assert.equal(chat.hasPendingRequest('deck',draft.requestId),false);
});

test('a definitive stale rejection ends uncertainty and keeps the error actionable',async()=>{
  let attempt=0;
  const chat=create({api:async()=>{
    if(++attempt===1)throw Object.assign(new Error('Server response uncertain'),{status:503});
    throw Object.assign(new Error('The slide changed. Review the current version first.'),{status:409});
  }});
  const draft={message:'Review',requestId:'uncertain-request',contentHash:'artifact-v1',slideId:'cover',
    slideContentHash:'slide-v1',assigned:'agent',forAgent:true};
  await assert.rejects(chat.send('deck',draft),{status:503});
  assert.equal(chat.hasPendingRequest('deck',draft.requestId),true);
  await assert.rejects(chat.send('deck',draft),error=>error.status===409&&error.stale===true);
  assert.equal(chat.hasPendingRequest('deck',draft.requestId),false);
});

test('unassigned shared discussion remains version independent',async()=>{
  let sent;
  const chat=create({api:async(path,body)=>{sent=body;return{id:1};}});
  await chat.send('deck',{message:'Discuss',requestId:'shared-discussion',slideId:'cover',
    contentHash:'old-artifact',slideContentHash:'old-slide'});
  assert.equal(sent.content_hash,'');
  assert.equal('slide_content_hash' in sent,false);
  assert.equal(sent.assigned,'');
});
