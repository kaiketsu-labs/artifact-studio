const {test}=require('node:test');
const assert=require('node:assert/strict');
const {webcrypto}=require('node:crypto');
if(!globalThis.crypto)globalThis.crypto=webcrypto;
const {create}=require('../web/artifact-editor.js');

function fixture(){
  const messages=[],sent=[],states=[],errors=[],inspections=[];
  const window={postMessage:message=>messages.push(message)}, frame={contentWindow:window};
  const artifact={id:'page',revision:1,content_hash:'a'.repeat(64)};
  let fail=false;
  const editor=create({getFrame:()=>frame,getArtifact:()=>artifact,onInspector:value=>inspections.push(value),onState:value=>states.push(value),onError:value=>errors.push(value),onSend:async body=>{sent.push(body);if(fail)throw Error('offline');}});
  const reply=(type,request_id,extra={})=>editor.receive({source:window,origin:'null',data:{type,request_id,selector:'#title',text:'Original',canText:true,typography:{},...extra}});
  return {editor,messages,sent,states,errors,inspections,artifact,window,reply,setFail:value=>fail=value};
}

test('edits retain original version and target across retry with stable request id',async()=>{
  const f=fixture(),target={kind:'element',selector:'#title',label:'Heading'};
  assert.equal(f.editor.inspect(target),true);
  f.reply('artifact:inspector',f.messages.at(-1).request_id);
  assert.equal(f.editor.preview({text:'New',font_size:2,font_size_unit:'rem'}),true);
  f.reply('artifact:inspector',f.messages.at(-1).request_id);
  f.artifact.content_hash='b'.repeat(64);target.label='changed';
  f.setFail(true);assert.equal(await f.editor.send(),false);assert.equal(f.editor.isEditing(),true);
  f.setFail(false);assert.equal(await f.editor.send(),true);
  assert.deepEqual(f.sent[0],f.sent[1]);
  assert.equal(f.sent[0].content_hash,'a'.repeat(64));
  assert.equal(f.sent[0].target.label,'Heading');
  assert.deepEqual(f.sent[0].edit_intent,{action:'update',values:{text:'New',font_size:2,font_size_unit:'rem'}});
  assert.equal(f.messages.at(-1).type,'artifact:edit-accept');assert.equal(f.editor.isEditing(),false);
});

test('only the opaque selected frame may acknowledge previews, and cancel clears the draft',()=>{
  const f=fixture();f.editor.inspect({kind:'element',selector:'#title'});f.reply('artifact:inspector',f.messages.at(-1).request_id);
  f.editor.preview({font_weight:'700'});const id=f.messages.at(-1).request_id;
  assert.equal(f.editor.receive({source:{},origin:'null',data:{type:'artifact:inspector',request_id:id,selector:'#title'}}),false);
  assert.equal(f.editor.receive({source:f.window,origin:'https://other',data:{type:'artifact:inspector',request_id:id,selector:'#title'}}),false);
  assert.equal(f.editor.finish(),false);assert.equal(f.editor.inspect({kind:'element',selector:'#other'}),false);
  assert.equal(f.editor.reset(),false);assert.equal(f.editor.cancel(),true);
  assert.equal(f.messages.at(-1).type,'artifact:edit-cancel');assert.equal(f.editor.finish(),true);
});

test('invalid edits are rejected and rejected bridge previews do not become proposals',async()=>{
  const f=fixture();f.editor.inspect({kind:'element',selector:'#title'});f.reply('artifact:inspector',f.messages.at(-1).request_id);
  assert.equal(f.editor.preview({font_size:2,font_size_unit:'url(foo)'}),false);
  assert.equal(f.editor.preview({color:'red;display:none'}),false);
  assert.equal(f.editor.preview({text:'ok'}),true);const id=f.messages.at(-1).request_id;
  f.reply('artifact:edit-error',id,{message:'Rejected'});
  assert.equal(f.editor.isEditing(),false);assert.equal(await f.editor.send(),false);assert.equal(f.sent.length,0);
});

test('reverting to original values clears the draft and does not submit phantom changes',async()=>{
  const f=fixture();f.editor.inspect({kind:'element',selector:'#title'});
  f.reply('artifact:inspector',f.messages.at(-1).request_id,{typography:{font_size:2,font_size_unit:'rem',font_weight:'400'}});
  f.editor.preview({text:'Changed',font_size:3,font_size_unit:'rem'});f.reply('artifact:inspector',f.messages.at(-1).request_id);
  f.editor.preview({text:'Original'});f.reply('artifact:inspector',f.messages.at(-1).request_id);
  assert.equal(f.editor.isEditing(),true);
  f.editor.preview({font_size:2,font_size_unit:'rem'});f.reply('artifact:inspector',f.messages.at(-1).request_id);
  assert.equal(f.editor.isEditing(),false);assert.equal(f.messages.at(-1).type,'artifact:edit-cancel');
  assert.equal(await f.editor.send(),false);assert.equal(f.sent.length,0);
});

test('cancel ignores obsolete replies and successful send becomes the new local baseline',async()=>{
  const f=fixture();f.editor.inspect({kind:'element',selector:'#title'});
  f.reply('artifact:inspector',f.messages.at(-1).request_id,{typography:{font_weight:'400'}});
  f.editor.preview({text:'Changed'});const old=f.messages.at(-1).request_id;
  f.editor.cancel();const count=f.inspections.length;
  assert.equal(f.reply('artifact:inspector',old,{text:'Changed'}),false);
  assert.equal(f.inspections.length,count);assert.equal(f.editor.isEditing(),false);
  f.editor.preview({text:'Accepted'});f.reply('artifact:inspector',f.messages.at(-1).request_id,{text:'Accepted'});
  assert.equal(await f.editor.send(),true);
  f.editor.preview({text:'Accepted'});f.reply('artifact:inspector',f.messages.at(-1).request_id,{text:'Accepted'});
  assert.equal(f.editor.isEditing(),false);
  assert.deepEqual(JSON.parse(f.sent[0].target.state),{typography:{font_weight:'400'}});
});

test('rapid edits are acknowledged in order and send waits for the final preview',async()=>{
  const f=fixture();f.editor.inspect({kind:'element',selector:'#title'});
  f.reply('artifact:inspector',f.messages.at(-1).request_id);
  f.editor.preview({text:'A'});const first=f.messages.at(-1).request_id;
  f.editor.preview({text:'AB'});const last=f.messages.at(-1).request_id;
  const sending=f.editor.send();
  f.reply('artifact:inspector',first,{text:'A'});
  assert.equal(f.sent.length,0);
  f.reply('artifact:inspector',last,{text:'AB'});
  assert.equal(await sending,true);
  assert.equal(f.sent[0].edit_intent.values.text,'AB');
});
